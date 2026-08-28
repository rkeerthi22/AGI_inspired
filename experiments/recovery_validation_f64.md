# Controlled recovery validation — specimen RV1

Date: 2026-08-28. F63 is frozen; this experiment changes no controller or prompt.
Hermes schedules must remain globally paused for the entire run.

## Question

Can the production harness recover one expired real-research task exactly once,
preserve its prior accounting, retry it through the normal task runner, and return
a bounded, useful, cited result?

## Fault and subject

- Queue one uniquely tagged current-week AIPRM research task under mission 001.
- Move it through `ledger.start_task`, record marker prior usage of 1,000 input and
  100 output tokens, then expire its lease. This represents a worker process lost
  after some accounted consumption.
- Invoke `scheduler.reconcile_interrupted_tasks` twice. The first invocation must
  move exactly that row from `running` to `interrupted` and increment
  `attempt_count` to 1. The second must be idempotent.
- Retry it with `task_runner.run_task` while holding the production batch lock.

## Acceptance gate

- No scheduled dispatch is resumed; the global pause exists before and after.
- Exactly one task is recovered; no unrelated ledger row changes during injection
  and reconciliation.
- The second reconciliation changes zero rows.
- Prior token markers survive and are added to retry usage, never overwritten.
- The retry executes no more than 8 retrieval calls, rejects/redirects no more than
  2 attempts, and makes exactly one evidence-only finalization call.
- JSONL retrieval accounting reconciles with usage and the exported session.
- The terminal row is honest (`done`, or `failed` with an explicit useful bounded
  partial); it is never stranded in `running` or `interrupted`.
- An independent check finds the result useful and its cited claims supported, with
  gaps stated explicitly.
- Total retry tokens remain below the 413,334-token pre-F63 useful specimen.
- Protected Git files and unrelated live state do not drift.

Any failure is evidence about recovery behavior. Do not modify F63 during this
specimen; diagnose first and change code only for a reproduced concrete defect.
