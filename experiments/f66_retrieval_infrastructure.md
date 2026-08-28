# F66 — unattended retrieval and complete mission accounting

Date: 2026-08-28. Scope is limited to the four defects reproduced by ME1.
F63 policy/state transitions and production research prompts are frozen.
Hermes schedules remain globally paused.

## Treatment

1. The harness-controlled Hermes launcher replaces the unusable search-only
   `web_extract` handler with a bounded local direct-HTTP extractor. Its schema
   states that it reads static public HTTP content and that dynamic/WAF pages
   require browser escalation. URL safety is checked before every request and
   redirect; CAPTCHA, login, and access-control bypass are out of scope.
2. Only harness research workers receive an explicit unattended-browser grant.
   They use Hermes' built-in local headless browser with the installed Chrome
   executable, rather than Browser Use's interactive attach-to-user-Chrome
   path. Other Hermes sessions/configuration are unchanged.
3. The critic persists its own usage file. Task accounting merges worker,
   evidence-only finalization, and critic calls/tokens into a mission usage
   file and the task row.
4. Citation extraction preserves first occurrence order but deduplicates URLs
   before fetching. The complete evidence table and fetch counts are persisted
   per task and included in mission accounting.

## Regression and live gate

- Focused F66 coverage proves truthful capability schema/handler replacement,
  explicit browser authorization and non-interactive selection, redirect
  safety, critic usage persistence/merge, citation deduplication, and evidence
  persistence.
- Existing F60/F63/F64 and the complete deterministic gate remain green.
- Run a new task with exactly the ME1 spec and unchanged mission/prompt/roles.
  Do not retry task 73 and do not automatically retry the new specimen.
- Reconcile worker session, retrieval JSONL, critic usage, citation evidence,
  mission usage, and task-row tokens. Grade with the unchanged critic/spec.
