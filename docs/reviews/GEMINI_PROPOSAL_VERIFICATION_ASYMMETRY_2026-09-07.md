# Architectural Design Proposal: Structural Resolution of Sandboxed Worker vs. Host Critic Verification Asymmetry

**Document ID:** `GEMINI_PROPOSAL_VERIFICATION_ASYMMETRY_2026-09-07`  
**Date:** 2026-09-07  
**Author:** Gemini CLI / Google DeepMind Agentic Assistant (Principal Architect & Review Authority)  
**Status:** **PROPOSAL ONLY — PENDING JOINT REVIEW (Claude Code + Hermes + Operator)**  
**Target Subsystems:** `orchestrator/citecheck.py`, `orchestrator/deliverable_preflight.py`, `orchestrator/evaluation.py`, `config/egress_policy.yaml`  
**Prior Art & Context:** F126 (Deliverable Preflight), F127 (Multi-Engine Search & Egress Parity), [`docs/EVIDENCE_INTEGRITY_TASKS_2026-09-07.md`](../EVIDENCE_INTEGRITY_TASKS_2026-09-07.md)

---

## 1. Problem Statement: The Asymmetry Trap

Under F124 and Step 2 host hardening, research workers operate inside a Windows Restricted Token (`S-1-5-12`) enforced by Windows Filtering Platform (WFP) rules:
1. `AGI_Worker_Allow_Broker_Loopback`: Allows worker TCP outbound exclusively to loopback egress broker (`127.0.0.1:8787`).
2. `AGI_Worker_Deny_Direct_Egress`: Denies all direct TCP outbound to the Internet for the worker SID.

Consequently, any external request attempted by the worker is mediated by `orchestrator/egress_broker.py`. If a search hit points to a domain not explicitly allowlisted in `config/egress_policy.yaml`, the broker intercepts the request and returns:
`HTTP 403 Forbidden: {"error": "host_not_allowlisted", "host": "<domain>"}`.

When an honest sandboxed worker receives this response, it complies with prompt instructions:
*"If a source URL returned 403 or was unreachable, note which sources failed, continue with available sources, and mark facts as confidence 1."*

However, the independent critic (`evaluation.py` / `citecheck.py`) executes **on the host controller without restricted token boundaries**, having unconstrained direct Internet access. During verification:
1. Critic takes every URL cited in the deliverable.
2. Critic issues direct host HTTP requests (bypassing the broker).
3. The novel domain responds with `HTTP 200 OK`.
4. Critic notes: *"Worker claimed URL was unreachable/failed, but URL returned 200 OK. Worker hallucinated tool failure or fabricated source unavailability."*
5. Critic issues `VERDICT: FAIL`.

### Why F127 Was an Instance Patch, Not a Structural Fix
F127 synchronized `config/egress_policy.yaml` with the specific domains encountered during the 7 frozen validation missions (PromptBase, Snack Prompt, ContentBot, etc.). This permitted the cohort to pass 7/7, but left the structural flaw untouched:
**The first time an autonomous worker in production discovers an un-allowlisted authoritative domain, the verification asymmetry trap recurs immediately.**

---

## 2. Comparative Analysis of Structural Fix Options

Claude Code's task brief (`EVIDENCE_INTEGRITY_TASKS_2026-09-07.md` §2 G5) identified two architectural paths. We evaluate them below with rigorous adversarial criteria:

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
  1. **Strict Epistemological Parity:** The critic cannot observe any network state that was unavailable to the worker.
  2. **Minimal Schema Complexity:** Citecheck remains a binary check (`reachable` vs `unreachable`).
  3. **No Prompt Engineering Required:** The critic LLM is not asked to weigh subtle policy disclosures.

* **Fatal Vulnerabilities / Failure Modes:**
  1. **The "Blind Critic" Vulnerability (Severe):** If a worker fabricates an entirely fake URL hosted on a non-allowlisted domain (e.g., `https://totally-fake-research-citations.org/data.html`), the broker returns 403 to the critic. If 403 is considered "unverifiable / excused", the critic cannot distinguish a real allowlist denial from a hallucinated or malicious domain. The critic is blinded to external ground truth.
  2. **Shared Blindness to Upstream Drift:** If the broker crashes, stalls, or has loopback connection fatigue, both worker and critic fail identically, creating false positives or masking silent harness degradation.
  3. **Violation of Critic Independence Principle:** The critic's role is objective verification of external reality. Confining the critic to the worker's security perimeter degrades the critic's authority.

---

### Option B: Policy-Denial Categorization (`host_not_allowlisted` as Distinct Classification)
**Concept:** Maintain the host's direct egress vantage point for `citecheck`, but augment the citecheck verification model to explicitly check worker egress policy and distinguish three mutually exclusive verification states:
1. `OK (200)`: URL is reachable, live, and content matches deliverable assertions.
2. `DEAD / FABRICATED (404, 500, NXDOMAIN, Timeout)`: URL does not exist or server failed. Hard fail.
3. `CONTAINMENT_POLICY_DENIED (403 host_not_allowlisted)`: URL is live on the open Internet (critic gets 200), BUT the domain was demonstrably blocked to the worker under the active `config/egress_policy.yaml` policy at run time.

* **Advantages:**
  1. **Preserves Objective Ground Truth:** The critic remains all-seeing. It verifies whether the target actually exists on the public Internet, defeating the "blind critic" exploit.
  2. **Audit Truthfulness:** Cleanly decouples **worker competence** (did the worker tell the truth about what it saw?) from **host policy constraints** (was the worker allowed to see it?).
  3. **Automated Policy Discovery (Self-Hardening):** When citecheck observes `CONTAINMENT_POLICY_DENIED` on high-quality sources, the harness can log a structured candidate suggestion to `runs/policy_expansion_candidates.jsonl` for operator approval.
  4. **Strict Fabrication Guard:** If a worker asserts Confidence 3 or verbatim quotes for a URL that was `CONTAINMENT_POLICY_DENIED`, the critic immediately flags **FABRICATION** (the worker claimed to read text on a page it was physically prevented from fetching!).

* **Disadvantages:**
  1. Requires schema expansion in `citecheck.py` and `evaluation.py`.
  2. Requires mechanical checks in `deliverable_preflight.py` to prevent workers from abusing policy disclosures.

---

## 3. Comparative Evaluation Matrix

| Metric | Option A: Broker Routing | Option B: Policy Denial Category | Evaluation Winner |
| :--- | :---: | :---: | :--- |
| **Epistemological Parity** | High (identical view) | High (critic knows both views) | **Tie** |
| **Hallucination Resistance** | **POOR** (Critic is blind to un-allowlisted domains) | **EXCELLENT** (Critic verifies live web reality) | **Option B** |
| **Fabrication Detection** | Weak (cannot verify what was claimed) | Strong (detects quotes from unreached URLs) | **Option B** |
| **Independence Principle** | Critic bound to worker sandbox | Critic remains independent host judge | **Option B** |
| **Implementation Complexity** | Low (point critic to proxy) | Moderate (structured taxonomy update) | **Option A** |
| **Operational Scalability** | Low (requires pre-allowlisting all sites) | High (un-allowlisted sources cleanly handled) | **Option B** |

---

## 4. Recommended Architectural Specification: Option B+

We recommend **Option B+ (Attested Two-Tier Verification)**:

### 4.1 Schema Expansion in `citecheck.py`
The result structure for URL verification in `citecheck.py` is extended:
```python
@dataclass(frozen=True)
class CitationCheckResult:
    url: str
    host: str
    reachable_on_host: bool
    http_status: int | None
    worker_policy_permitted: bool      # Checked against active egress_policy.yaml
    classification: str                # 'OK' | 'DEAD' | 'POLICY_DENIED' | 'UNREACHABLE'
    error: str | None = None
```

### 4.2 Classification Rules
1. If `reachable_on_host` is `False` (status 404, 5xx, NXDOMAIN, DNS fail):
   - `classification = "DEAD"` -> **Hard Failure.**
2. If `reachable_on_host` is `True` (status 200) AND `worker_policy_permitted` is `True`:
   - `classification = "OK"` -> **Verified Source.**
3. If `reachable_on_host` is `True` (status 200) AND `worker_policy_permitted` is `False`:
   - `classification = "POLICY_DENIED"`.
   - Citecheck verifies deliverable disclosure:
     - Did the worker mark the claim with `confidence: 1` or explicitly state the source was blocked under policy?
     - Did the worker abstain from verbatim quotation marks (`"..."`)?
     - If both hold: **PASS** (honest reporting of bounded evidence).
     - If worker claimed `confidence: 3` or verbatim quotation: **FAIL** (proven fabrication: worker claimed to read a page its network layer was blocked from receiving).

### 4.3 Provenance Cross-Check with Broker Audit Log
To eliminate any ambiguity, citecheck cross-references `runs/task{tid}_a{attempt}_worker.usage.retrieval.jsonl`.
`POLICY_DENIED` is ONLY granted if the worker actually attempted the domain during the run and the broker recorded an intercepted `host_not_allowlisted` event. An un-attempted URL can never claim policy-denial relief.

---

## 5. Review & Implementation Gate

Per Claude Code's task brief:
> **"Do NOT implement until Hermes + Claude review the proposal. This is architectural and gated by the verify → adversarial-review → comparison cycle."**

- **Gemini CLI Recommendation:** Adopt Option B+ as specified in §4.
- **Hermes Action Requested:** Review §2 and §3 for compatibility with `controlled_hermes.py` retrieval behaviors.
- **Claude Code Action Requested:** Review §4 threat model and approve transition to implementation phase.
- **Operator Action Requested:** Confirm structural direction before write scope is claimed.
