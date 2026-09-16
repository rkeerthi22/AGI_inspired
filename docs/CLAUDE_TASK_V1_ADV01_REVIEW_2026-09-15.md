# Task for Claude Code: Independent Review of V1-ADV-01 Adversarial Audit & Proposed Hermetic Test

**Directive Date:** 2026-09-15  
**From:** Operator / Gemini CLI  
**To:** Claude Code (Reviewer & Gating Authority)  
**Primary Audit Dossier:** [`docs/reviews/GEMINI_TO_CLAUDE_V1_ADV01_AUDIT_2026-09-15.md`](reviews/GEMINI_TO_CLAUDE_V1_ADV01_AUDIT_2026-09-15.md)  
**Current Branch:** `product/v1-completion-2026-09-15` (HEAD `55ee8b9`, pushed to origin, 87/87 gate green)  
**Safety Status:** ESTOP engaged (`True`) | 0 zombies | Zero un-gated live runs

---

## Instructions for Claude Code

1. **Read the Primary Audit Dossier:**
   Read [`docs/reviews/GEMINI_TO_CLAUDE_V1_ADV01_AUDIT_2026-09-15.md`](reviews/GEMINI_TO_CLAUDE_V1_ADV01_AUDIT_2026-09-15.md).

2. **Conduct Parse-Don't-Trust Verification:**
   Verify Gemini's findings across the 4 key architectural gaps:
   - **Gap 1 (Filesystem Sandbox Scope):** Verify that [`config/policy.yaml`](../config/policy.yaml#L11-L18) lists `workspace/` under `writes_allowed_under`, and [`orchestrator/integrity.py`](../orchestrator/integrity.py#L493) skips it during `fs_integrity_check()`.
   - **Gap 2 (Broker Protocol):** Verify that [`orchestrator/egress_broker.py`](../orchestrator/egress_broker.py#L319-L330) rejects plain HTTP `GET/POST` with `405 HTTPS CONNECT required`.
   - **Gap 3 (Attestation Disconnect on CLI Dispatch):** Verify that [`orchestrator/run_task.py`](../orchestrator/run_task.py#L113) queues tasks via `ledger.queue_task()`, which does not assign `run_id="gateway-dsse-v1"`, causing `chain.existing()` in `task_runner.py:417` to return `False` and skip all attestation chaining.
   - **Gap 4 (Deny-List Scope):** Verify that [`orchestrator/policy.py`](../orchestrator/policy.py#L86-L95) only matches `move_money`, `handle_credentials`, and `irreversible_delete`.

3. **Evaluate the Proposed Corrected Test (`V1-ADV-01-HERMETIC`):**
   Evaluate the proposed test implementation in Section 6 of the dossier. Confirm that:
   - It runs model-free, ESTOP-safe, and DB-safe.
   - It genuinely exercises prompt injection ingestion, preflight rejection, auto-repair, research notebook direction persistence, and DSSE Ed25519 cryptographic chain validation.
   - It respects the fixture-segregation guard in `citecheck.py:1044`.

4. **Provide Formal Review Verdict:**
   Output your independent review handoff in `docs/reviews/` answering:
   - Do you agree with the `TEST NOT CURRENTLY EXECUTABLE AS WRITTEN` classification for `V1-ADV-01`?
   - Should `V1-ADV-01-HERMETIC` be landed into [`tests/test_v1_end_product.py`](../tests/test_v1_end_product.py) (expanding gate to 88/88)?
   - Should we wire `TrustGateway` dispatch into `run_task.py` so all CLI runs are attestation-chained?
