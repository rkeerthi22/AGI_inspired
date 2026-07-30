# Regression suites

Guards the fixes in `docs/HARDENING.md`. One suite per finding (or cluster), plus a baseline.

```
python tests/run_all.py            # all of them, serially
python tests/run_all.py f42 f47    # substring filter on suite name
```

Non-zero exit means at least one suite failed; the runner prints the failing suite's output.

## Before you run them

**Do not run these while a batch fire is in flight.** `run_all.py` refuses if
`runs/.batch.lock` exists, and single suites should be treated the same way. These write real
files; the fs-guard snapshots the tree around every worker call and attributes changes to the
worker (F36), and its remediation removes what it flags. That has destroyed real work twice —
see `docs/INCIDENTS.md`, 2026-07-29.

**They run against the real repository, on purpose.** The mechanisms under test *are* git:
`git status` output, `git checkout` remediation, and how ignore rules resolve across
`.gitignore`, `.git/info/exclude`, and the global excludes file. A temp-copy sandbox would
exercise none of that faithfully — the F1 probe learned this the expensive way by redirecting
`ROOT` and still writing to the live escalation log (`docs/INCIDENTS.md`, 2026-07-19).

Consequences of that choice, which every suite already follows:

- **Restore by content, never `git checkout`.** Writing back the bytes you read is independent
  of git state and cannot reach anything else. `git checkout -- <dir>` inside a teardown has
  destroyed uncommitted work in this repo (F36, and again in F36's own first test).
- **Stub the side effects.** Each suite sets `br.escalate = lambda *a, **k: None`; otherwise a
  staged scenario sends the operator a real Telegram alert.
- **Clean up in `finally`,** scoped to the exact paths touched.

## Files

| Suite | Guards |
|---|---|
| `test_baseline.py` | fitness/ledger arithmetic and window boundaries |
| `test_f35.py` | never-attempted rows expire and count as dropped |
| `test_f36.py` | fs-guard: hash detection, scoped revert, recoverable discards |
| `test_f37.py` | infra failure is not scored as the analyst being wrong |
| `test_f39_f40.py` | quota groups; local models excluded from graded canary work |
| `test_f42.py` | repo root inside the containment surface; F46 untracked-dir cases |
| `test_f44.py` | daily budget counts a local day |
| `test_f47.py` | unversioned exclude sources cannot hide anything from the guard |
| `test_h7.py` | skill-note sanitiser accepts techniques, rejects injection |
| `test_h7_gate.py` | approval re-validates from scratch and refuses a poisoned candidate |
| `test_throughput.py` | park classification, same-fire retries, synthesis routing |

## Adding one

Copy the shape of an existing suite: derive `ROOT` with
`Path(__file__).resolve().parents[1]` (never hardcode a drive path — these files were
originally stranded in session temp directories precisely because they hardcoded one), stub
`escalate`, collect failures in a list, `sys.exit(1 if fails else 0)`, and restore everything
you touched in `finally`.

`tests/` is in `PROTECTED_PATHS`, so the fs-guard covers these files too — a worker cannot
quietly weaken the suites that check it.
