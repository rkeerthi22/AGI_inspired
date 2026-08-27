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

### F6 — Head-of-line blocking starves later seeds · **P1 · FIXED + PROVEN 2026-07-27**
On the first `quota_wait` the runner logs "parking remaining tasks" and `break`s. The next fire
re-processes the same ordered list, so a seed that reliably parks first permanently blocks the
seeds behind it. Live evidence — mission 002 (2026-07-18) and independently reconfirmed on
mission 001 during the 2026-07-27 Phase-2 health check:

| task | status | started_at |
|---|---|---|
| 12 (seed 1) | quota_wait | 2026-07-18T18:29:18 (attempted repeatedly) |
| 13 (seed 2) | queued | **None — never once attempted** |
| 14 (seed 3) | queued | **None — never once attempted** |
| 16 (seed 1, W30) | quota_wait | 2026-07-20T04:00:02 (attempted, started_at SET) |
| 17-19 (seeds 2-4, W30) | queued | **None — never once attempted, stuck a full week** |

**Fixed:** `queue_mission_tasks()` and the `--resume` block in `_run()` (`batch_runner.py`) no
longer return task_ids in fixed seed order every call. They now sort never-attempted rows
(`started_at IS NULL`) ahead of already-attempted ones (`started_at` set — the task reached
`start_task()`/`hermes_worker()` before hitting quota or the token budget), tie-broken by
`task_id`. On a mission's FIRST fire of the week every row is equally untried, so the order is
unchanged from before (seed 1,2,3,4); the fix only changes behavior on a RETRY fire, which is
exactly where the starvation happened. Proven on a DB copy: seeded the exact live scenario (seed
1 attempted+parked, seeds 2-4 queued/untouched) under the current week key, confirmed
`queue_mission_tasks()` now returns `[seed2, seed3, seed4, seed1]` instead of
`[seed1, seed2, seed3, seed4]`.

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

### F9 — Cross-provider failover is config-only · **P2 · FIXED + PROVEN 2026-07-27**
`models.yaml` defines `fallback_chain`, but no orchestrator code reads it. §1.6's core lesson —
429 is *account-level*, so failover must cross **providers** — is documented but unimplemented.

**Fixed:** `batch_runner.py` gained `load_fallback_chain()` + `worker_with_failover()` (wrapping
`hermes_worker()`, used by `run_task`/`run_canaries`) and `synthesis_with_failover()` (wrapping
`ollama_chat()`, used by `run_synthesis()` — quota shows up as `HTTPError(429)` there, not text in
a subprocess reply, so detection differs even though the chain-walking logic is shared). On a
quota error ONLY (a genuine subprocess timeout still propagates as `subprocess.TimeoutExpired`
exactly as before — this does not become a general retry-on-any-failure mechanism), the chain
advances worker → `fallback_chain` entries not already tried, ending at local `gemma4:12b`.
Operator decision 2026-07-27: complete the work on a slow local model rather than park it.

**Kill assumption probed live before building this** (per CLAUDE.md's "find the kill assumption
first"): one real `hermes -z ... --provider ollama -m gemma4:12b` run correctly answered a
factual question (Shopify founded 2006) with a genuinely reachable citation (HTTP 200, verified
via `citecheck.verify()`) in ~7 minutes for a single fact. **Residual, documented risk, not a
blocker:** the same probe self-reported "today's date" wrong by 2 years when asked to state it
unprompted — not disqualifying, because every real worker prompt already injects the literal
current date as text (`run_task()`, RULES clause) for the model to copy rather than compute; still,
every failed-over deliverable is escalated (new trigger `model_failover`, added to
`policy.yaml`'s `escalation.triggers` + `policy.VALID_TRIGGERS`) for spot-check priority rather
than trusted silently, since a smaller/local model is a real accuracy downgrade regardless of this
one probe's result.

Local (non-`:cloud`) rungs get `LOCAL_FALLBACK_TIMEOUT_S=3600` instead of the cloud
`WORKER_TIMEOUT_S=900` — gemma4:12b is measured at 1.54 tok/s (§1.6) and drives hermes's full
browser tool-calling loop; the cloud timeout would kill a real multi-fact brief mid-generation.
`ledger.update_model_used()` (new) keeps `model_used` truthful to whichever model actually
produced the output, including the deliverable's own footer text — provenance that used to be
silently wrong the moment a failover occurred. Proven via DB-copy/mocked-call tests: (1) sustained
quota on the two cloud rungs correctly reaches and succeeds on gemma, with the extended timeout
applied only to that rung; (2) an all-rungs-quota-exhausted chain correctly reports `exhausted`
(parks, same as pre-fix behavior); (3) a genuine `TimeoutExpired` on any rung still propagates
unchanged, not swallowed by the failover loop; (4) `ledger.update_model_used()` correctly updates
the live schema's `model_used` column.

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

### F15 — `promote.py` commits are not isolated · **P2 · FIXED + PROVEN 2026-07-27**
`cmd_approve`/`cmd_rollback` do `_git("add", <specific paths>)` then `_git("commit", -m …)` with
no pathspec — committing the entire staged index. An approval issued while other work is staged
sweeps it into a "Promote skill" commit, corrupting the audit trail's one-change-per-commit
property. Fix: `git commit -- <paths>` (explicit pathspec) or commit from a clean index check.

**Fixed, and a sharper fix than first drafted.** The naive version of "add a pathspec" broke
immediately when actually run: `_candidates/*.md` files are **never git-tracked**
(`cmd_review()` writes them with plain `write_text()`, no `_git()` call — confirmed via
`git log --all -- 'skills_analyst/_candidates/*'`, empty). `cmd_approve()` already unlinks the
source candidate from disk before the git call, so once a pathspec limits the commit to
`[dest, _candidates/]`, the `_candidates/` half matches nothing — `git commit -- <path matching
nothing>` **errors and aborts the whole commit**, true every time the approved skill is the only
pending candidate for its mission (the common case: `MAX_CANDIDATES_PER_MISSION=1`). Caught only
by actually running the fix (an isolated scratch git repo, per the H9-incident lesson that
git-based mechanisms need a real repository), not by reading the diff. Second, independent bug
the same root cause exposed: the original `git add <dir>` on `_candidates/` would have swept up
any OTHER still-pending, unreviewed candidate sitting in that same directory into whichever
candidate's approval commit happened to run — a second one-change-per-commit violation the
original F15 writeup didn't name. **Corrected fix:** drop `_candidates/` from the git add/commit
paths entirely — `cmd_approve` stages and commits `dest` only; `cmd_rollback` already scoped to
the single tracked `target` path, so it only needed the pathspec added, not the same rethink.
Proven in an isolated scratch git repo (unrelated staged change present, exactly the F15
scenario) for both commands: the resulting commit contains only the skill path, the unrelated
change remains staged-but-uncommitted afterward, and — unlike the first draft — the commit
succeeds instead of erroring when `_candidates/` is otherwise empty. The rollback proof reused
the actual production `newest_skill_below_baseline()` + `cmd_rollback()` functions (not a
reimplementation), doubling as the C2 auto-rollback verification below.

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

### F17 — Python-local vs SQLite-UTC clock mismatch · **P1 · PROVEN, found 2026-07-24 building H3**
Measured directly: this machine's Python `datetime.now()` runs **2 hours ahead** of SQLite's
`datetime('now')` (UTC). The codebase mixes both conventions — `created_at` defaults via SQL
`datetime('now')` (UTC); `started_at`/`finished_at` are set via Python `datetime.now().isoformat()`
(local). Any comparison mixing the two is silently wrong by the local UTC offset. Caught in
practice: H3's first lease implementation compared a Python-local "10 minutes ago" timestamp
against SQLite's UTC `datetime('now')` and the row did not register as expired — off by
~2 hours, in the direction that would have made a real crash recovery silently fail to fire.
Same class of risk applies to `ledger.weekly_fitness()`'s 7-day window (Python-computed cutoff
vs UTC-stored `created_at`) — low practical impact at a 7-day grain, but the exact mechanism
that just caused a real bug at a 25-minute grain. **Fix applied for the lease specifically**
(H3, below): compute and compare entirely in SQLite's own clock via `datetime('now', '+N
seconds')`, never mixing in a Python-computed value. **Not yet fixed elsewhere** — any future
tight time comparison (anything under ~a few hours) must use the same SQL-only pattern; treat
`datetime.now()` compared against a DB-stored UTC timestamp as unsafe by default in this codebase.

**CONFIRMED LIVE 2026-07-27** (not yet fixed — flagged as a follow-up task, not fixed inline
during the Phase-2 on-ramp pass that found it, to keep that pass's scope to what was actually
approved): while verifying the promotion machinery's scorecard build, directly measured the
predicted residual risk actually manifesting. `weekly_fitness()`'s window start (Python-local
`datetime.now() - timedelta(days=7)`) compared against `created_at` (SQLite-UTC) shifts the
real boundary ~2h later than a true 7-days-ago-in-UTC cutoff — confirmed by comparing the
computed boundary against real task rows straddling it. Same root cause recurs in TWO more call
sites never touched by the H3 fix: `scorecard.py`'s `_week_start()`, feeding both
`canaries_green()` and `crash_recovery_counts()`. Impact remains narrow (only rows within the ~2h
boundary sliver are affected, not a systemic score inversion) but is the same bug class F17 named
as generalizable. Fix sketched and handed off rather than built inline: reuse the SQL-only-clock
pattern from H3 (`datetime('now', '-7 days')` computed IN SQL, never in Python) across all three
sites, ideally behind one shared helper so they can't drift independently again.

**Fixed same day, F19 (below):** live measurement of the actual DB (not just the code) found the
impact was bigger than "narrow" — a second, compounding bug (Python `isoformat()`'s `T` separator
sorting after SQLite's own space separator) meant same-day rows were dropped outright, not just a
~2h sliver; `weekly_fitness()` undercounted `tasks_scheduled` 3 vs the true 7. See F19 for the fix
(`ledger.window_start_sql()`, one shared helper, all three sites) and the DB-copy-first proof.

### F18 — Task status ignored critic verdict; a REJECTED deliverable read as complete · **P0 · PROVEN, found 2026-07-24 fixing fitness reporting**
`run_task()`/`run_synthesis()` set `status="done"` unconditionally once the critic returned ANY
verdict — pass or fail — storing the actual judgment only in the separate `critic_verdict`
column, which `weekly_fitness()` (and `is_first_run_for_mission()`, and `queue_mission_tasks()`'s
dedup) never read. Live proof, this exact ledger, 2026-07-24: task_id 20/21/22 all carry
`critic_verdict='fail'` with `status='done'`. Combined with F7 (the denominator bug, above), the
scorecard was reporting **100% completion on a week whose true pass rate was 0/10** — not a
hypothetical, the exact live number at the moment this was found. This compounds directly into
W31's gated skill promotion (starting Mon 2026-07-27, three days after this was found): a skill
whose canary/fitness evidence is "100% completion" when the underlying content was rejected by
every single review would have been approved on fabricated evidence. **Fixed at the source**
(not patched around in the fitness formula): `status = "done" if verdict == "pass" else "failed"`
in `run_task`, `run_synthesis` (`batch_runner.py`), and the legacy hand-run path (`run_task.py`,
same bug, same fix, for consistency since it writes the same ledger). The three live mislabeled
rows were backfilled (status `done`→`failed`, `critic_notes` appended noting the correction —
update, not delete, per schema.sql's append-only-in-spirit convention) after proving the fix on a
DB copy first: `weekly_fitness()` against the copy returned exactly `completion_rate: 0.0`,
`tasks_scheduled: 10`, `dropped: 1`, `pending: 6` — matching hand-calculation. Live `ledger.db`
now reports the same, real number. See docs/INCIDENTS.md for the full writeup.

### F19 — F17's clock-domain bug recurred in 3 more call sites · **P1 · PROVEN, found + fixed 2026-07-27**
Live measurement, DB-copy-first (same discipline as F18): F17's lease fix (H3) was never
generalized to the codebase's other "last N days" comparisons. `ledger.weekly_fitness()`'s window
start (`datetime.now() - timedelta(days=7)`, Python-local) and `scorecard._week_start()` (same
pattern, feeding both `canaries_green()` and `crash_recovery_counts()`) both compared a
Python-computed boundary against `created_at` (SQL `datetime('now')`, UTC, space-separated).

Two mismatches compound here, not one: (1) the 2h local/UTC offset F17 already named, and (2) a
second, previously-undocumented bug in the same family — `datetime.isoformat()` emits a `T`
separator (`'...T02:49:05'`) while SQLite's own `datetime('now')` emits a space
(`'...  02:49:05'`); since `' ' < 'T'` in ASCII, a same-calendar-date `created_at >= boundary`
string comparison silently loses even when the actual wall-clock time is later. Live-measured
against a DB copy of the real `ledger.db`: the buggy window reported `tasks_scheduled: 3` when
the true 7-day window held 7 (task_ids 16–19, all `queued`/`quota_wait`, wrongly excluded) — not
the "narrow boundary sliver" the offset alone would suggest; the separator bug silently erased an
entire same-day cohort. This directly undermined H5/F7's own fix (never let pending/abandoned
work vanish from the scorecard): the 4 excluded tasks were exactly the `pending` rows F7 was
built to keep visible.

**Fixed**: `ledger.window_start_sql(days=7)` — the boundary is now always asked FROM SQLite
(`SELECT datetime('now', '-N days')`), never computed via Python's clock, guaranteeing both the
correct clock domain and identical string format to `created_at`. `weekly_fitness()`,
`scorecard._week_start()` (and its consumers `canaries_green()`/`crash_recovery_counts()`, whose
signatures changed from `datetime` to the SQL-domain `str`) all now share this one helper instead
of each reimplementing the comparison. Proven on a DB copy of the real `ledger.db`: patched
`weekly_fitness()` returns `tasks_scheduled: 7` (was 3), `pending: 4` (previously folded into an
undercount, invisible); `canaries_green`/`crash_recovery_counts` run clean against the same copy
(0/0 in both cases — no canary/crash rows fall in this particular window, confirmed by hand-query
before and after). Live `ledger.db` untouched throughout — verification was DB-copy-only, per the
same discipline established for F18. **Lesson for F17's own generalization**: "fixed the lease"
was not "fixed the bug class" — any future `datetime.now()`-vs-`created_at` comparison anywhere
in this codebase must be treated as unsafe by default until it goes through
`ledger.window_start_sql()` or the same `datetime('now', …)`-in-SQL pattern H3/H6 already used.

### F20 — The worker was graded against a spec it was never shown · **P0 · PROVEN, found + fixed 2026-07-27**
`run_critic()` feeds the critic `row['pass_criteria']` — the mission's **full** `## Done-definition`.
`run_task()` fed the worker only `mission_objective()`, the one-line `## Objective` section. Nothing
ever handed the analyst the requirements it was judged on, so the two halves of the loop were
grading against different documents.

Proven by the first real W31 run (2026-07-27 04:00, mission 001), not by reading code: **all three
attempted tasks failed review, 0/3**, and every stated reason was a done-definition item absent from
the worker's prompt — the top "Changes since last week" diff section (tasks 24, 25, 26), NEW flags on
unseen products (24, 26), ≥2 product URLs per price range (24), and one section per tracked
competitor (26). The research itself was substantially fine; the deliverables were shaped wrong
because the required shape was never communicated. This is a self-inflicted completion-rate floor:
no amount of worker improvement could have passed, and the fitness score would have kept reporting
the resulting 0% as an analyst-quality problem.

**Why it was not simply "send the mission file":** the done-definition also names `workspace/…`
paths and `memory/ledgerbook.db`, and handing a tool-holding worker our own storage layout is the
exact cause of the 2026-07-18 rogue-write incident (docs/INCIDENTS.md). The fix therefore filters:
`deliverable_requirements()` drops every line matching `_INTERNAL_CRITERIA_RE` (workspace/memory
paths, ledgerbook/ledger.db, verdict-logging) and takes each dropped line's continuations and
sub-bullets with it, so the worker never receives half a requirement. Injected into the prompt
*before* `baseline_note`, so a first-ever run's "do not attempt a week-over-week diff" still reads
as the later, overriding exception.

**Verified**: `deliverable_requirements()` on both live missions — 001 emits exactly the four
requirements that caused today's failures, 002 emits its evidence/sourcing rules, and an explicit
leak assertion confirms zero internal path/schema strings survive. Full `run_task()` prompt assembly
proven end-to-end against a **copy** of `ledger.db` with `escalate()` stubbed (per INCIDENTS.md
2026-07-24 — stub the side-effecting call, don't just redirect paths): 14/14 assertions pass,
including that the containment instruction and compliance floor are still intact and that the
critic's prior objections still replay after the requirements. Tasks 24/25/26 re-queued (status
only — `critic_verdict`/`critic_notes` deliberately preserved so the retry replays the reviewer's
exact objections) to prove the fix on the exact tasks that exposed it.

**Related, measured the same morning — the token cap is a gate, not a limiter.** Task 26 alone spent
**8,517,508** input tokens against a **3,000,000** daily hard stop, and the day closed at
**10,786,463 (360% of cap)**. `policy.token_budget_breached()` is checked *before* a call, so it
correctly parked the following task (27, the week's synthesis) but could not stop the one already in
flight. Cap raised 3M → 12M against that measurement (operator decision 2026-07-27) — at 3M it was
not protecting quota, it was parking honest work. The in-flight overshoot remains **unfixed and
open**: a single runaway task can still exceed the daily cap several times over before anything
notices.

### F21 — A retry erased the previous attempt's accounting AND its review history · **P1 · PROVEN, found + fixed 2026-07-28**
Found by running the F20 proof, which is the only reason it surfaced: `finish_task()`'s
consumption and verdict columns defaulted to `0`/`0.0`/`NULL` and were written unconditionally,
but **every** infra path (timeout, quota park, infra_failed, short-output) omits them. So retrying
a task overwrote whatever the first attempt had recorded.

Measured live, not reasoned: task 24 held `tokens_in=1,781,395` from its 04:00 run; tonight's
retry timed out and reset it to `0`, and `policy.tokens_used_today()` — which SUMs that column —
fell `10,786,463 → 9,001,225`, exactly the erased amount. The direction is the dangerous one: every
retry made the daily budget guard protect **less**, and a timed-out run's real spend (tokens are
burned even when no usage file comes back) vanished from the record entirely.

The worse half was `critic_verdict`. An infra failure says nothing about content, yet writing
`NULL` erased the prior review — and `run_task()`'s retry-with-feedback block is gated on
`critic_verdict == 'fail'`. Task 24's timeout turned `fail` + 337 chars of specific objections
into `NULL` + `'worker timeout'`, so the retry the whole exercise existed to perform would have
run **without the reviewer's objections**, silently. A mechanism built to make the loop learn was
disabled by an unrelated timeout.

**Fixed**: consumption columns and `critic_verdict` now write through `COALESCE(?, col)`, so
omitting preserves; `finish_task(..., append_note=True)` appends to `critic_notes` instead of
replacing, and all 11 infra/quota call sites pass it. `cost_usd` was fixed in the same line
despite being inert under Ollama's `$0` reporting — it is the identical defect and would start
erasing real money the day a paid key is added (the F17→F19 lesson: fix the class, not the
instance you happened to measure). Verified on DB copies: 13 assertions across two scripts,
covering preservation on infra failure, append-not-replace on notes, and that a genuine new
verdict still overwrites cleanly with no append leakage.

**Related, same night — `WORKER_TIMEOUT_S` 900 → 1800 with `LEASE_SECONDS` 1500 → 2400.** The
first post-F20 run of mission 001 seed 1 hit the 900s ceiling with zero output. Leading hypothesis
is that F20 itself caused it: 900s was calibrated on tasks that never saw the done-definition and
therefore did far less work (~4.6 min / 35 api_calls); handing the worker the real spec multiplies
the browser work. **Causation is unconfirmed** — no usage file, session dump, or partial output
survived the kill, so a hermes hang or cloud slowness remain live alternatives, and the raise is
deliberately framed as the discriminating test rather than a fix. The lease had to move with it:
at 1500s a worker legitimately running 1800s would be declared crash-orphaned by
`reconcile_interrupted_tasks()` and burn one of its 3 `MAX_TASK_ATTEMPTS`. **Still open:** gemma's
`LOCAL_FALLBACK_TIMEOUT_S` (3600s) already exceeds even the raised lease, so a failed-over local
run can still outlive it — the next instance of this same coupling.

### F22 — The daily token cap measured the wrong day · **P1 · PROVEN, found + fixed 2026-07-28**
`policy.tokens_used_today()` filtered on `created_at >= datetime('now','start of day')`, which
measures *tokens belonging to tasks created today* — not *tokens spent today*. Those differ in
exactly the workflow this harness is built around: park on quota and resume the next day, retry a
stale row, work a backlog. Measured live: the 02:15 run burned **7,219,268** tokens on tasks 24/25
(created 07-27, finished 07-28) and `tokens_used_today()` returned **0**. The guard was blind to
the entire night's spend and would have authorised a second full 12M budget on top of it.

**Fixed**: filter on `finished_at`, the point at which spend is known and recorded. Verified live —
the same query now returns 7,219,268 with 4.78M headroom. Note this is the *third* independent
defect found in the same guard (F8 declared-but-unread, F21 erased-by-retry, F22 wrong-clock);
the in-flight overshoot remains open, so it is still a gate, not a limiter.

### F23 — The citation checker falsely accused correct work of fabrication · **P0 · PROVEN, found + fixed 2026-07-28**
The mechanical truth signal built in Phase 1 (H4, fixes F3) had two defects that together made its
literal check unreliable on real pages, and it reported the results as *the claimed value is not on
the cited page* — which reads to a critic as fabrication.

1. **Truncation.** `MAX_BYTES = 20_000` and the checker read only that prefix.
   `promptbase.com/apps` is 232,645 chars; the claimed "4.9" sits at char 85,999. The checker saw
   9% of the page, missed the value, and reported it absent.
2. **Format intolerance.** A bare `literal.lower() in body.lower()` fails on presentation
   differences carrying no meaning: the worker claims `$14`, the page renders the symbol and number
   in separate markup; `42,000` vs `42000`.

**Impact, measured not estimated.** Tasks 24 and 25 were FAILED on this evidence, with the critic
writing that "multiple high-confidence facts are cited to URLs that do not contain the claimed
values". Re-running the corrected checker against the **unchanged** deliverables: **14 of 15
citations verify**, `dead_frac` 0.07, no hard fail. Re-judged on that corrected evidence, both
deliverables **PASS**. The research was sound the whole time; the harness failed it and then
recorded the failure as the analyst's.

**Fixed**: `MAX_BYTES` 20_000 → 400_000, and `_literal_present()` retries after normalising
whitespace/commas/currency symbols on both sides. Deliberately still a substring test, so it
remains advisory evidence — `is_hard_fail()` keys only on unreachable citations, so the literal
signal informs the critic and never fails a deliverable alone. Verified: 9 assertions (format
tolerance, exact-match preserved, genuinely-absent values still report False) plus the two live
URLs that produced the original false negatives.

**Lesson — the expensive one.** A verification mechanism that is wrong in the *accusatory*
direction is far more damaging than no mechanism, because its output is indistinguishable from the
failure it claims to detect: "cited value not found on page" looks exactly like a fabricating
worker. It cost a full mission day scored at 0% and nearly had three fabricated "lessons" promoted
into permanent skill notes (see the F20 lesson-pool retraction). H4's own build note said the
mechanical check is "a genuinely independent signal" — independent is not the same as correct, and
this one was never tested against a page larger than its own read cap.

### F24 — The cap could gate but never refuse; admission control · **P1 · IMPLEMENTED + PROVEN 2026-07-28**
`token_budget_breached()` is a pure gate: it stops the *next* task once the cap is already blown,
and cannot stop one in flight. A hermes subprocess is not interruptible mid-research, so that gap
is not closeable by monitoring — on 2026-07-27 a single seed spent 8,517,508 tokens against a 3M
cap in one uninterruptible call, reaching 360% of cap.

The honest fix is to refuse **admission** to work already predictable as too large, rather than
pretend it can be halted later. `policy.estimated_tokens_for()` prefers the task's own recorded
spend from a previous attempt — the most accurate predictor available, and one that exists *only*
because F21 stopped retries from zeroing it — falling back to the largest recent completed task in
the same mission (largest, not mean: mission 001's seeds span 0.5M–8.5M, and a mean waves the
expensive ones through). An unknown estimate admits the task; the guard acts on evidence, never on
ignorance.

**Proven end-to-end, zero tokens spent:** with 7,219,268 of 12M used, task 26 (estimate 8,524,468)
was refused before any worker call — `status=quota_wait`, escalation raised, spend unchanged.
Tasks 24/25 (4.5M/2.7M) correctly still admit.

### F22b — Two correct fixes composed into a wrong one · **P1 · PROVEN, found + fixed 2026-07-28**
Found by *running* F24, minutes after shipping F21 and F22 together. F21 made `finish_task()`
preserve a prior attempt's token accounting; F22 made `tokens_used_today()` sum on `finished_at`.
Individually both are right. Composed, they were not: parking a task re-stamped `finished_at` to
now, so the 8,517,508 tokens task 26 had carried since 2026-07-27 were **re-attributed to tonight**.
The counter jumped 7,219,268 → 15,743,736 with nothing executed, past the 12M cap — the guard would
then have refused all further work on entirely fictional consumption, and every subsequent park
would have inflated it further.

Root cause is a semantic one: `finished_at` was being written on any resolution, but parking or
re-queueing is not an ending. **Fixed**: only a status in `TERMINAL_STATUSES` stamps it; others
preserve the existing value via `COALESCE`. Verified with 5 assertions (park and interrupted do not
re-date or inflate; a terminal status still stamps and still counts) plus a live re-run.

**Lesson:** the first attempt at this fix changed the SQL to `COALESCE(?, finished_at)` but left the
parameter tuple still passing `datetime.now()`, so it coalesced to a non-NULL value every time and
did nothing. It looked correct in review and failed on the first probe. Two lessons compound here:
composing individually-verified changes needs its own test, because neither change's test covers
the interaction; and a fix to a data-integrity bug must be run, never eyeballed — the code read
exactly like a working fix.

### F25 — Substring matching verified claims that were false · **P1 · PROVEN by spot-check, fixed 2026-07-28**
The first real operator-style spot-check (tasks 24, 25) failed both deliverables the automated
critic had just passed — and the gap was the literal check itself. It confirms a *string appears
somewhere on the page*, not that it appears *as the claimed fact*. `"19"` is a substring of
`"$194"`, so "PromptHero Starter $19/mo" (confidence 3) verified cleanly against a page whose only
prices are $0/$16/$24/$59/$99/$194/$296. F23's normalisation made this worse: stripping `$` and
whitespace to fix false negatives also made short numerics match more places.

Worse was found by reading rather than matching: task 24 presents
`"$14 for your first month — then $19/mo. Cancel anytime."` as a **direct quotation** at confidence
3. The cited page's entire visible text is `$100 / $14 / $3.99 / $9.99` — no `$19`, no "first
month"; it advertises a flat `$14/mo`. citecheck passed it because `$14` does appear. A fabricated
quotation attributed to a real source is invisible to any substring test.

**Fixed**: numeric literals now require a token boundary — a digit run adjacent to more digits is a
different number, not evidence. Thousands separators are still collapsed *inside* numbers so
rendering differences match, without gluing neighbours together the way whole-string normalisation
did. 10 assertions: the three false positives now fail, all seven F23 cases still pass.

**Lesson:** three defects in this one checker in a single night (F23 truncation, F23c URL
corruption, F25 substring) all shared a root: it was written to answer "does this string exist"
when the question is "does this page support this claim". Those diverge in both directions, and the
mechanical check can only ever answer the first. Confirming a literal means a claim is
*supportable*, never that it is true. The operator spot-check is not a formality on top of the
critic — it is the only layer that reads claim against source, and it caught what every automated
layer passed.

### F26 — Structured (JSON-LD) values were invisible to the literal check · **P2 · IMPLEMENTED + PROVEN 2026-07-28**
Some page values live only in a `<script type="application/ld+json">` block — e.g.
`notion.com/templates/ultimate-brain` carries `"offers":{"price":129}` and `"ratingValue":4.87` in
structured data. `_literal_present()` only ever searched raw response text.

**Fixed**: `_jsonld_text()` extracts every `application/ld+json` block, parses it, and flattens all
leaf scalars (keys dropped — they're schema field names, not claim content) into a search string
merged with the raw body before the existing check runs. Malformed JSON-LD (common on real pages)
is skipped, never raised. 11 assertions: value-only-in-JSON-LD found, absent values still absent,
malformed blocks don't crash, pages with no JSON-LD are unaffected, `@graph` arrays and multiple
script blocks both parsed, bool/null leaves excluded.

**Honest correction to how this was first framed**: it does *not* retroactively explain why task 26
passed. Fresh live re-fetch, tested empirically rather than assumed: notion.com's JSON-LD sits at
char 10,732, well inside F23's already-raised 400,000-byte window, so raw-body search alone already
found `$129` and `4.9` — this feature changed nothing for that specific page today. Its real value
is pages where relevant JSON-LD falls outside whatever the byte cap is (this module reads JSON-LD
from the SAME truncated buffer as everything else — no separate wider fetch — so it does not protect
against JSON-LD past `MAX_BYTES` on very large pages), and giving a clean, schema-typed signal that
doesn't depend on markup rendering. A claim that "seemed obviously true" turned out to need the same
verify-before-stating discipline as everything else in this file.

### F27 — Raw substring matching can confirm the right digits for the wrong reason · **P2 · FOUND, NOT FIXED 2026-07-28**
Found while verifying F26, not sought. On the same live `notion.com` page, the token-boundary-aware
search for `"129"` (F25) matches **4 separate, unrelated locations** — SVG icon path coordinates, an
analytics score field, pixel dimensions — none of them the price. `literal_found=True` was correct
on this page only because the JSON-LD price also happens to be 129; a page where the real price
were absent but an SVG path or tracking pixel coincidentally contained the same digits would report
identical, confidently-wrong evidence to the critic.

**Not fixed this pass.** No live case has yet produced an actual wrong pass/fail from this — every
other finding tonight met that bar before a fix was designed, and a rushed mitigation risks the
exact false-negative/false-positive trade-off mistake F25 already made once. Recorded so it isn't
silently lost: a plausible bounded fix (require a numeric match to sit in prose-like context, not
inside long digit/coordinate runs or a `<script>`/`<path>` payload) exists but is unbuilt and
untested against real failure evidence.

### F28 — A spot-check performed by the assistant is schema-identical to an operator's · **P1 · IMPLEMENTED + PROVEN 2026-07-28**
`human_verdict` exists specifically to be *independent* of the system it's grading — spotcheck.py's
own docstring calls it "the missing input for the fitness accuracy term." On 2026-07-28 the assistant
fetched real sources, compared claims against them, and recorded three verdicts through that exact
CLI. The tool cannot tell an operator's keystroke from an assistant acting on the operator's behalf;
both write the identical `human_verdict`/`critic_notes` columns. This is the same failure shape as
the 2026-07-18 rogue-write incident and the F5 manager==critic note — a check that's supposed to be
independent, quietly not being independent — one layer up.

**Fixed, without touching the locked formula.** `weekly_fitness()` gained `spot_checked_ai`, counting
rows whose `critic_notes` contain the literal marker `"AI-PERFORMED CHECK"` — now a documented
convention in spotcheck.py's own docstring, not an ad hoc note. `accuracy`/`fitness`/`W` are
unchanged (§3.2 locks them for 8 weeks; discounting these rows would itself be a formula change this
session has no standing to make). Instead, both `scorecard.render_md()` and `telegram_line()` now
surface the count as a visible caveat — "Accuracy (spot-checked 3, 3 AI-performed pending operator
confirmation): 33%" — so the number can never be read as more independent than it actually is.
Verified live: current W31 state correctly reports `spot_checked_ai: 3` (all three of tonight's
checks), rendering into both the markdown scorecard and the Telegram line.

### F29 — The URL regex bug, third instance: trailing backticks · **P0 · PROVEN, found + fixed 2026-07-29**
Same defect as F23c, different character. Mission 002's task 30 wrote every citation inside a markdown
code span, so `_URL_RE` swallowed the closing backtick and **all 8** extracted URLs were malformed.
Four were reported unreachable — `dead_frac=0.50`, well over the 0.34 line — and `is_hard_fail()`
rejected the deliverable **with no LLM call at all**. A regex bug was issuing verdicts.

**Fixed as a class, not as a character.** Three separate incidents from patching one delimiter at a
time is the actual lesson, so `_URL_RE` now excludes every structural markdown/HTML delimiter
(`` ` ``, `*`, `|`, `\`, `^` alongside the existing set) and a single `_clean_url()` strips trailing
sentence punctuation — replacing an inline `rstrip('.,;:)')` that had already drifted out of sync
with the regex's own exclusion list. Measured on the live artifact: **`dead_frac` 0.50 → 0.12,
`is_hard_fail` True → False**. Note what it deliberately does *not* rescue: task 30 also cited a
literal `https://www.youtube.com/watch?v=...` placeholder and a `/.../` elided Google blog path.
Both still fail, correctly — they are fabricated citations, and the fix was checked specifically to
confirm it did not launder them into passes.

### F30 — Synthesis seeds were silently routed to the browser worker · **P0 · PROVEN, found + fixed 2026-07-29**
`seed_is_synthesis()` required the seed text to *start with* "synthesis". Mission 002's seed 3 reads
**"Cross-channel synthesis: …"** — one word off — so every week it took the full `run_task()` path
with browser tools instead of the tool-free `run_synthesis()` path. It therefore went and did fresh
web research instead of combining the two channel briefs it exists to combine. Visible in task 30's
own output: it invented a channel called "AI News Recap" (the mission tracks *The Story Engine* and
an *AI-Productivity* channel, neither of which is that) and cited corticallabs.com, bbc.com and a
Google blog post about self-healing roads. **Every 002 synthesis has failed since the mission went
active — tasks 14, 22 and 30. This is why.**

**Fixed** by matching `synthesi[sz]` anywhere in the seed's leading clause (to the first colon,
capped at 80 chars, so a research seed that merely mentions synthesis in its body is not misrouted
into a path that cannot do lookups). Verified across all three missions' seeds and the canary specs:
exactly the three synthesis seeds route tool-free, everything else routes to research. It also
caught mission **003**'s `"Synthesize keyword/angle ideas…"` seed — same bug, latent, before that
mission ever goes active.

### F31 — Every task was graded against the whole mission's spec · **P0 · PROVEN, found + fixed 2026-07-29**
F20 was right that the worker must see the done-definition, and too blunt in how it delivered it: a
done-definition describes the mission's **combined weekly brief**, while each task produces one seed's
share of it. Both halves of that mismatch were live and both were unpassable:

- **Task 27** is the *tool-free* synthesis, and was graded on "a review-sentiment signal: current
  average rating + one recurring theme" **for each tracked competitor**. The critic failed it for
  exactly that — "three of five tracked competitors are missing the required review-sentiment signal"
  — on a task forbidden from performing the lookups that would produce one, working from three briefs
  covering three competitors. **No possible output passes.**
- The per-competitor seeds are each told they must deliver "one section per tracked competitor" and
  "a top 'Changes since last week' diff section", neither of which a single-competitor task can do.

**Fixed** with `task_scope_note()` — a role-aware statement of which slice of the spec this task owns,
returned to the **worker and the critic from one function**. That sharing is deliberate: F20's root
cause was the two being handed different specs, and re-deriving the note at each call site would
rebuild that exact failure mode. **Proven by re-judging task 27's unchanged deliverable bytes with a
live critic call: `fail` → `pass`.** The ledger row was corrected with an audit note recording that
the deliverable never changed and why the verdict did.

### F32 — A *successful* retry overwrote the failed attempt's token accounting · **P1 · PROVEN, found + fixed 2026-07-29**
F21 made an **omitted** token count preserve whatever a prior attempt recorded. It does nothing for
the opposite case: a retry that succeeds passes real numbers, which overwrite — so the failed
attempt's spend disappears from `tokens_used_today()` and the daily guard again protects less than it
should, the same direction of error F21 was written to stop. Latent while retries were rare; found
while building directive-2 below, which makes retries routine, so it had to be closed first.
**Fixed** by accumulating onto the row's prior total in `run_task()` (`row` is read before the attempt
starts, so a first run adds zero — verified both directions in the regression test).

### F33 — Synthesis token spend was never recorded, at all · **P1 · PROVEN, found + fixed 2026-07-29**
Found while *verifying* F32 rather than while looking for it — the re-run of task 30 completed and the
daily counter did not move by a single token. Three independent measurements agreed: `run_synthesis()`
called `finish_task()` with **no** `tokens_in`/`tokens_out` argument; `ollama_chat()` returned
`msg.get("content")` and discarded the rest of the reply; and `policy.tokens_used_today()` read
exactly **4,640,719 before and after** a real synthesis run.

Ollama reports consumption as `prompt_eval_count` / `eval_count` at the **top level** of the
`/api/chat` reply, and this function only ever looked inside `message` — so the spend was thrown away
at the source, before any accounting layer could have seen it. The consequence is the same direction
of error as F21 and F22: **the daily budget guard protected less than it claimed**, and was
structurally blind to an entire task type rather than to an edge case. It also means F32's fix, which
was real, covered only the research path — worth stating plainly, because the commit message for that
change could be read as broader than it was.

**Fixed** with an optional `usage_out` dict on `ollama_chat()` (an out-param in the same shape as the
existing `trace_path`, so the four other call sites are untouched), threaded through
`synthesis_with_failover()`, and accumulated onto the row's prior total exactly as F32 does — this
path is retried like any other. Proven twice: a live API probe captured real counts
(`{'input_tokens': 17, 'output_tokens': 60}`), then a full production re-run of task 30 moved the
daily counter **4,640,719 → 4,655,381 (+14,662)** while the task row went `863,372/5,396` →
`870,795/12,635` — a row delta of exactly 14,662, matching the counter, with the prior 863k total
preserved rather than overwritten.

### F34 — Approving a skill in a week with no canaries silently disarmed its rollback · **P1 · PROVEN, found + fixed 2026-07-29**
`_current_canary_green()` counted passes in the **current** ISO week and returned a bare `0` when
that week had no canary rows — unable to distinguish *"the canaries ran and none passed"* (a real 0)
from *"the canaries have not run yet"* (no data at all). Those are opposite situations and only one
of them is a baseline.

The consequence is a safety net that reads as armed and is not.
`newest_skill_below_baseline()` rolls back when `week_green < canary_baseline`, so a baseline of 0
**can never trigger** — no green count is below zero. Caught at the moment it happened: both skills
approved on 2026-07-29 were stamped `canary_baseline: 0`, because W30's canaries were refused by the
battery bug and W31's Sunday cron had not fired yet, leaving W29 as the last week that actually ran.
Auto-rollback was permanently disabled for both, silently, as a side effect of *when* approval was
issued.

**Fixed** by falling back to the most recent week that genuinely ran canaries. A week counts as
having run if it holds rows in a resolved state (`done`/`failed`); `stale` and quota-parked rows are
ignored, so a parked week cannot masquerade as a real observation. A returned 0 now means what it
says. The function also returns the source week, which `cmd_approve()` records as
`canary_baseline_week` in the skill file and in the promotion commit message — a baseline carried
over from an earlier week is a materially different claim from one measured this week, and the file
is the only place that distinction survives. A baseline of 0 now also logs an explicit warning that
rollback cannot fire.

Proven on a ledger copy across six states, including the two the old code could not tell apart:
current week with 5 green → `(5, W31)`; current week that ran and passed none → `(0, W31)` *not* a
stale fallback; stale-only week ignored → falls back to `(3, W29)`; most recent real week wins over
an older better one; empty history → `(0, "none")`. The two live skills were re-stamped to
`canary_baseline: 3` (W29's real count) with a note recording why, and rollback selection was
verified end-to-end: `week_green` 5/4/3 → no target, 2/0 → correctly selects one. Under the old
baseline it selected nothing at any value.

### F35 — Never-attempted work was unrunnable forever AND invisible to the score · **P1 · PROVEN, found + fixed 2026-07-29**
`expire_stale_parked()` covered `quota_wait` only. The omission of `queued` stranded
never-attempted work permanently: **no code path could reach such a row.**
`queue_mission_tasks()` matches only specs carrying the CURRENT week, `--resume` selects only
`quota_wait`/`interrupted`, `reconcile_interrupted_tasks()` touches only `running`, and this
function skipped it. Five rows were in exactly that state — tasks 4, 13, 14, 17, 19 (W29/W30 seeds,
four of them `started_at` NULL with zero tokens).

**The honesty cost was the worse half.** `weekly_fitness()` reports a `queued` row as `pending` only
while it sits inside the 7-day window; once it ages out it is counted nowhere at all, and `dropped`
read **0** despite five abandoned seeds. That is the same vanishing-work failure H5/F7 was written to
close — *"a week that drops most of its scheduled work must show that plainly"* — recurring at a
boundary that fix did not reach.

**Found by failing, not by reading.** A hand-run fire could not pick task 18 up at all; setting it to
`quota_wait` so `--resume` would see it caused *this very function* to expire it to `stale` seconds
later, because its spec was not the current week. The dead end was the symptom that exposed the class.

**Fixed** by expiring previous-week `queued` rows alongside `quota_wait`, so they land in `stale`,
which `weekly_fitness()` already counts as `dropped`. Never-attempted rows get a distinct
`NEVER ATTEMPTED` note and their own count in the log line — `started_at IS NULL` means the seed was
starved before it ever reached a worker (the F6 signature), a different operational signal from work
that ran and then parked. `interrupted` is deliberately left alone: `--resume` can still reach it, so
it is not stranded, and expiring it would break H3's crash-recovery path.

Proven on a copy of the live ledger against the five real rows: all five expire; nothing is left in
`queued`; the four never-attempted ones are labelled as such and task 4 — which carries `started_at`
and `critic_verdict='fail'`, i.e. it *was* attempted and re-queued — correctly is not; current-week
`queued` and `quota_wait` survive untouched; `interrupted` survives; and the expired rows then
register as `dropped` in `weekly_fitness()` rather than vanishing.

### F36 — The filesystem guard cannot tell the worker from the operator, and destroys uncommitted work · **P1 · PROVEN 2026-07-29 · BLAST RADIUS + RECOVERABILITY FIXED; ATTRIBUTION DELIBERATELY NOT**
Live during the task 18 fire. H9's `fs_integrity_check()` snapshots `git status` before the worker
call and compares after; anything modified is attributed to the worker and reverted with
`git checkout`. It fired correctly by its own logic — and the "tampering" it reverted was **this
session's own uncommitted F35 fix and documentation**, edited in a separate process while the worker
happened to be running:

```
[19:08:00] FILESYSTEM INTEGRITY VIOLATION during task 18 worker call:
           [' M docs/HARDENING.md', ' M orchestrator/batch_runner.py']
[19:08:00] reverted tracked changes via git checkout
```

The guard has no notion of *who* made a change — only that the working tree differs from its
pre-call snapshot. Every earlier F14 test planted tampering deliberately, so this failure mode never
appeared: it needs a legitimate concurrent editor, which only exists when a human or assistant works
on the repo while a fire is in flight. `git checkout` on a tracked file is **unrecoverable** for
uncommitted work — there is no reflog for a working-tree discard.

Both files were restored by hand from session context and re-verified (9/9 F35 assertions), so
nothing was permanently lost this time. That was luck, not design.

**Not fixed in code, deliberately.** The obvious narrowing — ignore paths the worker "shouldn't"
touch, or diff only files the worker could plausibly reach — weakens the exact guard that is the real
containment boundary for F14, and does so to accommodate operator convenience rather than a security
need. Getting that trade wrong silently re-opens the hole the 2026-07-18 rogue-write incident is
named for. Candidate directions, none yet tested against real failure evidence: stash-and-restore
around the worker call, snapshot file *hashes* attributable to a pre-call commit, or simply refuse to
start a fire when the working tree is dirty (loudest, safest, and most annoying).

**A second defect on a different axis — FIXED 2026-07-29, later the same day.**
Everything above concerns **attribution**: the guard cannot tell *who* changed a file, and the
rejected narrowings all try to infer that. Independently of attribution, the *remediation* was not
scoped to the detection:

```python
new_entries = after - before                    # detection: precise, only what changed
...
subprocess.run(["git", "-C", str(ROOT), "checkout", "--", *PROTECTED_PATHS])   # revert: everything
```

Once **any** entry is flagged, the revert discards every dirty tracked file under *all* of
`orchestrator/`, `config/`, `missions/`, `ledger/schema.sql`, `docs/`, `skills_analyst/`, `CLAUDE.md`
and `HARNESS_DESIGN.md` — including files the guard did not flag and the worker never touched. A
worker modifying one file therefore destroys unrelated uncommitted work across the entire protected
set. This was invisible on 2026-07-29 only because both dirty files happened to be flagged ones; the
blast radius was never exercised.

Worth separating from the rejected fixes above because it is a different trade, not the same one:
passing `*tracked` (the flagged entries) instead of `*PROTECTED_PATHS` ignores no path, infers
nothing about who made a change, and reverts a strict **subset** of what is reverted today — every
entry the guard flags is still reverted. It cannot weaken detection, because it does not touch
detection. Its cost is a small amount of porcelain parsing (`entry[3:]`, plus the `old -> new` form
for renames) in a security-relevant path, which is a real cost and the reason it is written down
rather than applied.

A second, even smaller option in the same spirit: copy each file's bytes into
`runs/reverted_<timestamp>/` **before** discarding, and log the location. `runs/` is gitignored and
outside the protected set, so this changes neither what is detected nor what is reverted — it only
makes an unrecoverable `git checkout` recoverable. This is the cheap version of the
"stash-and-restore" direction listed above.

#### Both were implemented, and the porcelain cost turned out to be the wrong worry
The reservation above — "a small amount of porcelain parsing in a security-relevant path" — was
aimed at the wrong risk. Scoping the revert to the flagged entries is unsafe *while detection is
porcelain-based*, for a reason neither the finding nor the rejection noticed: a file that was
**already dirty before the call and modified again during it** produces an identical ` M path` line
both times, so `after - before` is empty and the tamper is **invisible**. Today's blanket revert
caught that case by accident, purely because it reverted everything. A scoped revert on top of blind
detection would have silently stopped catching it.

So detection was strengthened first: `fs_integrity_snapshot()` now also carries a **sha256 of every
tracked file** under the protected paths (31 files / ~510KB measured, i.e. milliseconds per call),
and `fs_integrity_check()` compares content rather than status lines. That makes detection *stronger*
than before — it catches the re-modification case the old guard could not see — and only then makes
scoping safe. The revert now targets exactly the changed paths, and flagged files are copied to
`runs/reverted_<ts>/` before anything is discarded.

Net effect on every axis: detection strictly stronger, blast radius strictly smaller, discards
recoverable.

**Proven against the real repo** (the guard's mechanism is `git status`/`git checkout`, so a
sandboxed copy would prove nothing — with `escalate()` stubbed, per the 2026-07-24 lesson): an
unrelated dirty file survives a violation in another file; the discarded content is recoverable from
`runs/reverted_*`; a re-modified already-dirty file is caught (with the test asserting that porcelain
alone is blind to it, so the regression would show up as a failure rather than a silent gap); planted
untracked files are still removed; and a clean call creates no stash and no churn. 8/8, tree restored
to baseline afterwards.

**One more instance of the bug, in the fix's own test.** The first version of that test cleaned up
with `git checkout -- config orchestrator` and destroyed the F36 implementation mid-run — the exact
over-broad revert under test, reproduced in its own teardown, minutes after being diagnosed. That is
the strongest available argument for the `runs/reverted_<ts>/` copy: the pattern is easy to write by
accident even while concentrating on it, so the useful mitigation is making the discard recoverable
rather than trusting anyone to be careful. The test's cleanup is now scoped to the two files it
actually touches.

Neither has been tested against real failure evidence, which is the same bar the other candidate
directions have not cleared — they are recorded here to be decided deliberately, not adopted by
default because they sound safe.

**Standing rule until then, which costs nothing:** commit before triggering a fire, and do not edit
tracked files while one is running. `runs/`, `workspace/` and `ledger/` are gitignored, so agent
output is unaffected — this is purely about the repo's own source and docs.

### F37 — Infrastructure failure was scored as the analyst being wrong, in the one path that deletes skills · **P0 · PROVEN, found + fixed 2026-07-29**
Found by running the canaries manually rather than waiting for Sunday — i.e. by stressing the system,
which is exactly what the run was for. Two defects chained, and the chain came within **one canary**
of auto-deleting an operator-approved skill for a VRAM problem.

**Link 1 — `run_canaries()` never classified infra failures.** `run_task()` has always called
`worker_failed()` and recorded an API/model failure as `infra_failed`, excluded from scoring. The
canary path went straight from the failover check to `grade(out)`. With cloud quota exhausted by the
day's 11.4M tokens, C2 and C5 failed over to local `gemma4:12b`, which **never started**:

```
deterministic: MISS | API call failed after 3 retries: HTTP 500: llama-server startup fail
```

The grader searched that error string for a year/city, missed, and wrote `critic_verdict='fail'`.
Infrastructure flakiness entered the ledger as the analyst answering incorrectly. Note the split:
3/3 canaries on cloud models passed, 0/2 on gemma passed — the verdicts tracked the *model*, not the
task.

**Link 2 — F9 had silently voided the gate's data-quality precondition.** Auto-rollback is skipped
when data is incomplete, originally `week_pending == 0`, on the sound principle that quota-starved
canaries are not evidence about a skill. But F9's cross-provider failover means quota exhaustion no
longer **parks** a canary — it **completes** one on a degraded model. So `week_pending` was 0, the
gate opened, and it opened on data exactly as unrepresentative as a park. A fix built for one
subsystem quietly disabled a safety property of another; neither change was wrong on its own.

**The near miss:** green fell 5 → 3 against a baseline of 3. The trigger is `week_green < baseline`,
and `3 < 3` is False. One more canary landing on gemma would have made it 2, and
`promote.cmd_rollback()` would have `git rm`-ed a skill approved that same day.

**Fixed on both links.** `worker_failed()` now classifies the canary path as it does the mission path,
and the gate counts `infra_failed` as unjudged alongside parked (`week_unjudged == 0`), so partial
data skips the judgement entirely — what the gate always meant to do. The regression escalation now
distinguishes *"answered incorrectly"* from *"could not run"*, which tonight's alert conflated
("canary regression: 3/5 green (2 failed)" — nothing had regressed).

Proven on a ledger copy replaying tonight's exact states, with `cmd_rollback` stubbed so no skill
could really be deleted: under the old rules the gate opens on 3/5 and misses by one; under the fix
the same night leaves 3 green / 2 unjudged / **0 content failures** and the gate stays shut. Critically
the fix does **not** disarm the mechanism — 4 genuine content failures still select a rollback target
— and quota parks still block judgement exactly as before. 11/11. The two live rows were reclassified
to `infra_failed` with an audit note; W31 canaries now read 3 green / 2 infra, gate shut.

### F38 — The failover chain's last rung had never once worked · **P1 · PROVEN, found + fixed 2026-07-29**
F9 built a cross-provider chain terminating in local `gemma4:12b` so that quota exhaustion would
**complete** work rather than park it. Measured tonight, that rung had never completed anything:
every attempt died with `llama-server reported out-of-memory during startup`.

**The obvious diagnosis was wrong.** A 7.6GB model on a 4GB RTX 3050 reads like a weights-vs-VRAM
problem, and that reading survives right up until you test a smaller model. `llama3.1:latest` (4.9GB)
failed identically, and its error named the real allocation:

```
failed to allocate CPU buffer of size 16642998272      <- 15.5 GB
llama_init_from_model: failed to allocate buffer for kv cache
```

It is the **KV cache**, sized from the model's default context window — 262,144 tokens for
`gemma4:12b` — not the weights. No amount of free VRAM on this machine was ever going to satisfy it.
Neither the hermes CLI nor `orchestrator.ollama_chat()` passes `num_ctx`, so the full default cache
was allocated on every single call.

Confirmed by sweeping the parameter directly (both models load fine once it is capped):

| model | `num_ctx` | result |
|---|---|---|
| llama3.1 | default | **FAIL** — OOM on kv cache |
| llama3.1 | 8192 / 4096 | OK, 4.2 / 4.5 tok/s |
| gemma4:12b | default | **FAIL** — CUDA OOM |
| gemma4:12b | 8192 / 4096 | OK, 1.1 / 1.5 tok/s |

**Fixed as a model variant, not in code.** `config/gemma4-12b-ctx4k.Modelfile` bakes
`num_ctx 4096` into `gemma4:12b-ctx4k`, and `models.yaml`'s last rung points at it. A variant fixes
the hermes CLI and `ollama_chat()` simultaneously and keeps model choice in config, per CLAUDE.md's
model-agnostic constraint. 4096 over 8192 deliberately: this rung exists to load *reliably* under
memory pressure (free RAM swung between 2.8GB and 7.9GB within one hour of testing), and headroom is
worth less than starting at all. Proven with **no caller options**, the way hermes actually calls it:
`gemma4:12b` FAILS, `gemma4:12b-ctx4k` loads in 103s at 1.5 tok/s. Verified the orchestrator reads
the new chain and still classifies the variant as LOCAL, so it gets `LOCAL_FALLBACK_TIMEOUT_S`
(3600s) rather than the 1800s cloud timeout.

**What this does NOT fix, and it matters.** Asked the C1 canary question tool-free (Shopify's founding
year, truly 2006, which the cloud model got right tonight), llama3.1 answered **2004** and gemma
answered **2013**. Today those attempts became `infra_failed` — unjudged, rollback gate shut, skills
safe (F37). A rung that loads turns them into *genuine content failures*: judged, green count drops,
and auto-rollback deletes an operator-approved skill. Fixing the load therefore **increases**
skill-deletion risk unless the local rung is kept out of graded work. Caveat held honestly: that test
was tool-free, so it is direct evidence about the **synthesis** path (`ollama_chat`) and only
suggestive about the tool-driven canary/research path, where the A3 probe did once get 2006 by
looking it up.

Also worth stating plainly, since it comes up: **`glm-5.2:cloud` cannot serve as the quota fallback.**
It is already rung 1 of the chain and returned 429 for both canaries tonight — 429 is account-level
(§1.6), so a second Ollama Cloud model shares the exhausted quota, and it is the manager/critic model
besides. The only genuine fix for account-level exhaustion is the commented-out second provider.

### F39 — "429 is account-level" was a comment no code could read · **P1 · IMPLEMENTED + PROVEN 2026-07-29**
`models.yaml` has always said it: *"A second Ollama model does NOT survive quota exhaustion."* Nothing
enforced it, so the chain dutifully called every cloud rung in turn after the account was already
refusing. Measured 2026-07-29: each canary spent **~30s** calling `glm-5.2:cloud` immediately after
`kimi-k2.7-code:cloud` had 429'd — same account, guaranteed refusal, twice over.

Fixed by making the fact **data**: an optional `quota_group` on each role and chain entry. Once any
rung in a group returns a quota error, the rest of that group is skipped with a log line. Absent by
default on purpose — an undeclared model is never skipped by inference, so adding a genuinely separate
provider needs no code change, and a mis-declared group can only ever cost a wasted call, never a
skipped good rung. The commented Anthropic rung deliberately carries **no** group, since a separate
account is the entire point.

### F40 — The local rung must not grade the system that can delete its own skills · **P1 · IMPLEMENTED + PROVEN 2026-07-29**
F38 made the local rung loadable, which created a new problem rather than only solving one. The canary
green count is the only signal that **automatically deletes** an operator-approved skill, and it is
scored from whichever model happened to be reachable. The correlation was stark: the three canaries
that ran on cloud all passed; the two that failed over to gemma both failed. Asked tool-free, the
local models answer C1's question **2004** and **2013** against a true **2006**. While the rung was
broken those attempts were `infra_failed` — unjudged, gate shut, skills safe (F37). Once it loads they
become *scoreable content failures*, and the next quota-exhausted Sunday costs a skill for a VRAM
problem.

Fixed with `allow_local=False` on the canary path only. A quota-exhausted canary now **parks** instead
of degrading: `week_pending` rises, F37's gate stays shut, and the skill survives to be judged on real
data. Everything else keeps the local rung — completing a deliverable slowly beats parking it, which
was the operator's explicit F9 decision, and a mission task's grade is *reported* rather than acted on
automatically. The distinction that matters is not "graded" but **"grades an automated
self-modification decision."**

### F41 — Locality was inferred from a naming convention, not from where the model runs · **P1 · PROVEN, found + fixed 2026-07-29**
`_is_local_model()` tested `":cloud" not in model`, making locality a property of a **name**. Any
non-Ollama model is therefore "local" — `anthropic/claude-sonnet-5` included, the exact rung
`models.yaml` keeps pre-wired and CLAUDE.md calls PREFERRED. Latent while that line stayed commented,
where the only visible cost was a 3600s timeout on a fast API. It became a **correctness** bug the
instant F40 began excluding local models from canaries: a genuinely separate provider would have been
excluded too — precisely backwards, since surviving an Ollama-account 429 is its whole purpose. Now
keyed on `provider` (`LOCAL_PROVIDERS = {"ollama"}`) and then the `:cloud` suffix. Verified across
`ollama` local/cloud, `anthropic`, and `openai`.

Worth recording *how* this surfaced: not by reading the function, but because the F39/F40 test
**simulated adding the Anthropic rung** and asserted a canary could still fail over to it. The
assertion failed, and the failure was the bug. A test written only against today's config would have
passed and shipped it.

### F42 — The repo root was outside the containment surface · **P1 · PROVEN, found + fixed 2026-07-30**
Found because a 699-line `extensive_research.md` appeared at the repo root and **no guard noticed**.
Benign in this instance — the operator put it there deliberately as reference material, and its
timestamp predated the run — but the gap it exposed was not: `PROTECTED_PATHS` listed
`orchestrator`, `config`, `ledger/schema.sql`, `missions`, `CLAUDE.md`, `HARNESS_DESIGN.md`, `docs`
and `skills_analyst`, so anything written *directly to the root* matched nothing and was invisible.

Two distinct holes:

1. **Existing root files were unprotected** — `.gitignore` and `IDENTITY.md`. `.gitignore` is the
   serious one: detection is `git status`, which **honours ignore rules**, so a worker that appended
   its own output path to `.gitignore` would make itself invisible to this very guard. Both are now
   in `PROTECTED_PATHS` and hashed like everything else.
2. **New root files were undetectable** — `PROTECTED_PATHS` covers files that *exist*, and a diff
   over those paths can never see a file that appears somewhere else. Closed with a depth-0 scan of
   untracked, non-ignored root entries, folded into the same snapshot/compare pair.

**Deliberately not fixed by adding `"."` to `PROTECTED_PATHS`, even though that tests clean today.**
`memory/` and `workspace/` are policy.yaml **writable** roots living inside the repo root, so `"."`
would declare the same subtree both writable and protected. `policy.validate_paths()` compares
literal paths and would *not* catch that contradiction — the drift-check the project relies on would
pass on a genuine inconsistency, which is precisely the failure mode F13 exists to prevent. A depth-0
scan states the real intent: the root itself accepts no new files, its writable subdirectories are
untouched.

Verified against the real repo with `escalate()` stubbed: a planted root file is detected and removed;
a tampered `.gitignore` is reverted; a legitimate `workspace/` deliverable is **not** flagged (it is
gitignored, so it never enters the snapshot); the root scan holds no path separators, i.e. it does not
recurse; `validate_paths()` stays consistent; and a clean call still creates no stash. 9/9.

**A consequence worth knowing before it surprises someone:** now that `.gitignore` is protected, an
**uncommitted** ignore rule cannot survive a worker run — the guard reverts the file to HEAD, taking
the new rule with it. This was proven the annoying way, when the F42 test's own teardown did
`git checkout -- .gitignore` and discarded this session's uncommitted rule. That is the third instance
today of an over-broad revert destroying uncommitted work (see F36), and the third time the fix was
the same: scope the operation, or restore by content rather than by git. The standing rule from F36 —
commit before triggering a fire — now covers `.gitignore` edits too.

### F43 — An `infra_failed` task could never be retried, and F37 turned that from harmless into blocking · **P1 · PROVEN, found + fixed 2026-07-30**
The dedup/resume gate in `queue_mission_tasks()` and the equivalent one in `run_canaries()` both
listed `("quota_wait", "queued", "interrupted")` as the statuses a **later** invocation may pick up
again. `infra_failed` was missing from both.

This was inert for as long as an API or model failure was mis-recorded as a content `fail` — F37's
bug — because `fail` was skipped too. **F37 fixed that classification and in doing so activated this
one.** Once a model that will not load is correctly booked as infrastructure rather than as a wrong
answer, the row stops being a wrong answer that deserves to stand and becomes work that was never
judged — and the gate deciding what may be re-attempted had no entry for it. A canary that failed
because `gemma4:12b` could not load stayed failed for the rest of the ISO week, after the
infrastructure had recovered.

Found concretely rather than by inspection: cloud quota reset, the operator asked for a full canary
re-run, and all five would have been skipped — three "already done", two "already infra_failed" — for
zero tasks executed, with nothing in the log to say that anything had been suppressed.

Fixed by introducing `RESUMABLE_STATUSES` as the single definition and pointing both gates at it,
rather than editing two tuples in two files and trusting them to stay aligned.

**This does not contradict the throughput directive's deliberate exclusion of `infra_failed` from
`retry_failed_this_fire()`,** and the distinction is the part worth keeping: that exclusion governs a
retry inside the SAME fire, where conditions have not changed and a re-attempt burns another 1800s
timeout for the identical reason. This gate governs a later invocation, whose entire premise is that
conditions may have changed. Same status, opposite correct answer, because the question is different.

**One claim in the commit message is wrong, and is corrected here rather than left to be inherited.**
It says the two tuples "had already drifted apart once." They had not — `git log -S` over
`batch_runner.py` returns exactly two commits touching that tuple, and before H3 (`66985a1`) both
sites carried `("quota_wait", "queued")` identically, widened together by it. The drift that really
happened was between the dedup gates and `expire_stale_parked()`, which is F35. The
single-definition fix is still the right one, for the ordinary reason, not for a history that did not
occur.

Worth recording what deliberately stays separate, since a naive "de-duplicate the status lists"
cleanup would break two of these. Four sets are maintained by hand and they are **not** redundant:

| site | set | question it answers |
|---|---|---|
| `RESUMABLE_STATUSES` | quota_wait, queued, interrupted, infra_failed | may a later fire pick this row up? |
| `PARK_STATUSES` | quota_wait, budget_skip, chain_exhausted | did this fire stop early? |
| `expire_stale_parked()` | quota_wait, queued | is a previous week's row stranded? |
| `scorecard.canaries_green()` | quota_wait, queued, interrupted, infra_failed | did this row produce no judgement? (F45) |

`expire_stale_parked()` omits `interrupted` on purpose — `--resume` still reaches it, so it is not
stranded, and expiring it would break H3's crash recovery. It omits `infra_failed` because such a row
needs no expiry: it resolved, it was counted in that week's fitness, and the next week's scan
generates a fresh spec and therefore a fresh row. The last set coincides with `RESUMABLE_STATUSES`
only by accident of today's status list — "may be retried" and "was never judged" are different
properties, and `stale` is the case that separates them.

Verified live against today's code (F44 and F45 landed after the F43 commit, so this is a re-run, not
a quotation of it): `RESUMABLE_STATUSES` resolves at call time despite being defined *after* its
first use at line 665 — `queue_mission_tasks --dry` runs clean with no `NameError`;
infra_failed/quota_wait/queued/interrupted resume while done/failed/stale correctly do not; suites
`f39_f40`, `f37`, `f35` and `throughput` re-run **green**, and `f42` re-run **red** on one assertion,
which is finding F46 below.

**Lesson, and it generalises past this bug:** a fix that changes how an outcome is *classified* is
not finished until every path that *reads* that classification has been updated. F37 was correct and
complete as a classification change and still shipped a regression, because correctness at the write
site says nothing about the read sites. Third instance in two days of two individually correct
changes composing into a wrong one — F22b, F44, now F43.

### F44 — The daily budget counted a UTC day against local timestamps · **P1 · PROVEN, found + fixed 2026-07-30**
`tokens_used_today()` compared `finished_at` against `datetime('now','start of day')`. Its own comment
cited F17's lesson correctly — *"compute 'today' entirely in SQLite's own clock"* — and applied it to
the wrong reference column.

`ledger.window_start_sql()` is right to stay in UTC, because it compares against `created_at`, which
SQLite itself writes via `datetime('now')`: UTC, space-separated. **`finished_at` is different.**
`ledger.finish_task()` writes it with Python's `datetime.now().isoformat()` — **local** time with a
**`T`** separator. Comparing that to a UTC boundary is the F17/F19 mismatch in *both* of its
dimensions at once.

Measured live at 01:12 local / 23:12 UTC the previous day:

| | value |
|---|---|
| `finished_at` format | `2026-07-30T01:12:38` (local, `T`) |
| old boundary | `2026-07-29 00:00:00` (**UTC**, space) |
| correct boundary | `2026-07-30T00:00:00` |

The counter reported **11,390,219 tokens spent on a day that had spent nothing** — four of the
previous day's tasks, swallowed whole. Both directions do damage: an inflated counter makes admission
control (F24) refuse work that would comfortably fit, and at 02:00 local the counter drops to
today-only *mid-flight*, so a run spanning that instant sees the budget reset and can exceed the real
cap. Worst-case window width is 26 hours.

**Third recurrence of this class** — F17 (leases), F19 (the fitness window), now the budget guard —
and **F22 introduced it**: switching `created_at` → `finished_at` was the right fix to a real bug, but
carried the old boundary along. Same compose-two-correct-changes-into-a-wrong-one shape as F22b.
Fixed with `replace(datetime('now','localtime','start of day'), ' ', 'T')`, which matches the
**format** as well as the clock, and exposed as `policy.today_start()` so it is directly assertable.

**The test is written to fail at any hour, which took deciding.** Row-inclusion assertions alone are
insufficient: the UTC and local day boundaries only disagree between local midnight and UTC midnight
(two hours at UTC+2), so a purely behavioural test would have passed on the broken build for 22 hours
out of 24 and looked green. The suite therefore asserts the **boundary expression itself** — clock
domain, `T` separator, and that it is not the UTC value — alongside row attribution either side of
local midnight. 10/10.

And the regression test was itself validated against the defect rather than assumed to catch it:
planting a row one minute before local midnight, the pre-F44 boundary pulls **5,000,000 tokens** into
today and the fixed one returns 0. A regression test that passes on both the fixed and broken code is
decoration.

### F45 — The scorecard's canary line divided by the number that RAN · **P1 · PROVEN, found + fixed 2026-07-30**
Found while updating the committed scorecard view, by checking what it *would* print before publishing
it. `canaries_green()` selected `status='done'` only and returned `(passed, ran)`; both renderers then
divided by `ran or CANARY_TOTAL`. For W31 — 3 passed, 2 quota-parked, out of a fixed set of 5 — that
renders **`Canaries green: 3/3`**: a perfect-looking score whose denominator silently shrank to hide
the two canaries that never ran.

This is precisely the vanishing-denominator dishonesty **H5/F7** fixed for mission tasks
(*"a week that drops most of its scheduled work must show that plainly"*), still live in the canary
line, and the whole reason **F37** bothered to separate *"answered incorrectly"* from *"never produced
a judgement"*. It would have been published to a committed markdown view and pushed to Telegram.

**Fixed:** `canaries_green()` now returns `(passed, ran, unjudged)` counting parked/queued/interrupted/
infra rows, and the denominator is always `CANARY_TOTAL`. Renders as
`3/5 (2 never produced a judgement — parked/infra, retry pending)`, and the Telegram line as
`canaries 3/5 (2 unjudged)`. Verified a fully green week gains no spurious suffix (`5/5`) and a week
where nothing ran reads `0/5 (5 never produced a judgement)`.

Worth noting *why* this was caught: the task was "update the docs", and regenerating would have been
the obvious way to do it. Publishing a generated artifact without first checking what it will say is
how a reporting bug becomes a committed claim.

### F46 — An untracked *directory* reopened F42's hole one level deeper, aimed at the supervising agent · **P1 · PROVEN, found + fixed 2026-07-30**
F42 brought the repo root inside the containment surface with `_root_untracked()`, listing depth-0
untracked entries from `git status --porcelain`. In its **default** untracked mode git collapses an
untracked directory into a single line — `?? .claude/` — and never names the files inside it. The
depth-0 filter then guaranteed the collapse could not be undone, because any expanded path contains a
separator and was dropped by the filter itself.

Measured, not reasoned about — `_root_untracked()` before and after planting a file, and again after
rewriting one:

```
before        : ['.claude/']
after plant   : ['.claude/']          -> delta [] , UNDETECTED
after tamper  :                          delta [] , UNDETECTED
```

Two distinct misses in one function. A file planted **inside** an untracked directory produced no
delta because the collapsed entry never changed. And an in-place rewrite of an **already-untracked**
file produced no delta either, because `??` re-prints identically regardless of content — the same
blind spot F36 had already fixed for *tracked* files by hashing, left open for untracked ones.

**What makes this P1 rather than tidiness is which directory it was.** `.claude/` is Claude Code's own
configuration tree — settings, hooks, skills, agent definitions. A payload written there does not
target the worker; it targets the **supervising agent**, i.e. the reviewer that is supposed to catch
the worker. It is the highest-value prompt-injection target in the system, and it was the one place
the F14 surface could not see. `.claude/HANDOFF.md` sharpens it: a rewrite of the handoff is a message
injected directly into the next session's context, and that was the specific case detection missed.

Fixed by replacing `_root_untracked()` with `_untracked_files()`: `--untracked-files=all` so
directories expand into files, sha256 per file so rewrites are visible, and the depth-0 filter removed
as the thing that was enforcing the bug. Untracked paths that `policy.yaml` declares writable
(`workspace/`, `memory/`, the ledger DB) are excluded via `policy.is_path_writable()` — a new
`memory/scorecards/*.md` written mid-run is authorised work, not tampering — read from the policy file
rather than a second hardcoded list, per F13. Remediation needed no change: new and tampered untracked
files both flow through F36's preserve-then-remove path, which is fail-closed and recoverable
(`git checkout` cannot restore a file git never tracked, so removal after preserving is the only
honest option).

Scope is repo-wide rather than root-only, because the collapse is not a property of the root — an
untracked directory anywhere behaves identically. Cost stays bounded by gitignore (`runs/`,
`workspace/`, `inbox/*`, `__pycache__/`, `extensive_research.md`) and git does not recurse into the
nested worktree under `.claude/worktrees/`; the live non-ignored untracked set is **1 file**.

Verified by the probe that found it, now folded into the `f42` suite: a plant inside an untracked
directory is detected; a hash-only rewrite is detected; a depth-0 root plant is still detected (F42
preserved); a `memory/scorecards/` write is correctly ignored; and a pre-F46 snapshot whose
`untracked` bucket is F42's bare set degrades to `{path: None}` without crashing — a snapshot is taken
before a worker call and read after it, so a mid-run code change must not make the halves
incompatible. All **10** suites green (`f35 f36 f37 f39_f40 f42 f44 h7 h7_gate baseline throughput`),
zero `runs/quarantine_*.json`.

**Two things worth recording beyond the fix.** First, `test_f42`'s failing assertion — *"root snapshot
holds no path separators"* — was not merely stale, it **asserted the bug**: it could only pass while no
untracked directory existed, and it encoded the depth-0 filter as the desired property. A test can
pin a defect in place as firmly as it pins a behaviour.

Second — and this needed two contradicting measurements to get right — the flaw and the *exposure*
have different dates. The flawed code shipped with F42. But `.claude/` has existed since 2026-07-27
(`.claude/worktrees/`) without ever appearing in `git status`, and the reason is a **second ignore
source that is not in the repository at all**: `.git/info/exclude` contains `.claude/worktrees/`.
With only excluded content inside it, `.claude/` had nothing non-ignored to report; creating
`.claude/HANDOFF.md` at 01:28 gave it its first visible entry and with it the first directory the
collapse could hide anything behind. So: latent in the code from F42, exploitable only from today.

That second ignore source deserves its own line, because the containment surface silently inherits it.
`git status` honours `.git/info/exclude`, which is **not version-controlled, not visible in any diff,
and outside `PROTECTED_PATHS`** — so appending a path to it removes that path from the guard's view
permanently, leaving no trace in git history. Everything the fs-guard can see is defined partly by a
file the audit trail does not cover. **Fixed as F47 below**, on operator instruction, immediately
after: it undercut every "the guard would catch it" claim in this document by exactly one file.

### F47 — The guard's field of view was defined partly by files outside version control · **P1 · PROVEN, found + fixed 2026-07-30**
Detection is `git status`/`git ls-files`, and those honour **three** ignore sources. Only one is in
the repository:

| source | in repo? | in the audit trail? | protected? |
|---|---|---|---|
| `.gitignore` (per directory) | yes | yes | yes — F42 added it to `PROTECTED_PATHS` for exactly this reason |
| `.git/info/exclude` | yes, but never committed | **no** | **no** |
| global — `core.excludesFile`, else git's XDG default `~/.config/git/ignore` | **no** | **no** | **no** |

F42 saw half of this: its `PROTECTED_PATHS` comment says rewriting `.gitignore` is how a worker would
make its own output invisible to the guard. The same argument applies to the other two sources and
they were left out, which is worse rather than equivalent — `.gitignore` at least produces a tracked
diff when edited.

Proven, not argued: plant a file under `orchestrator/`, confirm the guard sees it, append one line to
`.git/info/exclude`, and the guard goes blind. No commit, no diff, no trace. The global source is not
hypothetical either — `core.excludesFile` is unset here, yet git still honours the XDG default, and
that file exists and contains `**/.claude/settings.local.json`. A Claude Code settings file — hooks
and permissions, read by the *supervising* agent — was already masked from the guard by a rule living
outside the project entirely.

**Fixed on three axes, because one alone is escapable.**

1. **Enumeration no longer honours untrusted sources.** `_untracked_files()` moved from
   `git status --porcelain -uall` (which implies `--exclude-standard`, i.e. all three sources) to
   `git ls-files --others --exclude-per-directory=.gitignore`, which honours only the
   version-controlled files. A local exclusion can no longer hide an untracked file from the scan at
   all. Bonus: `ls-files --others` names files individually, so there is no directory collapse of the
   F46 kind left to undo.
2. **The sources themselves are watched.** The snapshot carries the exact **bytes** of each
   unversioned source (`_local_exclude_state()`), so an edit across a worker call is detected and the
   pre-call bytes are written back. Bytes rather than hashes deliberately: a hash proves something
   changed without letting you undo it, and `git checkout` is useless on a file git never tracked, so
   the snapshot is the only source of truth. A source that did not exist is recorded as `None`, which
   makes *creating* one a change too — the cheapest blinding move is to write a
   `.git/info/exclude` that was not there.
3. **Masking of a protected path is named directly.** `_masked_under_protected()` asks git the same
   question twice — once with `--exclude-standard`, once honouring only `.gitignore` — and diffs. The
   difference is precisely the set of paths under `PROTECTED_PATHS` that an unversioned rule is
   hiding. This catches a mask planted *before* the snapshot, which the byte comparison cannot see;
   together they cover the pre-planted and the freshly-written case. A non-empty result logs a
   WARNING on every snapshot, not only when it changes.

**One deliberate limit.** Remediation rewrites only sources **inside** the repo. The global ignore is
the operator's personal configuration, and silently editing a file outside the project is more than a
containment guard should do unasked; out-of-repo tampering is detected, escalated, and left alone.
Verified that remediation leaves it byte-identical.

**Residual, stated plainly.** A nested repository still reports as one collapsed entry, because git
will not enumerate another repo's files — `.claude/worktrees/jolly-gauss-8e52cb/` is recorded as
`<nested-repo>`, so its appearance and disappearance are detected but changes inside it are not. It is
a separate checkout that the running harness does not execute from, which is why this is recorded
rather than fixed.

Verified by a new `f47` suite — 15 assertions, all green: baseline unmasked and the scan stable across
calls (no spurious deltas); the demonstrated attack detected on all three axes; the exclude restored
byte-identical and the plant removed; a created source removed; the global source classified as
outside and left untouched; masking `.claude/` failing to hide `HANDOFF.md`; and a clean call still a
no-op. Full set **11/11 green** (`f35 f36 f37 f39_f40 f42 f44 f47 h7 h7_gate baseline throughput`),
compile clean, zero `runs/quarantine_*.json`, `.git/info/exclude` byte-identical afterwards.

### F48 — Canary token spend was measured, then dropped on the floor · **P1 · PROVEN, found + fixed 2026-07-30**
`run_canaries()` called `ledger.finish_task()` with no `tokens_in`/`tokens_out`. The data was right
there: `worker_with_failover()` returns `usage`, and the line immediately above consumed it via
`worker_failed(out, usage)`. It was simply never passed on.

Not a rounding error — **every canary that has ever resolved records zero spend**:

| mission | resolved | tokens_in | zero-token rows |
|---|---|---|---|
| `000-onboarding` | 1 | 7,021 | 0 |
| `001-shopify-competitor-intel` | 7 | 23,492,390 | 2 |
| `002-content-niche-research` | 6 | 12,263,874 | 0 |
| **`canaries`** | **6** | **0** | **6 of 6** |

`policy.tokens_used_today()` sums that column, so the `tokens_per_day_hard_stop` guard under-counted
by exactly the canary spend — *"the daily guard protects less than it should"*, the same sentence F21,
F22b and F32 were each written to stop being true. The spend was never even lost: it is written to
`runs/canary_<name>.usage.json` on every call. It just never reached the ledger, and **if it is not in
the ledger it did not happen** (CLAUDE.md).

**This is F33 in a path F33 never checked.** F33 fixed synthesis token accounting — `run_synthesis()`
passed no tokens and `ollama_chat()` discarded Ollama's top-level counts — and the canary path has the
identical omission. Third path with the same defect (mission retry F32, synthesis F33, canaries F48),
which is why the fix consolidates rather than adds a fourth copy: the accumulate arithmetic is now
`accumulated_tokens()`, one definition called by both `run_task()` and `run_canaries()`. Two call
sites computing the same thing from two copies of three lines is the exact failure shape of F43's
duplicated status tuples.

**All three post-call paths now record, not just the green one.** A quota-parked canary keeps whatever
the failed rungs burned, and an `infra_failed` canary keeps its spend — which matters because
`infra_failed` is in `TERMINAL_STATUSES`, so those tokens do count toward the day. Parking still does
not stamp `finished_at` (F22b holds), so a parked row's spend is carried on the row and counted when
it finally resolves. Resumed canaries accumulate, because `RESUMABLE_STATUSES` makes resumption a live
case, not a theoretical one — the dedup query was widened to fetch the prior totals for that reason.

**One acknowledged gap:** a canary killed by `subprocess.TimeoutExpired` still records nothing, because
that path has no `usage` to record — the same limitation `finish_task()`'s own F21 comment describes
("a timed-out run burns tokens without returning a usage file"). Detected and stated rather than
papered over.

Found by inspecting the ledger after a canary re-run, not by reading the code: the run log showed
`3/5 green` while the rows behind it showed `tok=0/0` for canaries that had done real browser work.

Verified by a new `f48` suite — 19 assertions — **and validated against the defect rather than assumed
to catch it**: reverting only the `tokens_in=`/`tokens_out=` arguments on the done path (restored by
file copy, never `git checkout` — F36) turns exactly three assertions red — the row carrying spend,
resume accumulating, and `tokens_used_today()` moving. The suite also replays the pre-fix call shape
directly on a row to show it yields 0/0, so a green run cannot be green for some unrelated reason, and
asserts the helper reproduces `run_task()`'s old inline expression term-for-term so the mission path is
untouched by the refactor. Full set **12/12 green**, compile clean, real ledger never opened (every
assertion runs against a `shutil.copy2()` copy with escalate/rollback/integrity guards stubbed).

### F49 — Synthesis silently receives a truncated brief and reports the missing part as a data gap · **P1 · PROVEN, found + fixed 2026-07-30**
`run_synthesis()` builds its input with

```python
f"### {p.name}\n{p.read_text(encoding='utf-8')[:6000]}" for p in briefs[:6]
```

Two silent caps: **6,000 characters per brief**, and **6 briefs**. Neither leaves a marker, so the
synthesis model cannot tell a complete document from a bisected one, and the critic — which never
sees the prompt — cannot tell either.

**Found by spot-checking task 30, which is the spot-check doing exactly the job F3 says only a human
pass can do.** That deliverable declares its third Channel-2 topic a data gap: *"The supplied
AI-productivity brief for 2026-W31 contains only two topic opportunities with dated, sourced
evidence."* Task 29 had in fact delivered three, the third carrying confidence-3 quotes that verify
verbatim against metaintro.com. The obvious reading is an analyst error. It is not:

| | measured |
|---|---|
| task 29 brief length | 12,464 chars |
| supplied to synthesis | 6,000 |
| dropped | 6,464 |
| "## Topic Opportunity 3" begins at char | **6,060** |
| paradox 80% quote at | 6,304 |
| metaintro URL at | 6,515 |
| competitor video 247K view count at | 7,731 |

The cut misses the third topic's heading by **sixty characters**. The analyst's statement is
therefore *literally true of the material it received*, and reporting a data gap rather than
inventing a topic is precisely what its own prompt instructs. **Grading that as a content failure
would repeat F37** — infrastructure scored as the analyst being wrong — so task 30 was spot-checked
**pass**, and the defect recorded here instead.

**Task 27 is hit harder and nobody noticed.** Its three W31 input briefs are 10,994 / 11,388 / 8,279
chars, so *all three* were cut, and roughly 18KB of researched material never reached the synthesis
that exists to consolidate it. It still produced a five-competitor diff table, which is why the loss
is invisible: the output looks complete.

This is the **F20/F31 family** — a worker judged on material it was never shown — with one property
that makes it worse than either. F20 and F31 withheld *requirements*, which produced visibly wrong
deliverables. F49 withholds *evidence*, and the deliverable it produces is well-formed, internally
consistent, and wrong only in what it omits. The failure mode is an operator being told to go source
information the harness already has.

Adjacent latent instance, same shape: `_recent_fact_lines(days=14, cap=120)` caps the fact block at
120 rows across all missions with no marker. 108 facts exist today, so it has not bitten yet; it will
when it does.

**Fixed with the marker** (operator's call, 2026-07-30), the option that is correct regardless of
which else is later chosen. `build_brief_block()` replaces the inline expression and states every
omission: a truncated brief carries `[TRUNCATED BY THE HARNESS: n of m characters ... researched and
exists ... NOT a data gap]`, and briefs dropped by the count cap get their own named section. The
caps are now `SYNTHESIS_BRIEF_CHARS` / `SYNTHESIS_MAX_BRIEFS` rather than magic numbers, so raising
them stays a one-line, independent decision.

**The marker alone would not have fixed this, and that is the part worth remembering.** The original
failure was not the model reasoning badly — it reasoned *correctly* from "absent" when the truth was
"withheld", because nothing in its input distinguished the two. So the prompt now carries the rule
that gives the marker meaning: *truncation is not a data gap; report it as supplied-material
truncated, naming the brief and the omitted amount, and do not tell the operator to research it —
they already have it.* A marker the model cannot interpret would still have been read as absence.

**What the marker does and does not buy.** It does not recover the omitted text: a synthesis built
from a cut brief is still built from a cut brief. What changes is that the loss becomes *reportable*
— the deliverable can say which part of its task it could not cover and why, instead of quietly
converting a harness limit into an instruction for the operator to redo work the harness already
holds. That is the honest floor, and it holds at any cap.

**The cap was then raised 6,000 → 24,000 (operator's call), after measuring rather than guessing.**
The measurement is the part that matters, because it reframes the bug:

| | measured 2026-07-30 |
|---|---|
| briefs on disk | 13 |
| **exceeding the old 6,000 cap** | **11 of 13** |
| largest brief | 15,968 chars (9,968 lost) |
| median brief | 9,328 chars |

Truncation was **the normal case, not an edge case**. Essentially every synthesis this project has
ever run was built on cut input; task 30 is simply the one where the cut landed somewhere that made
the damage legible. 24,000 clears the largest observed brief with ~50% headroom.

Measured cost against the 20,000,000-token daily cap: a shopify synthesis prompt grows ~25,200 →
~39,000 chars (**~+3,400 tokens**), a content one ~11,500 → ~17,700 (~+1,500). That increase *is* the
previously-withheld research finally arriving. Worst case at these caps — 6 briefs × 24,000 plus the
fact block — is ~41k tokens, inside every cloud rung.

**A constraint that looks binding and is not**, worth writing down so it is not re-litigated: the
last fallback rung `gemma4:12b-ctx4k` has a 4,096-token context, so it cannot run a synthesis at
24,000 — but it could not at 6,000 either, where the prompt already needed 8,226 (content) to 11,662
(shopify) tokens. Raising the cap does not break that rung. It has never worked for this path. That
is recorded separately as F50.

The fact block deserves a mention because the measurement was surprising: at 108 rows it is **18,432
chars (~4,608 tokens)** — larger than the entire brief block used to be. `_recent_fact_lines`'s
`cap=120` is the real limiter there, and it is the next one to bite.

Verified by a new `f49` suite — 28 assertions — anchored on **the actual file that caused the bug**,
and deliberately covering both halves of the fix separately, because they are independent:

- *Marker, at the historical cap:* `build_brief_block(cap=6000)` on task 29's brief emits
  `TRUNCATED BY THE HARNESS: 6464 of 12464`, with the exact figures.
- *Validated against the defect:* the same section reproduces the **pre-fix expression** on that same
  file and confirms it emitted no marker and dropped Topic 3 without a trace.
- *Cap, at the shipped value:* the same brief is **no longer truncated at all**, and the suite asserts
  that `Topic Opportunity 3` and the `metaintro.com` evidence task 30 was denied now actually reach
  the model — the specific failure, closed at the source rather than merely annotated.
- *Regression guard:* the shipped cap must exceed the largest brief on disk and must not drop below
  16,000, so a future edit cannot quietly reintroduce routine truncation.
- Boundaries pinned both ways (exactly `cap` is not truncated; `cap+1` reports one omitted character),
  and the second silent cap (`max_briefs`) names the dropped files.

End-to-end on the real `workspace/content` W31 directory: the brief block grows 11,472 → **17,709**
chars with **zero** markers emitted. 13/13 suites green, compile clean.

One test bug found and fixed while writing it, recorded because it is the same class of error the
suite exists to catch: the fixture filled briefs with `"x"` and asserted the body was exactly 6,000
characters, which failed at 6,001 — the marker prose contains "e**x**ists". The assertion was wrong,
not the code.

### F50 — The last fallback rung cannot fit a synthesis prompt, and never could · **P2 · PROVEN, found + fixed 2026-07-30**
`synthesis_with_failover()` calls `_failover_candidates(worker_cfg)` with the default
`allow_local=True`, so `gemma4:12b-ctx4k` is in the synthesis chain. Its context is **4,096 tokens**.
Measured at the **old** 6,000 cap, a synthesis prompt already needed **8,226 tokens** (content) to
**11,662** (shopify) — two to three times what that rung can accept. At the new cap it is ~9,800 to
~15,100.

So the rung has never been able to serve a synthesis, at any cap this project has used. F38 fixed the
adjacent problem — the model would not *load* because Ollama sized its KV cache from a 262,144-token
default — and made it loadable at `num_ctx 4096`. Loadable is not usable: the fix that made the rung
start is not the fix that makes it fit.

Consequence when cloud quota is exhausted and synthesis falls through to it: a prompt that cannot fit,
on a rung measured at **1.5 tok/s**, against `LOCAL_FALLBACK_TIMEOUT_S`. The cost is a long stall
ending in a failure that says nothing useful, not a degraded-but-real answer.

**Fixed by testing the cause, not a proxy for it.** `allow_local=False` — F40's tool, one line, and
the obvious candidate — was **rejected**: locality is not why this rung fails. It would wrongly skip a
future local model with a large context, and wrongly *keep* a small-context cloud one. The rung fails
because the prompt does not fit, so that is what `_fits_context(cfg, prompt)` now tests, in both
`synthesis_with_failover()` and `worker_with_failover()`.

**Opt-in, following F39's rule exactly.** A rung declares `context_tokens` in `config/models.yaml` or
it is never skipped by inference — so a missing declaration can only cost a wasted call, never skip a
rung that would have worked. This also keeps the model fact in config: swapping a model stays a config
edit, never a code edit (CLAUDE.md's model-agnostic constraint). `gemma4:12b-ctx4k` now declares the
4,096 that F38 baked into its Modelfile; both cloud rungs deliberately declare nothing.

The budget reserves `RESPONSE_RESERVE_TOKENS = 1500`, because a rung that can swallow the prompt with
no room to answer is no more useful than one that cannot swallow it. The estimate is `len/4`, which is
crude on purpose — it decides only fits/doesn't-fit with a wide margin, and never touches accounting,
which comes from the provider's own counts (F33).

The failure is now legible where it was silent. Instead of a 1800s stall, the log says:

```
skipping ollama/gemma4:12b-ctx4k (1/1) — prompt needs ~11285 tok
(incl. 1500 reserved for the reply) but gemma4:12b-ctx4k declares only 4096
```

Verified by a new `f50` suite — 21 assertions, no model ever called (`ollama_chat`/`hermes_worker`
stubbed) — using the **measured** sizes of real W31 prompts (39,141 chars content / 60,420 shopify).
It pins the wrong fix out as well as the right one in: a large-context *local* rung is accepted, and
the 4k rung still *runs* a small prompt, so nothing is banned wholesale. Reserve boundaries pinned
either side; config asserted to declare 4096 on the gemma rung and `None` on both cloud rungs.
**Validated against the defect:** removing just the guard from both loops makes the suite fail with
`AssertionError: should not have been called: gemma4:12b-ctx4k` — the stall itself, reproduced.
14/14 suites green, `policy.validate_paths()` still consistent.

### F51 — The fact ledger truncated silently, and dropped the alphabetical tail · **P1 · PROVEN, found + fixed 2026-07-30**
`_recent_fact_lines(days=14, cap=120)` ended `return "\n".join(...) for r in rows[:cap]`. Third
member of F49's family — **F49 withheld the briefs, F50 withheld the model's context, F51 withheld
the facts** — and the one that was closest to firing. Three defects in one line:

**1. Silent.** `rows[:cap]` dropped the overflow with no marker, so a synthesis could not tell a
complete fact ledger from a clipped one. That is precisely what made F49 damaging rather than merely
lossy, and the fix is the same marker in the same words, for the same reason: an omission the model
cannot see becomes an absence it reports.

**2. Twelve rows from biting.** Measured 2026-07-30:

| | measured |
|---|---|
| facts inside the 14-day window | **108** |
| cap | **120** |
| headroom | **12 rows** |
| facts produced by week W30 alone | **70** |
| mean line length | 169 chars (median 147, max 694) |

One ordinary week would have crossed it. Raised 120 → 300, which covers a fortnight at more than
double the busiest observed rate; worst case ~50,700 chars (~12,675 tok).

**3. Dropping the wrong rows, deterministically.** The ordering was `ORDER BY entity, id`, so the
overflow was always the alphabetical **tail** — the same entities every time, chosen by name rather
than by age or relevance. On today's data that tail is `ai-productivity`, `dark-academia`,
`modern-stoicism`: the entire onboarding niche-selection set, which is exactly the material a
cross-channel synthesis would want. A cap that silently discards a fixed slice of the alphabet is
worse than a random one, because the loss is systematic and invisible at once.

Fixed by **selecting** the newest `cap` rows and then **presenting** them grouped by entity — a
subquery ordered `id DESC LIMIT ?` inside an outer `ORDER BY entity, id`. Truncation now drops the
oldest, which is defensible; reading order stays grouped, which is why `entity` was there in the
first place. Neither property had to be traded for the other.

`db` is now injectable and resolved at call time, so the suite runs against a synthetic ledgerbook
and never opens the live one — **F12's lesson**, which cost a junk row in the real ledger the first
time it was ignored.

Behaviour today is byte-identical: 108 < 300, so nothing truncates and the final ordering is
unchanged. This is protection for the next fortnight, not a change to the current prompt.

Verified by a new `f51` suite — 20 assertions, entirely on synthetic ledgerbooks in a temp dir.
**Validated against the defect with a case where the two orderings genuinely disagree**, which the
obvious fixture would have hidden: five OLD facts named `aaa*` and five NEW ones named `zzz*`, cap 5.
The fix keeps `zzz0..zzz4` (the newest); the pre-fix expression keeps `aaa0..aaa4` (the oldest) — the
bug, reproduced exactly. Also pinned: marker counts (`100 of 400 facts`), that the marker says the
recent rows were kept, that presentation remains entity-ordered, empty-ledger degradation, and that
the shipped cap exceeds the live window and cannot regress below the old 120.
14/14 → **15/15 suites green**, compile clean.

### F52 — Committing the handoff silently removed it from the containment surface · **P1 · PROVEN, found + fixed 2026-07-30**
Self-inflicted, and found within a minute of causing it. `.claude/HANDOFF.md` had been covered by
`_untracked_files()`'s hashes (F46/F47) **precisely because it was untracked**. Committing it removed
it from that set without adding it to `_tracked_hashes()`, which walks only `PROTECTED_PATHS` — and
`.claude` was not in the list.

Measured, not argued. A simulated tamper immediately after the commit:

| detection channel | delta |
|---|---|
| tracked hashes | `[]` |
| new untracked | `[]` |
| tampered untracked | `[]` |
| porcelain | `[]` |

Silent on all four. And the tree in question is Claude Code's own config directory — agents, skills,
settings, hooks — i.e. the one place where a written file steers the **supervising** agent, which is
exactly the reasoning F46 used when it *refused to gitignore* `.claude`. Tracking the file achieved
the same blindness by the opposite route.

**The general shape is worth more than the instance: changing a file's VCS status silently changes
which guard covers it.** `git add` is not usually thought of as a security-relevant operation. Here
it moved a file across the boundary between two mechanisms that each assumed the other had it. The
F42→F46→F47→F52 sequence is one hole re-appearing at each new layer — root files, then untracked
directories, then unversioned exclude sources, now VCS-status transitions.

Fixed by adding `.claude` to `PROTECTED_PATHS`. Re-measured after: the tamper is caught on **two**
channels (tracked hash + porcelain), `policy.validate_paths()` stays consistent, protected tracked
files rise 51 → 53, and `.claude/worktrees/` remains collapsed as `<nested-repo>` rather than being
enumerated.

**Two knock-on effects, both handled rather than suppressed:**

1. **`test_f47`'s baseline legitimately moved.** It asserted *no* protected path is masked at rest;
   with `.claude` protected, `.git/info/exclude`'s `.claude/worktrees/` rule now masks one — a real,
   operator-DECIDED collapse. The test now captures `AT_REST` and asserts the **only** at-rest mask
   is that worktree, with the attack still required to add exactly one entry on top and remediation
   still required to return to `AT_REST`. Detection power is unchanged; a mask on any other protected
   path still fails it.
2. **`test_f47` section 7 was testing a channel that no longer applies.** It asserted HANDOFF.md
   appears in `_untracked_files()`; a tracked file never will. The property is unchanged and now
   holds through a *stronger* channel — `git ls-files` does not consult ignore rules at all, so a
   tracked file cannot be masked by any exclude source. That is the real payoff of F52 over relying
   on untracked enumeration, and it is now asserted directly.

**Residual, stated:** F47's mask detector now logs a WARNING on every snapshot naming
`.claude/worktrees/jolly-gauss-8e52cb/`. That is F47 working, not failing — but a warning that fires
always is a warning that stops being read. `git worktree remove .claude/worktrees/jolly-gauss-8e52cb`
clears it at the source; it is the operator's worktree, so it is offered, not done.

**Residual closed — 2026-07-30.** `git worktree remove .claude/worktrees/jolly-gauss-8e52cb` (clean,
detached at `8f9338b`, no `--force` needed) followed by `git worktree prune`. Re-measured:
`_masked_under_protected()` → `[]`, `git worktree list` → one entry, `.claude/HANDOFF.md` still in
`_tracked_hashes()`. `.git/info/exclude`'s stale `.claude/worktrees/` line was left in place
deliberately — deleting it buys nothing once the masked set is already empty, and an unversioned
exclude source is exactly the surface F47 exists to watch, so it isn't touched without a measured
reason. `test_f52.py` §3 and §5 hardcoded the worktree's *presence*, so removing it would have taken
the suite from 16/16 to 13/16 without any actual regression; both sections now branch on live
`git`/snapshot state (present → collapsed to one `<nested-repo>` marker, absent → zero entries /
nothing masked), so the invariant is enforced in either world rather than assuming this one.
**16/16 suites green** after the rewrite.

Verified by the `f52` suite — 10 assertions — including the defect itself: the pre-F52 surface
(`PROTECTED_PATHS` minus `.claude`) is shown not to cover the file at all, while the shipped surface
catches the tamper on two channels. Restoration is by content copy, never `git checkout` (F36).
**16/16 suites green**, `.git/info/exclude` byte-identical afterwards.

### Directive-1 — One expensive seed cancelled every cheap seed behind it · **P1 · IMPLEMENTED + PROVEN 2026-07-29**
The batch loop treated any `quota_wait` as "stop the whole fire", but a task parks for three
materially different reasons. `budget_skip` means admission control (F24) refused **this** task's
predicted cost — a cheaper seed behind it may fit, and the check costs zero model calls. `quota_wait`
means the daily cap is blown — every remaining seed will park too, but parking them is free and
leaves an honest annotated row instead of a silent `queued`. Only `chain_exhausted` (every fallback
model quota-limited) has a retry that costs anything real.

Collapsing all three into a full stop was **F6's head-of-line blocking rebuilt one layer up**: on
2026-07-28 task 26 alone estimated ~8.5M tokens while tasks 28 and 29 needed 2.4M and 1.4M — the
expensive seed parked first and the two affordable ones behind it were never attempted. Now only a
repeated `chain_exhausted` stops a pass. Proven on a ledger copy with `run_task()` stubbed: a
`budget_skip` on seed 1 still attempts seeds 2–4; two consecutive `chain_exhausted` stops; a single
success resets the streak.

### Directive-2 — The Evaluate → next-attempt edge existed in code but nothing ever walked it · **P1 · IMPLEMENTED + PROVEN 2026-07-29**
`run_task()` has built `prior_feedback` from the critic's objections all along, and no code path ever
reached it. A critic-rejected task was left failed, and next week's fire does not pick it up either —
`queue_mission_tasks()` dedups on a spec containing the ISO week, so a new week creates a **new** row
and the rejected one is never revisited. `retry_failed_this_fire()` closes the loop inside the same
fire, capped at `MAX_RETRIES_PER_FIRE=3`, with synthesis retried **last** so it rebuilds from briefs
the research retries have just corrected. `infra_failed` is deliberately excluded: re-running a
worker timeout costs another 1800s to most likely time out again, which is an operator's budget call,
not an automatic one. Proven on a ledger copy: selects only content failures (not `done`, not
`infra_failed`), orders synthesis last, honours the cap, and stops on an exhausted chain.

### Directive-5 — Accuracy depended on the operator remembering to go looking · **P1 · IMPLEMENTED + PROVEN 2026-07-29**
Accuracy is 30% of fitness and the only term the system cannot produce for itself. The only way to
learn there was anything to check was to remember to run `spotcheck.py list`. `notify_pending()` now
pushes the queue to Telegram, and a batch fire that produced deliverables sends it without being
asked (fail-soft — an undeliverable notification never fails a batch). It surfaces the **F28** rows
separately, which the pull-based view could not: an AI-performed check already has `human_verdict`
set, so it drops out of `list` entirely despite being the row that most needs a human.

One correction found by reading the first real message rather than the code: it opened with W29
canaries, burying this week's work under "…and 3 more". `weekly_fitness()` computes accuracy over
`mission_id != 'canaries'` inside a 7-day window, so a canary spot-check contributes **nothing** to
it and a three-week-old task contributes nothing to the current week — plain `ORDER BY task_id` had
put exactly those two categories first. Now excludes canaries and sorts newest-first, so a 3–5 minute
weekly budget lands on rows that move the number.

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

### H2 — Provenance-based integrity, not count-based (fixes F1 properly) · **IMPLEMENTED + PROVEN 2026-07-24**
`tasks.run_id`/`facts.run_id` columns added (both live DBs migrated + schema.sql updated);
`ledger.RUN_ID` generated once per process; every orchestrator insert (`ledger.queue_task`,
`extract_facts`, `onboarding_autonomy`'s fact insert) stamps it. `db_integrity_check()` rewritten
around `WHERE id > snapshot_max_id` (exact new-row identification, not count-diff guessing) and
quarantines only rows with `run_id IS NULL` on the two provenance-tracked tables — a legitimate
concurrent write (any non-null run_id) is spared and logged as informational, never deleted.

Proven with the same rigor as the original F1 finding, both directions, on DB copies: a row
stamped with a foreign-but-valid run_id (simulating another live orchestrator process) survived
the check; a row with `run_id IS NULL` (simulating an actual rogue write — exactly what a worker
using its terminal/python tools directly against the DB would produce, since it is never told
this schema exists) was correctly quarantined. This time the probe also explicitly sandboxed
`ESCALATIONS` (not just `ROOT`/`RUNS`), applying the lesson from the 2026-07-19 false-alarm
incident (docs/INCIDENTS.md) — verified zero leakage into the real repo.
Give every orchestrator run a `run_id` (UUID). Stamp `tasks.run_id` and `facts.run_id` on every
insert the orchestrator makes. The guard then quarantines **only rows whose `run_id` is NULL or
unknown** — i.e. genuinely un-attributable writes (the actual rogue-worker signature) — and
ignores rows correctly attributed to any live run. This preserves true detection while making
false positives structurally impossible, instead of relying on "no one else is running."

### H3 — Crash recovery via leases (fixes F2) · **IMPLEMENTED + PROVEN 2026-07-24**
`tasks.lease_expires_at` + `attempt_count` added (schema + both live DBs migrated).
`ledger.start_task()` sets a 1500s (25 min) lease — worker timeout (900s) + critic call + fact
extraction + margin — computed entirely via SQLite's own `datetime('now', '+N seconds')` (see
F17: a Python-computed lease compared against SQLite's clock was the first, broken version of
this). `reconcile_interrupted_tasks()` runs at the start of every `_run()`, before any queueing:
a `running` row past its lease → `interrupted` (attempt_count+1, dedup-resumable) or, past
`MAX_TASK_ATTEMPTS=3`, an honest `failed` with a note — never a silent infinite crash-loop.
`queue_mission_tasks()`, `run_canaries()`, and `--resume` all updated to treat `interrupted` as
resumable. `scorecard.py` surfaces recovered/gave-up counts explicitly (file + Telegram line) —
never silent, per spec.

Proven on DB copies, 5 cases: a fresh crash (attempt 0, expired lease) → recovered to
`interrupted`; a task with a **still-valid** lease (genuinely running) → left untouched, not
falsely reaped; a task already at 2 prior interruptions → 3rd crash hits the cap → `failed` with
an honest note, not another silent retry; `interrupted` confirmed in the dedup-resumable set;
`scorecard.crash_recovery_counts()` confirmed non-zero after a real recovery. Separately verified
the real `ledger.start_task()` production path sets a lease exactly ~1500s ahead of SQLite's own
clock. Real repo untouched throughout (0 test rows leaked).

### H4 — Make the critic check truth, not shape (fixes F3, F4) · **IMPLEMENTED + PROVEN 2026-07-24**
`orchestrator/citecheck.py`: SSRF-guarded (private/loopback/link-local IPs resolved and blocked
before any connect — the URL comes from worker output, effectively untrusted input),
bounded-concurrency (`ThreadPoolExecutor`, 4 workers) fetch+verify of every cited URL in a
deliverable, capped at 15 citations. For each: reachability + whether the claim's key literal
(a price/number extracted from the surrounding line, URL text excluded) appears in the first
20KB of the fetched page. Only this structured evidence table reaches the critic prompt — never
raw fetched page content (F10 already flags fetched-content-into-a-future-prompt as an injection
path; this keeps that surface closed). `run_critic()` in `batch_runner.py` now: mechanically
hard-fails (no LLM call) when ≥3 citations were checked and >34% are dead; otherwise feeds the
evidence table to the LLM critic as *additional* context, not a replacement for its judgment.
Tolerant `VERDICT: PASS|FAIL` parse (regex) replaces the old `.startswith("PASS")` check (fixes
F4); an unparseable reply or a critic call failure now returns `needs_review` — never a silent
fail — which `run_task`/`run_synthesis` escalate (`pass_criteria_ambiguous`, matching policy.yaml's
own declared trigger) and correctly exclude from `status='done'` (see F18 below).

Proven live: a real probe (`citecheck.verify()`) against four citations — a genuine live URL
(shopify.com/about, reachable, literal found), a nonexistent domain (correctly reported "dns
resolution failed", NOT confused with an SSRF block), and two private-address targets
(`127.0.0.1`, the AWS metadata IP `169.254.169.254`, both correctly blocked before any connect
attempt) — produced `dead_frac=0.75`, correctly triggering the mechanical hard-fail path. Two
real bugs were found and fixed during this same probe: (1) the SSRF-block error message was
originally applied to plain DNS failures too, mislabeling ordinary dead links as blocked attack
attempts; (2) `_key_literal` was extracting digits from *inside* the cited URL itself (e.g. `123`
from `abc123xyz.com`) rather than from the surrounding claim text — fixed by stripping URLs from
the line before literal-hunting.

**Not done: F5 (critic self-anchoring) is only partially addressed.** H4's other prescribed fix —
"a distinct critic model whenever a second provider exists" — is NOT applied: manager and critic
remain the SAME model (`glm-5.2:cloud`) because the model hierarchy stays Ollama-only until the
operator adds a second provider (locked decision, CLAUDE.md model hierarchy section) — this isn't
a new gap, it's an explicit surfacing of a precondition the original F5 fix assumed and doesn't
yet hold. What DOES reduce the exposure: `run_critic()` was already blind to its own prior notes
before this change (prior critic feedback is injected into the WORKER's retry prompt only, in
`run_task`, never into the critic's own prompt) — confirmed by code inspection, not a new fix.
citecheck's mechanical hard-fail is a genuinely independent signal regardless, since it calls no
LLM at all.

### H5 — Fair scheduling + honest scarcity accounting (fixes F6, F7) · **F7 IMPLEMENTED + PROVEN 2026-07-24; F6 STILL NOT DONE**
**F7 (scarcity vanishing from the metric) is fixed** — see F18 below for the fuller writeup,
since the two bugs compounded on the same live data. `ledger.weekly_fitness()`'s denominator now
counts EVERY non-canary task scheduled in the window (`tasks_scheduled`), not just the subset
that reached `done`/`failed` — `stale` and still-`queued`/`quota_wait`/`interrupted`/`running`
rows can no longer silently vanish from the score. `dropped`/`pending` counts are new, always-
shown fields (`scorecard.py`'s markdown + Telegram line both surface them as a first-class line,
per H5's original "abandoned is a first-class outcome" intent) — achieved WITHOUT adding a new
weighted fitness term, since `W` is locked for 8 weeks (§3.2) and a denominator fix doesn't touch
it. `avg_cost_usd`/`intervention_rate` stay computed over resolved (`done`/`failed`/`infra_failed`)
rows only, deliberately — folding never-run rows into THOSE denominators would have diluted them
in the opposite direction (more unattempted work → fake-lower average cost).

**F6 (head-of-line blocking) is NOT fixed.** `run_task`/`run_canaries` still `break` on the first
`quota_wait` this pass, and there is still no rotating start offset — a seed that reliably parks
first can still permanently shadow the seeds behind it in the ordered list. Out of scope for this
pass (the user's ask was policy enforcement + critic truth-checking + fitness honesty, not
scheduling fairness); tracked for a future pass.

### H6 — Real budget enforcement (fixes F8, F9) · **F8 (token half) IMPLEMENTED + PROVEN 2026-07-24; USD/F9 STILL NOT DONE**
`policy.yaml` gained `cost_caps.tokens_per_day_hard_stop` (3,000,000 — ~2.7x the measured 7-day
daily average at the time this was set); `orchestrator/policy.py`'s `token_budget_breached()`
checks it via `SELECT SUM(tokens_in)+SUM(tokens_out) ... WHERE created_at >= datetime('now',
'start of day')` — computed entirely in SQLite's own clock (F17 lesson: never mix Python-local
date math with the DB's UTC domain). Checked before every worker-consuming call
(`run_task`/`run_synthesis`/`run_canaries`'s `hermes_worker`/`ollama_chat` calls); a breach parks
the task as `quota_wait` (reusing all existing dedup/resume machinery for free) and escalates
with `trigger="cost_cap_breach"`. A separate `manager_calls_per_day` counter
(`policy.record_manager_call()`/`manager_call_budget_breached()`, persisted in
`runs/policy_state.json`, protected by the same run-lock that already serializes every
`batch_runner.py` invocation — no new locking needed) gates the critic and fact-extraction calls,
returning `needs_review` rather than silently skipping when exhausted.

**Not done: the USD cap and F9 (cross-provider `fallback_chain` traversal).** `usd_per_day_hard_stop`
stays unenforced — correctly, since `cost_usd` is always `0.0` on Ollama and there is nothing real
to check yet; wiring it is deferred until a paid provider key exists, per the original finding.
`fallback_chain` traversal on 429/5xx is unimplemented — out of scope for this pass.

### H7 — Constrain the skill-promotion surface (fixes F10) · **IMPLEMENTED + PROVEN 2026-07-29**
- ~~Candidate notes must match a **strict template** (technique statements only); strip URLs and
  imperative "visit/fetch/run" constructions at draft time.~~
- ~~`promote.py list` shows a full diff plus the source lesson/task provenance, so approval is
  informed rather than a skim.~~
- ~~Cap total injected skill text and log every injection in the run log.~~

**One deliberate departure from the spec above, and it matters.** "Strip imperative visit/fetch/run
constructions" is a **verb** ban, and a verb ban is the wrong rule: this project's own first approved
skill legitimately reads *"open every cited URL and confirm the exact claimed value"*, which such a
ban rejects. What separates a technique from an injection is not the verb but whether the note names
a **specific place to go or thing to run**. "Open every cited URL" carries no attacker-chosen target;
"visit evil.example.com" does. So `sanitize_note()` bans concrete **targets and execution**, not
verbs:

- **URLs and bare domains are STRIPPED** (replaced with `[link removed]`) and the removal is surfaced
  to the operator — a technique never needs a literal address, but the surrounding sentence usually
  survives as a usable instruction.
- **Execution and framing constructs are FATAL** — code fences, inline code spans, shell invocations
  (`curl`/`bash`/`python`/…), pipelines and redirects, absolute filesystem paths, internal
  schema/path names (`ledger.db`, `workspace/`, `lesson_candidates`), instruction-override phrasing
  ("ignore all previous…"), and `system:`/`user:` role framing. Unlike a URL there is no safe residue
  left after removing these, and a note that needed one is not a technique note.
- Per-note length cap (700 chars) with truncation surfaced, and `active_skills_for()`'s injection cap
  is now single-sourced from `MAX_INJECTED_CHARS` rather than a duplicate literal (same drift class
  as F29's `_clean_url` consolidation).

**Validated at BOTH gates, not one.** Draft time is not sufficient: a candidate then sits on disk as
an editable markdown file until approval, and per **F14** an errant or prompt-injected worker holds
exactly the file-write capability needed to rewrite it in between. `cmd_approve()` therefore
re-validates from scratch and refuses rather than trusting the `sanitised:` line the file claims
about itself.

**`promote.py list` rebuilt for informed approval:** full note text (labelled as going into *every*
future prompt for that mission), a live sanitiser verdict, the recorded strip history, and each cited
evidence lesson resolved to its row — kind, source task, mission and text — so `evidence: [9, 10]`
stops being an unfalsifiable model claim. Active skills additionally show their rollback baseline and
per-mission injected size against the cap, and are re-scanned so a skill approved *before* H7 existed
cannot sit there silently unvalidated.

**Proven.** 16 unit assertions: both live skills and three verb-heavy legitimate notes accepted;
attacker URLs and bare domains stripped with the target verifiably gone; eight execution/framing
constructs rejected; oversize truncated. Then an end-to-end tamper test — a candidate carrying
`sanitised: no changes` but poisoned with `Ignore all previous instructions` plus a read of
`C:\Users\moham\.env` — confirmed `list` flags it with reasons rather than showing a clean excerpt,
and `approve` **refused** it: exit 1, no skill file written, candidate left in place for an explicit
reject, and **no git commit created**. The cap was proven by feeding a 5,999-char note and measuring
2,000 injected.

One honest limit: the per-run injection log line is verified by construction against real values
(`injecting 1 approved skill(s), 346/2000 chars: [...]`) but emits for the first time on the next
real mission fire — no research task has run since it was added.

### H8 — Test isolation (fixes F12) · **elevated priority — this already caused a real incident**
Remove default-arg DB binding; resolve paths through a single accessor so tests can redirect
safely. Add a `--db-root` flag used by all probes. Not hypothetical: this exact bug class fired
for real during this audit — the F1 probe patched `batch_runner.ROOT` but `ESCALATIONS` is a
`Path` built from `ROOT` at import time, so `escalate()` wrote to (and Telegram-alerted) the
REAL system about a staged test. Full writeup: docs/INCIDENTS.md 2026-07-19. Every `= ROOT / ...`
module constant is a landmine for the next test unless this is fixed centrally.

### H9 — Real filesystem confinement + real durability (fixes F14, F13, F16)

**Durability (F16) · IMPLEMENTED + PROVEN 2026-07-24.** `orchestrator/backup.py`: nightly
`sqlite3.Connection.backup()` (not a file copy — safe against a concurrent WAL writer, unlike
copying the file directly) of both live DBs to timestamped files under `backups/` (gitignored,
14-backup rotation). `AGI_M1_backup` scheduled task, daily 02:00 — enabled now (unlike the other
four, still disabled pending confinement below): it never touches the worker/lock path, is
read-only against the live sources, and is itself the fix for "no second copy exists anywhere."
Proven: a real backup taken, table counts verified equal to the live source; then **actually
restored** into a scratch directory and diffed — both DBs matched exactly. A backup that was
never restore-tested is a hope, not a guarantee; this one is tested. Offsite/second-drive
replication (surviving a full local-disk failure, not just corruption/accidental deletion) is
still an operator decision — no remote is configured, and creating one wasn't done unilaterally.

**Confinement (F14) · IMPLEMENTED (detection + auto-revert) 2026-07-24 — not true sandboxing.**
True prevention needs a container (Docker was removed from this machine 2026-07-08, per standing
machine facts — not reinstalled unilaterally) or a separate low-privilege OS user (an invasive
system change requiring explicit operator consent this pass did not have). `hermes profile`
switching was investigated earlier in this project and found to be sticky/global state, unsafe
per-invocation. What's built instead reuses the one mechanism already proven for the databases:
every file worth protecting (`orchestrator/`, `config/`, `missions/`, `ledger/schema.sql`, the
top-level docs, `skills_analyst/`) is git-tracked, so `git status --porcelain` on that set IS the
integrity check — same snapshot/diff/quarantine shape as `db_integrity_check()`, reusing git's
own tracking instead of a hand-rolled file watcher. A detected change is auto-reverted via
`git checkout --` (tracked files) or deletion (newly-appeared untracked files), then escalated.
This is honest about its limits: a worker COULD still read a secret or do damage in the window
before the next check fires — it is detection-and-undo, not a sandbox that prevents the read/write
from happening at all. It closes the specific F14 nightmare (the harness silently and permanently
loses its own integrity mid-run) without an invasive unilateral system change.

Proven live against the real repo (had to be — the mechanism IS git, so a fake sandboxed copy
tests nothing real): planted an untracked file inside a protected path → detected, removed, tree
clean afterward. Modified an existing tracked file (the literal F14 nightmare — a worker
overwriting the harness's own code) → detected, `git checkout` restored it **byte-for-byte**
(diffed against the original content, not just "looked fine"). Both correctly escalated — see
docs/INCIDENTS.md 2026-07-24 for the real side effect that caused (two more false Telegram
alarms, same class as the 07-19 incident, this time a conscious tradeoff not a missed patch).

**Policy as code (F13) — IMPLEMENTED + PROVEN 2026-07-24.** New `orchestrator/policy.py` is the
single loader for `policy.yaml`; `batch_runner.py` now calls into it at every point the doc
declared a control. `escalate()` gained a `trigger=` param validated against policy.yaml's own
`escalation.triggers` list (`policy.validate_trigger`) — an unknown trigger name raises, keeping
the declared list authoritative rather than free-text decoration. All four triggers are now real,
firing code paths: `deny_list_match` (regex scan of worker output for language claiming a
`hard_exclusions` action — move_money/handle_credentials/irreversible_delete — deliberately
conservative, catches self-reported claims not a determined attacker), `pass_criteria_ambiguous`
(critic `needs_review`, from H4), `cost_cap_breach` (token budget, H6), `repeated_task_failure`
(new: a mission accumulating ≥3 content-FAILED tasks in the current week escalates once, at the
threshold crossing). `compliance_floor` + `hard_exclusions` are now injected as an explicit
"HARD RULES" block into the worker prompt (previously never mentioned at all).
`policy.validate_paths()` cross-checks the fs-guard's `PROTECTED_PATHS` (H9, above) against
`workspace_confinement.writes_allowed_under`, run once at every `_run()` startup — and it
immediately caught a REAL, previously undocumented drift: policy.yaml declared the whole
`ledger/` directory writable, which silently included `ledger/schema.sql` — a file the fs-guard
deliberately protects and that should never be runtime-writable. Fixed by narrowing the
declaration to `ledger/ledger.db` specifically. `supply_chain.hub_skill_install` and
`weekly_osv_audit` are explicitly OUT of scope: this codebase has no Hermes hub-skill-install path
to gate (`promote.py` only handles the repo's own `skills_analyst/`), so there is nothing real to
wire yet. `self_modification.orchestrator_code: forbidden_in_m1` is enforced by the existing
fs-guard (H9), not duplicated here.

---

### F53 — 25% of the fitness score was awarded unconditionally, on every task ever run · **P0 · PROVEN, found + fixed 2026-07-31**

**Symptom.** `F = 0.35·completion + 0.30·accuracy + 0.25·(1−intervention) + 0.10·cost_eff`
reads as four scored dimensions. Two of them could not move. Measured across the whole
ledger: `SELECT DISTINCT interventions FROM tasks` returns exactly `[0]` — **all 32 rows,
every mission, every week since the project began**. `cost_usd` is likewise `0.0` on all 32.
So `intervention_norm = 0` always → the term contributes its full `0.25` on every task, and
`cost_eff = min(1, 0.50/avg_cost) if avg_cost > 0 else 1.0` falls to the `else` branch every
time → a further `0.10`. **The live dynamic range of F is 0.35–1.00, not 0–1.** The smoking
gun is already in this project's own `scorecards` table: two rows read `fitness: 0.35` with
`completion_rate: 0.0, accuracy: None` — weeks where *nothing completed and nothing was
verified* still scored 0.35. Worse, HARNESS_DESIGN §7's M1 acceptance criterion
"interventions −30% vs baseline by week 8" was **structurally unprovable**: 0 → 0 is not a
30% decline, it is undefined. One of the four acceptance criteria could never be evaluated.

**Root cause — two independent defects, and BOTH had to be fixed for either to matter.**
1. **The signal was generated and then discarded.** `escalate()` appended to
   `workspace/ESCALATIONS.md`, logged, pushed to Telegram — and never touched the ledger
   row. Ten call sites produce genuinely task-scoped escalations (ambiguous critic verdict,
   deny-list hit, budget exhaustion, degraded failover) and every one of them died in a
   markdown file. This is exactly F33 (synthesis tokens measured, never recorded) and F48
   (canary tokens measured, never recorded). **Third instance of the same bug class**: a
   real measurement that never reaches the column that scores it.
2. **Even a correct increment would have been erased at task end.** `finish_task()` wrote
   `interventions=?` with a default of `0`, unconditionally overwriting. It is the one
   consumption column **F21 missed** when it moved `cost_usd`/`tokens_in`/`tokens_out`/
   `critic_verdict` to `COALESCE` — invisible precisely *because* the value was already
   always 0, so the clobber never destroyed anything anyone could observe. A latent defect
   that only becomes reachable once the other half is fixed.

**Fix.** `ledger.record_intervention(task_id, kind)` increments the counter and appends the
trigger to `intervention_types`; `finish_task()`'s write becomes
`interventions=COALESCE(?, interventions)` with the default moved to `None` (F21's own
pattern, finally applied to the column it skipped — an explicit caller value still wins, so
existing callers are unaffected); `escalate()` gains an optional `task_id` and records an
intervention when given one. **Run-scoped escalations deliberately do NOT count** — "ollama
unreachable" and "batch aborted" pass no `task_id`, because infrastructure failure is not a
verdict on any one task's autonomy, the same line F37 draws.

**Reporting, because the number could not simply be corrected.** `weekly_fitness()` now
returns `intervention_measured`, `cost_measured`, and `fitness_floor`; `scorecard.py`
renders "⚠ **0.35 of F was awarded unconditionally**" in the markdown and
"⚠ 0.35 of F unearned (interv/cost not measured)" in the Telegram line. This is the
F7/F45 honesty fix applied to a numerator instead of a denominator: the score is not wrong,
it is just not what it looks like. **W is untouched — LOCKED (§3.2)**, and asserted so by
the test.

**Not backfilled, and the discontinuity is stated rather than smoothed.** W29–W31 recorded 0
because nothing *could* write the column; their intervention term is a structural artefact,
not a measurement. The first post-F53 week will therefore likely show a fitness DROP that
means *the metric went live*, not that the analyst got worse — `intervention_measured`
exists so that distinction survives into the scorecard rather than living only in this
entry. **`cost_eff` remains unmeasurable and is deliberately NOT faked**: Ollama genuinely
reports $0 on a flat subscription, so inventing a per-task dollar figure would replace an
honest constant with a dishonest variable. It is now labelled, not invented.

Found by measuring the ledger while rating the harness, rather than by any test failing —
every suite was green throughout, because nothing was *broken*; the metric was simply
measuring less than it claimed. · `tests/test_f53.py` (17 assertions, incl. the pre-F53
clobber reproduced explicitly and the 0.35 floor rebuilt from a clean schema)

---

### F54 — An operator re-verification could never clear the AI-performed flag · **P0 · PROVEN, found + fixed 2026-08-01**

**Symptom.** The operator personally opened the cited sources for tasks 28 and 29 on
2026-08-01, confirmed the three YouTube titles and both blog.google quotes, and the verdicts
were recorded through `spotcheck.py pass`. `spot_checked_ai` stayed at **7 of 7**. The single
transition the whole F28 apparatus exists to enable — self-graded → independently verified —
was structurally impossible, and `spotcheck.py`'s own docstring promised it worked:
*"Re-running this command yourself on the same task overwrites the row with a genuine
independent read."* **That sentence was false.**

**Root cause — two defects, the second hiding inside the fix for the first.**
1. **`cmd_verdict()` APPENDS, it does not overwrite:**
   `critic_notes = COALESCE(critic_notes,'') || ' | HUMAN(...)'`. That is *correct* for an
   append-only audit trail and was deliberate. But every classifier — `weekly_fitness()`'s
   `spot_checked_ai` and `spotcheck.pending_rows()`'s `ai_done` — grepped the **whole field**
   for `"AI-PERFORMED CHECK"`. One historical AI check therefore marked a row AI-performed
   **permanently**, no matter how many genuine operator reads followed it.
2. **The marker test was substring-anywhere, but F28 specifies the note must *start* with
   the marker.** So prose that merely *mentions* it also matched — and the first real
   operator note said *"supersedes the earlier AI-PERFORMED CHECK on this row"*, which
   re-flagged the very row it was clearing. Written by the assistant, in the same action
   that was supposed to record independence: the fix carried the bug.

**Fix.** `ledger.latest_human_note()` extracts only the most recent `HUMAN(...)` segment;
`ledger.is_ai_performed()` applies F28's own stated test (`startswith`) to that segment
alone. Both classifiers call it. Audit history is untouched — every earlier segment is still
on the row, which is the point of appending.

**Fails closed, deliberately.** Pre-convention rows that name an assistant without using the
marker (task 2, `"verified in live browser 2026-07-18 by Claude session (not operator)"`)
still count as non-independent. Independence is the property being *proven*, so a false
"independent" is a corrupted result while a false "AI-performed" is only a missed credit —
the asymmetry decides the default. That row would otherwise have started counting as
independent the moment this fix landed, silently inflating the number this fix exists to
protect.

**Measured, before → after:** in-window independent spot-checks **0 → 2**; `spot_checked_ai`
**7 → 5**; tasks 28/29 dropped off the "still needs YOUR confirmation" Telegram nag; task 2
correctly *appeared* as non-independent for the first time. `accuracy` and `fitness` are
unchanged at **0.714 / 0.914** — this fixes provenance reporting, not the score. **W is
untouched — LOCKED**, asserted by the test.

**Timing made this P0 under the execution-only directive's standing exception** ("a defect
actively corrupting the data being collected"): the W31 scorecard cron fires **2026-08-02
04:00**, and would have reported 7/7 self-graded — hours after two of them stopped being so.
· `tests/test_f54.py` (18 assertions, incl. the exact live failure reproduced and the pre-F54
substring test shown returning the wrong answer)

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

**Phase 1 — Trust the numbers (during W30 baseline).** H4 (citation validator + parse), H5's F7
half (honest completion denominator), H6's token-budget half, H13/F13 (policy.yaml enforcement),
and the newly-found F18 (status/verdict mismatch) — **IMPLEMENTED + PROVEN 2026-07-24**, three
days before W31's gated promotion starts. Rationale: M1's entire claim is "measurable
improvement." F3/F7/F18 meant the current numbers could look perfect while being wrong — the
metric had to be trustworthy *before* the promotion gate starts optimising against it at W31.
*Exit, all proven live against this repo's real ledger, not a synthetic case:* a deliberately
fabricated-citation deliverable (4 URLs, 3 dead/SSRF-blocked) mechanically auto-fails
(`dead_frac=0.75` > threshold); the scorecard now shows `dropped`/`pending` counts as first-class
lines; `weekly_fitness()` corrected the live completion rate from a reported 100% to the true 0%
on the exact week this was found, and that correction is reflected in the live ledger, not just a
test fixture.
**Still open (deliberately out of scope this pass — not silently dropped):** F6 (head-of-line
blocking / no rotating start offset), F9 (`fallback_chain` cross-provider traversal), the USD
half of F8 (no paid provider exists yet to make it real), F15 (`promote.py` commit pathspec
isolation).

**Phase 2 on-ramp — 2026-07-27 (the day W31 was scheduled to start).** A read-only health check
ahead of enabling gated promotion found the loop's own ignition was broken: both Sunday crons
that feed it (`AGI_M1_canaries`, `AGI_M1_scorecard` — the latter is what actually runs
`promote.cmd_review()`, see `_run()`'s `--scorecard` branch) were **refused by Task Scheduler**
that morning (Win32 4320, "operator/administrator has refused the request") because the laptop
woke on battery and every `AGI_M1_*` task had `DisallowStartIfOnBatteries=true` — confirmed by the
complete absence of any `batch_*.log` for that fire; Python never started. Fixed (operator
decision: run regardless of power) by flipping `DisallowStartIfOnBatteries`/
`StopIfGoingOnBatteries` to `false` and `StartWhenAvailable` to `true` on all 5 scheduled tasks,
verified by re-reading each task's settings and confirming triggers/actions were untouched by the
mutation. Same pass also closed **F6, F9, and F15** (all three above) as direct blockers/adjacent
risks to a clean promotion cycle, and verified the promotion machinery itself end-to-end against
the live pool and an isolated auto-rollback rehearsal (see `promote.py review --dry`,
`newest_skill_below_baseline()`/`cmd_rollback()` proof under F15, above). See
`docs/INCIDENTS.md` for the full incident writeup. **Follow-on found the same day, RESOLVED
2026-07-27:** independently re-checked all 5 tasks afterward and found `Principal.LogonType =
Interactive` still set on every one — a second, independently-documented cause of the exact same
Win32 4320 refusal, untouched by the battery fix above. Needed an elevated
`Set-ScheduledTask -Principal ... -LogonType S4U`, which this session could not apply itself
(`Access is denied` under UAC token filtering). The operator ran it elevated; a first reported
success did NOT independently verify (`docs/INCIDENTS.md`'s second follow-on entry — the report was
consistent with a non-elevated run whose per-task errors didn't halt the loop). Re-issued with
explicit per-task try/catch, an elevation guard, and `AGI_M1_*` enumeration instead of five
hardcoded names (which would have missed the one-time `AGI_M1_F20proof`, registered hours later for
the same night's proof run). The operator re-ran it; independently confirmed via two separate read
paths (`Get-ScheduledTask` after a forced module reload, and `schtasks /query /xml`) — both report
`S4U` on all 6 tasks. Both independent causes of the Win32 4320 refusal are closed. **Two things
this pass explicitly did NOT
close, surfaced rather than silently skipped:** no git remote is configured
(`git remote -v` empty), so recovery stays local-only — CLAUDE.md's "nightly backup + `git push`
= recovery" is still partly aspirational until one is added, which needs an operator choice
(external service + auth), not a code fix; and the F17 clock-mismatch class was reconfirmed live
in `weekly_fitness()`'s window boundary (see F17, above) but not fixed in this pass — handed off
as a separate follow-up rather than expanding this pass's approved scope.

**Phase 2 go-live — 2026-07-28.** W31's first real fire scored 0/3 on mission 001, and the
mechanism was the on-ramp's own remaining gap: worker prompts included the fairness/failover/audit
fixes above but never the mission's `## Done-definition` — the critic grades against it, the worker
never saw it (**F20**, P0). Proving the fix live surfaced five further defects, all in the
machinery that judges the work rather than the work itself: retries silently erasing prior token
accounting and the critic's review history (**F21**), the daily budget counting the wrong day and
then two individually-correct fixes for that composing into a third bug (**F22, F22b**), and
citecheck falsely accusing sound research of fabrication via byte-cap truncation, format-brittle
matching, and a URL regex that swallowed trailing HTML tags (**F23, F23c**) — proven by re-judging
the *same, unchanged* deliverables on corrected evidence: two verdicts flipped FAIL→PASS with no
work redone. Admission control (**F24**) closes the cap's last real gap: refusing a task before it
starts if its predicted cost cannot fit the remaining budget, since a hermes subprocess cannot be
stopped mid-research once admitted.

The most consequential result came from the *first real operator-style spot-check*, which failed
two deliverables the automated critic had just passed: a bare substring match let `"19"` verify
against a page whose only prices were `$16`/`$24`/`$194`/`$296`, and one deliverable quoted a price
sentence that does not exist on its cited page — a fabricated quotation the mechanical check could
not catch (**F25**). Confidence-level semantics ("3 means you loaded the page and read the value")
and a verbatim-quotation rule were added to the worker prompt as the direct response. JSON-LD
parsing was added to citecheck for structured (schema.org) values (**F26**) — genuinely built and
tested, but an honest correction is on record: it did not change any outcome that night, since
F23's raised byte cap already covered the one case that motivated it. Verifying it surfaced a
distinct, unfixed risk — raw substring matching can confirm the right digits for the wrong reason,
4 unrelated matches for "129" on one real page (**F27**, found, not fixed — no live wrong-verdict
evidence yet). Finally, spot-checks the assistant performs on the operator's behalf are now flagged
(`spot_checked_ai`) in the scorecard rather than read as indistinguishable from an independent
check (**F28**) — the same failure shape as the 2026-07-18 rogue-write incident, one layer up.

**W31 stood at, after this pass:** completion 43%, accuracy 33% (3 spot-checks, all AI-performed
pending operator confirmation), fitness 0.60 — honestly low, every correction carrying an audit
trail, not a claim of a clean night. *(Superseded 2026-07-29: the throughput pass found that two of
those failures were also harness defects — F30/F31 — and W31 closed at completion 86% / F 0.75. The
0.60 figure was the honest reading of the evidence available on 2026-07-28; it is left here as the
record of that night rather than retconned.)*

**Phase 2 — W31 promotion under hardened conditions.** H7 (still open — candidate-note template
constraints / F10 injection-surface hardening was not part of this pass either) remains the one
item the original roadmap wanted before enabling the gate; the ignition/fairness/audit-trail
fixes above are a precondition for a clean cycle, not a substitute for H7. The self-improvement
loop must not be the thing that discovers these flaws.

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
This document started as an audit + blueprint; sections are updated in place as work lands, each
marked IMPLEMENTED + PROVEN with the date and what was actually verified (never claimed from
code-reading alone — see CLAUDE.md's verification ladder). As of 2026-07-28: Phase 0 (H1, H2, H3,
H9) and Phase 1 (H4, F7/F18, the token half of H6, H13/F13) are done. The Phase 2 on-ramp closed
F6, F9, F15, and F19 (the reconfirmed F17 clock-mismatch class in `weekly_fitness()`'s window
boundary — worse than first scoped), fixed the cron battery-refusal bug, and its LogonType
follow-on is now also closed (independently reverified — see the Phase 2 on-ramp entry above; a
first reported fix did not verify and is documented as its own lesson). Phase 2 go-live
(2026-07-28) fixed F20 through F26 and F28, found F27 without fixing it (no live wrong-verdict
evidence yet to design a fix against), and left W31 at a real, honestly-reported fitness of 0.60.

The **throughput pass (2026-07-29)** raised the loop's work rate on operator instruction, with the
safety and honesty rules unchanged. It fixed F29 through F35 and F37 through **F45**, closed **H7**, and implemented
directives 1, 2 and 5 (all above). **F36** was found live when the guard reverted this session's own
uncommitted work; its blast-radius and recoverability halves are fixed (and detection strengthened
along the way), while the attribution half stays deliberately unfixed — inferring *who* edited a file
trades containment for convenience. Task 30 was then re-run through the production batch path as a true
synthesis: it passes, cites 10/10 reachable URLs with 0 missing literals (was 4 falsely-dead, 4
missing), covers the mission's two real channels instead of an invented one, and — the clearest
evidence F31 works — reports an explicit **DATA GAP** for a third topic the supplied brief did not
contain, rather than inventing one, with the critic passing it rather than failing it for the
absence. The theme is worth stating plainly, because it is now the third session running:
**every failure investigated this pass was in the machinery that JUDGES or SCHEDULES the work, not
in the work itself.** Task 27 was unpassable by construction (F31); task 30 was researching the
wrong thing entirely (F30) and then rejected for the wrong reason on top of that (F29). Three
lesson-candidates derived from those bogus verdicts were retracted before drafting, on the same
grounds as F20's retractions — left in the pool they would have taught the analyst to work around
bugs that no longer exist. W31 was corrected to completion **86%**, fitness **0.75**, entirely by
fixing our own defects; no deliverable was edited and task 30 was deliberately left FAILED, since
its content really is wrong even though the recorded reason was ours.

**The promotion gate fired for the first time on 2026-07-29**: both candidates were operator-approved
and are live in `skills_analyst/`, injecting into their missions' worker prompts (346 and 430 chars).
That immediately surfaced F34 above — the approval stamped a rollback baseline of 0 and disarmed its
own safety net. Worth noting as a pattern: the gate's first real use found a defect in the gate,
exactly as the first real use of the reasoning trace found F23.

**H7 is now closed** (2026-07-29) — the F10 injection surface is constrained at both the draft and
approval gates, `list` supports informed approval, and injection is capped and logged. It was built
*after* the gate had already been used, which is the wrong order; both live skills were re-scanned
under the new rules and pass.

Still open: the USD half of F8, **F27** (raw substring matching's coincidental-match risk), no git
remote (recovery is local-only), and Phase 2.5 onward (runtime abstraction, M2).

Task 30's re-run as a true synthesis is **done** and was removed from this list on 2026-07-29 — it
completed at 18:02:46 with `status='done'`, `critic_verdict='pass'`, 10/10 citations reachable and an
honest DATA GAP where the source brief was short. It is described as completed at F32 and F33 above,
so this list had been contradicting the same document two sections earlier.

### F55 worker partial-output resilience
Worker prompt-side instruction added so that on tool failure (HTTP 503/403,
timeouts, browser-blocked pages) the worker emits a partial deliverable with
an explicit `PARTIAL OUTPUT` marker rather than a silently empty one. Does NOT
address `loop_web_search_cap` separately — that cap is a structural ceiling,
not a recovery mechanism. Committed 2026-08-26; F55 number reserved since.

### F56 orchestrator logging proxy
Three logger implementations (`batch_runner.log`, `integrity.log`,
`execution.log`) had silently captured the same function object across the
Move 1/2 era, but module-global rebinding (`br.log = lambda`) only silenced
the binding on the patched module — `integrity` and `execution` kept writing
to stdout and to `runs/schtask_last.log`. Re-export shims do not redirect
module-global lookups inside function bodies. The proxy in
`runtime_context.log` delegates to `_logger` at call time, so a single
`_logger` patch is visible to every orchestrator module. `tests/_silence.py`
exposes `silence_log()` / `capture_log()` as the truthful patch points;
legacy `br.log = lambda` no longer suffices and was updated across the
regression suites. Fixed 2026-08-27 · `orchestrator/runtime_context.py:25-64`
· `tests/test_f56.py` (18 assertions)

### F57 evaluation extraction (Move 5c behaviour preservation)
`run_critic`, `extract_facts`, `_parse_json_array`, `ENTITY_TYPES` lived in
`batch_runner.py` (alongside the Move 4 leaves `seed_is_synthesis`,
`retract_facts` already in `evaluation.py`). Move 5c moves them all into
`orchestrator/evaluation.py` as a single evaluation-and-memory service;
`batch_runner.py` retains explicit re-exports for backwards compatibility.
The dependent model call is module-qualified (`execution.ollama_chat(...)`,
not `from execution import ollama_chat`) so tests and future capability
injection can patch `execution.ollama_chat` truthfully. `tests/test_f57.py`
(36 assertions) pins every observable behaviour before extraction: parsing
tolerance, validation, normalisation, budget gating, hard-fail short-circuit,
PASS/FAIL parsing, unparseable → needs_review, baseline + scope injection,
trace path, and the identity check `br.X is ev.X` for all six names.
F57 also locks the dependency shape: the patch point is the owning module
(`execution`), not the compatibility alias (`batch_runner`). The move is
behaviour-preserving — measured across the deterministic gate, not
assumed. · `orchestrator/evaluation.py` · `tests/test_f57.py`

### F58 workflow extraction (Move 5c′ behaviour and state isolation)
`run_synthesis`, `run_canaries`, `retry_failed_this_fire`, and
`_check_repeated_failure` moved from `batch_runner.py` to the new
`orchestrator/workflow.py`; `batch_runner.py` retains explicit compatibility
re-exports. Workflow resolves mutable runtime paths and logging through
`runtime_context` at call time and receives `run_task` through the approved
`run_task_fn` injection seam, so it imports neither `batch_runner` nor
`task_runner`. C4/C5 canary definitions were restored byte-for-byte to their
pre-extraction values from `4d1a401` after the mechanical move altered them.

`tests/test_f58.py` now patches canonical owning modules after extraction and
protects the real repository during canary characterization: promotion rollback
is intercepted fail-closed, live policy state is redirected, and whole-suite
snapshots cover HEAD/status, active skill paths and hashes, ledger state, run
artifacts, escalation state, and policy state. Two repeat runs completed with
zero drift and zero rollback attempts. The complete deterministic gate then
passed 21/21 suites with only `test_baseline` quarantined as the designated
live-data check. The aggregate gate intentionally creates preserved rollback
and escalation artifacts in its integrity suites; F58 itself remains
side-effect-free. · `orchestrator/workflow.py` · `tests/test_f58.py`

### F60 task-runner extraction (Move 5d behaviour and state isolation)
`run_task(tid, mission, roles)` moved to `orchestrator/task_runner.py`;
`batch_runner.py` remains the composition layer and explicitly re-exports the
same function object. The canonical runner uses module-qualified dependencies,
resolves runtime root/run/log bindings at call time, keeps prediction hooks
fail-soft, and preserves the established persistence and classification order.

`tests/test_f60.py` patches canonical owning modules and pins prompt inputs,
prior-review and baseline text, skill/compliance injection, synthesis and
research ownership, both budget gates, raw-output persistence before
classification, deliverable creation before criticism, token accumulation,
fact writes, compatibility identity, and AST dependency boundaries. Two repeat
runs preserve HEAD, status, ledger, escalation, and policy hashes exactly. The
focused F48/F57/F58/F59/F60/throughput gate passed 6/6 before Move 5e.
· `orchestrator/task_runner.py` · `tests/test_f60.py`

### F61 CLI composition cleanup (Move 5e / W9 completion)
`batch_runner.py` now defines only `load_roles`, `main`, and `_run`. Accidental
aliases into integrity, execution, prompts, and scheduler were removed; affected
tests patch the canonical owner instead. The retained compatibility identities
are declared explicitly in `__all__`. Production callers now import
`ollama_chat` from `execution` and `retract_facts` from `evaluation` directly.

F61 verifies the function inventory, removed aliases, compatibility identities,
CLI wiring, explicit retry injection, canonical production imports, and zero
live-state drift. W9 ends here. The next phase is real end-to-end harness
validation—missions, recovery, efficiency, capability selection, and outcome
quality—not further refactoring. · `orchestrator/batch_runner.py` ·
`tests/test_f61.py`

### F62 task-scope dependency lost during W9 extraction
The first production recovery after W9 reached `prompts.task_scope_note()` and
raised `NameError: seed_is_synthesis is not defined` before the worker call.
The function retained a bare reference after the predicate moved to
`evaluation.py`. It now resolves through the canonical owner as
`evaluation.seed_is_synthesis(...)`; `tests/test_f62.py` covers both research
and synthesis scope routes. The failed recovery left task 64 recoverable and
spent no worker tokens. The subsequent retry completed normally. ·
`orchestrator/prompts.py` · `tests/test_f62.py`
