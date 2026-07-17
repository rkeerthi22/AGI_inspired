# workspace/ — the analyst's write area

Everything the agent produces lands here. This is the ONLY place the agent may write
(besides `ledger/` and `memory/`) — enforced by `config/policy.yaml` workspace confinement.
Contents are gitignored (the audit trail lives in `ledger/ledger.db`, not here); only this
README is tracked so the layout survives a clone.

## Layout (one folder per mission's deliverables)
- `onboarding/` — M0 smoke-test output (`hello_report.md`)
- `shopify/`    — mission 001 weekly competitor-intel briefs (`competitor-intel-YYYY-WW.md`)
- `content/`    — mission 002 weekly niche briefs (`niche-brief-YYYY-WW.md`)
- `adforge/`    — mission 003 per-client market briefs (`<client>-market-brief-YYYY-MM-DD.md`)

Deliverable paths are declared in each mission's pass criteria (`missions/*.md`).
