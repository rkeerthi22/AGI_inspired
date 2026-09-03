"""F66: truthful unattended retrieval plus complete critic/citation accounting."""

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERMES = Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
sys.path[:0] = [str(ROOT / "orchestrator"), str(HERMES)]

import citecheck
import evaluation
import hermes_capabilities

checks = 0
fails = 0


def check(name, got, want=True):
    global checks, fails
    checks += 1
    if got != want:
        fails += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")
    else:
        print(f"PASS {name}")


# The grant is explicit and process-local; without it browser selection stays untouched.
initial = hermes_capabilities.install_harness_capabilities(unattended_browser=False)
check("unattended browser requires explicit grant", initial["browser"]["authorized"], False)

installed = hermes_capabilities.install_harness_capabilities(unattended_browser=True)
check("direct extractor is real bounded static HTTP", installed["web_extract"], "bounded-static-http")
check("browser grant selects local headless mode", installed["browser"]["mode"], "builtin-local-headless")
check("browser executable exists", Path(installed["browser"]["executable"]).is_file())
check("browser executable exported to child tools", os.environ.get("AGENT_BROWSER_EXECUTABLE_PATH"),
      installed["browser"]["executable"])

from tools.registry import registry

extract = registry.get_entry("web_extract")
description = extract.schema["description"]
check("extract schema says direct fetch", "Directly fetch static" in description)
check("extract schema disclaims JavaScript", "does not execute JavaScript" in description)
check("extract schema points dynamic content to browser", "browser capability" in description)
check("interactive browser_exec is hidden", registry.get_entry("browser_exec").check_fn(), False)
check("built-in browser navigation is available", registry.get_entry("browser_navigate").check_fn(), True)


class _DynamicFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = b"""<!doctype html><html><body><main id='app'></main><script>
let annual = false;
function render() {
  const cadence = annual ? 'Annual billing' : 'Monthly billing';
  const price = annual ? '$200/year' : '$20/month';
  document.getElementById('app').innerHTML = `
    <h1>JavaScript Pricing Fixture</h1>
    <button id='billing'>${annual ? 'Monthly billing' : 'Annual billing'}</button>
    <table aria-label='Pricing plans'><tr><th>Tier</th><th>Price</th><th>Seats</th></tr>
    <tr><td>Fixture Pro</td><td>${price}</td><td>3 seats</td></tr></table>
    <p>${cadence}</p>`;
  document.getElementById('billing').onclick = () => { annual = !annual; render(); };
}
render();
</script></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, _format, *_args):
        return


# Behavioral browser preflight: render JavaScript and interact with a local-only
# fixture. This contacts no provider or external host and catches a capability
# that is merely advertised but cannot launch, render, snapshot, or click.
from tools import browser_tool

fixture_server = ThreadingHTTPServer(("127.0.0.1", 0), _DynamicFixtureHandler)
fixture_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
fixture_thread.start()
fixture_task = "f66-dynamic-browser-fixture"
test_tmp_root = ROOT / ".tmp"
test_tmp_root.mkdir(parents=True, exist_ok=True)
original_tempdir = tempfile.tempdir
try:
    # ignore_cleanup_errors: the browser subprocess's stderr handle can lag
    # process termination on Windows (WinError 32); the .tmp root is disposable.
    with tempfile.TemporaryDirectory(dir=test_tmp_root,
                                     ignore_cleanup_errors=True) as browser_tmp:
        tempfile.tempdir = browser_tmp
        try:
            fixture_url = f"http://127.0.0.1:{fixture_server.server_port}/pricing"
            navigated = json.loads(browser_tool.browser_navigate(fixture_url, task_id=fixture_task))
            check("local JavaScript browser navigation succeeds", navigated.get("success"), True)
            monthly_text = json.dumps(navigated, ensure_ascii=False)
            check("browser renders JavaScript-populated monthly price", "$20/month" in monthly_text, True)
            check("browser renders JavaScript-populated seat count", "3 seats" in monthly_text, True)
            match = re.search(r'Annual billing.*?ref[=:\\" ]+([A-Za-z0-9_-]+)', monthly_text)
            check("browser exposes annual-toggle accessibility ref", bool(match), True)
            if match:
                clicked = json.loads(browser_tool.browser_click(match.group(1), task_id=fixture_task))
                check("browser clicks JavaScript billing toggle", clicked.get("success"), True)
                annual = json.loads(browser_tool.browser_snapshot(task_id=fixture_task))
                annual_text = json.dumps(annual, ensure_ascii=False)
                check("browser captures JavaScript-populated annual price", "$200/year" in annual_text, True)
        finally:
            browser_tool.cleanup_browser(fixture_task)
finally:
    tempfile.tempdir = original_tempdir
    fixture_server.shutdown()
    fixture_server.server_close()
    fixture_thread.join(timeout=5)

async def exercise_handler():
    with patch.object(hermes_capabilities, "_fetch_static",
                      return_value={"url": "https://example.com", "content": "real page",
                                    "title": "Example", "status": 200, "error": ""}) as fetch:
        result = json.loads(await extract.handler(
            {"urls": ["https://example.com"], "char_limit": 3000}))
    return result, fetch


handler_result, fetch = asyncio.run(exercise_handler())
check("extract handler returns fetched content", handler_result["results"][0]["content"], "real page")
check("extract handler invokes actual fetch path", fetch.call_count, 1)

# The static fetcher blocks metadata/private targets before opening a session.
blocked = hermes_capabilities._fetch_static("http://169.254.169.254/latest/meta-data", 3000)
check("direct fetch keeps URL safety", blocked["error"], "Blocked by URL safety policy")

# Citation URLs are stable-order deduplicated before the fetch fan-out.
dupe_text = (
    "Price $20 https://example.com/pricing\n"
    "Cadence monthly https://example.com/pricing\n"
    "Review 4.0 https://example.com/reviews\n"
)
citations = citecheck.extract_citations(dupe_text)
check("citation extraction deduplicates URL", [c["url"] for c in citations],
      ["https://example.com/pricing", "https://example.com/reviews"])

td = ROOT / "runs"
if True:
    runs = Path(td)
    row = {"task_id": 660066, "pass_criteria": "Must be cited"}
    cleanup_paths = [runs / f"task660066_{suffix}" for suffix in (
        "critic.usage.json", "citation_evidence.json", "mission.usage.json",
        "worker.usage.retrieval.jsonl")]
    for path in cleanup_paths:
        path.unlink(missing_ok=True)
    critic_usage = {}
    evidence = [
        {"url": "https://example.com/pricing", "reachable": True,
         "http_status": 200, "literal": "$20", "literal_found": True,
         "line": "Price $20"},
        {"url": "https://example.com/reviews", "reachable": True,
         "http_status": 200, "literal": "4.0", "literal_found": True,
         "line": "Review 4.0"},
    ]

    def chat(_model, _prompt, **kwargs):
        kwargs["usage_out"].update(input_tokens=101, output_tokens=7)
        return "VERDICT: PASS\nComplete."

    with patch.object(evaluation, "RUNS", runs), \
         patch.object(evaluation.citecheck, "verify", return_value=evidence), \
         patch.object(evaluation.citecheck, "summarize",
                      return_value={"checked": 2, "dead": 0, "dead_frac": 0,
                                    "literal_checked": 2, "literal_missing": 0}), \
         patch.object(evaluation.citecheck, "is_hard_fail", return_value=False), \
         patch.object(evaluation.policy, "manager_call_budget_breached", return_value=False), \
         patch.object(evaluation.policy, "record_manager_call"), \
         patch.object(evaluation.execution, "ollama_chat", side_effect=chat):
        verdict, _ = evaluation.run_critic(
            row, "brief", {"critic": {"model": "critic"}}, False,
            usage_out=critic_usage)

        check("unchanged critic verdict", verdict, "pass")
        check("critic call persisted", critic_usage["api_calls"], 1)
        check("critic input persisted", critic_usage["input_tokens"], 101)
        check("critic output persisted", critic_usage["output_tokens"], 7)
        check("critic total persisted", critic_usage["total_tokens"], 108)
        check("citation fetch accounting persisted", critic_usage["citation_fetches"], 2)
        check("critic usage file exists", (runs / "task660066_critic.usage.json").is_file())
        evidence_file = runs / "task660066_citation_evidence.json"
        check("citation evidence file exists", evidence_file.is_file())
        persisted = json.loads(evidence_file.read_text(encoding="utf-8"))
        check("complete evidence table persisted", persisted["evidence"], evidence)

        (runs / "task660066_worker.usage.retrieval.jsonl").write_text(
            json.dumps({"event": "research_finished", "executed_retrieval_calls": 6,
                        "rejected_calls": 1}) + "\n", encoding="utf-8")
        mission = evaluation.build_mission_usage(
            660066, {"input_tokens": 1000, "output_tokens": 100, "api_calls": 5,
                 "retrieval_finalization_calls": 1}, critic_usage)
        check("mission calls reconcile", mission["api_calls"], 6)
        check("mission input reconciles", mission["input_tokens"], 1101)
        check("mission output reconciles", mission["output_tokens"], 107)
        check("mission total reconciles", mission["total_tokens"], 1208)
        check("agent retrieval accounting visible", mission["executed_agent_retrieval_calls"], 6)
        check("citation retrieval accounting visible", mission["citation_fetches"], 2)
        check("all external retrieval visible", mission["total_external_retrieval_calls"], 8)
        check("mission usage file exists", (runs / "task660066_mission.usage.json").is_file())

    for path in cleanup_paths:
        path.unlink(missing_ok=True)

execution_source = (ROOT / "orchestrator" / "execution.py").read_text(encoding="utf-8")
launcher_source = (ROOT / "orchestrator" / "controlled_hermes.py").read_text(encoding="utf-8")
check("worker explicitly grants unattended browser", 'env["HARNESS_UNATTENDED_BROWSER"] = "1"' in execution_source)
check("launcher installs scoped capabilities", "install_harness_capabilities(" in launcher_source)

print(f"\n{checks - fails}/{checks} assertions passed")
raise SystemExit(1 if fails else 0)
