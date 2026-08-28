# Capability-selection validation — AIPRM CSA1

Date: 2026-08-28. F63 and production prompts are frozen. Schedules remain paused.

## Question

After a useful partial fails review because a mandatory value was missed on an
already reachable source, can the existing critic-feedback retry choose a useful
retrieval capability and close that precise evidence gap within F63 bounds?

## Treatment

Retry failed task 72 once through `workflow.retry_failed_this_fire`, injecting
the production `batch_runner.run_task` binding. The harness's existing
retry-with-feedback path supplies the critic's exact objection: the current
AIPRM average rating was omitted despite the reachable Chrome Web Store page.
Do not edit the prompt, controller, tool mapping, or task row by hand.

## Acceptance gate

- One and only one task retry; no duplicate or scheduled dispatch.
- No more than 8 executed retrieval calls, 2 rejected/redirected attempts, and
  exactly one evidence-only finalization call.
- The final brief supplies a current average rating from a cited, successfully
  retrieved source, or gives a technically specific bounded explanation proving
  why that reachable source cannot yield the value.
- The critic passes the required field, or independently identifies a new
  substantive evidence defect; it must not repeat the same omission.
- JSONL, usage, and redacted session accounting reconcile exactly.
- Retry tokens remain below 413,334 and accumulate onto task 72's prior total.
- Protected repository state does not drift and schedules remain paused.

Failure is a capability-selection/outcome-quality result. Do not tune prompts or
change F63 during the specimen.
