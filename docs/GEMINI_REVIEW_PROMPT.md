# Gemini architecture review — AGI_like harness

You are an independent architecture reviewer for the AGI_like harness at
`S:\AGI_like`. Operate **read-only**: no file edits, no DB mutations, no Git
changes, no ESTOP changes, no model calls, no mission/cohort execution.

## Evidence protocol

1. Cite every claim with exact file and line, Git output, or database query.
2. Label claims: VERIFIED, INFERENCE, UNVERIFIED, or CONTRADICTED.
3. Live state overrides docs, handoffs, and comments. Raw logs over prose.
4. A green test proves only the exercised path. Identify mocks and omissions.

## Startup

1. Read `docs/GEMINI_REVIEW_DOSSIER.md` — ground truth, current state, open questions.
2. Read `AGENTS.md`.
3. Run `python orchestrator/continuity.py recover` (read-only).
4. Inspect `git status --short --branch`, `git rev-parse HEAD`, `git diff --stat HEAD`.
5. Verify ESTOP exists and is engaged: `python -c "from orchestrator.execution_pause import pause_engaged; print(pause_engaged())"`.
6. Check no batch_runner/cohort/controlled_hermes processes are running.

## Read these files

1. `orchestrator/provider_chat.py` — provider-neutral typed chat boundary
2. `orchestrator/runlock.py` — lock safety with OS process identity
3. `orchestrator/execution_pause.py` — ESTOP sentinel
4. `orchestrator/batch_runner.py` — batch execution (F99 ESTOP check at main())
5. `orchestrator/controlled_hermes.py` — Hermes subprocess + finalization
6. `orchestrator/workflow.py` — synthesis, canary orchestration, verdict mapping
7. `orchestrator/onboarding_autonomy.py` — typed staged onboarding (F100 INSERT OR IGNORE)
8. `orchestrator/outcomes.py` — shared outcome types
9. `orchestrator/execution.py` — model invocation, failover, worker calls
10. `orchestrator/evaluation.py` — critic, fact extraction
11. `orchestrator/task_runner.py` — single-task execution pipeline
12. `orchestrator/ledger.py` — fitness computation
13. `orchestrator/scheduler.py` — mission parsing, queue, dedup, stale expiry
14. `orchestrator/integrity.py` — fs-guard, escalation, db integrity check
15. `workspace/validation/cohort_isolation.py` — transactional dispatcher isolation
16. `workspace/validation/run_cohort.py` — cohort runner
17. `config/models.yaml` — provider declarations, fallback chain
18. `docs/HARDENING.md` — F1–F100 fix registry (project-local)

## Then inspect

- `tests/test_architecture_blockers.py` — lock safety, provider dispatch, ESTOP, fitness
- `tests/test_cohort_isolation.py` — isolation state machine
- `tests/test_onboarding_contract_red.py` — onboarding recovery idempotency
- `tests/test_f57.py` — critic infra failure regression
- `tests/test_f53.py` — fitness honesty regression
- `tests/test_f63.py` — retrieval controller regression

## Questions to answer

Answer the five questions from the dossier (§4). Then add any findings you
discover that are not covered by those questions.

Report your findings in `docs/reviews/GEMINI_REVIEW_2026-08-30.md`. Read-only —
write the review file, do not modify anything else.
