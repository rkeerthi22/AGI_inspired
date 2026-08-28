# Current architecture

This is the short, current map of AGI_like. `HARDENING.md` and `INCIDENTS.md`
remain chronological records; they are not current operating instructions.

## Runtime path

Mission specs enter `orchestrator/scheduler.py`, are persisted by `ledger.py`,
and run through `batch_runner.py` / `task_runner.py`. Hermes is accessed through
the versioned contract in `orchestrator/hermes_contract.py`. Retrieval policy and
per-assistant-tool-batch accounting live in `retrieval_progress.py`. Critics and
finalization write durable run evidence; Git and the databases are authoritative.

The global Hermes ESTOP is a fail-closed execution boundary. Its state is runtime
state and must be checked live; this document never claims whether it is engaged.

## Prediction path

`prediction_machine/` is the only prediction implementation. The batch runner
uses `prediction_machine.integrations.batch_runner_hook`; daily collection and
evaluation use `prediction_machine/run_daily.py`. Paths default relative to the
repository and may be overridden through the supported environment settings.

The former `orchestrator/simulate.py` duplicated models, storage, and fallbacks.
Repository search and installed-runtime inspection found no executable caller;
the one installed script that mentioned it uses its own heuristic instead. The
legacy module was therefore removed rather than preserving two sources of truth.
Historical references to it describe past state only.

## Continuity and tests

Compact Brief schema v2 records `repository.based_on_head`: the commit observed
when the brief was assembled. It intentionally has no `record_commit`, which
cannot be embedded in the commit that creates it without self-reference. Recovery
treats the basis as provenance and independently prefers current Git/runtime/DB
state.

The default test gate runs unit, containment, and model-free Hermes integration
tiers. Live model/network/mission tests require explicit opt-in, and the default
gate fails if a test attempts a live path.

## `_needs_review` disposition

The durable F63 outcome and crash findings are already preserved in validation
and incident history. The review directory is not authoritative. These files are
flagged for deletion (no deletion is performed by this change):

- `README.md`, `CRASH_AUDIT_2026-08-28.md`: superseded audit narratives.
- `cohort_analysis_INCOMPLETE.json`, `abbvie_app_INCOMPLETE.pdf`: explicitly
  incomplete/broken artifacts.
- `F63_COHORT_COMPLIANCE_CORRECTED.md`: superseded duplicate of the final
  validated accounting.
- `audit_checks.py`: one-off audit helper, not a maintained test.
- `full_inventory_mtime_desc.csv`: bulky point-in-time inventory; archive outside
  the working tree only if forensic retention is desired.
