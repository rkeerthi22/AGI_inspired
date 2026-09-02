"""F63: retrieval progress is enforced outside model/query decisions."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from retrieval_progress import (  # noqa: E402
    DYNAMIC_BROWSER_PROFILE,
    RetrievalPolicy,
    RetrievalProgressController,
    retrieval_policy_for_profile,
    tool_stage,
)
import execution  # noqa: E402
import execution_pause  # noqa: E402
from controlled_hermes import (finalizer_call_options, finalizer_provider,
                               merge_finalization_usage)  # noqa: E402


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
      "retrieval_strategy_redirect")
check("redirect still consumes no retrieval call", c.executed_calls, 3)
check("nonconsecutive second rejection remains recoverable", c.state.rejected_calls, 2)

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
second_parallel = parallel.before("web_search", {"query": "same batch overshoot"})
check("same parallel batch is not falsely terminal", second_parallel["terminal"], False)
check("both blocked calls remain accounted", parallel.state.rejected_calls, 2)
check("parallel batch counts one feedback opportunity",
      parallel.state.redirect_violations, 1)
for index in range(3):
    parallel.after("web_search", {"query": str(index)},
                   useful(f"https://parallel.example/{index}", str(index)), False)
check("parallel completions capped", parallel.state.calls[0], 3)
check("parallel completions do not double-transition", parallel.required_strategy,
      "direct_fetch")
post_feedback = parallel.before("web_search", {"query": "ignored after feedback"})
check("post-feedback repeat terminates", post_feedback["terminal"], True)

# Once retrieval is exhausted, parallel siblings from one assistant response
# still represent one feedback opportunity. The old stage>=3 branch counted
# every sibling as an independent redirect violation (M7: four calls -> four
# violations) because rejected calls have no reservation released by after().
partial_parallel = RetrievalProgressController(policy)
partial_parallel.state.stage = 3
partial_parallel.begin_tool_batch()
partial_decisions = [
    partial_parallel.before("web_search", {"query": f"sibling-{index}"})
    for index in range(4)
]
partial_parallel.end_tool_batch()
check("stage-3 parallel siblings all rejected",
      [decision["code"] for decision in partial_decisions],
      ["retrieval_strategy_redirect"] * 4)
check("stage-3 parallel batch counts one redirect violation",
      partial_parallel.state.redirect_violations, 1)
check("stage-3 parallel siblings remain accounted",
      partial_parallel.state.rejected_calls, 4)
partial_parallel.begin_tool_batch()
next_feedback = partial_parallel.before("web_search", {"query": "after-feedback"})
partial_parallel.end_tool_batch()
check("next stage-3 feedback round is terminal", next_feedback["terminal"], True)
check("stage-3 feedback rounds stop at limit",
      partial_parallel.state.redirect_violations, 2)

proxy = RetrievalProgressController(policy)
check("delegated retrieval is always blocked",
      proxy.before("delegate_task", {"prompt": "research this"})["code"],
      "retrieval_strategy_redirect")
check("delegation consumes no hidden budget", proxy.executed_calls, 0)
check("computer use counted as browser/other", tool_stage("computer_use", {}), 2)
check("future plugin tool cannot escape", tool_stage("mcp_research_magic", {}), 0)

# Approved skill loading is bounded setup, not retrieval. A live feedback retry
# tried skill_view twice before its first search; charging those as unknown
# browser escapes exhausted the rejection budget and forced an empty finalizer.
setup = RetrievalProgressController(policy)
check("first skill setup allowed", setup.before("skill_view", {"name": "a"}), None)
check("second skill setup allowed", setup.before("skill_view", {"name": "a"}), None)
check("setup does not consume retrieval", setup.executed_calls, 0)
check("setup calls are counted", setup.state.setup_calls, 2)
check("third setup is redirected", setup.before("skill_view", {"name": "a"})["code"],
      "retrieval_strategy_redirect")
setup.state.stage = 3
check("setup disabled after research",
      setup.before("skill_view", {"name": "a"})["code"],
      "retrieval_strategy_halt")

# Full budget: eight successful calls, a bounded consecutive-feedback halt, and
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
check("consecutive halt uses two rejected attempts", ceiling.state.rejected_calls, 2)
check("inference call ceiling includes setup, rejects, and finalizer",
      ceiling.total_call_ceiling, 14)
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
check("Hermes BytePlus selector maps to provider-neutral finalizer",
      finalizer_provider("custom:byteplus-coding"), "byteplus_coding")
check("controlled Hermes passes BytePlus provider into finalization",
      finalizer_call_options(["-z", "ping", "--provider", "custom:byteplus-coding"]),
      {"provider": "byteplus_coding", "purpose": "retrieval_finalization"})

# A successful compliant call resets the consecutive-feedback streak, while a
# separate global ceiling still bounds repeated redirections.
separated = RetrievalProgressController(policy)
first_reject = separated.before("browser_exec", {})
check("first separated rejection continues", first_reject["terminal"], False)
separated.before("web_search", {"query": "progress"})
separated.after("web_search", {"query": "progress"},
                useful("https://separated.example", "progress"), False)
second_reject = separated.before("terminal", {"command": "escape"})
check("second nonconsecutive rejection continues", second_reject["terminal"], False)
third_reject = separated.before("terminal", {"command": "escape-again"})
check("consecutive repeat reaches bounded halt", third_reject["terminal"], True)
check("global rejected count pinned at three", separated.state.rejected_calls, 3)

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
    check("audit classifies tool failure without raw content",
          rows[0]["result_class"], "tool_error")
    check("audit records active profile", rows[0]["profile"], "default")
finally:
    audit.unlink(missing_ok=True)

# Production worker invocation uses Hermes' own venv Python plus the adapter
# launcher and gives each attempt an adjacent audit path.
usage = ROOT / "workspace" / "test_f63.usage.json"
usage.unlink(missing_ok=True)
audit_attempt = usage.with_suffix(".retrieval.jsonl")
audit_attempt.write_text("stale prior attempt\n", encoding="utf-8")
captured = {}
real_which = execution.shutil.which
real_auth_env = execution.provider_transport.authentication_env_from_config
real_pause = execution_pause.pause_engaged
try:
    execution.shutil.which = lambda name: str(
        Path("C:/Hermes/venv/Scripts/hermes.exe"))

    class FakePipeDrain:
        text = ""

    class FakeProc:
        returncode = 0
        def wait(self, timeout=None):
            pass
        def kill(self):
            pass

    def fake_create_contained(cmd, cwd=None, env=None):
        captured.update(cmd=cmd, cwd=cwd, env=env)
        return FakeProc(), 12345, FakePipeDrain(), FakePipeDrain()

    import pty_daemon
    real_create = pty_daemon.create_contained_process
    pty_daemon.create_contained_process = fake_create_contained
    execution.provider_transport.authentication_env_from_config = lambda cfg: {
        "ARK_API_KEY": "test-only-placeholder"}
    # Mock pause_engaged to False so the ESTOP watchdog doesn't kill
    # the mock process (real ESTOP is engaged on this system).
    execution_pause.pause_engaged = lambda: False
    out, measured = execution.hermes_worker(
        "mission", {"provider": "p", "model": "m",
                    "authentication_reference": "env:ARK_API_KEY"}, usage,
        retrieval_profile=DYNAMIC_BROWSER_PROFILE)
    check("worker output preserved", out, "")
    check("missing usage remains empty", measured, {})
    check("venv Python selected", captured["cmd"][0].endswith("python.exe"), True)
    check("controlled launcher selected",
          captured["cmd"][1].endswith("controlled_hermes.py"), True)
    check("per-attempt audit injected",
          captured["env"]["HARNESS_RETRIEVAL_AUDIT"].endswith(
              "test_f63.usage.retrieval.jsonl"), True)
    check("structured retrieval profile injected",
          captured["env"]["HARNESS_RETRIEVAL_PROFILE"],
          DYNAMIC_BROWSER_PROFILE)
    check("declared provider credential injected into Hermes child only",
          captured["env"].get("ARK_API_KEY"), "test-only-placeholder")
    check("prior attempt audit removed before launch", audit_attempt.exists(), False)
finally:
    execution.shutil.which = real_which
    pty_daemon.create_contained_process = real_create
    execution.provider_transport.authentication_env_from_config = real_auth_env
    execution_pause.pause_engaged = real_pause
    usage.unlink(missing_ok=True)
    audit_attempt.unlink(missing_ok=True)

# The structured profile must survive the failover wrapper too; otherwise the
# first quota reroute would silently fall back to the generic search-first policy.
real_candidates = execution._failover_candidates
real_worker = execution.hermes_worker
profiles_seen = []
try:
    execution._failover_candidates = lambda cfg, allow_local=True: [cfg]

    def profile_worker(prompt, cfg, path, timeout=None, retrieval_profile=None):
        profiles_seen.append(retrieval_profile)
        return "usable controlled output", {"total_tokens": 1}

    execution.hermes_worker = profile_worker
    _, _, _, exhausted = execution.worker_with_failover(
        "mission", {"provider": "p", "model": "m"}, usage,
        "test profile", retrieval_profile=DYNAMIC_BROWSER_PROFILE)
    check("failover wrapper preserves profile", profiles_seen,
          [DYNAMIC_BROWSER_PROFILE])
    check("profile-preserving attempt is usable", exhausted, False)
finally:
    execution._failover_candidates = real_candidates
    execution.hermes_worker = real_worker

# M2's explicit profile starts in browser mode and reallocates the unchanged
# eight-call retrieval ceiling to dynamic interactions.
dynamic_policy = retrieval_policy_for_profile(DYNAMIC_BROWSER_PROFILE)
dynamic = RetrievalProgressController(dynamic_policy)
check("dynamic profile starts in browser", dynamic.required_strategy, "browser")
check("dynamic profile keeps eight-call ceiling", sum(dynamic_policy.max_calls), 8)
check("dynamic first browser navigate allowed",
      dynamic.before("browser_navigate", {"url": "https://app.aiprm.com/pricing?lang=en"}),
      None)
large_snapshot = useful("https://app.aiprm.com/pricing?lang=en", "monthly") + "x" * 12000
dynamic.after("browser_navigate", {}, large_snapshot, False)
check("dynamic evidence excerpt expanded", len(dynamic.evidence[0]["content"]), 10000)
check("dynamic search is outside profile",
      dynamic.before("web_search", {"query": "aiprm pricing"})["code"],
      "retrieval_strategy_redirect")
try:
    retrieval_policy_for_profile("invented-profile")
    raise AssertionError("unknown profile unexpectedly accepted")
except ValueError:
    checks += 1

cohort = json.loads((ROOT / "workspace" / "validation" / "cohort_missions.json")
                    .read_text(encoding="utf-8"))
m2 = next(item for item in cohort["specs"] if item["id"] == "M2")
check("M2 declares explicit retrieval profile", m2["retrieval_profile"],
      DYNAMIC_BROWSER_PROFILE)
check("M2 pins canonical pricing URL",
      "https://app.aiprm.com/pricing?lang=en" in m2["spec"], True)
cohort_runner = (ROOT / "workspace" / "validation" / "run_cohort.py").read_text(
    encoding="utf-8")
check("cohort propagates structured profile",
      'retrieval_profile=spec.get("retrieval_profile")' in cohort_runner, True)

print(f"F63 retrieval-progress controller: {checks}/{checks} assertions passed")
