"""F39 (quota-group skipping) + F40 (local rung excluded from graded work).

Stubs hermes_worker so no model is called and no tokens are spent; asserts on which
candidates the failover loop actually attempts.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402
import execution  # noqa: E402  -- Move 2 of the W9 5-file split: hermes_worker /
                   #                     load_fallback_chain / worker_with_failover now
                   #                     live here, and the internal calls inside
                   #                     worker_with_failover resolve against
                   #                     execution's globals. Patching batch_runner.X
                   #                     would rebind the public name but not redirect
                   #                     the internal call -- so monkey-patch on execution.

fails = []
attempted = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


QUOTA = "Error: 429 too many requests"


def fake_worker(quota_for):
    """Return a hermes_worker stub that 429s for the named models, succeeds otherwise."""
    def _w(prompt, cfg, path, timeout=None):
        attempted.append(cfg["model"])
        return (QUOTA if cfg["model"] in quota_for else "real answer text"), {}
    return _w


WORKER = br.load_roles()["worker"]
print(f"worker role: {WORKER['provider']}/{WORKER['model']} "
      f"quota_group={execution._quota_group(WORKER)}")

# ------------------------------------------------------------------ F39
print("\n=== F39: a 429 on one cloud rung skips its same-account siblings ===")
attempted.clear()
execution.hermes_worker = fake_worker({"kimi-k2.7-code:cloud", "glm-5.2:cloud"})
out, usage, cfg, exhausted = execution.worker_with_failover("p", WORKER, Path("x.json"), "test")
check("kimi 429 -> glm SKIPPED (same group), falls straight to local",
      attempted, ["kimi-k2.7-code:cloud", "gemma4:12b-ctx4k"])
check("completed on the local rung, not exhausted", (cfg["model"], exhausted),
      ("gemma4:12b-ctx4k", False))

print("\n=== F39: no 429 means no skipping at all ===")
attempted.clear()
execution.hermes_worker = fake_worker(set())
out, usage, cfg, exhausted = execution.worker_with_failover("p", WORKER, Path("x.json"), "test")
check("first rung succeeds, nothing else tried", attempted, ["kimi-k2.7-code:cloud"])

print("\n=== F39: a rung with NO quota_group is never skipped by inference ===")
attempted.clear()
execution.hermes_worker = fake_worker({"kimi-k2.7-code:cloud", "glm-5.2:cloud",
                                       "gemma4:12b-ctx4k"})
out, usage, cfg, exhausted = execution.worker_with_failover("p", WORKER, Path("x.json"), "test")
check("local still attempted despite cloud group being dead",
      attempted, ["kimi-k2.7-code:cloud", "gemma4:12b-ctx4k"])
check("only now is the chain exhausted", exhausted, True)

# ------------------------------------------------------------------ F40
print("\n=== F40: graded work (canaries) never touches a local model ===")
cands = [c["model"] for c in execution._failover_candidates(WORKER, allow_local=False)]
check("no local rung offered when allow_local=False", cands,
      ["kimi-k2.7-code:cloud", "glm-5.2:cloud"])
check("local IS offered for ordinary work",
      [c["model"] for c in execution._failover_candidates(WORKER, allow_local=True)],
      ["kimi-k2.7-code:cloud", "glm-5.2:cloud", "gemma4:12b-ctx4k"])

print("\n=== F40: quota-exhausted canary PARKS instead of degrading ===")
attempted.clear()
execution.hermes_worker = fake_worker({"kimi-k2.7-code:cloud", "glm-5.2:cloud"})
out, usage, cfg, exhausted = execution.worker_with_failover(
    "p", WORKER, Path("x.json"), "canary C2", allow_local=False)
check("never reached the local model", "gemma4:12b-ctx4k" in attempted, False)
check("reports exhausted -> caller parks it (week_pending rises, gate shuts)",
      exhausted, True)

print("\n=== F40: canaries still fail over BETWEEN cloud models when groups differ ===")
# Simulate a genuinely separate provider having been added (the commented Anthropic rung).
real_chain = execution.load_fallback_chain
execution.load_fallback_chain = lambda: [
    {"provider": "ollama", "model": "glm-5.2:cloud", "quota_group": "ollama-cloud"},
    {"provider": "anthropic", "model": "claude-sonnet-5"},          # no group = own pool
]
attempted.clear()
execution.hermes_worker = fake_worker({"kimi-k2.7-code:cloud", "glm-5.2:cloud"})
out, usage, cfg, exhausted = execution.worker_with_failover(
    "p", WORKER, Path("x.json"), "canary C2", allow_local=False)
check("skips the dead ollama sibling, reaches the separate provider",
      attempted, ["kimi-k2.7-code:cloud", "claude-sonnet-5"])
check("canary completes on the second PROVIDER, no park", exhausted, False)
execution.load_fallback_chain = real_chain

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
