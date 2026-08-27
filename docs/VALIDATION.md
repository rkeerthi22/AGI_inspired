# Real-world harness validation

## Required sequence

1. Real mission validation.
2. Controlled recovery validation.
3. Retrieval-strategy validation.
4. Capability-selection validation.
5. Independent outcome evaluation.
6. Cost and efficiency evaluation.
7. Resume scheduled missions only after gates 1–6 pass.
8. Prove 5–10 real missions end-to-end.

W9 is complete. This phase does not authorize further structural refactoring.

## Checkpoint 1 — Shopify W35 specimen and task-64 recovery

Date: 2026-08-27/28. Production entry point:
`batch_runner.py --mission 001-shopify-competitor-intel --max-tasks 4`.

The CLI correctly made zero duplicate calls because all W35 dedup keys already
existed. The existing W35 run was therefore audited as the first real specimen:
three research tasks failed and the synthesis passed by honestly reporting data
gaps. Mission-level usefulness failed: the synthesis was accurate about missing
inputs but did not provide actionable competitive intelligence.

Controlled recovery then retried failed task 64 through
`workflow.retry_failed_this_fire(..., run_task_fn=batch_runner.run_task)` under
the production batch lock. It exposed F62 before the worker call:
`prompts.task_scope_note` referenced the moved `seed_is_synthesis` without an
import. The row remained recoverable and no worker tokens were spent. F62 fixes
the call through the canonical `evaluation` owner and covers both research and
synthesis scope routes.

After F62, the same recovery path produced a substantive PromptBase brief:

- elapsed worker-to-ledger time: 3m12s;
- 26 API calls;
- 912,666 input + 13,813 output = 926,479 new worker tokens;
- accumulated task total: 997,315 input + 15,944 output tokens;
- configured daily hard cap: 20,000,000 tokens; measured day total after run:
  1,013,259 tokens;
- USD remained unmeasured (`cost_status: unknown`), so the $0.50 target is not
  proven;
- raw output and deliverable persisted; no protected repository mutation;
- final status `failed/needs_review` because the manager-call budget was already
  40/40, so the critic could not judge the recovered output.

Independent mechanical retrieval checked 18 cited claims: 18/18 URLs returned
HTTP 200 and 18/18 extracted key literals were present. The brief is useful with
caveats: current product prices, subscription price, marketplace fees, model
categories, and App Store rating are supported; the claimed count of 43 parsed
products is not independently supported, `NEW` is inferred without a supplied
prior brief, and the recurring sentiment theme relies on a secondary aggregator.

## Gate status

| Gate | Status | Evidence / blocker |
|---|---|---|
| Real mission | Failed | W35 Shopify produced 1/4 passing tasks and no actionable combined brief |
| Recovery | Partial pass | task 64 recovered useful output; automated critic blocked by 40/40 manager quota |
| Retrieval strategy | In progress | direct HTTP succeeded after browser/search-loop failures; controlled comparison pending |
| Capability selection | In progress | correct Shopify verification skill injected; causal benefit unproven |
| Independent quality | Partial pass | 18/18 citation checks; three caveats above |
| Cost/efficiency | Failed | within token hard cap, but 926k tokens for one competitor and USD unmeasured |
| Resume schedules | Blocked | prerequisite gates are not green |
| 5–10 mission proof | Not started | graduation test follows schedule-readiness gates |

## Checkpoint 2 — early retrieval switching experiment

Question: when ordinary search stops adding usable evidence, does the agent
recognize stagnation early and switch retrieval method before excessive spend?

Historical matched control: task 65 (AIPRM), which ended at the search-loop
guardrail with no deliverable after 14 model API calls and 626,860 tokens.

Treatment: the same AIPRM objective with a predeclared policy requiring no more
than three web searches, a switch after two consecutive searches added no new
usable URL/required field, no return to search after switching, and a final
per-call retrieval trace. The redacted Hermes session was exported as
`workspace/validation/retrieval_switch_session.jsonl` and measured from its
actual tool-call records rather than the model's self-report.

Result: **FAIL**.

- 84 messages and 46 tool calls;
- 8 `web_search` calls, violating the declared maximum of 3;
- 5 `web_extract`, 1 `browser_exec`, 21 `execute_code`, and 11 `terminal` calls;
- 32 code/terminal calls despite the research-only instruction;
- no final deliverable and no retrieval trace;
- 2,142,263 input + 15,535 output = 2,157,798 tokens;
- cost status remained unknown;
- manually interrupted after the bounded experiment failed to finish.

The treatment used 3.44× the control's tokens and still failed. Prompt-level
switch instructions are therefore not a reliable control. The current Hermes
guardrail recognizes identical call signatures/results and hard tool caps, but
semantically different low-yield queries evade no-progress detection. Any next
capability experiment must measure/enforce evidence novelty outside the model;
schedules remain blocked until that control is demonstrated.
