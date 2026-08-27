"""F50: the last fallback rung could never fit a synthesis prompt, and nothing checked.

`synthesis_with_failover()` walked the whole chain including `gemma4:12b-ctx4k`
(4,096-token context) while a synthesis prompt measured 8,226-11,662 tokens even at the
OLD 6,000-char brief cap. F38 made that rung LOADABLE by capping num_ctx; loadable is not
usable, and the gap cost a ~1800s stall at 1.5 tok/s ending in an unhelpful failure.

Fixed by testing the real cause -- declared context vs prompt size -- rather than
locality. Assertions below therefore pin BOTH that the too-small rung is skipped and that
locality alone never causes a skip, because `allow_local=False` was the tempting wrong fix.

No model is ever called: ollama_chat / hermes_worker are stubbed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml  # noqa: E402
import batch_runner as br  # noqa: E402
import execution  # noqa: E402  -- Move 2: ollama_chat / hermes_worker /
                   #                     load_fallback_chain / log now live here, and
                   #                     internal calls inside synthesis_with_failover
                   #                     and worker_with_failover resolve against
                   #                     execution's globals. Patching batch_runner.X
                   #                     would rebind the public name but not redirect
                   #                     the internal call -- so monkey-patch on execution.
from _silence import capture_log  # noqa: E402  -- F55: capture via the shared logger proxy

fails = []
sink, capture_ctx = capture_log()
capture_ctx.__enter__()


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


SMALL = {"provider": "ollama", "model": "gemma4:12b-ctx4k", "context_tokens": 4096}
BIG_LOCAL = {"provider": "ollama", "model": "hypothetical-local-32k", "context_tokens": 32768}
UNDECLARED = {"provider": "ollama", "model": "kimi-k2.7-code:cloud"}

tiny = "hi"
synth = "Q" * 39_141        # measured size of a real W31 content synthesis prompt
shopify = "Q" * 60_420      # measured size of a real W31 shopify synthesis prompt

print("=== 1. the unit decision: declared context vs prompt size ===")
check("undeclared rung is NEVER skipped (F39's opt-in rule)",
      br._fits_context(UNDECLARED, shopify), True)
check("4k rung rejects a real content synthesis prompt",
      br._fits_context(SMALL, synth), False)
check("4k rung rejects a real shopify synthesis prompt",
      br._fits_context(SMALL, shopify), False)
check("4k rung still ACCEPTS a small prompt (not banned wholesale)",
      br._fits_context(SMALL, tiny), True)
check("a large-context LOCAL rung is accepted — locality is not the test",
      br._fits_context(BIG_LOCAL, synth), True)

print("\n=== 2. the reply reserve is real, not decorative ===")
# 4096 tok context, 1500 reserved => at most ~2596 tok => ~10384 chars of prompt.
just_fits = "Q" * (( 4096 - br.RESPONSE_RESERVE_TOKENS) * br.CHARS_PER_TOKEN)
just_over = just_fits + "Q" * br.CHARS_PER_TOKEN
check("a prompt exactly at the budget fits", br._fits_context(SMALL, just_fits), True)
check("one token more does not", br._fits_context(SMALL, just_over), False)
check("reserve is non-zero (a prompt that fills the context must NOT pass)",
      br._fits_context(SMALL, "Q" * (4096 * br.CHARS_PER_TOKEN)), False)

print("\n=== 3. synthesis_with_failover skips the rung instead of stalling on it ===")
calls = []


def fake_chat(model, prompt, timeout=300, trace_path=None, usage_out=None):
    calls.append(model)
    raise AssertionError(f"should not have been called: {model}")


execution.ollama_chat = fake_chat
execution.load_fallback_chain = lambda: [SMALL]
out, cfg_used, exhausted = br.synthesis_with_failover(synth, SMALL, log_prefix="t")
check("the too-small rung was never called", calls, [])
check("reported as exhausted, so the caller parks", exhausted, True)
check("no output invented", out, None)
skip = [m for m in sink if "declares only 4096" in m]
check("logged WHY, with the numbers", len(skip), 1)
if skip:
    print(f"         log: {skip[0]}")

print("\n=== 4. a rung that fits is still used ===")
served = []


def ok_chat(model, prompt, timeout=300, trace_path=None, usage_out=None):
    served.append(model)
    return "deliverable text"


execution.ollama_chat = ok_chat
execution.load_fallback_chain = lambda: [SMALL]
out, cfg_used, exhausted = br.synthesis_with_failover(tiny, SMALL, log_prefix="t")
check("small prompt DOES run on the 4k rung", served, ["gemma4:12b-ctx4k"])
check("not exhausted", exhausted, False)

print("\n=== 5. worker_with_failover got the same guard ===")
wcalls = []
execution.hermes_worker = lambda prompt, cfg, path, timeout=None: (wcalls.append(cfg["model"]), ("", {}))[1]
execution.load_fallback_chain = lambda: [SMALL]
out, usage, cfg_used, exhausted = br.worker_with_failover(
    synth, SMALL, ROOT / "runs" / "t.usage.json", log_prefix="t")
check("worker path also skips a rung that cannot fit", wcalls, [])
check("and reports exhausted", exhausted, True)

print("\n=== 6. the shipped config actually declares it ===")
cfgf = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
chain = cfgf["fallback_chain"]
gem = [c for c in chain if "gemma" in c["model"]]
check("gemma rung present in the chain", len(gem), 1)
check("...and declares context_tokens=4096", gem[0].get("context_tokens"), 4096)
check("fallback ROLE declares it too", cfgf["roles"]["fallback"].get("context_tokens"), 4096)
cloud = [c for c in chain if "cloud" in c["model"]]
check("cloud rungs deliberately declare NOTHING (never skipped by inference)",
      [c.get("context_tokens") for c in cloud], [None] * len(cloud))

print("\n=== 7. validated against the defect ===")
# Before F50 the only gate was quota_group; a 4k rung sailed straight through it.
pre_fix_would_call = br._quota_group(SMALL) is None   # no group => not skipped pre-F50
check("pre-F50 the 4k rung passed every existing gate", pre_fix_would_call, True)
check("...and post-F50 it is stopped by the size gate", br._fits_context(SMALL, synth), False)

print("\nFAILURES:", fails if fails else "none")
capture_ctx.__exit__(None, None, None)
sys.exit(1 if fails else 0)
