# Architectural Design Proposal: Structural Resolution of Sandboxed Worker vs. Host Critic Verification Asymmetry

**Document ID:** `GEMINI_PROPOSAL_VERIFICATION_ASYMMETRY_2026-09-07`  
**Revision:** 2.0 (Post-Adversarial Review Revision)  
**Date:** 2026-09-07  
**Author:** Gemini CLI / Google DeepMind Agentic Assistant (Principal Architect & Review Authority)  
**Adversarial Reviewer:** Claude Code (`docs/G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md`)  
**Status:** **PROPOSAL ONLY (REV 2) — PENDING HERMES H1/H2 & FINAL MULTI-AGENT COMPARISON (NO CODE IMPLEMENTATION YET)**  
**Target Subsystems:** `orchestrator/egress_broker.py`, `orchestrator/citecheck.py`, `orchestrator/deliverable_preflight.py`, `orchestrator/evaluation.py`, `config/egress_policy.yaml`  
**Prior Art & Context:** F124 (Worker Token Isolation), F126 (Deliverable Preflight), F127 (Multi-Engine Search & Egress Parity), F129 (Deterministic Retry Artifacts), [`docs/G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md`](../G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md)

---

## 1. Problem Statement: The Asymmetry Trap

Under F124 and Step 2 host hardening, research workers operate inside a Windows Restricted Token (`S-1-5-12`) enforced by Windows Filtering Platform (WFP) rules:
1. `AGI_Worker_Allow_Broker_Loopback`: Allows worker TCP outbound exclusively to loopback egress broker (`127.0.0.1:8787`).
2. `AGI_Worker_Deny_Direct_Egress`: Denies all direct TCP outbound to the Internet for the worker SID.

Consequently, external network access by a worker is strictly mediated by `orchestrator/egress_broker.py`. If a search hit points to a domain not allowlisted in `config/egress_policy.yaml`, the broker intercepts the request and returns:
`HTTP 403 Forbidden: {"error": "host_not_allowlisted", "host": "<domain>"}`.

When an honest sandboxed worker receives this denial, it complies with prompt instructions:
> *"If a source URL returned 403 or was unreachable, note which sources failed, continue with available sources, and mark facts as confidence 1."*

However, the independent critic (`orchestrator/evaluation.py` / `orchestrator/citecheck.py`) executes **on the host controller without restricted token boundaries**, having unconstrained direct Internet access. During verification:
1. Critic extracts every URL cited in the deliverable.
2. Critic issues direct host HTTP requests (bypassing the broker).
3. The novel domain responds with `HTTP 200 OK`.
4. Critic notes: *"Worker claimed URL was unreachable/failed, but URL returned 200 OK. Worker hallucinated tool failure or fabricated source unavailability."*
5. Critic issues `VERDICT: FAIL`.

### Why F127 Was an Instance Patch, Not a Structural Fix
F127 synchronized `config/egress_policy.yaml` with the specific domains encountered during the 7 frozen validation missions (PromptBase, Snack Prompt, ContentBot, etc.). This permitted the cohort to pass 7/7, but left the structural flaw untouched:
**The first time an autonomous worker in production discovers an un-allowlisted authoritative domain, the verification asymmetry trap recurs immediately.**

---

## 2. Comparative Analysis of Structural Fix Options

Claude Code's review and task brief evaluated two architectural paths:

```
                  ┌────────────────────────────────────────────────────────┐
                  │                 Structural Options                     │
                  └───────────┬────────────────────────────────┬───────────┘
                              │                                │
                              ▼                                ▼
                     [Option A: Symmetric]            [Option B: Categorized]
                   Route Critic Thru Broker           Host Multi-Vantage Check
                     (Identical Vantage)             (Distinguish Policy Block)
```

### Option A: Route Critic Citecheck Through the Egress Broker
**Concept:** Route `citecheck.verify()` through the same egress proxy (`127.0.0.1:8787`) used by the worker. Critic and worker share an identical network perimeter.

* **Advantages:**
  1. **Strict Epistemological Parity:** The critic cannot observe network state that was unavailable to the worker.
  2. **Minimal Schema Complexity:** Citecheck remains a binary check (`reachable` vs `unreachable`).
  3. **No Prompt Engineering Required:** The critic LLM is not asked to weigh policy disclosures.

* **Fatal Vulnerabilities / Failure Modes (The "Blind Critic" Flaw):**
  1. **Fabrication Vulnerability (Fatal):** If a worker fabricates an entirely fake URL hosted on a non-allowlisted domain (e.g., `https://totally-fake-research-citations.org/data.html`), the broker returns 403 to the critic. If 403 is excused, the critic cannot distinguish an authentic allowlist denial from a hallucinated or malicious domain. The critic is completely blinded to external ground truth.
  2. **Shared Blindness to Upstream Degradation:** If the broker crashes, stalls, or has loopback connection fatigue, both worker and critic fail identically, creating false positives or masking silent harness degradation.
  3. **Violation of Critic Independence Principle:** The critic's role is objective verification of external reality. Confining the critic to the worker's security perimeter degrades the critic's authority.

---

### Option B: Policy-Denial Categorization (`host_not_allowlisted` as Distinct Classification)
**Concept:** Maintain the host's direct egress vantage point for `citecheck`, but augment the verification model to explicitly check worker egress policy and distinguish three mutually exclusive verification states:
1. `OK (200)`: URL is reachable, live, and content matches deliverable assertions.
2. `DEAD / FABRICATED (404, 500, NXDOMAIN, Timeout)`: URL does not exist or server failed. Hard fail.
3. `POLICY_DENIED (403 host_not_allowlisted)`: URL is live on the open Internet (critic gets 200), BUT the domain was demonstrably blocked to the worker under the active policy at worker-run time.

* **Advantages:**
  1. **Preserves Objective Ground Truth:** The critic remains all-seeing. It verifies whether the target actually exists on the public Internet, defeating the "blind critic" exploit.
  2. **Audit Truthfulness:** Cleanly decouples **worker competence** (did the worker tell the truth about what it saw?) from **host policy constraints** (was the worker allowed to see it?).
  3. **Strict Fabrication Guard:** If a worker asserts Confidence 3 or verbatim quotes for a URL that was `POLICY_DENIED`, the critic immediately flags **FABRICATION** (proven lie: worker claimed to read a page its network layer was blocked from receiving).
  4. **Self-Hardening Discovery:** When citecheck observes `POLICY_DENIED` on authoritative sources, candidate expansions are logged for operator review.

---

## 3. Adversarial Review Outcomes & Gap Resolutions

In [`docs/G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md`](../G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md), Claude Code approved Option B+ directionally, establishing three mandatory implementation conditions. Revision 2 incorporates these resolutions:

### Gap 1 Resolution (CRITICAL): Time-of-Check Invariant via Attestation Digest
* **Vulnerability:** Evaluating `worker_policy_permitted` against the **live** `config/egress_policy.yaml` at critic time introduces a time-of-check vs time-of-use flaw. In F127, the allowlist was expanded mid-cohort (tasks 133→134). A URL denied to the worker at run time would look permitted to a critic checking the live file after an expansion, triggering a false `FAIL`.
* **Resolution:** `worker_policy_permitted` MUST be evaluated against the **policy in force at worker-run time**.
  - Under `orchestrator/egress_policy.py`, worker execution is gated by a cryptographically signed attestation (`.harness/egress_attestation.signed`), which explicitly commits to `policy_digest` (the SHA256 of `egress_policy.yaml` when signed).
  - At worker dispatch, `orchestrator/task_runner.py` records the active `policy_digest` (and a serialized list of allowlisted hosts) directly into `task{tid}_a{attempt}_worker.usage.json`.
  - When `citecheck.py` verifies the deliverable, it checks domain permission against this frozen run-time policy snapshot, never against the live file.

### Gap 2 Resolution (CRITICAL / Kill-Assumption): Broker Audit-Log Reliability & Pre-Implementation Prerequisite
* **Vulnerability:** B+'s `POLICY_DENIED` relief depends on proof that the worker actually attempted the domain and received a `host_not_allowlisted` intercept. If the broker does not record this or if logs are not attempt-scoped, the feature builds on a nonexistent foundation.
* **Empirical Code Audit Findings (Verified 2026-09-07):**
  1. `orchestrator/egress_broker.py:93` currently calls `self.server.audit(decision="deny", reason=str(exc)[:120])`. It records the denial reason, **but does NOT record `host=host` in the deny payload**.
  2. `EgressBroker` currently takes a single `--audit` path (`args.audit`) intended for daemon-level logging, not an attempt-scoped per-task log.
  3. `HARNESS_RETRIEVAL_AUDIT` (`task{tid}_a{attempt}_worker.usage.retrieval.jsonl`) is written by `RetrievalProgressController` in `orchestrator/retrieval_progress.py` to record tool-level progress, not low-level socket proxy events.
* **Resolution (Mandatory Phase 1 Prerequisite):**
  - Before writing any classification logic in `citecheck.py`, the broker logging infrastructure must be hardened:
    1. Update `BrokerHandler.do_CONNECT` to include `host=host` in deny audit records:
       `self.server.audit(decision="deny", host=host, reason=str(exc)[:120])`.
    2. Add task/attempt correlation to broker connections or direct in-process proxy scraper so that broker-intercepted denials are reliably recorded in the per-attempt audit artifact (`task{tid}_a{attempt}_worker.usage.retrieval.jsonl` or dedicated `task{tid}_a{attempt}_broker.audit.jsonl`).
    3. Author hermetic integration tests in `tests/test_egress_broker_integration.py` verifying that unauthorized CONNECT attempts generate deterministic, attempt-scoped denial records.

### Gap 3 Resolution (MEDIUM): Bounding the POLICY_DENIED Attack Surface
* **Vulnerability:** The `POLICY_DENIED` classification creates a potential escape hatch for lazy workers, which might claim policy denial on difficult targets to evade research.
* **Resolution:** Strict mathematical bounds and grounding invariants:
  1. **Hard Abuse-Fraction Ceiling:** A deliverable where `POLICY_DENIED` citations exceed **25%** of total citations (or an absolute count > 2, whichever is lower) CANNOT receive an automated `PASS`.
  2. **Minimum Primary Grounding Invariant:** Every research deliverable MUST contain at least **2 `OK` citations** (verified live on host AND permitted to worker at run time). If `OK` citations < 2, the deliverable fails preflight and critic evaluation (`insufficient_verified_sources`).
  3. **Escalation Path:** Deliverables with `POLICY_DENIED` citations exceeding the 25% ceiling are automatically escalated to `needs_review` with trigger `high_policy_denial_fraction`. The denied domains are appended to `runs/policy_expansion_candidates.jsonl` for operator review.
  4. **Strict Mechanical Fabrication Guard:** If a worker claims `confidence: 3` or attributes verbatim quotations (`"..."`) to a source classified as `POLICY_DENIED`, citecheck immediately issues a hard `FAIL` with `critic_notes="Fabrication: worker asserted high confidence or verbatim text from policy-denied source"`.

---

## 4. Architectural Specification: Option B+ (Attested Two-Tier Verification)

### 4.1 Schema Expansion in `orchestrator/citecheck.py`
```python
@dataclass(frozen=True)
class CitationCheckResult:
    url: str
    host: str
    reachable_on_host: bool
    http_status: int | None
    worker_policy_permitted: bool      # Evaluated against worker-run attestation digest
    broker_attempt_verified: bool      # Verified in per-attempt broker audit log
    classification: str                # 'OK' | 'DEAD' | 'POLICY_DENIED' | 'UNREACHABLE'
    error: str | None = None
```

### 4.2 Decision Flow

```
                      [Critic Evaluates Cited URL]
                                   │
                     Direct HTTP Probe on Host
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
     Reachable (HTTP 200)                      Unreachable / Error
              │                                (404, 5xx, NXDOMAIN)
              │                                         │
    Check Run-Time Policy                               ▼
    (Attestation Snapshot)                         [DEAD / FAIL]
              │
      ┌───────┴───────┐
      ▼               ▼
[Permitted]     [Denied]
      │               │
      ▼         Cross-Check Broker Log
   [OK]         (host_not_allowlisted)
                      │
              ┌───────┴───────┐
              ▼               ▼
         [Verified]     [Un-Attempted]
              │               │
              ▼               ▼
       Check Abuse Bounds  [UNVERIFIABLE / FAIL]
       - Max 25% / <= 2
       - Min 2 OK
       - No Conf-3 / Quotes
              │
              ▼
       [POLICY_DENIED / PASS]
```

### 4.3 Controller & Critic Egress Principle
Under Path 2 (Enterprise Three-Identity Deployment), the critic executes under the `AGI_Controller` identity (unrestricted Internet access). Maintaining the critic's unrestricted vantage point is essential to preserve external ground truth and avoid recreating Option A's blind-critic exploit.

---

## 5. Phased Implementation Roadmap

| Phase | Milestone | Subsystem / Files | Model-Free Tests |
| :--- | :--- | :--- | :--- |
| **Phase 0** | **Governance & Joint Review** | `docs/reviews/GEMINI_PROPOSAL_VERIFICATION_ASYMMETRY_2026-09-07.md`<br>`docs/G5_ASYMMETRY_REVIEW_AND_NEXT_ACTIONS_2026-09-07.md` | Doc-only review; 76/76 gate green |
| **Phase 1** | **Broker Logging Hardening (Kill-Assumption)** | `orchestrator/egress_broker.py`<br>`orchestrator/execution.py`<br>`tests/test_egress_broker_integration.py` | Add deny `host` logging; verify per-attempt JSONL audit |
| **Phase 2** | **Attestation Snapshot & Citecheck Schema** | `orchestrator/task_runner.py`<br>`orchestrator/citecheck.py`<br>`tests/test_citecheck.py` | Bind policy snapshot at run time; implement `CitationCheckResult` |
| **Phase 3** | **Abuse Bounds & Preflight Integration** | `orchestrator/deliverable_preflight.py`<br>`orchestrator/evaluation.py`<br>`tests/test_deliverable_preflight.py` | Enforce 25% bound, min 2 `OK`, and fabrication guard |

---

## 6. Implementation Governance & Hold Directives

1. **HOLD Code Implementation:** Strictly zero implementation code will be written until:
   - Hermes delivers H1 (prediction_machine diagnosis) and H2 (broker audit log review).
   - Claude Code issues final comparative review and greenlight.
   - Operator authorizes write scope.
2. **Path 2 Blocking:** Three-identity deployment packaging remains strictly blocked on G5 implementation completion.
3. **OmniRoute:** Strictly held decoupled from the live execution path.
4. **ESTOP:** Strictly maintained as `True`.
