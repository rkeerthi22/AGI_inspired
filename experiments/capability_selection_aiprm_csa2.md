# Capability-selection validation — AIPRM CSA2

Date: 2026-08-28. Matched rerun after CSA1 exposed two concrete controller
defects: approved `skill_view` setup was rejected as retrieval, and retry JSONL
was appended to the prior attempt. The narrow treatment allows at most two
audited non-retrieval skill loads and resets the per-attempt audit file. F63's
8 retrieval / 2 rejection / 1 finalizer limits and production prompt are
otherwise unchanged. Schedules remain paused.

Retry task 72 once through the same injected production path. Preserve all CSA1
spend and failure history; do not edit the row manually.

Acceptance is the CSA1 gate plus: both setup calls are visible as bounded setup,
the new JSONL contains only CSA2, at least one real retrieval executes, and the
brief closes the current-rating gap or specifically proves why the reachable
source cannot yield it. The critic must not repeat either the empty-evidence
failure or the original unexplained rating omission.
