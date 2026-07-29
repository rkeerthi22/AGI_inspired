# Incidents

## 2026-07-29 — The containment guard reverted the assistant's own uncommitted work

**What happened:** a mission fire was triggered by hand to verify H7's new skill-injection log line
emits in production. It did — `task 18: injecting 1 approved skill(s), 346/2000 chars: [...]` — and
the task completed `done`/`pass` with 26 facts extracted. While it ran, the F35 fix and its
documentation were being written in a separate process. Eleven minutes in:

```
[19:08:00] FILESYSTEM INTEGRITY VIOLATION during task 18 worker call:
           [' M docs/HARDENING.md', ' M orchestrator/batch_runner.py']
[19:08:00] reverted tracked changes via git checkout
```

H9's `fs_integrity_check()` snapshots `git status` before the worker call and compares afterwards,
attributing any difference to the worker. It cannot distinguish *who* made a change. The "tampering"
it detected and destroyed was legitimate uncommitted work by this session.

**Impact:** the F35 code fix and all F35 documentation were discarded. `git checkout` on a tracked
file is unrecoverable — there is no reflog for a working-tree discard. Both were rebuilt by hand from
session context and re-verified (9/9 F35 assertions pass again). Nothing was permanently lost, which
was luck rather than design: had the context been trimmed first, the work would simply be gone.

**Why it never showed up before:** every previous F14/H9 test planted tampering deliberately
(2026-07-24, both tamper tests). The failure mode requires a *legitimate* concurrent editor, which
only exists when a human or assistant edits the repo while a fire is in flight. Hand-triggering a
fire mid-session is exactly the scenario the tests never covered.

**Not fixed in code, on purpose.** Narrowing the guard — ignoring "operator-ish" paths, or diffing
only files the worker could plausibly reach — weakens the one real containment boundary for F14, to
buy operator convenience rather than to close a security gap. That trade is easy to get wrong and the
downside is silently re-opening the hole the 2026-07-18 rogue-write incident is named for. Recorded
as **F36** with candidate directions (stash/restore around the call, hash-based attribution to a
pre-call commit, or refusing to start a fire on a dirty tree) and no implementation, because none has
been tested against real failure evidence yet.

**Lesson, and it is the cheap one:** commit before triggering a fire, and do not edit tracked files
while one is running. `runs/`, `workspace/` and `ledger/` are gitignored, so agent output is
unaffected — this is purely about the repo's own source and docs. Note also that the guard **worked**:
it detected an unexpected modification to protected harness files during a worker call and reverted it
within seconds, which is precisely the behaviour F14 demanded. A guard that fires on the wrong culprit
is a precision problem, not a broken guard.

## 2026-07-29 — A synthesis task had been researching the wrong subject for three weeks, and nothing noticed

**What happened:** asked to raise throughput, the first step was reading the ledger rather than the
code. Every synthesis task in the project's history had failed or never run — mission 001's task 27,
mission 002's tasks 14, 22 and 30. "Failed every attempt" is not a quality problem, it is a
structural one, so the artifacts were opened instead of the verdicts being believed.

Task 30's deliverable describes a channel called **"AI News Recap"**. Mission 002 tracks *The Story
Engine* and an *AI-Productivity* channel. It is not one of them. The citations are corticallabs.com,
bbc.com and a Google blog post about self-healing roads — generic AI news with no relationship to the
operator's channels at all. A task written to combine two existing briefs had gone off and done fresh
web research on an unrelated topic.

**Root cause:** `seed_is_synthesis()` tested `startswith("synthesis")`. The seed reads *"Cross-channel
synthesis: …"*. One word of prefix meant the tool-free synthesis path was never taken, and the seed
ran through the full browser worker every week (F30).

**Two more defects were sitting on top of it, each independently sufficient to fail the task:**

- The recorded failure reason was `MECHANICAL FAIL: 4/8 cited URLs unreachable`. All 8 extracted URLs
  ended in a backtick — the worker had written its citations inside markdown code spans and the URL
  regex swallowed the closing delimiter. Measured after the fix: `dead_frac` **0.50 → 0.12**, no hard
  fail. This is the **third** instance of that same regex bug (F23c was `<br>` tags), which is the
  actual lesson: it was fixed one character at a time twice, and is now fixed as a class (F29).
- Task 27, the *tool-free* synthesis, was being graded on "a review-sentiment signal … for each
  tracked competitor" — live lookups it is forbidden to perform. **No output it could produce would
  have passed.** Re-judged on its unchanged bytes with a scope note: `fail` → `pass` (F31).

**Impact:** W31 went from completion 43% / F=0.60 to **86% / F=0.75**, with no deliverable edited and
nothing re-run — the entire difference was harness defects being recorded as analyst failures. Three
lesson-candidates derived from those bogus verdicts were retracted before the promotion review ran;
left in the pool they would have drafted skills teaching the analyst to work around bugs that had
just been fixed, then injected them into every future prompt for that mission.

**Deliberately NOT laundered into a pass:** task 30 stays FAILED. Its mechanical reason was ours, but
its content really is wrong — it researched the wrong subject. Only the recorded *reason* was
corrected, with an audit note; the task needs a genuine re-run now that F30 routes it correctly. The
F29 fix was also checked specifically against the two citations that *should* fail — a literal
`watch?v=...` placeholder and a `/.../` elided path — to confirm it rescued neither.

**Lesson:** "it fails every time" is a routing/spec signal, not a quality signal. Three weeks of
weekly failures were read as an analyst that could not synthesise, when the analyst had never once
been asked to. The ledger showed the pattern immediately; reading the deliverable rather than the
verdict is what explained it. Also: a mechanical check that can hard-fail a deliverable **without any
model call** is a high-privilege component — a regex in it issues verdicts, and it has now been the
proximate cause of a false rejection three separate times.

## 2026-07-28 — The citation checker failed correct work, and the harness recorded it as the analyst's failure

**What happened:** the F20 proof run completed (tasks 24, 25 — 813s and 685s, no timeout) and both
FAILED review. The critic's reason was damning and specific: *"multiple high-confidence facts are
cited to URLs that do not contain the claimed values"* — the signature of a fabricating worker.
Before accepting that, the newly-persisted critic reasoning trace (its first real use) was read in
full, and it contained the critic second-guessing its own evidence: *"the mechanical checker
fetches pages directly, which may also fail to render JS. So the absence of values on rendered
pages could be a shared limitation."* That doubt was worth a free probe. Fetching the two flagged
pages showed both were server-rendered and **did** contain the claimed values.

Root cause was two defects in `citecheck.py`, built in Phase 1 as the answer to F3:
`MAX_BYTES = 20_000` meant only a prefix of each page was searched (`promptbase.com/apps` is
232,645 chars; the claimed "4.9" is at char 85,999 — 9% of the page was read), and the literal test
was a bare substring match that broke on meaningless presentation differences (`$14` when markup
separates symbol from number; `42,000` vs `42000`).

**Impact:** re-running the corrected checker against the **unchanged** deliverables: 14 of 15
citations verify, `dead_frac` 0.07. Re-judged on corrected evidence, both deliverables **PASS**.
A full mission day had been scored 0% for a bug in the grader. The W31 fitness record went
0.0 → 0.286 completion, F 0.35 → 0.45, purely by correcting the judgement — no work was redone.
Verdicts were corrected in place with an audit note appended (F18 convention), token accounting and
`finished_at` preserved.

**Also found the same night:** `policy.tokens_used_today()` filtered on `created_at`, so it measured
tokens *belonging to tasks created today* rather than tokens *spent today*. The 02:15 run burned
7,219,268 tokens on tasks created the previous day and the guard reported 0 — blind to the entire
night's spend, and willing to authorise a second full budget on top. Fixed to `finished_at`
(F22). That is the third independent defect in the same budget guard (F8, F21, F22).

**Lesson:** a verification mechanism that errs in the *accusatory* direction is worse than none,
because its output is indistinguishable from the failure it claims to detect — "cited value not
found on page" reads exactly like fabrication, and there is no way to tell the difference without
going and looking. Two things saved it here, both cheap: the reasoning trace preserved the critic's
own doubt instead of discarding it with the rest of the deliberation (the one-sentence summary
would have read as a confident, closed case), and the flagged claim was checkable for free with a
single fetch. When a grader reports systematic failure across many independent items, suspect the
grader before the work — and note this one had never been tested against a page larger than its own
read cap.

## 2026-07-27 — W31's first real run scored 0/3 because the worker was graded on a spec it never received

**What happened:** The Monday 04:00 `AGI_M1_shopify` cron fired correctly (`Last Result: 0`, log
`runs/batch_20260727_040003.log`, 04:00→04:21) and produced the mission's worst possible outcome:
**all three attempted research tasks failed review**, fitness 0.35 with completion 0%. Reading the
critic's stated reasons made the pattern obvious — task 24 "omits the required top 'Changes since
last week' diff section… fails to flag any new products with 'NEW'… provides only one product URL
instead of the required ≥2", task 25 the missing diff section, task 26 the diff section plus wrong
document structure ("organized thematically rather than with one section per tracked competitor").
Every single objection cited a requirement from the mission's `## Done-definition`. The worker's
prompt contains no part of that section: `run_task()` passes `mission_objective()`, which extracts
only `## Objective`. The critic, meanwhile, receives `row['pass_criteria']` — the full
done-definition. The two halves of the loop were reading different documents, and the analyst was
being marked down for not satisfying requirements it had no way to know existed.

**Root cause:** an asymmetry introduced by a *previous, correct* security fix. After the 2026-07-18
rogue-write incident the worker prompt was deliberately reduced to a path-free one-line objective
("prevention by ignorance"), because the mission file names `workspace/…` and `memory/ledgerbook.db`
and a tool-holding worker will act on paths it is shown. That fix protected containment but silently
took the *output specification* with it, and nothing on the critic side was narrowed to match. The
gap survived because the failure mode looks exactly like ordinary poor analyst work — a plausible
0% that invites tuning the worker rather than auditing what the worker was told.

**Fix applied:** `deliverable_requirements()` (`orchestrator/batch_runner.py`) extracts the
done-definition and strips every line naming an internal path or schema via `_INTERNAL_CRITERIA_RE`,
dropping each matched line's continuations and sub-bullets so a requirement is never half-delivered.
Injected into the worker prompt ahead of `baseline_note` so a first-ever run's "no week-over-week
diff" exception still overrides the diff requirement. Verified before going anywhere near a live
run: leak assertions on both real missions (zero internal strings survive), then full prompt
assembly through the real `run_task()` against a **copy** of `ledger.db` with `escalate()` stubbed —
14/14 assertions including containment and compliance blocks still intact. Tasks 24/25/26 re-queued,
status-only, with `critic_verdict`/`critic_notes` preserved so the retry replays the reviewer's
exact objections.

**Also fixed this session:** `ollama_chat()` was discarding `message.thinking`. Verified live by
calling `/api/chat` twice, with and without the API's `think` flag — `glm-5.2:cloud` returns a
populated reasoning trace either way, so there was never a "high-tier reasoning mode" to enable; the
harness was simply throwing the trace away. Critic calls now persist it to
`runs/task<id>_critic_reasoning.txt` (file only — never fed back into a prompt, per F10). Confirmed
with a real call: 1,222 chars captured, verdict parsing unaffected.

**Lesson:** when a security fix removes context from a prompt, something downstream is still
grading against the removed context — check both sides of every evaluator/executor pair in the same
session, because the resulting failure is indistinguishable from genuine incompetence. More
generally: a 0% completion rate is evidence about the *harness* at least as often as about the
model. The first question on a total failure should be "was the thing being graded ever told the
rules," not "why is the model bad" — reading the critic's own stated reasons answered this in
minutes, and they were sitting in `critic_notes` the whole time.

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

## 2026-07-27 — follow-on: the battery fix left the real ignition blocker untouched

**What happened:** Independently re-checked all 5 `AGI_M1_*` tasks after the battery-flag fix
above landed (commit `ff0013a`) and found `Principal.LogonType = Interactive` on every one of
them — unchanged by that fix, and never mentioned by its diagnosis or its docs. `LogonType:
Interactive` requires an actual unlocked interactive desktop session to exist at fire time; with
nobody logged in (locked screen, or logged off), Task Scheduler refuses the launch with the exact
same error this incident already named — Win32 4320 / `0x800710E0`, "the operator or
administrator has refused the request." Confirmed this is a second, independent, well-documented
cause of that identical error code (not a guess): see [Fix: Operator Refused the Request Error in
Windows Task Scheduler](https://www.geeksforgeeks.org/techtips/operator-refused-request-error-in-win/),
whose fix #2 is switching the task to "run whether user is logged in or not" — i.e. changing
`LogonType` away from `Interactive` (to `S4U`, which needs no stored password).

**Why this matters:** the battery fix and this fix are two separate necessary conditions for the
same symptom, not one fix and a rediscovery of it. A pass that only closes the power-conditions
path can genuinely believe "ignition is fixed" — settings re-read correctly, triggers intact,
commit written, docs updated — while the crons are still exactly as capable of silently refusing
to fire on a future Sunday when the laptop happens to be locked instead of on battery. Two
independent locks were installed here; only one had a fix applied to it and be verified.

**Fix status: APPLIED + INDEPENDENTLY VERIFIED 2026-07-27** (see the closing note at the end of
this file's second follow-on entry). Originally blocked on privilege — changing a
scheduled task's `Principal`/`LogonType` requires an elevated session; a non-elevated
`Set-ScheduledTask -Principal ...` call returned `Access is denied` (confirmed live:
`whoami /groups` shows this shell's `BUILTIN\Administrators` membership is "Group used for deny
only" — the token is UAC-filtered). This needs the operator to run, in an elevated PowerShell:

```powershell
# Refuse to run non-elevated -- the whole point of the 2026-07-27 follow-on below is that a
# non-elevated run FAILS PER TASK while still printing a plausible-looking summary.
$pr = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host "NOT ELEVATED - open PowerShell as Administrator and re-run. Aborting." -ForegroundColor Red
  return
}
# Enumerate rather than hardcode: one-time tasks get added (e.g. AGI_M1_F20proof, 2026-07-28)
# and a fixed 5-name list silently leaves them on Interactive.
$ok = 0; $bad = 0
foreach ($t in (Get-ScheduledTask -TaskName "AGI_M1_*").TaskName) {
  try {
    $principal = New-ScheduledTaskPrincipal -UserId "moham" -LogonType S4U -RunLevel Limited -ErrorAction Stop
    Set-ScheduledTask -TaskName $t -Principal $principal -ErrorAction Stop | Out-Null
    Write-Host "  OK    $t" -ForegroundColor Green; $ok++
  } catch {
    Write-Host "  FAIL  $t -- $($_.Exception.Message)" -ForegroundColor Red; $bad++
  }
}
Write-Host "applied=$ok failed=$bad"
# Independent re-read -- believe THIS, not the loop above.
Get-ScheduledTask -TaskName "AGI_M1_*" | Select-Object TaskName, @{n='LogonType';e={$_.Principal.LogonType}}
```
Every row must read `S4U`. Any row still reading `Interactive` means that task was not changed,
regardless of what the loop printed.

**Lesson:** "verified by re-reading each task's settings" only verifies what you re-read. The
original health check's own root-cause claim (`DisallowStartIfOnBatteries=true`) was never
cross-checked against the *other* documented cause of the identical error code, so the fix closed
the diagnosed cause without checking whether it was the *only* cause. Same shape as the lesson
just above it in this file, one incident later: a control existing (or now correctly configured)
is not the same as it being the complete set of controls that gate what depends on it.

## 2026-07-27 — second follow-on: elevation self-trigger failed; a reported fix didn't verify

**What happened:** Two more attempts at closing the LogonType gap above, both worth recording.

First, tried triggering the elevation from inside the session itself via `Start-Process
powershell -Verb RunAs -Wait -EncodedCommand ...`. Windows returned `Start-Process : This command
cannot be run due to the error: The operation was canceled by the user.` almost immediately —
consistent with there being no interactive desktop session available to render the UAC consent
dialog on (the exact condition this whole finding is about), though a real dismissal can't be
ruled out either. Either way: self-elevation from this session is not a working path.

Second, the operator reported running the elevated fix manually and having all 5 tasks read
`S4U`. Independent re-verification immediately after — via `Get-ScheduledTask` (with a forced
`Remove-Module`/`Import-Module ScheduledTasks -Force` first, to rule out a stale cmdlet cache) AND
separately via `schtasks /query /tn ... /xml` (a completely different code path, reading the Task
Scheduler service's raw XML directly) — showed `LogonType` still `Interactive` /
`<LogonType>InteractiveToken</LogonType>` on every task checked. Not a caching artifact: two
independent read paths agreed with each other and disagreed with the report.

**Why this matters:** same shape as F18 and the HC-verification pattern elsewhere in this
project — a report of success is not evidence of success. The most probable explanation is a
non-elevated run: `Set-ScheduledTask -Principal` under a normal token throws `Access is denied`
per iteration inside the `foreach`, which does not halt the loop (no `-ErrorAction Stop`), so the
loop completes, the trailing `Get-ScheduledTask ... LogonType` line runs and prints `Interactive`
for all 5 (unchanged) — easy to misread as `S4U` at a glance if skimmed quickly. A genuinely
elevated run silently no-op'ing (e.g. a missing "Log on as a batch job" right) remains possible
but unconfirmed.

**Status: RESOLVED 2026-07-27, verified — this entry's own protocol is what closed it.** The
command was re-issued with explicit per-task try/catch, plus two further hardening changes made
after this entry: it now (a) refuses to run when not elevated, instead of failing per-task and
printing a plausible summary — the exact shape of the false positive above — and (b) enumerates
`Get-ScheduledTask -TaskName "AGI_M1_*"` rather than hardcoding five names. (b) mattered
immediately: a sixth task (`AGI_M1_F20proof`, the one-time F20 proof run scheduled for 02:15 the
same night) had been registered in between, and the hardcoded version would have left exactly the
task that needed to fire that night still on `Interactive`.

The operator re-ran it elevated and reported success. Per this entry's own lesson that report was
NOT taken as closure: live state was re-read through both independent paths that exposed the first
false positive — `Get-ScheduledTask` after a forced `Remove-Module`/`Import-Module ScheduledTasks
-Force`, and `schtasks /query /tn … /xml`. **Both return `S4U` for all 6 tasks.** Also cross-checked
that the principal change disturbed nothing else (the failure mode this file already warns about —
"re-reading each task's settings only verifies what you re-read"): all triggers/next-run-times
intact, `DisallowStartIfOnBatteries`/`StopIfGoingOnBatteries` still `False` with
`StartWhenAvailable=True` (the A1 battery fix survived), action lines unchanged, all tasks `Ready`.
Both independent causes of the Win32 4320 refusal are now closed.

**Residual, worth knowing:** `Register-ScheduledTask` defaults new tasks to `Interactive`, so any
future task silently reintroduces this gap for itself. The standing rule is now recorded in
`missions/_M1_INDEX.md`: re-read `Principal.LogonType` after adding or re-registering any task.

**Lesson:** when a fix depends on privilege the assistant cannot itself confirm was actually
exercised (elevation happens in a separate, unobserved window), do not accept "ran it, looks
fixed" as closing the loop — re-read the live state through an independent code path before
updating any status field. Two read paths that disagree with a report beat one report, every time.

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
