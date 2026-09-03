"""F104: structured critic output (weak-AI efficiency, spec-compliance half).

The critic is now prompted to emit a parseable ``MISSING:`` bullet list on FAIL
instead of "one sentence why" prose, and the retry path turns that list into a
numbered checklist for the worker. A cheap model can act on "fix items 1-3" but
not on "the brief felt incomplete". This is the spec-compliance counterpart to
F103's citation-evidence wiring.

Two sections, no live LLM call:
  - S1 _extract_missing_list: the parser is a pure function -- bullet/numbered
    extraction, backwards-compatible fallback to [] for the legacy prose stubs
    ("VERDICT: FAIL\\nMissing citations." has no MISSING: colon header), and
    a trailing context sentence is never misread as a gap.
  - S2 run_critic prompt: stubs citecheck/policy/execution exactly as test_f57
    S3 does, captures the prompt sent to the model, and pins that it now asks
    for the structured MISSING format (so the evaluation.py change cannot
    silently regress to prose).
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluation as ev  # noqa: E402
import execution  # noqa: E402
import policy  # noqa: E402
import citecheck  # noqa: E402
import task_runner as tr  # noqa: E402
from _silence import silence_log  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


# S1. _extract_missing_list ─────────────────────────────────────────────────

print("=== 1. _extract_missing_list parser ===")

structured = (
    "VERDICT: FAIL\n"
    "MISSING:\n"
    "- source URL for the Notion pricing claim\n"
    "- competitor pricing for Shopify Basic\n"
    "- weekly traffic figure for week 2\n"
    "Overall the brief was thin on sourcing."
)
check("structured bullets parsed",
      tr._extract_missing_list(structured),
      ["source URL for the Notion pricing claim",
       "competitor pricing for Shopify Basic",
       "weekly traffic figure for week 2"])
check("trailing context sentence not captured",
      "Overall the brief was thin" in " ".join(tr._extract_missing_list(structured)),
      False)

numbered = (
    "VERDICT: FAIL\n"
    "MISSING:\n"
    "1) first gap\n"
    "2. second gap\n"
)
check("numbered list parsed",
      tr._extract_missing_list(numbered), ["first gap", "second gap"])

# Legacy prose stubs used by test_f57/test_f58 ("Missing citations." etc.) have
# no "MISSING:" colon header, so the parser MUST return [] and let the caller
# fall through to the prose-injection path. This is the backwards-compat guard.
check("legacy prose stub -> [] (no colon header)",
      tr._extract_missing_list("VERDICT: FAIL\nMissing citations."), [])
check("no MISSING header -> []",
      tr._extract_missing_list("VERDICT: FAIL\nThe brief was incomplete."), [])
check("MISSING header with no bullets -> []",
      tr._extract_missing_list("VERDICT: FAIL\nMISSING:\nThe brief was thin."), [])
check("case-insensitive missing: header",
      tr._extract_missing_list("missing:\n- a gap\n- b gap"), ["a gap", "b gap"])
check("empty string -> []", tr._extract_missing_list(""), [])
check("None -> []", tr._extract_missing_list(None), [])
oversized = "MISSING:\n" + "\n".join(
    f"- gap {index} " + ("x" * 400) for index in range(30))
bounded = tr._extract_missing_list(oversized)
check("structured retry list has a hard item cap",
      len(bounded), tr.MAX_RETRY_GAPS)
check("structured retry items have a hard character cap",
      max(map(len, bounded)), tr.MAX_RETRY_GAP_CHARS)


# S2. run_critic prompt asks for the structured MISSING format ──────────────

print("\n=== 2. run_critic prompt elicits MISSING list ===")

captured = {}


def capture_chat(model, prompt, trace_path=None, usage_out=None, **kw):
    captured["model"] = model
    captured["prompt"] = prompt
    if usage_out is not None:
        usage_out.update(input_tokens=0, output_tokens=0)
    return "VERDICT: PASS\nLooks good."


tmp = Path(tempfile.mkdtemp(prefix="f104_"))
(tmp / "runs").mkdir()
saved = {
    "ev_RUNS": ev.RUNS,
    "exec_chat": execution.ollama_chat,
    "cc_verify": citecheck.verify,
    "cc_summarize": citecheck.summarize,
    "cc_hard": citecheck.is_hard_fail,
    "mgr_breached": policy.manager_call_budget_breached,
    "mgr_record": policy.record_manager_call,
}
ev.RUNS = tmp / "runs"
execution.ollama_chat = capture_chat
citecheck.verify = lambda _: []
citecheck.summarize = lambda e: {"dead": 0, "checked": 0, "dead_frac": 0.0}
citecheck.is_hard_fail = lambda s: False
policy.manager_call_budget_breached = lambda: False
policy.record_manager_call = lambda: None

try:
    with silence_log():
        verdict, text = ev.run_critic(
            row={"task_id": 9901, "pass_criteria": "Must cover competitor pricing."},
            out="some deliverable text", roles={"critic": {"model": "m"}},
            baseline=False, scope_note="", usage_out={})
finally:
    ev.RUNS = saved["ev_RUNS"]
    execution.ollama_chat = saved["exec_chat"]
    citecheck.verify = saved["cc_verify"]
    citecheck.summarize = saved["cc_summarize"]
    citecheck.is_hard_fail = saved["cc_hard"]
    policy.manager_call_budget_breached = saved["mgr_breached"]
    policy.record_manager_call = saved["mgr_record"]

prompt = captured.get("prompt", "")
check("critic call captured a prompt", prompt != "", True)
check("critic verdict parsed from stub", verdict, "pass")
check("prompt asks for VERDICT line", "VERDICT: PASS" in prompt and "VERDICT: FAIL" in prompt, True)
check("prompt asks for MISSING block", "MISSING:" in prompt, True)
check("prompt no longer says 'ONE sentence why'", "ONE sentence why" in prompt, False)
check("prompt names the fixable-gap format", "- <concrete missing/wrong item>" in prompt, True)

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF104 PASS — structured critic MISSING list parses + the prompt elicits it")
