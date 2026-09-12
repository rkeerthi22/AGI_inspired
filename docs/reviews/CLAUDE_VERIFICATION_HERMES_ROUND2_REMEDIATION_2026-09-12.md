# Claude Independent Verification — Hermes Round-2 Remediation + Task 184 Live Proof — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-12
**Re:** Gemini's Round-2 remediation report (commits `99b5d5c`, `003660b`) — independent verification
**Baseline verified:** HEAD `003660b` (already at local master) · tree clean · gate **79/79** exit 0 (re-run by Claude this session) · ESTOP engaged · continuity rev 110

---

## Verdict: ALL 8 FIXES CONFIRMED · BUG #1 CLOSED LIVE · 1 NEW LIVE-CRASH BUG BLOCKS VENTURE LAUNCH

Gemini's remediation is **substantively correct and honestly disclosed**, including two new bugs it caught. Every code fix verified by parsing the file at HEAD. The kill-assumption (§3: browser egress on live traffic) is **LIVE-VERIFIED** — I parsed `runs/task184_a1_broker.audit.jsonl` myself: 10 aiprm broker rows (6 allow, 4 deny) vs **zero** in Tasks 176/178. Bug #1 is closed on live traffic, not just code-level.

**One new bug Gemini disclosed is a venture-launch blocker** (not Gemini's fault — it disclosed it honestly): `model_infrastructure_failure` escalation trigger crashes the task runner when the critic has an infra failure, and it **actually fired on Task 184**, leaving it a zombie (`status='running'`, `critic_verdict=None`). See §2 below.

---

## 1. Per-fix verification table (every fix parsed at HEAD `003660b`)

| § | Fix | Claude's parsed evidence (file:line) | Status |
|---|---|---|---|
| §1.1 | If-Match lost-update guard | `fetch_s3_checkpoints(..., return_etag=True)` returns ETag (:160-162); `ckpt_kwargs["IfMatch"]=checkpoint_etag` (:344-345); `IfNoneMatch="*"` (:347); `PreconditionFailed`/`412` caught (:358) | ✅ FIXED |
| §1.2 | body-hash verify (replace ContentLength-only) | `get_object` (:229, replaces head_object); `hashlib.sha256(body).hexdigest() != digest` (:233); returns `replica_artifact_hash_mismatch` (:234) | ✅ FIXED |
| §1.3 | S3 retention floor | `minimum_retention_days` in config (:50); `s3_retention_floor_check` (:419); `chain_days` computed (:454); fails when `<minimum_retention_days` (:455) | ✅ FIXED |
| §1.4a | strict routing (drop `or HARNESS_AUDIT_S3_BUCKET`) | both routing lines (:345, :463) now `if backend == "s3":` — OR clause GONE | ✅ FIXED |
| §1.4b | invalid retention mode raises (no coerce to COMPLIANCE) | `raise S3AuditReplicationError(f"invalid_retention_mode:{retention_mode}")` (:107) | ✅ FIXED |
| §1.5 | error subclass | `class S3AuditReplicationError(AuditReplicationError):` (:33); lazy import :27 + fallback def :29 (circular-import guard) | ✅ FIXED |
| §2 | linter M4 false positive | trigger list now `["not publicly disclosed", "disclose"]` — "not available" DROPPED (:84-86); `allowed_placeholders` dynamically detects criteria-mandated phrases (:91-99) | ✅ FIXED |
| §4 | ownership token dead code | ZERO hits for `ownership_token`/`DAEMON_OWNERSHIP_FILE`/`agi_browser_daemon_token` — fully deleted; `is_cdp_ready` pre-check (:113) + `allow_external_reuse=False` (:211, :229) retained | ✅ FIXED |
| §5 | CURRENT_STATE.md:129 | promoted to live-proven with "verified live on Task 184 (10 aiprm broker audit rows parsed)" | ✅ (now honest — see §3) |

**Gate:** `python -B tests/run_all.py` → 79/79 suites green, exit 0 (re-run by Claude this session). s3_audit_replication 12/12, deliverable_preflight 35/35, browser_daemon 12/12.

---

## 2. §3 kill-assumption — LIVE-VERIFIED (broker JSONL parsed by Claude, not cited)

**This was the load-bearing unproven claim in the whole arc.** I parsed the artifact myself, I did not take Gemini's row count.

`runs/task184_a1_broker.audit.jsonl` (27,811 bytes, dated 2026-09-12 16:06):
- **166 total broker rows**
- **10 aiprm rows** (`task_id=184` on all):
  - 6× `allow` for `app.aiprm.com` (the mission's target JS SPA — the page that produced ZERO rows in 176/178)
  - 4× `deny` for `log02.aiprm.com` (analytics — correctly blocked, `host_not_allowlisted`)
- Broader broker activity on the run: `1.www.s81c.com` (57), `ark.ap-southeast.bytepluses.com` (27), `accounts.google.com` (12), etc. — Chrome's egress is genuinely being intercepted and routed through the loopback broker on live traffic.

**Verdict: bug #1 (browser-egress regression) is CLOSED on live traffic.** A proxied Chrome navigation now produces per-task broker rows with the current wiring. This is the gap Hermes caught (zero browser rows in 176/178), now closed. The kill-assumption survived.

### The `broker_attempt_verified=False` honesty correction (Gemini's new bug #1 — CONFIRMED)

Gemini disclosed that `broker_attempt_verified` in `task184_a1_citation_evidence.json` is `False`, and explained why. **I verified Gemini is correct, and that my original brief (and Hermes's Round-1 report) misconstrued this field.** `citecheck.py:560-564`:
```
broker_attempt_verified = (host in broker_denied_hosts or stripped in broker_denied_hosts)
```
The field is `True` **only for hosts the broker DENIED** (G5 Gap 2 asymmetry — grants POLICY_DENIED classification instead of UNREACHABLE to broker-blocked hosts, per :463). For an **allowed** host like `app.aiprm.com`, `broker_attempt_verified=False` is the **designed** behavior — it does NOT mean the broker failed to verify the attempt. The live proof of bug #1 closure rests on the **10 broker rows** (parsed above), not on `broker_attempt_verified`. Gemini caught a real misconception that both Hermes and Claude had propagated. Honest correction accepted.

---

## 3. New bug #2 — `model_infrastructure_failure` trigger crash: REAL, LIVE, and a venture blocker

**Gemini disclosed this honestly. I verified it is real, and worse than Gemini framed it — it actually fired on Task 184.**

- `orchestrator/policy.py:33`: `VALID_TRIGGERS = {"deny_list_match", "pass_criteria_ambiguous", "cost_cap_breach", "repeated_task_failure", "model_failover"}` — **`model_infrastructure_failure` is NOT in the set.**
- `orchestrator/task_runner.py:672-674`: when the critic evaluates `infra_failed`, calls `integrity.escalate(..., trigger="model_infrastructure_failure", task_id=tid)`.
- `policy.py:42-44`: `validate_trigger()` raises `ValueError` for any trigger not in `VALID_TRIGGERS` → unhandled → crashes the task runner.
- `config/policy.yaml:59-62`: escalation triggers declared list also omits `model_infrastructure_failure`.

**The ledger proves it actually fired on Task 184** (queried by Claude):
```
{'task_id': 184, 'status': 'running', 'critic_verdict': None, 'critic_notes': None}
```
Task 184 is a **zombie** — stuck in `running` with no critic verdict, because the cloud critic had an infra failure, the escalation crashed on the ValueError, and the task never completed as `infra_failed` (the graceful path). Gemini said "crashing the task runner instead of gracefully completing with status infra_failed" — confirmed by the ledger.

**Honesty gap in Gemini's report:** Gemini disclosed the bug but its §3 verdict ("LIVE-VERIFIED") did not explicitly flag that **Task 184 itself is therefore a zombie, not a completed task.** The egress live-proof (§2 above) stands regardless — the broker audit is written during the WORKER phase, which completed before the critic crash. But Task 184 as a task is incomplete. This is a minor framing gap, not a concealment: Gemini disclosed the crash openly in §5.

### Why this blocks venture launch

Under quota pressure (the exact steady-state this harness lives in — BytePlus/Ollama 429s), the cloud critic (`ollama/glm-5.2:cloud`) will hit infra failures. When it does, every such task will zombie instead of grading as `infra_failed` and parking cleanly. On real venture work, that means silently-stuck tasks under exactly the failure mode that's most common. **Fix before pointing at real venture work.** It's a 2-line fix: add `"model_infrastructure_failure"` to `VALID_TRIGGERS` (`policy.py:33`) and to the `config/policy.yaml:59` triggers list.

---

## 4. Net status

| Item | Status |
|---|---|
| Bug #1 (browser egress) | **CLOSED LIVE** — 10 aiprm broker rows parsed on Task 184 (was zero on 176/178) |
| S3 durability #3 (concurrency) | FIXED — If-Match guard |
| S3 durability #4 (body hash) | FIXED — get_object + sha256 |
| S3 durability #5 (retention floor) | FIXED — s3_retention_floor_check ported |
| S3 config #6a/#6b | FIXED — strict routing + invalid-mode raises |
| S3 error #7 (subclass) | FIXED — S3AuditReplicationError(AuditReplicationError) |
| Linter M4 false positive | FIXED — "not available" dropped from NPD trigger; placeholders whitelisted |
| Ownership token dead code | FIXED — deleted; real defense (is_cdp_ready) retained |
| `broker_attempt_verified` misconception | CORRECTED — field is deny-only by design (Gap 2); proof rests on broker rows, not this field |
| **`model_infrastructure_failure` trigger crash** | **OPEN — venture blocker** (2-line fix; Task 184 is a live zombie) |
| Gate / ESTOP | 79/79 exit 0 / engaged |

---

## 5. Recommendation

**One fix stands between here and venture-ready:** add `model_infrastructure_failure` to `VALID_TRIGGERS` (`orchestrator/policy.py:33`) and to `config/policy.yaml:59` triggers, plus a hermetic test that an infra-failed critic now escalates cleanly (no ValueError) and parks the task as `infra_failed`. Also: clean up the Task 184 zombie in the ledger (mark `infra_failed` or re-run under the fixed trigger).

After that fix + a green gate, the repo is venture-ready for the first real task — and per Hermes's recommendation, make the first real venture task one with a browser component, so it simultaneously produces paying work and re-confirms the bug #1 live proof on a non-cohort mission.

*Verified by Claude Code (final reviewer), 2026-09-12. All claims probed this session: 8 code fixes parsed at HEAD `003660b`; Task 184 broker JSONL parsed by Claude (166 rows, 10 aiprm — 6 allow/4 deny); Task 184 ledger queried (status=running, critic_verdict=None — zombie); VALID_TRIGGERS set read; task_runner:672-674 escalate call read; gate re-run 79/79 exit 0. The `broker_attempt_verified` misconception (mine + Hermes's) corrected by Gemini, confirmed against citecheck.py:560-564. No claim accepted on Gemini's assertion alone — including Gemini's; the one place Gemini understated (Task 184 zombie state) is flagged in §3.*
