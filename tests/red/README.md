# `tests/red/` — red-team gap tests (NOT in the gate suite)

These are **red tests**: executable proofs that an open gap is real. Each one asserts
the *desired* behavior and **fails on the current code**, demonstrating the gap concretely
rather than in prose. They are deliberately excluded from `tests/tiers.json`, so
`python tests/run_all.py` does not run them — the model-free gate stays green while the
gap is open.

## Convention

- One file per gap, named `test_<gap-slug>.py`.
- Header comment names the gap ID (e.g. `RC-1`), states the symptom + root cause +
  primary evidence (file:line or measured values), and links the blueprint doc.
- Each file defines `test_*` functions (pytest-compatible) AND a `__main__` block, so it
  can be run directly: `python -B tests/red/test_<gap-slug>.py` prints `[RED]`/`[GREEN]`
  per case. No live network or provider calls — all external surfaces are mocked or
  constructed in-process.
- A red test has two kinds of case:
  - **The gap case** — asserts the desired behavior → `[RED]` until the gap closes.
  - **The guard case** — asserts a related invariant that must *stay* true after the fix
    (prevents the fix from being over-lenient). → `[GREEN]` now and after the fix.

## Closing a gap

When a fix lands: run the red test directly — the gap case must flip `[RED]` → `[GREEN]`
and the guard case must stay `[GREEN]`. Then move the file into `tests/` proper and add it
to `tests/tiers.json` so it becomes a permanent regression guard. Delete this README's
mention of it. The fix-number goes in `S:\ObsidianVault\Fix Registry.md`.
