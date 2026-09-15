# Gemini Engineering Report — V1 Productization & Unified Interface (MiroFish + Munder Difflin + AGI_like)

**Date:** 2026-09-14
**Author:** Gemini CLI (Independent Principal Architect)
**Audience:** Claude Code (Final Reviewer), System Operator
**Baseline Verified:** HEAD `a871d9a` · Tree Clean · ESTOP True · 0 Zombies · Continuity Rev 124
**Directives Fulfilled:**
- `docs/GEMINI_TASK_V1_PRODUCTIZATION_DIRECTIVE_2026-09-14.md` (Phases 1–3)
- `docs/reviews/CLAUDE_VERIFICATION_PHASE0_YIELD_GATE_2026-09-14.md` (Gating Sign-off & In-Transit Fix Mandate)
- Operator Directive: "couple MiroFish and Munder Difflin and get an interface like a couple baby together"

---

## 1. Executive Summary

Following Claude Code's verification and formal greenlight of the Phase 0 Yield Gate (`CLAUDE_VERIFICATION_PHASE0_YIELD_GATE_2026-09-14.md`), we have resolved the in-transit broker caching bug and implemented the commercial V1 interface and governance layer:

1. **In-Transit Fix — Egress Broker Dynamic mtime Hot-Reload (`orchestrator/egress_broker.py`):**
   - Implemented request-time `st_mtime` inspection on `config/egress_policy.yaml`.
   - When policy changes, the running broker reloads `egress_policy.load_policy()` in-memory without dropping in-flight connections or requiring a process restart.
   - Eliminates the attestation↔runtime divergence identified during Phase 0 (where `justprompt.io` was denied despite being added to the policy).
   - Added Section 12 integration test to `tests/test_egress_broker_integration.py` (49/49 checks passing).
   - Restarted the running standalone daemon on `127.0.0.1:8787` with fresh hot-reloading code.

2. **Phase 1 — Policy Governance Engine & CLI (`orchestrator/policy_manager.py`):**
   - Implemented operator-gated propose-and-confirm policy management (strictly rejecting non-deterministic auto-admission).
   - Added RFC 1035 / RFC 1123 hostname syntax checks and IP address literal rejection.
   - Added DNS liveness resolution via `socket.getaddrinfo` and anti-SSRF address bounds checking (rejecting private, loopback, link-local, and reserved IP ranges).
   - Added static risk heuristics (flagging dynamic DNS providers like DuckDNS/ngrok and high-risk TLDs like `.zip`/`.mov`).
   - Implemented `approve` with atomic YAML write, alphabetical ordering, Ed25519 attestation re-signing via `scripts/enforce_worker_firewall.ps1 -Action Attest`, and append-only audit logging to `runs/policy_approvals.audit.jsonl`.
   - Implemented `reject` recording explicit operator decisions to prevent re-proposal.
   - Verified by `tests/test_policy_manager.py` (42/42 checks passing).

3. **Phase 2 — MCP Server & Thin CLI Gateway (`orchestrator/gateway.py`):**
   - Implemented standalone Model Context Protocol (MCP) server conforming to JSON-RPC 2.0 over stdio for Claude Desktop, Cursor IDE, and CI/CD agentic invocation.
   - Exposes tools: `dispatch_task`, `check_status`, `get_deliverable`, `get_attestation`, `propose_domains`, `approve_domain`, `reject_domain`.
   - Enforces strict per-task budget hard-stops ($1.00 USD / 100,000 tokens default; hard ceiling at $5.00 / 250,000 tokens).
   - Verified by `tests/test_gateway.py` (18/18 checks passing).

4. **Phase 3 — Unified Executive Web Console ("The Couple Baby" — `orchestrator/web_ui.py`):**
   - Synthesizes the three distinct inspirations into a unified, zero-dependency commercial operations deck:
     - **MiroFish Layer (Cognitive Simulation & Intelligence Graph):** Interactive SVG network topology visualizer displaying domain reachability nodes (Green: Allowlisted, Amber: Review, Red: Denied) linked to the kernel, with real-time tool/thought streams and visual mission dispatch.
     - **Munder Difflin Layer (Autonomous Swarm Office & Operations Deck):** Card-based office floor featuring 4 autonomous stations (Worker Desk: Hermes/S-1-5-12, Auditor Desk: GLM-5.2 ground truth, Boundary Warden: Egress Broker 8787, Scribe Desk: AGI_AuditSigner service), live Kanban mission table, and token/cost odometer.
     - **AGI_like Hardened Core (Cryptographic Trust & Governance):** Ed25519 signature badge with digest inspection, split-view Literal Citation Evidence Inspector modal (verbatim broker receipt matching), and 1-Click Policy Governance drawer.
   - Verified by `tests/test_web_ui.py` (16/16 checks passing).

5. **Test Gate Expansion:**
   - Registered `test_policy_manager`, `test_gateway`, and `test_web_ui` into `tests/tiers.json`.
   - Full test gate expanded from 79 to **82 test suites**, 100% green across all tiers.

---

## 2. In-Transit Fix: Egress Broker Hot-Reload

### The Operational Bug Diagnosed
In Task 226, `justprompt.io` was denied once by the running broker PID 12128 even though it had been added to `config/egress_policy.yaml`. The running broker had been active since September 12 and cached `self.policy` in memory. This produced an **attestation↔runtime divergence**: the signed attestation attested to policy digest `17f08fd6`, but the runtime enforced the old policy digest.

### The Remediation
In `orchestrator/egress_broker.py`:
1. Added `policy_path: Path | None` and `_policy_mtime: float | None` attributes to `EgressBroker`.
2. Implemented `reload_policy_if_changed()`:
   ```python
   def reload_policy_if_changed(self) -> bool:
       if not self.policy_path or not self.policy_path.is_file():
           return False
       try:
           current_mtime = self.policy_path.stat().st_mtime
           if self._policy_mtime is None or current_mtime > self._policy_mtime:
               new_policy = egress_policy.load_policy(self.policy_path)
               self.policy = new_policy
               self._policy_mtime = current_mtime
               return True
       except Exception:
           pass
       return False
   ```
3. In `BrokerHandler.do_CONNECT`, before authorizing destination:
   ```python
   if hasattr(self.server, "reload_policy_if_changed"):
       self.server.reload_policy_if_changed()
   ```
4. Added Section 12 test to `tests/test_egress_broker_integration.py`:
   - Configures broker with `allowed_hosts: ["initial-allowed.test"]`.
   - Probes `new-domain.test:443` -> gets HTTP 403.
   - Writes `new-domain.test` into the policy YAML.
   - Probes `new-domain.test:443` again -> immediately succeeds with HTTP 200 without restart.
   - Verifies `dyn_broker.policy.allowed_hosts` contains `new-domain.test`.

---

## 3. Policy Manager (`orchestrator/policy_manager.py`)

### Architecture & Capabilities
The policy manager provides the deterministic, operator-gated boundary lifecycle:

```
                      runs/policy_expansion_candidates.jsonl
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │   PolicyManager.propose   │
                        └─────────────┬─────────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
      RFC Syntax / IP Check                      DNS Liveness & SSRF
  (RFC 1035/1123, 253 chars,                  (socket.getaddrinfo, check
   anti-IP literal, TLD check)               private/loopback/link-local)
                 │                                         │
                 └────────────────────┬────────────────────┘
                                      │
                                      ▼
                             Operator Decision
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
      [1-Click Approve]                               [1-Click Reject]
              │                                               │
              ├─► Append to egress_policy.yaml                └─► Record in
              ├─► Re-sign Ed25519 token (Attest)                  policy_approvals.audit.jsonl
              └─► Record in policy_approvals.audit.jsonl          (Filtered from future proposals)
```

### CLI Interface
- `python orchestrator/policy_manager.py list`: Lists all observed candidate expansion domains with request counts and task IDs.
- `python orchestrator/policy_manager.py propose`: Evaluates candidates, validates syntax, tests DNS, and outputs structured safety proposals.
- `python orchestrator/policy_manager.py approve <domain> [--rationale <text>]`: Adds domain, re-signs attestation token, and logs to audit JSONL.
- `python orchestrator/policy_manager.py reject <domain> [--reason <text>]`: Records rejection in audit JSONL.

---

## 4. MCP Gateway & CLI (`orchestrator/gateway.py`)

### Protocol & Tool Schemas
Conforms to Model Context Protocol (MCP) specification:
- `dispatch_task(spec, pass_criteria, mission_id, max_budget_usd)`: Enforces budget ceilings ($1.00 USD default, $5.00 hard limit) and queues into `ledger/ledger.db`.
- `check_status(task_id)`: Returns full execution lifecycle, token spend, duration, and critic notes.
- `get_deliverable(task_id)`: Returns finalized deliverable markdown from disk.
- `get_attestation(task_id)`: Decodes signed attestation JWT, inspects claims, and verifies task policy snapshot.
- `propose_domains()`, `approve_domain()`, `reject_domain()`: Exposes policy governance tools to LLM orchestrators.

---

## 5. The Unified Executive Web Console (`orchestrator/web_ui.py`)

### Coupling the Three Inspirations
The web interface brings together the visual simulation of MiroFish, the autonomous office structure of Munder Difflin, and the cryptographic security of AGI_like into an integrated single-page console:

| Feature Dimension | Inspiration | Implementation in `orchestrator/web_ui.py` |
|---|---|---|
| **Cognitive Topology Graph** | MiroFish | Interactive dynamic SVG node-link graph showing target domains, allowlist state (Green/Amber/Red), and request counts. |
| **Swarm Office Floor** | Munder Difflin | 4-Station Office Deck (Worker, Auditor, Warden, Scribe) with live health indicators, active models, and token burn odometers. |
| **Mission Pipeline** | Munder Difflin | Real-time Kanban table showing mission progression, critic verdicts (PASS/FAIL/REVIEW badges), durations, and token spends. |
| **Cryptographic Attestation** | AGI_like | Prominent header shield with live Ed25519 policy digest, active lease time, and WFP containment indicator. |
| **Literal Citation Inspector** | AGI_like | Split-view inspection modal matching deliverable statements against raw HTTP CONNECT broker receipts. |
| **Policy Governance Portal** | AGI_like / Phase 1 | Candidate domain queue with 1-click Approve & Sign / Reject buttons. |
| **Emergency STOP** | AGI_like | Interactive operator ESTOP toggle switch with fail-safe confirmation. |

---

## 6. Empirical Verification & Test Matrix

All test suites execute model-free and run cleanly:

1. `tests/test_egress_broker_integration.py` (49/49 checks pass):
   - Section 12 proves dynamic mtime reload without broker restart.
2. `tests/test_policy_manager.py` (42/42 checks pass):
   - Tests syntax, SSRF rejection, candidate summarization, propose, approve, reject, and CLI.
3. `tests/test_gateway.py` (18/18 checks pass):
   - Tests dispatching, budget ceilings ($5.00 limit), ledger querying, attestation inspection, and MCP protocol.
4. `tests/test_web_ui.py` (16/16 checks pass):
   - Tests server startup on ephemeral port, HTML rendering, REST endpoints, task dispatch, and clean socket teardown.
5. Canonical Test Gate (`tests/run_all.py`):
   - **82/82 suites green, exit 0**.

---

## 7. State & Continuity Summary

- **Repository Branch:** `master`
- **Head Commit:** Working tree staged for review.
- **ESTOP Status:** Strictly engaged (`True`).
- **Running Daemons:**
  - Egress Broker: `127.0.0.1:8787` (fresh hot-reloading PID active).
  - Web Console: Ready to launch via `python orchestrator/web_ui.py --port 8080`.
  - Windows SCM Service: `AGI_AuditSigner` running as `.\AGI_Signer`.
- **Active Work Locks:** Ready for release upon operator review.
