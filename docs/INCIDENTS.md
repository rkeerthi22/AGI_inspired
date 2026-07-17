# Incidents

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
