"""F63: retrieval progress is enforced outside model/query decisions."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from retrieval_progress import (  # noqa: E402
    RetrievalPolicy,
    RetrievalProgressController,
    tool_stage,
)
import execution  # noqa: E402
from controlled_hermes import merge_finalization_usage  # noqa: E402


checks = 0


def check(label, actual, expected):
    global checks
    checks += 1
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def useful(url, topic):
    return (f"Source {url} provides independently observable evidence about {topic}. "
            + " ".join(f"fact{i}_{topic}" for i in range(30)))


policy = RetrievalPolicy(low_novelty_limit=2, max_calls=(3, 3, 2),
                         min_search_chars=20, min_content_chars=20)
c = RetrievalProgressController(policy)

# Future strategies cannot be selected early; model preference is subordinate.
d = c.before("browser_exec", {"url": "https://example.com"})
check("browser blocked while search required", d["code"], "retrieval_strategy_redirect")
check("blocked attempt did not consume budget", c.executed_calls, 0)

# Query reformulation cannot reset family-level novelty state.
check("first search allowed", c.before("web_search", {"query": "alpha"}), None)
check("first useful result no transition",
      c.after("web_search", {"query": "alpha"},
              useful("https://one.example/a?utm_source=x", "alpha"), False), None)
check("reformulated search allowed", c.before("web_search", {"query": "totally different"}), None)
check("same canonical evidence is low novelty",
      c.after("web_search", {"query": "totally different"},
              useful("https://one.example/a?utm_source=y", "alpha"), False), None)
transition = c.after("web_search", {"query": "third wording"}, "no useful results", False)
check("second low novelty forces transition", transition["code"],
      "retrieval_strategy_transition")
check("search budget exactly bounded", c.state.calls[0], 3)
check("required direct fetch", c.required_strategy, "direct_fetch")
check("search now forcibly redirected",
      c.before("web_search", {"query": "fourth wording"})["code"],
      "retrieval_strategy_halt")
check("redirect still consumes no retrieval call", c.executed_calls, 3)
check("second rejected attempt is terminal", c.state.rejected_calls, 2)

# Direct HTTP hidden in a general code tool is still part of the direct stage.
direct_args = {"code": "import requests; requests.get('https://one.example/a')"}
check("code HTTP classified as direct", tool_stage("execute_code", direct_args), 1)
check("opaque code cannot hide retrieval", tool_stage("execute_code", {"code": "print(2+2)"}), 1)
for index in range(3):
    check(f"direct {index} allowed", c.before("execute_code", direct_args), None)
    transition = c.after("execute_code", direct_args,
                         useful(f"https://direct.example/{index}", f"direct{index}"), False)
check("direct max forces browser despite novelty", transition["code"],
      "retrieval_strategy_transition")
check("required browser", c.required_strategy, "browser")

# Browser has a strict cap, then only partial output is permitted.
for index in range(2):
    args = {"url": f"https://browser.example/{index}"}
    check(f"browser {index} allowed", c.before("browser_exec", args), None)
    transition = c.after("browser_exec", args,
                         useful(args["url"], f"browser{index}"), False)
check("browser max forces partial", transition["code"], "retrieval_strategy_transition")
check("partial terminal state", c.required_strategy, "partial_result")
check("executed calls globally bounded", c.executed_calls, 8)
check("all later retrieval blocked",
      c.before("web_extract", {"url": "https://new.example"})["code"],
      "retrieval_strategy_halt")
check("blocked call cannot exceed bound", c.executed_calls, 8)

# Capacity is reserved before dispatch, so one parallel tool batch cannot race
# past the ceiling while all results are still in flight.
parallel = RetrievalProgressController(policy)
for index in range(3):
    check(f"parallel reservation {index} allowed",
          parallel.before("web_search", {"query": str(index)}), None)
blocked_parallel = parallel.before("web_search", {"query": "overshoot"})
check("parallel overshoot blocked", blocked_parallel["code"],
      "retrieval_strategy_redirect")
check("parallel reservation forces next strategy", parallel.required_strategy,
      "direct_fetch")
for index in range(3):
    parallel.after("web_search", {"query": str(index)},
                   useful(f"https://parallel.example/{index}", str(index)), False)
check("parallel completions capped", parallel.state.calls[0], 3)
check("parallel completions do not double-transition", parallel.required_strategy,
      "direct_fetch")

proxy = RetrievalProgressController(policy)
check("delegated retrieval is always blocked",
      proxy.before("delegate_task", {"prompt": "research this"})["code"],
      "retrieval_strategy_redirect")
check("delegation consumes no hidden budget", proxy.executed_calls, 0)
check("computer use counted as browser/other", tool_stage("computer_use", {}), 2)
check("future plugin tool cannot escape", tool_stage("mcp_research_magic", {}), 0)

# Full ceiling: eight successful calls, two rejected attempts, one and only
# one compact evidence-only finalizer. No model-selected tool name can escape.
ceiling = RetrievalProgressController(policy)
for stage, name in ((0, "web_search"), (1, "terminal"), (2, "computer_use")):
    for index in range(policy.max_calls[stage]):
        check(f"ceiling stage {stage} call {index} allowed",
              ceiling.before(name, {"index": index}), None)
        ceiling.after(name, {"index": index},
                      useful(f"https://ceiling.example/{stage}/{index}", str(index)), False)
check("all eight calls executed", ceiling.executed_calls, 8)
check("first post-retrieval tool rejected",
      ceiling.before("read_file", {"path": "escape"})["terminal"], False)
check("second post-retrieval tool terminates research",
      ceiling.before("write_file", {"path": "escape"})["terminal"], True)
check("two rejected attempts total", ceiling.state.rejected_calls, 2)
check("inference call ceiling includes finalizer", ceiling.total_call_ceiling, 11)
ceiling.finalization_started()
check("exactly one finalization call", ceiling.state.finalization_calls, 1)
try:
    ceiling.finalization_started()
    raise AssertionError("second finalization unexpectedly allowed")
except RuntimeError:
    checks += 1
check("bounded evidence storage", ceiling.state.evidence_chars <= 30000, True)
check("finalization prompt is compact",
      len(ceiling.finalization_prompt("mission")) < 40000, True)

accounting_audit = ROOT / "workspace" / "test_f63_accounting.jsonl"
try:
    accounting_audit.unlink(missing_ok=True)
    accounting = RetrievalProgressController(policy, accounting_audit)
    accounting.research_finished(api_calls=10, input_tokens=100, output_tokens=20,
                                 total_tokens=120)
    accounting.finalization_started()
    accounting.finalization_finished(success=True, input_tokens=30, output_tokens=10)
    accounting_rows = [json.loads(line) for line in
                       accounting_audit.read_text(encoding="utf-8").splitlines()]
    check("complete accounting event order", [row["event"] for row in accounting_rows],
          ["research_finished", "finalization_started", "finalization_finished"])
    check("research API accounting", accounting_rows[0]["api_calls"], 10)
    check("finalizer token accounting",
          (accounting_rows[2]["input_tokens"], accounting_rows[2]["output_tokens"]),
          (30, 10))
finally:
    accounting_audit.unlink(missing_ok=True)

merged = merge_finalization_usage(
    {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "api_calls": 10},
    {"input_tokens": 30, "output_tokens": 10},
)
check("final usage merged", merged,
      {"input_tokens": 130, "output_tokens": 30, "total_tokens": 160,
       "api_calls": 11, "retrieval_finalization_calls": 1})

# The rejection ceiling is global, not a streak that successful work can reset.
separated = RetrievalProgressController(policy)
first_reject = separated.before("browser_exec", {})
check("first separated rejection continues", first_reject["terminal"], False)
separated.before("web_search", {"query": "progress"})
separated.after("web_search", {"query": "progress"},
                useful("https://separated.example", "progress"), False)
second_reject = separated.before("terminal", {"command": "escape"})
check("second separated rejection terminates", second_reject["terminal"], True)
check("global rejected count pinned at two", separated.state.rejected_calls, 2)

# Failures count as low novelty and audit contains measurements/transitions only,
# never query text or raw fetched content.
audit = ROOT / "workspace" / "test_f63_retrieval.jsonl"
try:
    audit.unlink(missing_ok=True)
    audited = RetrievalProgressController(policy, audit)
    audited.after("web_search", {"query": "secret query"}, "Error: backend", True)
    t = audited.after("web_search", {"query": "another secret"}, "Error: backend", True)
    check("two failed searches transition", t["code"], "retrieval_strategy_transition")
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    check("audit event order", [r["event"] for r in rows],
          ["observation", "observation", "transition"])
    raw = audit.read_text(encoding="utf-8")
    check("audit excludes query", "secret query" in raw, False)
    check("audit excludes raw result", "backend" in raw, False)
finally:
    audit.unlink(missing_ok=True)

# Production worker invocation uses Hermes' own venv Python plus the adapter
# launcher and gives each attempt an adjacent audit path.
usage = ROOT / "workspace" / "test_f63.usage.json"
usage.unlink(missing_ok=True)
captured = {}
real_which = execution.shutil.which
real_run = execution.subprocess.run
try:
    execution.shutil.which = lambda name: str(
        Path("C:/Hermes/venv/Scripts/hermes.exe"))

    class Result:
        stdout = "controlled output"

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, kwargs=kwargs)
        return Result()

    execution.subprocess.run = fake_run
    out, measured = execution.hermes_worker(
        "mission", {"provider": "p", "model": "m"}, usage)
    check("worker output preserved", out, "controlled output")
    check("missing usage remains empty", measured, {})
    check("venv Python selected", captured["cmd"][0].endswith("python.exe"), True)
    check("controlled launcher selected",
          captured["cmd"][1].endswith("controlled_hermes.py"), True)
    check("per-attempt audit injected",
          captured["kwargs"]["env"]["HARNESS_RETRIEVAL_AUDIT"].endswith(
              "test_f63.usage.retrieval.jsonl"), True)
finally:
    execution.shutil.which = real_which
    execution.subprocess.run = real_run
    usage.unlink(missing_ok=True)

print(f"F63 retrieval-progress controller: {checks}/{checks} assertions passed")
