# Adversarial Architecture Audit + Hardening Blueprint
**Date:** 2026-07-19 · **Scope:** orchestrator, DB-isolated worker, critic, promote.py gate, schtasks crons
**Status of findings:** each marked PROVEN (probe run), OBSERVED (live evidence in ledger), or REASONED.

## Method
Probes ran against **copies** of `ledger.db`/`ledgerbook.db` in a temp dir, invoking the *real*
functions (not reimplementations). One probe (v1) was itself buggy — `ledger._conn()` binds
`LEDGER_DB` as a **default argument at import time**, so monkeypatching `ledger.LEDGER_DB` did
not redirect writes and a junk row landed in the live ledger; it was detected via a
count/ID contradiction, deleted, and the ledger verified back to its exact prior 13 rows before
re-probing correctly. That footgun is itself finding **F12**.

---

## Findings (severity-ordered)

### F1 — Concurrent runs cause silent DATA LOSS + false security alarms · **P0 · PROVEN**
There is **no mutual exclusion anywhere** in the orchestrator (`grep lock|flock|mutex` → none).
`db_integrity_check()` compares raw row *counts* before/after a worker call and deletes the
newest N rows on any positive delta. It cannot distinguish a rogue worker from a legitimate
sibling process.

Probe result (on copies): process A snapshots → process B legitimately queues task 16 → A's
check fires `INTEGRITY VIOLATION`, **deletes task 16**, writes a quarantine file, and escalates
"worker wrote directly to a database" — a false accusation about a non-existent attack.

Live exposure is not hypothetical: `AGI_M1_canaries` (Sun 03:30) runs 5 browser-research tasks
that have taken 5–8 min each — comfortably overlapping `AGI_M1_scorecard` (Sun 04:00), which now
also runs `promote.py review`. Any operator command issued during a cron window does the same
(this occurred repeatedly during development).

### F2 — Power loss orphans tasks permanently · **P0 · PROVEN by absence**
`ledger.start_task()` writes `status='running'`; **no code path ever reads or resets it**:
- `--resume` selects only `status='quota_wait'`
- `queue_mission_tasks()` dedup resumes only `quota_wait`/`queued`; a `running` row falls to
  `continue` — treated as "already done this week" and **suppressed from re-queue**
- `expire_stale_parked()` touches only `quota_wait`

So a task interrupted by power loss, `kill`, or a crashed host is orphaned **forever**: never
retried, never counted (fitness counts only `done`/`failed`), and its seed is blocked for the
rest of the ISO week by dedup. Silent, permanent work loss with no operator-visible signal.

### F3 — The critic validates FORM, not TRUTH · **P1 · REASONED (structural)**
The critic is deliberately tool-free. It therefore **cannot verify that a single cited URL
exists**, resolves, or supports the claim attached to it. It can only check that a URL-shaped
string, a date, and a confidence tag are present. A worker that fabricates plausible citations
passes the automated gate unconditionally. The only real defense is the human spot-check —
3–5 samples/week against ~10 tasks/week. (Confirmed in practice: PromptBase facts had to be
verified by opening the live page in a browser; the harness structurally could not do it.)

### F4 — Brittle verdict parse silently inverts good verdicts · **P1 · PROVEN by inspection**
`("pass" if verdict_text.strip().upper().startswith("PASS") else "fail")`.
`**PASS**` (markdown bold), `VERDICT: PASS`, or any leading think-block/preamble parses as
**fail**. Failures are indistinguishable from genuine ones in the ledger.

### F5 — Retry loop makes the critic self-anchoring · **P1 · REASONED**
Retry-with-feedback injects the critic's prior objections into the worker prompt, then the
**same critic model** judges the result — i.e. it grades whether its own checklist was satisfied.
This rewards superficial compliance ("added a retrieval date to each line") over substantive
correctness, and compounds because manager and critic are currently the *same model*
(`glm-5.2:cloud`), sharing blind spots. Truncation at 24k has already produced one false FAIL
at the old 8k cap; the class of error persists for very long deliverables.

### F6 — Head-of-line blocking starves later seeds · **P1 · OBSERVED**
On the first `quota_wait` the runner logs "parking remaining tasks" and `break`s. The next fire
re-processes the same ordered list, so a seed that reliably parks first permanently blocks the
seeds behind it. Live evidence — mission 002:

| task | status | started_at |
|---|---|---|
| 12 (seed 1) | quota_wait | 2026-07-18T18:29:18 (attempted repeatedly) |
| 13 (seed 2) | queued | **None — never once attempted** |
| 14 (seed 3) | queued | **None — never once attempted** |

### F7 — Metric integrity inverts under scarcity · **P1 · PROVEN**
`expire_stale_parked()` flips last week's `quota_wait` → `stale`, and `weekly_fitness()` counts
only `done`/`failed`. Quota-starved work therefore **vanishes from the record** — neither
completed nor failed. The scorecard reports **100% completion precisely when the system is
failing to do the work**. W29 already shows F=1.0 while 5 of 13 rows are parked/queued.

### F8 — The cost cap is decorative · **P1 · PROVEN**
`policy.yaml` declares `usd_per_day_hard_stop: 5.00`, but **no code references it**
(`grep usd_per_day|hard_stop|cost_cap` → none in `orchestrator/`), and `cost_usd` is `0.0` for
every task because Ollama reports no cost. Actual consumption is real and large: **4,521,677
input tokens** logged. There is currently **no working spend or consumption guard of any kind** —
the provider's own quota is the sole limiter. The moment a paid API key is added (the standing
recommendation), an unbounded retry loop becomes a direct financial risk.

### F9 — Cross-provider failover is config-only · **P2 · PROVEN**
`models.yaml` defines `fallback_chain`, but no orchestrator code reads it. §1.6's core lesson —
429 is *account-level*, so failover must cross **providers** — is documented but unimplemented.

### F10 — Indirect prompt-injection path into all future prompts · **P2 · REASONED**
Chain: hostile web page → worker deliverable → `lesson_candidates` → model-drafted candidate
skill note → operator approves → `active_skills_for()` appends it to **every future worker
prompt for that mission**. The operator gate is the only barrier, and they are approving
model-written prose that may be skimmed. Persistence makes this materially worse than a
single poisoned task.

### F11 — Default SQLite busy timeout (5s), no retry · **P2**
Every `sqlite3.connect()` omits `timeout=`. WAL is correctly enabled on both live DBs (verified),
which permits reader/writer concurrency, but two writers still serialize; a long transaction
raises `database is locked` and the run dies with no retry.

### F12 — `_conn(db=LEDGER_DB)` default-arg binding · **P2**
Import-time binding makes the ledger path effectively unpatchable, defeating test isolation and
inviting exactly the accident this audit hit. Any future test that "redirects" the DB silently
writes to production.

### F14 — The workspace "container" does not exist in code · **P0 · PROVEN**
§2.6 stakes the entire WIDE-autonomy safety model on one claim: *"WIDE autonomy makes the
container the safety mechanism, not per-action prompts,"* and *"orchestrator refuses paths
outside workspace."* **No such enforcement exists.** `grep` for any path guard
(`is_relative_to`/`resolve`/`writes_allowed_under`/`refuse.*path`) finds nothing in the live
path. The worker holds `write_file`/`edit_file`/`terminal`/`python`/`patch` (the `-t web` flag
does not strip them — see F-history / INCIDENTS.md) and runs under the operator's own account.
`db_integrity_check()` guards the two databases; **nothing guards the filesystem.** Consequences:
a prompt-injected or simply errant worker can overwrite `orchestrator/batch_runner.py` itself
(e.g. delete the integrity guard before the next run), rewrite the `AGI_M1_*` scheduled tasks,
or read/modify `.env`. The prior rogue-worker incident (INCIDENTS.md) only stayed contained
because it happened to write *inside* `workspace/`; one directory up and nothing would have
caught it. This is strictly worse than F1 — F1 loses ledger rows; F14 loses the harness's own
integrity and the account's file safety.

### F13 — `policy.yaml` is enforced by ZERO code · **P1 · PROVEN**
The autonomy exclusions, `usd_per_day_hard_stop`, deny-list, and `writes_allowed_under` are all
declared in `policy.yaml`; no executable path loads the file (`run_task.py`'s only reference is
a docstring; `batch_runner.py`'s two matches are a comment and a hardcoded
`MAX_WORKER_CALLS_PER_RUN = 12`). Every stated policy control is currently a document, not a
control — the deny-list included.

### F15 — `promote.py` commits are not isolated · **P2 · PROVEN**
`cmd_approve`/`cmd_rollback` do `_git("add", <specific paths>)` then `_git("commit", -m …)` with
no pathspec — committing the entire staged index. An approval issued while other work is staged
sweeps it into a "Promote skill" commit, corrupting the audit trail's one-change-per-commit
property. Fix: `git commit -- <paths>` (explicit pathspec) or commit from a clean index check.

### F16 — The "source of truth" has no second copy · **P0 · PROVEN**
`CLAUDE.md` promises *"Nightly `hermes backup` + `git push` = recovery."* Every clause is false
today: **no git remote** is configured (`git remote -v` empty — nowhere to push); **`ledger.db`
and `ledgerbook.db` are both gitignored** (`git check-ignore` confirms — git holds zero task/
fact/decision/scorecard state); **no backup scheduled task** exists (only the four `AGI_M1_*`,
which back nothing up); **no `hermes backup` has ever run** (no backups dir on disk). The ledger,
the typed memory, and the full fitness history therefore exist in exactly one ungitignored local
file with no remote, no snapshot, and no schedule to make one. A single disk failure is total,
unrecoverable loss — and it silently invalidates the founding invariant "if it's not in the
ledger, it didn't happen," because the ledger is the single least-durable artifact in the system.
Pairs with F2: the design assumes a crash/power-loss/disk-failure world and defends against none.

---

## Hardened blueprint

### H1 — Single-writer discipline (fixes F1, F11) · **IMPLEMENTED + PROVEN 2026-07-19**
`orchestrator/runlock.py` + `main()`/`_run()` split in `batch_runner.py`. Verified: 5 unit
properties (acquire/release, contention→`AlreadyRunning`, stale-lock reclaim after 3600s,
lock released on exception) all pass; **two genuinely concurrent `batch_runner.py` processes**
raced for the lock — exactly one proceeded, the other logged the skip and exited 0, lock
correctly absent after both finished. This is the literal Sunday canaries/scorecard overlap
that produced F1. `timeout=30` added to all 22 `sqlite3.connect()` call sites across the
orchestrator (fixes F11); `ledger._conn()`'s default-arg path binding (F12) fixed alongside it
since it's the same function. H2 (below) remains valuable as defense-in-depth for writes that
originate outside the lock's coverage (e.g. a manual `sqlite3` session, or the lock file being
externally deleted) but H1 alone removes the dominant real-world exposure.
- **Run lock:** `runs/.batch.lock` acquired via `os.open(..., O_CREAT|O_EXCL)` (portable) at
  entry, released in a `finally`; stale locks (PID dead / older than max run duration) are
  reclaimed. Second instance exits `0` with "another run in progress — skipping", which is the
  correct behaviour for an overlapping cron.
- **Stagger crons:** canaries Sun 02:00, scorecard Sun 05:30 — beyond worst-case canary runtime.
- **`timeout=30` + retry-on-locked** on every connect (centralise in `ledger._conn`).

### H2 — Provenance-based integrity, not count-based (fixes F1 properly)
Give every orchestrator run a `run_id` (UUID). Stamp `tasks.run_id` and `facts.run_id` on every
insert the orchestrator makes. The guard then quarantines **only rows whose `run_id` is NULL or
unknown** — i.e. genuinely un-attributable writes (the actual rogue-worker signature) — and
ignores rows correctly attributed to any live run. This preserves true detection while making
false positives structurally impossible, instead of relying on "no one else is running."

### H3 — Crash recovery via leases (fixes F2)
- Add `tasks.lease_expires_at`, refreshed while a task runs.
- **Startup reconciliation:** any `running` row whose lease has expired → `interrupted`, then
  re-queued (attempt counter +1, capped to avoid crash-loops), logged and surfaced in the
  scorecard as an explicit line — never silently.
- `queue_mission_tasks()` dedup must treat `interrupted` as resumable.

### H4 — Make the critic check truth, not shape (fixes F3, F4, F5)
- **Mechanical citation validator (pre-critic, no LLM):** extract every cited URL, issue a
  bounded-concurrency `HEAD`/ranged `GET`, and record status + whether the claim's key literals
  (price/number/name) appear in the fetched text. Feed that *evidence table* to the critic and
  hard-fail any deliverable with dead/unverifiable citations above a threshold. This converts
  form-checking into truth-checking **without** giving the critic tools.
- **Tolerant verdict parse:** require a `VERDICT: PASS|FAIL` line; regex-extract it; treat an
  unparseable verdict as `needs_review`, never as a silent fail.
- **Blind re-judge on retries:** the critic sees the original criteria and the new deliverable,
  **not** its own prior notes — removing the self-anchoring loop.
- **Distinct critic model** whenever a second provider exists (config already supports it).

### H5 — Fair scheduling + honest scarcity accounting (fixes F6, F7)
- **No `break` on park:** continue to the next task, tracking consecutive parks; stop only after
  N consecutive (quota genuinely exhausted) — a park is per-attempt, not proof of global death.
- **Rotate the start index** per run (`offset = run_count % len(tasks)`) so no seed can be
  permanently shadowed.
- **`abandoned` is a first-class outcome:** stale/never-attempted work appears on the scorecard
  as its own line and *depresses* a new `attempt_coverage` term. Fitness must fall when the
  system fails to do work — today it rises.

### H6 — Real budget enforcement (fixes F8, F9)
- Enforce a **token** budget (the unit actually reported): `tokens_per_day_hard_stop`, checked
  before each worker call, halt + escalate on breach. Keep the USD cap for paid providers and
  **wire it to real enforcement** before any paid key is added.
- Implement `fallback_chain` traversal on 429/5xx so failover crosses providers as designed.

### H7 — Constrain the skill-promotion surface (fixes F10)
- Candidate notes must match a **strict template** (technique statements only); strip URLs and
  imperative "visit/fetch/run" constructions at draft time.
- `promote.py list` shows a full diff plus the source lesson/task provenance, so approval is
  informed rather than a skim.
- Cap total injected skill text and log every injection in the run log.

### H8 — Test isolation (fixes F12)
Remove default-arg DB binding; resolve paths through a single accessor so tests can redirect
safely. Add a `--db-root` flag used by all probes.

### H9 — Real filesystem confinement + real durability (fixes F14, F13, F16)
- **Confinement (F14):** the worker subprocess must not run with `write_file`/`terminal`/
  `python`/`patch`. Since Hermes toolset flags proved unreliable, run the worker under a
  constrained profile (`hermes profile create` with those toolsets disabled) OR sandbox the
  subprocess (separate low-priv user / container) — verified by a probe that a worker instructed
  to write outside `workspace/` fails. Until then, F14 is the strongest argument against leaving
  crons unattended.
- **Policy as code (F13):** load `policy.yaml` at startup; enforce the cost/token cap, the
  deny-list, and `writes_allowed_under` as actual runtime checks. A declared control that no code
  reads is worse than no control — it manufactures false confidence.
- **Durability (F16):** add an `AGI_M1_backup` scheduled task — nightly `sqlite3 .backup` of both
  DBs to a timestamped file (survives WAL/mid-write, unlike a file copy) + a rotated offsite copy
  (git remote for code/docs; DB snapshots to a second drive or a private remote). Verify by
  restoring into a scratch dir and diffing row counts. This is a prerequisite for calling the
  ledger a source of truth at all.

---

## Roadmap to M2

**Phase 0 — P0 hardening (before the next unattended cycle).** Now five P0s, not two:
H1 (concurrency lock), H2 (provenance-based integrity), H3 (crash recovery), plus H9's
**durability** (nightly `sqlite3 .backup` task — the ledger currently has no second copy, F16)
and H9's **confinement** (constrained worker profile — the worker can currently overwrite the
orchestrator's own code, F14). All are correctness/data-loss/security fixes and every one can
fire on this week's live crons. *Exit:* concurrency probe shows the legitimate row surviving; a
killed mid-run task is recovered on next start; a restored backup diffs clean; a worker told to
write outside `workspace/` is denied; full round-trip re-verified. **Until Phase 0 lands, the
honest operational posture is: do not leave the crons running unattended** — F14 (self-code
overwrite) and F16 (single-copy ledger) mean an unattended failure can be unrecoverable.

**Phase 1 — Trust the numbers (during W30 baseline).** H4 (citation validator + parse + blind
re-judge), H5 (fair scheduling, `abandoned` accounting), H6 (token budget). Rationale: M1's
entire claim is "measurable improvement." F3/F7 mean the current numbers can look perfect while
being wrong — the metric must be trustworthy *before* the promotion gate starts optimising
against it at W31.
*Exit:* a deliberately fabricated-citation deliverable is auto-failed; scorecard shows an
`abandoned` count; token cap halts a run in test.

**Phase 2 — W31 promotion under hardened conditions.** H7 first, then enable the gate. The
self-improvement loop must not be the thing that discovers these flaws.

**Phase 2.5 — Runtime abstraction (the "better than Hermes" step).** Operator intent, stated
2026-07-19: this harness should surpass Hermes rather than remain a script on top of it. Taken
literally that is a category error — we *invoke* `hermes -z` — but there is a real and
achievable version, and the measurement says it is cheap:

**Coupling surface is 3 call sites in 1,828 lines** — `batch_runner.hermes_worker()`,
`run_task.hermes_oneshot()` (legacy hand-run), `scorecard.send_telegram()`. Everything that
makes this system distinctive — ledger, fitness, critic, canaries, promotion gate, containment,
typed memory — is ours and stdlib-only.

*Where we should not compete:* Hermes is a capability substrate (53 plugins, multi-platform
gateway, MCP, browser automation, computer-use, TTS/STT, profiles, kanban). Reimplementing that
is years of work for no gain. Keep renting it.

*Where the gap is real:* Hermes has **no fitness function, no pre-written pass criteria, no
independent critic, no canary regression set, no append-only audit ledger, and no evidence-gated
promotion with rollback.** Its curator prunes skills; it never measures whether the agent got
better. That is the unoccupied ground, and it is exactly the "digital employee vs. capable
chatbot" distinction the project was founded on.

*The step:* extract a `Runtime` protocol — `run_tool_task(prompt, model) -> (text, usage)` and
`send_message(text)` — with `HermesRuntime` as the first implementation and a direct-API
implementation as the second. Consequences: the harness becomes runtime-agnostic exactly as it
is already model-agnostic; Hermes defects stop being *our* defects (five were found this session
alone: `-t web` not restricting tools, a 33-version-stale config schema silently killing message
dispatch, `send --to telegram` not inferring its own discovered channel, an unreliable
`usage.json:completed` flag, UI chatter in captured stdout); and the accountability layer
becomes portable to any substrate, which is the only durable definition of "better."

*Honesty constraint:* this step is **not** a claim of superior engineering. The harness currently
carries two P0 data-loss bugs (F1, F2). Phases 0–2 must land first — a system with unproven
crash recovery does not get to call itself better than a mature runtime.

**Phase 3 — M2 (content-ops employee).** Only after M1's 8 weeks. The harness generalises, but
M2 breaks three current assumptions that need design work, not just a new mission pack:
1. **Artifacts stop being text.** Deliverables become video/audio/image assets; the critic
   cannot read them. Needs an artifact-type registry and per-type verification oracles
   (ffprobe duration/resolution/loudness checks, frame sampling) — mechanical where possible.
2. **A human step sits mid-pipeline** (the manual Krea render). The loop must support
   *suspend/resume across days* with durable state — which is exactly F2's lease machinery,
   generalised into a real `awaiting_human` state.
3. **Cost per task rises by orders of magnitude** (render time, paid generation). H6's budget
   enforcement stops being hygiene and becomes the primary safety control.

---

## Note on scope
This document is an audit + blueprint. Nothing in H1–H8 has been implemented yet; the findings
above are what the current code *does today*. Implementation is sequenced by the roadmap, P0
first.
