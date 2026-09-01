# Codex Handoff - M2 Dynamic Browser Remediation

Date: 2026-09-01
Scope: Task 108 failure diagnosis and pre-rerun remediation only
Outcome: REMEDIATION COMPLETE; M2 LIVE RERUN NOT PERFORMED

## Root cause

Task 108 did not reach the AIPRM page with a browser. The retrieval audit proves
that `browser_navigate` was rejected at `executed_calls=0` because the generic
controller required search first. The worker then completed two `web_search`
calls, had `web_extract` rejected, and finalized from snippets. There is therefore
no evidence of a browser navigation timeout or a Cloudflare/anti-bot challenge in
Task 108: the browser navigation never executed.

The defect was a retrieval-policy and prompt-routing gap, not a missing Hermes
browser capability. M2 was labeled `dynamic_browser_required` in prose but that
requirement was not propagated as structured execution policy.

Primary evidence:

- `runs/task108_worker.usage.retrieval.jsonl`
- `runs/task108_worker_raw.txt`
- `runs/task108_critic_reasoning.txt`
- `workspace/shopify/2026-W36_cohort-2026-w36-m2-dynamic-browser-aiprm-pricing-page-captu.md`

## Remediation implemented

- Added an explicit `dynamic_browser_required` retrieval profile and propagated it
  from the cohort manifest through task preparation, failover, the worker process,
  and the controlled Hermes launcher.
- The profile begins at browser stage, assigns the existing eight-call retrieval
  budget to browser interaction, expands the retained rendered-page excerpt, and
  rejects search/extract/terminal/code detours.
- M2 now pins `https://app.aiprm.com/pricing?lang=en`. Its worker instructions
  require browser navigation first, rendered snapshots, scrolling, and interaction
  with monthly/annual states to capture tier names, prices, seats, and visible
  promotions. CAPTCHA/WAF bypass is explicitly forbidden.
- Retrieval audit contract v2 records the active profile, result size, result class,
  and terminal reason without storing raw retrieved content. It distinguishes tool
  error, timeout, and challenge/access-block evidence.
- Rejection accounting now tolerates a compliant call between redirects while still
  halting after two consecutive feedback violations or three rejected calls overall.
  Parallel siblings remain one feedback round.
- Preserved the original generic retrieval launch contract when no explicit profile
  is supplied, including across failover.
- Fixed the Windows PowerShell `agi.ps1` launcher path construction.

## Verification

- Focused remediation gate: 6/6 suites green
  (`test_critical_path_regressions`, `test_f60`, `test_f63`, `test_f66`,
  `test_hermes_contract`, `test_operator_cli`).
- Full model-free gate: 46/46 suites green across unit, containment, and integration.
- `test_f66` runs a real Hermes browser against a loopback JavaScript-rendered
  pricing fixture, takes a rendered snapshot, activates the annual control via its
  accessibility reference, and observes the updated annual price and seat count.
- `test_operator_cli` invokes the repaired `agi.ps1 status -Json` wrapper on Windows.
- `git diff --check` exits 0 (only expected Git LF-to-CRLF notices).

## Safety and rerun boundary

No provider was contacted and no live M2 rerun was performed. Live status after the
gate showed ESTOP engaged, isolation restored, no run lock, and Munder quiesced with
zero offenders. The Task 108 clean-closeout result remains intact.

The next action is a separately authorized, single-task M2 rerun under the controlled
window. Its audit must begin with an allowed `browser_navigate` under profile
`dynamic_browser_required`. If the rendered page returns a real timeout or access
challenge, the v2 audit will classify that evidence; the worker must report it and
must not attempt a bypass.

`docs/ACTIVE_WORK.json` and `.harness/continuity/current.json` were not edited because
they are owned by the active Hermes closeout task. Their owner should reconcile the
new committed remediation state before the authorized rerun.
