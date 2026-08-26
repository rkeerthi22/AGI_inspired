# Quarantined live-data checks

These suites exercise real repository state or live data. They are honest checks,
but their pass/fail status depends on data that changes from week to week, so they
are NOT part of the deterministic regression gate. Run them separately with:

    python tests/run_all.py --live-data

| Suite | Reason |
|---|---|
| `test_baseline.py` | Copies the live `ledger.db` and asserts canary counts by week. Expectations (W29 fallback, W30/W33 counts) drift as the live ledger advances. |

The deterministic suites (everything else) must all be green before any refactor
or mission fire is considered safe.
