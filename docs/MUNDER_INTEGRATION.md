# Munder Difflin Pattern Integration Blueprint

**Status:** DRAFT — uncommitted, awaiting operator sign-off and Gemini review
**Author:** Hermes (specialist task worker), under direct operator instruction, 2026-08-31
**Scope:** Design blueprint only. No code exists for any pattern in this document. Nothing here is implemented or live.
**Authority constraint:** Every pattern below is subordinate to the existing control plane — ESTOP, runlock, DatabaseMutationGuard, and operator-gated promotion. Munder patterns are coordination conveniences, never authority. The 2026-08-31 boundary review stands: mailboxes carry proposals and reports; only the operator, through `run_task.py`, queues missions.

---

## 0. Verification Discipline

Per the operator's no-fabrication rule (2026-08-31): facts in this document are labeled.

- **[VERIFIED]** — read directly from repository code/files during the 2026-08-31 architectural review; source file named.
- **[DESIGN]** — engineering judgment for the proposed integration. No implementation exists. These claims are unproven until a test exists.

---

## 1. Purpose

The Munder Difflin hive (relocated 2026-08-31 out of this repository to `S:\MunderState\AGI_like\hive\` — a separate Electron + Claude Code multi-agent office UI) has demonstrated five architectural patterns worth porting into the AGI_like orchestrator as lightweight Python. This document is the integration blueprint: build priority, implementation strategy per pattern, and the edge-case registry any implementation must survive. It explicitly does NOT import Munder's authority model — no god-agent, no human-proxy autonomy, no dispatch authority over missions.

Current harness state relevant to this blueprint **[VERIFIED]**:
- No mailbox/mailbus system exists in `orchestrator/` (grep verified).
- No PTY manager exists; mission workers are subprocess oneshots launched through `orchestrator/controlled_hermes.py` (`execution.py:hermes_worker`).
- No compaction hook exists; within-turn research context is bounded by the retrieval controller's strategy ladder (`retrieval_progress.py`).
- No embeddings exist anywhere in the harness; recall is FTS5/exact.
- Fail-soft observability exists: `health_events.py` emits `agi.health_event.v1` rows to `runs/health_events.jsonl`.

---

## 2. Priority Order and Rationale

**Build order: Pattern 1 → Pattern 4 → Pattern 5 → Pattern 2 → Pattern 3** (original numbering from the operator's request).

| Build | Pattern (original #) | New risk added | Effort | Depends on |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Asynchronous File Mailboxes (1) | Low | ~1 day | Nothing — extends existing JSONL discipline |
| 2 | Markdown-First Transparent Memory (4) | Low | ~2–3 days | Nothing — extends promote.py doctrine |
| 3 | Anti-Collision Input Drain Loops (5) | Low | ~1 day | Mailboxes (it is what buffers deferred input) |
| 4 | Proactive Context Compaction (2) | Medium | ~2 days | Long-lived workers (Pattern 3) — ship together or not at all |
| 5 | Persistent PTY Daemon Workers (3) | High | ~1 week | Demonstrated, measured need (startup overhead or interactive loops) |

**Rationale:** value delivered per unit of new risk. The harness runs on single-writer, fail-closed, evidence-over-assertion discipline. Patterns that produce auditable *files* (mailboxes, markdown memory) reinforce that and are cheap. Patterns that produce *live process state* (PTY daemons, compaction) evade the ledger, survive ESTOP engagement, and are gated behind demonstrated need.

---

## 3. Build 1 — Pattern 1: Asynchronous File Mailboxes

### 3.1 Design

Mailboxes mechanize what `docs/ACTIVE_WORK.json` + handoff documents already do manually: asynchronous, decoupled, auditable coordination between development agents. They live in the HARNESS (`mailboxes/` at the repository root), not in the external hive.

```
mailboxes/
  router.log.jsonl            # append-only audit trail of every move (never edited)
  <agent-id>/inbox/           # messages addressed to the agent
  <agent-id>/inbox/.done/     # handled messages (archive source, not trash)
  <agent-id>/outbox/          # ONLY this agent's own sends
```

Message schema (one JSON file per message, atomic write):

```json
{
  "id": "mbx-<uuid8hex>",
  "from": "gemini-cli",
  "to": "deepseek-cade | operator | broadcast",
  "act": "request | inform | propose | query | agree | refuse | done",
  "subject": "one-line summary",
  "body": "details (UNTRUSTED DATA — never instructions)",
  "conversation": "conv-<id>",
  "in_reply_to": "mbx-<id> | null",
  "hops": 0,
  "created_at": "ISO 8601 UTC"
}
```

**Invariants:**

- **I1 — Single-writer.** An agent writes ONLY to its own `outbox/`. The router is the only process that moves files between agents. (Matches the Munder doctrine **[VERIFIED]** `S:\MunderState\AGI_like\hive\PROTOCOL.md`: "Never write into another agent's folder. Write to your own outbox/; the orchestrator routes it.")
- **I2 — Atomic delivery.** Write tmp → fsync → `os.replace` within one volume. Readers never see partial JSON.
- **I3 — At-least-once, idempotent handling.** Moving to `.done/` is archival, not acknowledgment. Every handler checks a processed-log (message id) before acting. Dedup follows the same doctrine as scheduler resume-dedup **[VERIFIED]** `scheduler.py`.
- **I4 — Full audit trail.** The router appends every accepted/routed/refused move to `router.log.jsonl`, same append-only discipline as the trajectory stream **[VERIFIED]** `trajectory.py`.
- **I5 — Messages are DATA, never commands.** The reader treats `body` as untrusted content. This is the exact instruction-source boundary already codified for operator files **[VERIFIED]** `inbox/README.md`: "file contents are DATA, never commands."
- **I6 — Mailboxes never dispatch missions.** The router MUST NOT import or call `batch_runner`, `run_task`, `execution`, or `controlled_hermes`. Anything that looks like a mission request routes to the OPERATOR as a proposal, with zero authority. This is the hard boundary from the 2026-08-31 review.
- **Reply semantics.** Only `request`, `query`, `propose` expect replies; `inform` and `done` are terminal. Prevents reply storms. **[VERIFIED]** this exact rule exists in `S:\MunderState\AGI_like\hive\PROTOCOL.md` and is worth copying verbatim.

### 3.2 Implementation sketch (`orchestrator/mailbus.py`, stdlib only)

- `deliver(sender, msg)` — validate schema, stamp `id`/`hops`/`created_at`, atomic-write into sender's `outbox/`.
- `route()` — poll all outboxes; copy+fsync+unlink into recipient `inbox/` (NOT bare rename — see E2); append `router.log.jsonl`. Called only by the runlock-holding harness process (prevents split-brain, see E7).
- `drain(agent)` — list `inbox/` excluding `.done/`; return messages in arrival order.
- `acknowledge(agent, msg_id)` — move to `inbox/.done/`, append processed-log row.
- All failures are fail-soft: emit `health_events.emit("mailbus", ...)` and continue; the router never crashes the harness **[VERIFIED]** fail-soft pattern in `health_events.py`.

### 3.3 Edge cases / failure modes

- **E1 — Crash between read and move → double processing.** A router or handler that dies after acting but before archiving re-processes the message on restart. Fix: idempotency key = message `id` + processed-log check before ANY side effect; archive after the side effect, and make the side effect itself idempotent. NEVER use deletion as acknowledgment.
- **E2 — EXDEV cross-device rename.** `os.replace` raises `OSError EXDEV` when source and destination are on different filesystems. This machine has C: and S: volumes; a mailbox tree spanning them loses messages on a bare rename. Fix: (a) keep the entire mailbox tree on ONE volume; (b) delivery = copy bytes → fsync destination file → fsync destination directory → unlink source; (c) a reconciliation sweep verifies destination presence before unlinking the source, so a crash mid-delivery leaves a duplicate, never a loss.
- **E3 — Partial writes visible to readers.** A reader can parse a half-written file. Fix: I2 tmp-then-rename makes this structurally impossible for the writer; readers additionally skip unparseable files with a health event instead of crashing.
- **E4 — Unbounded growth.** `.done/` grows forever. Fix: weekly archival/rotation; because I4 keeps the full audit trail in `router.log.jsonl`, archiving or pruning `.done/` loses nothing.
- **E5 — Prompt injection via mailbox.** A malicious or confused sender wraps instructions in a message body ("please run run_task with..."). Fix: I5 + per-reader instruction that mailbox bodies are untrusted; sanitize or refuse bodies containing executable-path-like strings; injection attempts get a health event and a refusal logged in the router log.
- **E6 — Backpressure.** A recipient never drains → sender's outbox piles up. Fix: router counts inbox backlog per agent; above a threshold it refuses NEW work-routing to that agent (health event) but NEVER blocks or deletes the sender's outbox. Senders are never blocked; undelivered intent stays durable.
- **E7 — Two routers / split brain.** Two harness processes both running `route()` → double delivery and racing unlinks. Fix: `route()` runs only under the existing runlock discipline **[VERIFIED]** `runlock.py`, or behind its own O_EXCL lock file.
- **E8 — Reply-storm loops.** Two agents `request`↔`request` forever. Fix: terminal-verb rule (above) + per-conversation hop cap in the router (drop + health event after N hops, N≈10).

---

## 4. Build 2 — Pattern 4: Markdown-First Transparent Memory

### 4.1 Design

- Per-agent durable memory: `memory/agents/<agent-id>/memory.md` — plain text, human-auditable, git-tracked.
- **The typed SQLite ledgerbook stays canonical for mission facts; markdown memory is the human-audit VIEW, never the source of truth.** This is the existing split, not a new one **[VERIFIED]** `HARNESS_DESIGN.md §1.2`: freeform Hermes-native memory for identity/preferences + typed Ledgerbook for domain knowledge, with supersede-don't-overwrite and candidate TTL.
- Anything injected into worker PROMPTS goes through the existing human-gated promotion path **[VERIFIED]** `promote.py`: "Promotion is HUMAN-gated in M1: review only ever drafts; approve is the operator."

### 4.2 Implementation

- `append_fact(agent, fact, provenance)` — append-only with timestamp + provenance line (mandatory); corrections are appended, history is never edited in place.
- Search: index paragraphs into an SQLite FTS5 table at write time (stdlib, exact, zero new dependencies); `memory.md` remains the display source. Weekly consolidation merges/dedups per the existing manager/curator cycle doctrine **[VERIFIED]** §1.2 write policy.
- Facts remain subject to the candidate-TTL + weekly promotion cycle; nothing is silently permanent.

### 4.3 Why embeddings are deferred **[DESIGN]**

- Embeddings insert a model dependency into memory recall; recall quality becomes unmeasurable, which directly conflicts with the no-fabrication culture (exact recall or no recall).
- FTS5 covers exact-token recall. Measure recall misses for at least two weeks (log a `memory_lookup_failed` health event per miss); add embeddings only when measured evidence shows FTS5 misses that matter. Do not build on speculation.

### 4.4 Edge cases

- **E9 — Memory drift / stale facts.** Fix: supersede-don't-overwrite with validity windows, exactly the ledgerbook doctrine **[VERIFIED]** §1.2; a stale fact's window closes, history preserved.
- **E10 — Cross-agent memory poisoning.** Agent A writes a wrong "fact" about agent B's work and it propagates. Fix: `memory.md` is per-agent SELF-authorship only; cross-agent claims travel by mailbox → review, never direct writes into another agent's memory.
- **E11 — Memory as injection surface.** Memory eventually feeds prompts, so the same untrusted-content rule as mailboxes applies; the mandatory provenance line is the audit anchor.
- **E12 — Git bloat from append-only.** Never-edit grows unboundedly. Fix: the consolidation pass MAY rewrite and shrink files — git history is the audit trail; that is exactly why the memory is markdown-in-git rather than a binary store.

---

## 5. Build 3 — Pattern 5: Anti-Collision Input Drain Loops

### 5.1 Design — scoped adaptation

AGI_like workers are not interactive typists, so the collision risk is orchestrator-level: new input (mailbox messages, scheduled fires, operator commands) arriving while a task is mid-flight.

- Extend the runlock concept from "one batch process" to "one dispatch cycle": the main loop polls mailboxes and admits new work ONLY between task state transitions, never mid-task.
- **Deferred, never dropped.** Today a second harness process fails closed with `AlreadyRunning` **[VERIFIED]** `runlock.py` — correct, but the caller must retry. The drain loop instead buffers the incoming input durably and applies it at the next safe point.

### 5.2 Edge cases

- **E13 — Dropped operator input during a long task.** The operator believes a command was seen; it never was. Fix: every buffered input gets an immediate durable "state: buffered" row that status output surfaces; nothing is silently swallowed.
- **E14 — Unbounded buffer during a runaway task.** Fix: cap the pending buffer (≈100); refuse with a health event beyond the cap; entries older than a TTL expire with a visible terminal state — the same queueing/expiration/crash-recovery doctrine the scheduler already implements **[VERIFIED]** `scheduler.py`.
- **E15 — Self-deadlock.** A task waits on an operator answer that sits in the very buffer the task's dispatch cycle is blocking. Fix: operator-destination messages BYPASS the drain gate (routed immediately); only agent-destination work is buffered.
- **E16 — Ordering and ownership.** Two commands for the same subsystem arrive during one task. Fix: both buffered, applied in arrival order, with ACTIVE_WORK.json ownership checked immediately before EACH applies — ownership may have changed while buffered.

---

## 6. Build 4 — Pattern 2: Proactive Context Compaction

### 6.1 Design — deferred until long-lived workers exist

Current workers are `hermes -z` oneshots **[VERIFIED]** `execution.py` — they exit per task, and within-turn research is already bounded by the retrieval controller's non-bypassable strategy ladder **[VERIFIED]** `retrieval_progress.py`. Compaction has nothing to compact. It becomes relevant only when Pattern 3 introduces long-lived conversational workers — build them together or not at all.

When built:

- Trigger at **70% of the model's reported `context_window_size`** — the number the provider reports, never an estimate.
- Checkpoint = a `context_compacted` lifecycle event in the trajectory stream; the summary is persisted as event payload; the conversation continues.
- **Hard rule: a compaction summary is NEVER mission evidence.** It is lossy by definition; citecheck would correctly fail any citation of it **[VERIFIED]** `citecheck.py` independently fetches and literal-matches every cited URL. Summaries are coordination state only.

### 6.2 Edge cases

- **E17 — Compaction mid-tool-batch.** Summarizing between a tool call and its result corrupts the batch's meaning. Fix: compact ONLY at tool-batch boundaries — the lifecycle the Hermes contract already defines **[VERIFIED]** `hermes_contract.py` (`begin_tool_batch`/`end_tool_batch` with `finally`-protected cleanup).
- **E18 — Memory cliff.** The summary drops the one fact the remaining 30% needed → silent quality loss. Fix: post-compaction self-check ("what did I drop?") recorded as a trajectory event; the critic sees that compaction occurred and can weight the verdict accordingly.
- **E19 — Compaction loop.** A large summary re-triggers compaction. Fix: measure post-compaction usage before continuing; summaries carry a hard token cap (~10% of window); if usage is still >70% post-compaction, the turn hard-stops into the bounded partial-result path per retrieval doctrine.
- **E20 — Lying trigger measurements.** Providers differ on measured vs. estimated counts. Use provider-measured counts wherever available — the harness already insists on provider-owned counts for accounting **[VERIFIED]** `execution.py` (F33 comment); where only estimates exist, trigger early (60%).

---

## 7. Build 5 — Pattern 3: Persistent PTY Daemon Workers

### 7.1 Design — gated on demonstrated need

Do NOT build until a measured pain exists: per-task oneshot startup overhead, or genuinely interactive development loops. This is the highest-risk pattern of the five: **a long-lived process is state the ledger does not own, survives ESTOP engagement, and evades runlock's PID-death detection.**

When built **[DESIGN]**:

- Windows ConPTY (pywinpty or a ctypes wrapper) with the PTY and ALL its children assigned to a Windows Job Object; kill = job termination (see E24).
- Worker contract: the daemon MUST poll `pause_engaged()` between EVERY tool batch and self-exit 75 on ESTOP; the harness reaps and never restarts until the operator clears ESTOP.
- A reader thread ALWAYS drains PTY output, extracts events into trajectory/health streams, and never stops reading (see E22).
- Durable state contract: any in-process learned state is written through trajectory/usage/audit files BEFORE a task is acknowledged; on daemon death, in-process state is by definition lost and the task is retried from ledger state.

### 7.2 Edge cases (the ones that bite)

- **E21 — ZOMBIE PTYs IGNORING ESTOP.** The sentinel file stops NEW dispatches; it cannot kill a live daemon **[VERIFIED]** `execution_pause.py` is a stat-check on a sentinel path. A daemon that keeps running after ESTOP engagement spends tokens with full tool access — the exact invariant violation this architecture forbids. Layered fixes:
  1. Daemon self-exit: polls `pause_engaged()` between every tool batch, exits 75 itself.
  2. Watchdog: harness enumerates owned daemons; each daemon's heartbeat writes a timestamped file; a daemon that has not observed the sentinel within N seconds is flagged.
  3. Escalation ladder before any kill: steer → constrain → stop (Munder's circuit-breaker ladder **[VERIFIED]** `S:\MunderState\AGI_like\hive\PROTOCOL.md` — worth copying).
  4. Any kill verifies process identity first (see E23).
- **E22 — Pipe-buffer drain deadlock.** If the manager stops reading a PTY's stdout while the child fills the OS pipe buffer (a few KB to tens of KB), the worker blocks on write forever mid-task. The reader thread must ALWAYS drain — after "timeout", after deciding to kill, during kill — drain first, then kill.
- **E23 — PID reuse.** The runlock already solved this for batch processes via `process_start_identity` (Windows creation FILETIME / Linux start-ticks) **[VERIFIED]** `runlock.py`. The PTY manager MUST use the same identity check before killing, or it can kill an innocent process that inherited a reused PID.
- **E24 — Windows conhost zombie leaks.** Each Windows PTY wraps a hidden console host; killing the shell but not the job leaks processes that hold file locks on `ledger/*.db` — which then blocks every future run with a spurious integrity failure (F1's failure shape returning through a new door). Fix: assign PTY + children to a Job Object; kill the job; verify no survivors by identity, not by name.
- **E25 — TERMINAL DESYNCHRONIZATION.** Typed input can land while the child is mid-output (spinner, progress bar, interactive prompt): bytes interpolate into whatever is on screen and the command is lost or corrupted — the PTY form of Munder's input-drain problem. Fixes:
  1. Prompt detection — never send input until the child's latest output matches a known prompt pattern.
  2. An input-drain gate INSIDE the manager (Pattern 5 synergy): queue input while busy (no prompt seen), send when idle.
  3. Resync ritual on suspicion: newline → wait for fresh prompt echo → discard until marker → retry with sequence numbers.
  4. Windows PTYs: Enter is `\r` and a bare `\n` may never submit — explicit submit semantics with echo verification, never a raw newline.
- **E26 — Secret bleed into scrollback.** A daemon that runs a command with env expansion echoes secrets into the PTY transcript. The trajectory redactor covers API keys/Bearer tokens in structured payloads **[VERIFIED]** `trajectory.py` (deep recursive redaction); extend the same redaction across any persisted PTY transcript BEFORE writing — and prefer persisting only extracted events, never raw transcripts.
- **E27 — Stale ≠ dead.** An idle daemon may be thinking, quota-blocked, or hung — externally indistinguishable. Heartbeat = tool events in the trajectory. No heartbeat + alive process = steer signal, not kill. Hard-kill only on operator instruction or breaker level 3.
- **E28 — Daemon state the ledger doesn't own.** In-process memory (partial work, learned facts) is invisible to the audit trail. Fix: the durable-state contract in 7.1; a daemon that dies loses its in-process state by design, and the task resumes from ledger state.

---

## 8. Cross-Cutting Invariants (all five patterns)

- **I-ESTOP:** Every pattern fails closed under ESTOP. The router may keep moving coordination messages (they are files), but NOTHING may create live execution while ESTOP is engaged; daemons self-exit; compaction halts new dispatch; the drain loop admits no new work.
- **I-RUNLOCK:** One dispatch cycle at a time; the router runs only under lock.
- **I-GUARD:** No process except ledger-owning modules writes to `ledger/ledger.db` or `memory/ledgerbook.db` — DatabaseMutationGuard holds **[VERIFIED]** `integrity.py`.
- **I-EVIDENCE:** Every claim of work carries a file path, exit code, or log row behind it (Handoff Protocol §1.2 doctrine).
- **I-NO-AUTHORITY:** No Munder-derived component may queue missions, authorize live calls, clear ESTOP, or act as the operator's proxy. Mailboxes carry proposals and reports; the operator dispatches.

---

## 9. Non-Goals

- No import of Munder's god/human-proxy authority model.
- No adoption of the hive as harness infrastructure — the patterns are ported as lightweight harness code; the hive remains a separate UI experiment, now external to the repository at `S:\MunderState\AGI_like\hive\` (this relocation, completed 2026-08-31, also resolves the nested-repo hygiene conflict flagged in the 2026-08-31 boundary review).
- No embeddings until measured recall misses justify them.
- No PTY daemons until measured startup overhead or interactive need justifies them.
- No live provider calls, no ESTOP changes, no mission execution as part of drafting this blueprint.

---

## 10. Open Questions for Gemini's Review

1. **Mailbox tree location:** git-track README + `router.log.jsonl` but gitignore raw message bodies (they may contain operator data), or track everything? Recommendation: track README + router log; ignore bodies.
2. **Router execution model:** long-running thread in the harness main loop, or a cron-fired sweep? Recommendation: cron-fired sweep — simpler, runlock-compatible, adds no new daemon.
3. **Compaction trigger:** fixed 70% vs. adaptive by task type (research-heavy vs. synthesis-heavy)?
4. **PTY library on Windows:** pywinpty vs. raw ConPTY via ctypes + Job Objects — needs a time-boxed spike before commitment.
5. **Input-drain scope:** is the keystroke-level drain gate needed at all for oneshot workers, or is it a Pattern-3-only concern? Recommendation: the orchestrator-level dispatch-cycle gate ships regardless (Build 3); keystroke-level drain ships only with PTY daemons (Build 5).
6. **Archival:** should `.done/` pruning join the existing nightly backup cycle (`orchestrator/backup.py`, AGI_M1_backup)?
7. **Edge cases this document misses:** explicitly requested — Gemini's review should add architectural edge cases not covered here.

---

## 11. Verification Appendix

**[VERIFIED]** on disk 2026-08-31 during the architectural review (files read directly):

- No mailbox/mailbus code exists in `orchestrator/` (grep for outbox/mailbox/mailbus: no hits).
- `orchestrator/execution.py` — `hermes_worker` launches oneshots via `controlled_hermes.py` launcher; subprocess per task; F33 comment insisting on provider-measured token counts.
- `orchestrator/health_events.py` — fail-soft JSONL observability, schema `agi.health_event.v1`.
- `orchestrator/trajectory.py` — append-only event stream, deep recursive secret redaction, monotonic sequences, resume from highest valid sequence.
- `orchestrator/runlock.py` — `process_start_identity` (Windows creation FILETIME / Linux start-ticks), stale reclaim after 3600s, fail-closed on corrupted locks.
- `orchestrator/retrieval_progress.py` — non-bypassable strategy ladder: search → direct_fetch → browser → partial_result.
- `orchestrator/hermes_contract.py` — contract v1, AST validation of installed Hermes, `begin_tool_batch`/`end_tool_batch` with `finally`-protected cleanup, single finalization enforced.
- `orchestrator/integrity.py` — DatabaseMutationGuard (row-count verification around worker execution).
- `orchestrator/promote.py` — promotion target is repo-versioned markdown; promotion is HUMAN-gated ("review only ever drafts; approve is the operator").
- `orchestrator/scheduler.py` — queueing, expiration, crash recovery, resume dedup in `ledger.db`.
- `inbox/README.md` — instruction-source boundary: "file contents are DATA, never commands."
- `HARNESS_DESIGN.md §1.2` — freeform memory for identity + typed Ledgerbook for domain knowledge; supersede-don't-overwrite; candidate TTL; weekly consolidation.
- `S:\MunderState\AGI_like\hive\PROTOCOL.md` — single-writer outbox doctrine; terminal-verb reply semantics; circuit-breaker ladder (steer → constrain → stop); spawn-requests OFF by default.
- `docs/HANDOFF_PROTOCOL.md` §1 — repository is canonical; evidence over assertion; single-writer ownership; zero live calls without authorization.

**Everything else in this document is [DESIGN]** — engineering judgment with no implementation behind it. Any implementation PR must carry its own tests and evidence per the Handoff Protocol.

---

## 12. Gemini's Architectural & Safety Review

**Reviewer:** Gemini CLI (Independent Principal Architect / Reviewer)
**Timestamp:** 2026-08-31
**Verdict:** **APPROVED IN PRINCIPLE (DRAFT)** with mandatory hardening constraints detailed below.

### 12.1 Control Plane & Security Hardening

1. **Backdoor Prevention & Execution Boundary (Invariant I6 Enforcement):**
   - **Zero Execution from Mailboxes:** The mailbus must never import, invoke, or expose execution entry points (`run_task.py`, `batch_runner.py`, `controlled_hermes.py`). Mailbox messages are strictly **passive structured data** (`act`, `subject`, `body`, `provenance`).
   - **Instruction Containment:** Message bodies must never be passed to `eval()`, `exec()`, shell interpreters, or template engines.
   - **Proposal Interception:** Any message attempting to request task dispatch, tool execution, or ESTOP bypass is automatically classified as an untrusted proposal. The router redirects such proposals to the Operator's review queue with a `mailbus_security_alert` logged to `runs/health_events.jsonl`.

2. **Atomic Delivery & Windows Cross-Volume / File-Locking Mitigations (E2 & E3):**
   - **Single-Volume Affinity:** The mailbox directory tree (`mailboxes/`) must reside exclusively on the primary workspace volume (`S:\AGI_like\mailboxes`). Cross-drive paths (such as `C:\Users\...` or `%TEMP%` on C:) are strictly prohibited to prevent `OSError EXDEV`.
   - **Atomic Staging Pattern:** Deliveries must follow the strict staging discipline:
     1. Write message to `mailboxes/<agent>/.tmp/<uuid>.tmp`.
     2. `file.flush()` followed by `os.fsync(file.fileno())`.
     3. Atomic rename via `os.replace` to target `inbox/` or `outbox/`.
   - **Windows Sharing Violation Resilience:** On Windows NTFS, anti-malware scanners, search indexers, or concurrent readers can momentarily hold open read handles resulting in `PermissionError` (Error 13 / 32). The router must implement a 3-step exponential backoff retry (10ms, 50ms, 200ms) with jitter before falling back to copy-verify-fsync-unlink.
   - **Directory Fsync Semantics:** Acknowledge that `os.fsync` on directory handles is a no-op/unsupported on Windows; atomic delivery is anchored by file-level fsync and directory-level atomic replacement.

3. **Runlock & Drain Loop Deadlock Prevention (E15 & E16):**
   - **Non-Blocking Ingress:** Enqueuing incoming inputs into the deferred buffer must be an append-only, non-blocking operation that never requires holding the primary execution runlock.
   - **Discrete Drain Points:** The dispatch loop drains the deferred buffer strictly at safe state transitions (between task completions or while idle), never interrupting active tool batches.
   - **Anti-Deadlock Invariant:** A task must **never** synchronously wait on a response from a deferred input while retaining the execution runlock. Any task requiring operator feedback or peer consensus must cleanly release the lock, enter a `WAITING_INPUT` state, and yield execution to the scheduler.
   - **Bounded Buffer & TTL:** The pending buffer is hard-capped at 100 entries. Entries exceeding a 3600s TTL without pickup are transitioned to `EXPIRED` with an explicit health event.

### 12.2 Technical Research Directives for Hermes

To prepare the implementation specifications, **Hermes** is assigned to execute deep technical research on the following 3 concrete implementation questions:

1. **Windows Filesystem Notification vs. Polling Mechanics:**
   - Investigate the reliability and resource overhead of Python `watchdog` (`ReadDirectoryChangesW`) vs. a deterministic 500ms `os.scandir` poll under the runlock on Windows NTFS/ReFS.
   - Specifically analyze handle leak risks, missing event bugs under burst I/O (`ERROR_NOTIFY_ENUM_DIR`), and antivirus lock contention.

2. **SQLite FTS5 Incremental Indexing for Markdown Memory:**
   - Design a minimal, standalone SQLite FTS5 virtual table schema to index paragraph chunks from `memory/agents/<agent>/memory.md`.
   - Define the exact synchronization trigger, BM25 rank weighting, and query interface that prevents database lock contention while remaining 100% compliant with `DatabaseMutationGuard`.

3. **Windows ConPTY Job Object Teardown & Process Tree Containment:**
   - Determine the minimal `ctypes` / `pywinpty` implementation required to bind spawned PTY daemon processes and all descendant processes (`conhost.exe`, subshells, compilers) to a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
   - Detail how to guarantee that engaging ESTOP instantly reaps all orphan processes without PID-reuse hazards.
