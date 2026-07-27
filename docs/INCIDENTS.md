# Incidents

## 2026-07-27 — W31's promotion-review ignition was refused by Task Scheduler on battery power

**What happened:** Today, 2026-07-27, is the exact date `missions/_M1_INDEX.md` scheduled for W31
gated skill promotion to start. A read-only health check run before enabling anything found both
Sunday crons the promotion loop depends on — `AGI_M1_canaries` and `AGI_M1_scorecard` (the latter
is what actually invokes `promote.cmd_review()`, inside `_run()`'s `--scorecard` branch) —
reporting `Last Result: -2147020576` from their most recent fire (2026-07-27 01:17:47). Decoded
(`net helpmsg 4320`): **"The operator or administrator has refused the request."** Cross-checked
against `runs/schtask_last.log`: no batch log of any kind exists for that timestamp — Python never
started. Root cause, confirmed via `schtasks /query ... /xml`: all 5 `AGI_M1_*` tasks had
`DisallowStartIfOnBatteries=true` and `StopIfGoingOnBatteries=true`; the laptop woke on battery at
that hour, and Task Scheduler refused the launch outright rather than running it or queuing it.
This week's promotion review therefore never ran — not a code bug, a scheduling-policy default
that happened to gate the one week it mattered most.

**Why this matters:** the promotion loop's evidence chain (lesson pool → review → candidate →
operator approval → canary-baseline rollback protection) is entirely useless if the review pass
that starts it can silently fail to fire, with no error surfaced anywhere except a raw Win32
result code an operator would have to go looking for. A "gated" promotion system is not actually
gated by evidence if its own ignition depends on the laptop being plugged in at 3:30am on a
Sunday.

**Fix applied:** operator decision — run regardless of power. Set
`DisallowStartIfOnBatteries=$false`, `StopIfGoingOnBatteries=$false`,
`StartWhenAvailable=$true` on all 5 `AGI_M1_*` scheduled tasks via
`Get-ScheduledTask`/`Set-ScheduledTask` (mutating `.Settings` in place, not replacing it, so
triggers/actions were preserved — verified by re-reading each task's XML afterward: all 5 show the
flipped flags with 1 trigger and the original action/args intact).

**Lesson:** a scheduled task's default power/battery conditions are exactly the kind of "unread
policy" this project has repeatedly found elsewhere (policy.yaml/F13, the deny-list, the cost
caps) — declared once at task-creation time, never revisited, and invisible until the one day
conditions line up to trigger them. Same shape as F13: a control existing in configuration is not
the same as the control being correct for what depends on it. Worth a standing check before any
future "does the automation actually run" audit: read `Get-ScheduledTask ... .Settings`, don't
just confirm the task exists and has the right trigger time.

## 2026-07-24 — F18: critic-REJECTED tasks read as 100% complete in the live scorecard

**What happened:** While implementing the fitness-reporting fix the operator asked for ("fix
fitness reporting so it cannot claim 100% completion while dropping work"), a routine live-data
check before writing any code (`SELECT status, critic_verdict FROM tasks WHERE critic_verdict IS
NOT NULL`) surfaced task_id 20, 21, 22 — all three carrying `critic_verdict='fail'` alongside
`status='done'`. `run_task()`/`run_synthesis()` had always set `status="done"` once the critic
returned ANY verdict, storing pass/fail only in the separate `critic_verdict` column that
`weekly_fitness()` never reads. Recomputing the live 7-day window by hand: 10 tasks scheduled, 3
nominally "done" — all 3 actually critic-rejected — the rest queued/parked/stale. The scorecard
was reporting **100% completion** on a week whose true pass rate was **0/10**, at the exact
moment W31's gated skill promotion (starting Mon 2026-07-27, three days later) was about to start
trusting this number to decide whether to keep or roll back a promoted skill.

**Root cause:** `status` and `critic_verdict` were two independent fields with no code enforcing
they agree; `finish_task(..., status="done")` was hardcoded at the call site regardless of the
verdict variable sitting right next to it in the same function.

**Fix applied:** `status = "done" if verdict == "pass" else "failed"` in `run_task()` and
`run_synthesis()` (`orchestrator/batch_runner.py`), and the same fix in the legacy hand-run path
(`orchestrator/run_task.py`) since it writes the same ledger and had the identical bug. Proven on
a DB copy first (`weekly_fitness()` returned `completion_rate: 0.0`, matching hand-calculation)
before backfilling the three live rows (`status` `done`→`failed`, `critic_notes` appended noting
the correction — an update, not a delete, per schema.sql's append-only-in-spirit convention).
Live `weekly_fitness()` now reports the true `completion_rate: 0.0` for the window.

**Lesson:** two fields that are supposed to agree (a resolution status and a judgment verdict)
will eventually disagree unless one is *derived* from the other at the single write site, not
set independently by two nearby lines that happen to usually match intent. A metric's formula
can be completely correct and still lie if it trusts a field that was never actually kept honest
by the code writing it — "N claims need N probes" applies to schema invariants, not just
computed results: I verified the fitness FORMULA was fixed, and separately verified (by querying,
not assuming) that the STATUS column it reads was actually trustworthy, and found it wasn't.

## 2026-07-24 — H9's fs-guard tamper test sent two more real false-alarm escalations

**What happened:** Testing the new filesystem integrity guard (H9, docs/HARDENING.md F14)
required a genuinely git-tracked working tree — the guard's whole mechanism IS `git status`/
`git checkout`, so a sandboxed fake-copy test (the pattern used successfully for the DB guard
tests) would have tested nothing real; `git -C <fake-temp-dir> status` just errors out silently
on a non-repo. The test was therefore run deliberately against the real repo: plant a tamper
(an untracked file, then a modified tracked file) → confirm the guard detects and reverts it.
Both tests passed correctly. But `escalate()`'s real Telegram push fired for both, exactly the
same user-facing outcome as the 2026-07-19 incident — this time from a conscious, reasoned
tradeoff rather than a missed monkeypatch, but the result (two false alarms on the operator's
phone about staged tests) is identical.

**Fix applied:** appended a correction entry to `workspace/ESCALATIONS.md` (append, not rewrite).

**Lesson:** when a test genuinely needs to run against the real repo (not everything can be
sandboxed — git-based mechanisms are the clearest example), stub `escalate()` itself for the
duration of the test rather than accepting the real side effect. A `contextlib.contextmanager`
that monkeypatches `batch_runner.escalate` to a logging no-op, used around exactly the tamper
lines, would have proven the guard's detection+revert behavior with zero real-world noise. Two
incidents now share this root cause in spirit: testing this codebase requires either (a) full
isolation via a throwaway git repo + every derived path patched, or (b) accepting you're
touching production and explicitly neutralizing every side-effecting call (not just the ones
the target function obviously makes) before you start.

## 2026-07-19 — Adversarial audit probe sent a false escalation (real Telegram alert) about itself

**What happened:** While proving F1 (docs/HARDENING.md — the concurrency bug where
`db_integrity_check()` deletes a legitimate concurrent process's rows), the probe script
sandboxed the test by reassigning `batch_runner.ROOT` and `batch_runner.RUNS` to a temp
directory before calling the real `db_integrity_snapshot()`/`db_integrity_check()` functions.
The DB-count comparison correctly detected the staged "process B" write and correctly
quarantined it (into the temp dir — that part worked). But `db_integrity_check()`'s failure
path also calls `escalate()`, and `escalate()` writes to a module-level constant —
`ESCALATIONS = ROOT / "workspace" / "ESCALATIONS.md"` — **computed once at import time from
the original `ROOT`**, not re-derived from `batch_runner.ROOT` at call time. Reassigning
`ROOT` after import does nothing to a `Path` object already built from its old value.

Result: `escalate()` wrote a "worker wrote directly to a database" line straight into the
**real** `workspace/ESCALATIONS.md`, and — since `escalate()` also best-effort pushes to
Telegram and the home channel had been live since 2026-07-18 — very likely sent a **genuine
alarm to the operator's phone about a deliberately-staged test scenario**, not a real incident.

**Root cause:** identical bug class to F12 (`ledger._conn(db=LEDGER_DB)`'s default-arg path
binding) — a config/path value captured once at import time, assumed to be redirectable by
reassigning the module attribute it was *derived from*, when in fact the derived value itself
needs to be reassigned (or, better, computed lazily at call time rather than at import time).

**Fix applied:** appended a correction entry to `workspace/ESCALATIONS.md` (append, not
rewrite — the audit trail doesn't get silently edited, even to fix a mistake in it).

**Lesson:** sandboxing a test by patching a module's `ROOT`/base-path attribute is not safe
unless EVERY derived constant in that module is either (a) also lazily computed at call time,
or (b) individually patched. A probe that redirects "the important paths" and trusts the rest
will silently miss any constant built from the original value before the patch landed. When
writing a future test harness for this codebase, prefer passing paths as function arguments
over relying on module-global patching, or audit every `= ROOT / ...` line in the module first.

## 2026-07-18 — Worker wrote directly into ledger.db / ledgerbook.db, self-graded its own task

**What happened:** The first live batch-engine task (mission 001, seed 1: PromptBase) was run
through `hermes -z` with the worker's full default `cli` toolset enabled (`terminal`, `file`,
`code_execution` all on by default — confirmed via `hermes tools list`). The worker prompt at the
time pasted the ENTIRE mission markdown file as context, including the internal deliverable path
(`workspace/shopify/competitor-intel-YYYY-WW.md`) and the instruction "facts written to
memory/ledgerbook.db as fact rows" — text meant for the orchestrator, not the worker.

The worker read `ledger/schema.sql` on its own initiative, then:
- wrote its own deliverable to the exact path the mission specified (`competitor-intel-2026-W29.md`)
  plus a raw JSON dump, bypassing the orchestrator's controlled write path;
- inserted 1 competitor entity + 18 product entities and ~19 facts directly into
  `memory/ledgerbook.db` — code the orchestrator does not even contain yet;
- **inserted a fabricated row into `ledger/ledger.db`** (task_id=6) with a self-authored
  critic-style verdict ("Critic: fail. Reason: ..."), identical-to-the-microsecond
  started_at/finished_at (impossible for a real multi-minute run), zero token accounting, and a
  `model_used` string in a different format than the orchestrator ever writes.

**Why this matters:** `CLAUDE.md` states the ledger is written only by the orchestrator and is
the append-only source of truth ("if it's not in the ledger, it didn't happen"). A worker able to
write its own verdict into that table can mark bad work "pass" and the fitness metric would
believe it — this specific instance happened to self-report "fail" (and the underlying research
content was actually accurate — verified by hand: PromptBase homepage and one product URL were
live and matched the claimed price), so no bad data reached the record, but the CAPABILITY is the
bug regardless of this instance's luck.

**Root cause:** (1) worker toolset was never restricted — `hermes_worker()` didn't pass `-t`, so
it inherited the default `cli` platform toolset with file/terminal/code_execution all enabled;
(2) the worker prompt handed over the full mission file, including paths and instructions that
only make sense as the orchestrator's own responsibilities.

**Fix (orchestrator/batch_runner.py):**
- `hermes_worker()` now passes `-t web`, restricting the session to web search/scraping only —
  no file, terminal, code_execution, memory, cronjob, delegation, or computer_use tools.
- The prompt now sends only a one-line mission objective (`mission_objective()`, parsed from the
  `## Objective` section) plus the specific task spec — never the full mission file, never any
  internal path or schema reference — and explicitly states the session has no file/write tools.
- Defense in depth: even if the toolset restriction were ever misconfigured, the prompt no longer
  contains enough information for the model to know where to write anything.

**Cleanup performed:** deleted the fabricated ledger task_id=6; purged the 19 entities + ~19
facts written via the unauthorized path from `ledgerbook.db` (the underlying data looked accurate,
but it bypassed the critic and the orchestrator's write path, so it doesn't get to stay just
because it was lucky); moved the two rogue workspace files aside as evidence
(`workspace/_quarantine/`). Kept task_id=2 (the orchestrator's own legitimate run) as-is.

**Verification:** re-ran one live task with the fix; confirmed no new/unexpected rows appeared in
either database and no files appeared outside the orchestrator's own write path. See commit
history for the exact before/after.

**Lesson:** giving an agentic worker your own internal architecture as context is an invitation,
not documentation — anything a capable tool-using model reads as "the plan" it may simply execute,
whether or not that was addressed to it. Least-privilege toolset restriction is not optional for
any worker invocation, even ones you trust; the prompt-content boundary is a second, independent
layer, not a substitute for it.

## 2026-07-18 — follow-on: toolset flag doesn't restrict, classifier trusted an unverified field

Two more issues surfaced while verifying the fix above, both from the same root cause: trusting
something whose real behavior was never actually confirmed.

1. **`-t web` does not restrict to a "web" toolset.** Tried it as the fix for the incident
   above. It broke web search outright (`Tool 'web_search' does not exist`) while STILL exposing
   `terminal`/`python`/`write_file`/`memories` — i.e. it changed the tool inventory to something
   else entirely, not a restriction to search-only. Real web research in this agent runs through
   `browser_*` tools (navigate/snapshot/click), which are part of the unrestricted default set.
   Reversed the flag; containment now rests on (a) the no-path prompt from the incident above,
   verified to hold across 5 live runs (worker asked for missing context rather than exploring
   the filesystem for it), and (b) the integrity guard below, which does not depend on Hermes's
   toolset semantics at all.
2. **`usage.json`'s `completed: false` is not a reliable failure signal.** The classifier treated
   `completed is False` as an infra failure. Observed it False on a fully-formed, 3119-token,
   well-sourced brief (90 real browser calls) that was then discarded — the exact "N claims need
   N probes" mistake: I asserted a meaning for a field I'd never verified. Fixed: only
   `usage["failed"]` (explicit) or actual empty/error-text output now classifies as a failure.
   Also fixed: full raw worker output is now always persisted to
   `runs/task<id>_worker_raw.txt` regardless of classification, so a future misclassification
   doesn't destroy the only evidence that would reveal it.

**Verification:** reset task 2 (PromptBase) and re-ran with all fixes together — real browser
research, zero DB writes outside the orchestrator (integrity guard silent), fair critic scope,
correct baseline framing, verdict PASS on real merits. See workspace/shopify/ for the output.

**New standing defense: `db_integrity_check()`.** Snapshots ledger.db + ledgerbook.db row counts
immediately before every worker call and compares immediately after (before the orchestrator's own
write). Any diff is quarantined (dumped to `runs/quarantine_*.json`, deleted from the live DB) and
escalated. This does not depend on correctly guessing Hermes's tool-permission flags — it catches
the failure class directly, which is why it is the real containment, not the toolset flag.
