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

### H7 — Constrain the skill-promotion surface (fixes F10)
- Candidate notes must match a **strict template** (technique statements only); strip URLs and
  imperative "visit/fetch/run" constructions at draft time.
- `promote.py list` shows a full diff plus the source lesson/task provenance, so approval is
  informed rather than a skim.
- Cap total injected skill text and log every injection in the run log.

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
`docs/INCIDENTS.md` for the full incident writeup. **Follow-on found the same day, NOT yet
fixed:** independently re-checked all 5 tasks afterward and found `Principal.LogonType =
Interactive` still set on every one — a second, independently-documented cause of the exact same
Win32 4320 refusal, untouched by the battery fix above. The crons can still silently refuse to
fire whenever the machine is locked/no one is logged in, battery state aside. Needs an elevated
`Set-ScheduledTask -Principal ... -LogonType S4U` — blocked in-session by UAC token filtering
(`Access is denied`), so this is an operator action, not a code fix. Full writeup + exact command:
`docs/INCIDENTS.md`, "2026-07-27 — follow-on: the battery fix left the real ignition blocker
untouched." **Two things this pass explicitly did NOT
close, surfaced rather than silently skipped:** no git remote is configured
(`git remote -v` empty), so recovery stays local-only — CLAUDE.md's "nightly backup + `git push`
= recovery" is still partly aspirational until one is added, which needs an operator choice
(external service + auth), not a code fix; and the F17 clock-mismatch class was reconfirmed live
in `weekly_fitness()`'s window boundary (see F17, above) but not fixed in this pass — handed off
as a separate follow-up rather than expanding this pass's approved scope.

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
code-reading alone — see CLAUDE.md's verification ladder). As of 2026-07-27: Phase 0 (H1, H2, H3,
H9) and Phase 1 (H4, F7/F18, the token half of H6, H13/F13) are done, and the Phase 2 on-ramp
closed F6, F9, and F15, and F19 closed the reconfirmed F17 clock-mismatch class in
`weekly_fitness()`'s window boundary (found and fixed same day — it was worse than first scoped,
see F19). Also fixed the cron battery-refusal bug that had silently broken the promotion loop's
own ignition. Still open: H7 (candidate-note injection hardening — the roadmap's own stated
precondition for enabling the promotion gate), the USD half of F8, no git remote (recovery is
local-only), and Phase 2.5 onward (runtime abstraction, M2).
