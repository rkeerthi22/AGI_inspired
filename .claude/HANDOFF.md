# Handoff — AGI_like (M1 research/BI analyst harness) · updated 2026-07-31

Registry source of truth: `S:\AGI_like\docs\HARDENING.md` — **F1–F53 + F22b, 54 entries**, no gaps
(verified `grep -cE '^### F[0-9]+[a-z]? '`). F1–F42 were originally reconstructed from HARDENING.md
entries on disk, not from memory; F43–F53 were written as they landed.

**⚠ 5 COMMITS UNPUSHED.** `local master = afc67b6`, `origin/master = 613c950`. Everything from
`9416aca` onward — including **F53, the whole execution-only directive, and this handoff** — exists
only on this laptop. The remote was fully verified earlier this session, so this is drift, not a
broken setup: one `git push origin master` closes it. Auto-mode blocks the assistant from running
`git push`; it is an operator command. See §5 item 4.

**Working tree carries 2 untracked files ON PURPOSE.** `orchestrator/simulate.py` and
`docs/HANDOFF_SIMULATION.md` came from a parallel Hermes session; the operator has since confirmed
they were **requested**, and instructed that they stay **uncommitted for now**. This is an expected
state, not a containment incident — see §5 item 0, which is resolved rather than open. Because
`orchestrator/` is a PROTECTED_PATH, `simulate.py` will keep appearing in `_untracked_files()`
snapshots; that is the guard doing its job and should not be chased.

---

## 1. Architectural decisions

- **Ollama-only model stack, accepting quota-parked stretches** — operator chose this over adding an
  Anthropic key. `config/models.yaml` keeps the Anthropic rung pre-wired and commented. **LOCKED**
  (CLAUDE.md); not a re-decision, just uncomment when that changes.
- **Fitness weights `W = {completion 0.35, accuracy 0.30, intervention 0.25, cost 0.10}` fixed for
  8 weeks** (HARNESS_DESIGN §3.2). Every fix this session corrected the DATA feeding the formula,
  never the formula. **LOCKED** — re-weighting mid-window invalidates the comparison the 8 weeks exist
  to make.
- **Skill promotion is operator-gated; drafting is automatic, approval never is.** **LOCKED.**
- **Model choice lives in config, never in code** — F38 was therefore fixed as an Ollama *model
  variant* (`gemma4:12b-ctx4k`) rather than by passing `num_ctx` in Python, so it fixes the hermes CLI
  and `ollama_chat()` at once. **DECIDED.**
- **The local fallback rung completes work rather than parking it** (operator's F9 call) — **but is
  excluded from canaries** (F40). The line that matters is not "graded" but *"grades an automated
  self-modification decision."*
- **Containment is the boundary, not trust.** Toolset flags (`-t web`) do not restrict Hermes tools;
  `db_integrity_check()` + `fs_integrity_check()` are the real guards. **LOCKED** (CLAUDE.md).
- **`quota_group` is opt-in, absent by default** (F39) — an undeclared model is never skipped by
  inference, so a mis-declaration can only waste a call, never skip a good rung.
- ~~**Offsite backup built but left UNCONFIGURED**~~ → **CONFIGURED to OneDrive 2026-07-30**
  (operator reversed the 2026-07-29 call, in two steps: the repo bundle first, then the databases).
  `config/offsite_backup.path` → `C:\Users\moham\OneDrive\AGI_like_backups`, so the nightly
  `AGI_M1_backup` replicates **both** DB snapshots and the repo bundle. Classified `sync`, which the
  tool states plainly: it leaves the machine once the client uploads, and completion of that upload
  is **not verifiable from here**. **The pre-upload secret scans are the precondition for repeating
  this** — 490 git objects across all history, plus 1,537 text cells across both live DBs: no
  credential-shaped strings, no emails, `.env` never tracked in any commit. **DECIDED.**
- **Regression suites live in the repo at `S:\AGI_like\tests\`, and `tests` is in `PROTECTED_PATHS`**
  — they were stranded in two different session temp scratchpads, one of which had already vanished,
  so every "N suites green" claim cited evidence unrunnable from a clean checkout. A worker must not
  be able to weaken the suites that check the worker — same argument F42 made for `.gitignore`.
  **DECIDED 2026-07-30.**
- **This handoff is TRACKED, and `.claude` is a `PROTECTED_PATH`** (F52, 2026-07-30). It was untracked
  until the DR drill showed a full restore recovers the repo, both databases and every verdict — but
  not the one document explaining what any of it means. Committing it had an immediate cost that had
  to be paid in the same breath: while untracked it was guarded by `_untracked_files()`'s hashes, and
  tracking it moved it out of that set without moving it into `_tracked_hashes()`. **Changing a
  file's VCS status silently changes which guard covers it.** Both halves are now required together.
  **DECIDED.**
- **A nested worktree stays COLLAPSED in the untracked scan, IF one exists** — recorded as one
  `<nested-repo>` marker: appearance/disappearance detected, internal churn not. Expanding it would
  point a delete-on-sight remediation at a live git checkout whose `.git` internals and `__pycache__`
  change for legitimate reasons, buying visibility into a directory the harness never executes from.
  **DECIDED 2026-07-30 (operator).** The design is unconditional; the instance is not — see below.
  - **Residual CLOSED 2026-07-30.** `git worktree remove .claude/worktrees/jolly-gauss-8e52cb` (clean,
    detached at `8f9338b`, no `--force`) + `git worktree prune`. Re-measured:
    `_masked_under_protected()` → `[]`, one worktree in `git worktree list`, `.claude/HANDOFF.md`
    still in `_tracked_hashes()`. `tests/test_f52.py` §3/§5 hardcoded the worktree's *presence*, so
    removal alone would have flipped 3 assertions red; both now branch on live git/snapshot state
    (present → one collapsed marker, absent → zero entries / nothing masked) so the rule is tested in
    either world. **16/16 suites green** after the rewrite.
  - **`.git/info/exclude`'s `.claude/worktrees/` line left in place — deliberate non-action.** Deleting
    it buys nothing once the masked set is already empty, and an unversioned exclude source is exactly
    the surface F47 exists to watch — not touched without a measured reason. **DECIDED.**
- **F47 remediation rewrites only exclude sources INSIDE the repo.** The global ignore is the
  operator's personal config; silently editing a file outside the project is more than a containment
  guard should do unasked. Out-of-repo tampering is detected, escalated, and left alone. **DECIDED.**
- **A silent cap is a bug even when it is not yet biting** (F49/F50/F51, 2026-07-30). Three input
  paths clipped their material with no marker: briefs, model context, facts. The damage is never the
  clipping — it is that the model reasons *correctly* from "absent" when the truth is "withheld", and
  reports a data gap that sends the operator to re-research what the harness already holds. Every cap
  must therefore either state its omission in-band or provably not bite. **DECIDED.**
- **F50 fixed by declared context, NOT by `allow_local=False`** — the tempting one-liner (F40's tool)
  encodes the wrong cause. Locality is not why the rung failed; a 4,096-token context is. Keying on
  `context_tokens` skips a small *cloud* rung too and keeps a future large-context *local* one.
  Opt-in exactly like `quota_group`: undeclared is never skipped. **DECIDED.**
- **F51 separates SELECTION from PRESENTATION** — the fact ledger takes the newest `cap` rows
  (`id DESC LIMIT`) and then displays them grouped by entity. The old single `ORDER BY entity, id`
  conflated the two, so overflow dropped the alphabetical tail: the same entities every time, chosen
  by name rather than age. Truncation should drop the least recent; reading order should stay
  grouped. Neither had to be traded for the other. **DECIDED.**
- **WEEKS 4–8 ARE EXECUTION ONLY — no new features, no new hardening, no refactors.** **LOCKED
  2026-07-31** (`CLAUDE.md`, its own directive section, commit `afc67b6`). Rationale, agreed on
  measured evidence: the harness rates ~**6/10** against a finished system, but lopsidedly —
  self-hardening is 8–9/10 (54 registry entries, 17 green suites, guards that caught real
  violations, DR drilled), while *evidence the employee is improving* is 2–3/10. The bottleneck
  stopped being system quality and became **data**. Every hardening fix now has diminishing returns
  against 17/17 green; every week without independent verdicts costs a data point that cannot be
  recovered, because the 8-week window closes on schedule regardless of what is in it.
  - **One standing exception, deliberately narrow:** a defect *actively corrupting the data being
    collected* is still P0. A harness that silently poisons its own evidence defeats the point of an
    execution-only phase. Bar is "this run is producing false numbers **right now**", not "this
    could be better". F53 is the archetype — found by measuring, not by anything failing.
  - **Reconciled, not left to contradict:** the 2026-07-29 expanded-budget directive told a future
    session to *"fix the synthesis problem, draft skill candidates"* — both building. Its header now
    carries a **SUPERSEDED IN PART** note: items 1/2/4 (batch sizes, faster retries, spot-check
    backlog) survive as execution; items 3/5 are PAUSED.
- **Fitness reports which of its terms actually measured anything** (F53). `weekly_fitness()` returns
  `intervention_measured` / `cost_measured` / `fitness_floor`, and the scorecard prints
  "⚠ 0.35 of F was awarded unconditionally". **W is untouched — LOCKED**, asserted by
  `tests/test_f53.py`. This is the F7/F45 honesty fix applied to a *numerator* rather than a
  denominator: the score was never wrong, it just was not what it looked like. **DECIDED.**
- **`cost_eff` stays unmeasurable and LABELLED, never faked** (F53). Ollama genuinely reports $0 on a
  flat subscription; deriving a per-task dollar figure would swap an honest constant for a dishonest
  variable. It is reported as not-measured, not invented. **DECIDED.**
- **F53 was NOT backfilled, and the discontinuity is stated rather than smoothed.** W29–W31 recorded
  `interventions=0` because nothing *could* write the column — a structural artefact, not a
  measurement. The first post-F53 week will likely show fitness **DROP**, meaning the metric went
  live, not that the analyst got worse. `intervention_measured` exists so that distinction survives
  into the scorecard. **DECIDED.**
- **Run-scoped escalations do NOT count as interventions** (F53) — "ollama unreachable", "batch
  aborted" pass no `task_id`. Infrastructure failure is not a verdict on any one task's autonomy;
  same line F37 draws between infra failure and the analyst being wrong. **DECIDED.**
- **Simulation layer authorized, overriding HARNESS_DESIGN §5's DEFER verdict** — operator's
  reasoning, recorded verbatim in §5 of that doc: *"an AGI would likely predict human behavior more
  accurately."* That reframes simulation from cost-optimization (§5's original framing) to a
  capability claim, under which the M3 gate never applied. `orchestrator/simulate.py` stays
  **UNCOMMITTED by instruction**. **DECIDED 2026-07-31.**
- Rejected: **adding `"."` to `PROTECTED_PATHS`** to close the root gap (F42) — `memory/` and
  `workspace/` are policy.yaml *writable* roots inside the repo root, so `"."` declares the same
  subtree both writable and protected, and `policy.validate_paths()` compares literal paths so it
  would **not** catch the contradiction. Used a depth-0 root scan instead.
- Rejected: **narrowing the fs-guard to infer *who* edited a file** (F36 attribution half) — trades
  containment for operator convenience; getting it wrong silently reopens the 2026-07-18 rogue-write
  hole. Blast-radius and recoverability were fixed; attribution deliberately was not.
- Rejected: **a verb ban on "visit/fetch/run" in skill notes** (H7 as originally specified) — the
  project's own first approved skill legitimately says *"open every cited URL"*. Bans concrete
  **targets and execution** instead.
- Rejected: **`glm-5.2:cloud` as the quota fallback** — it is already chain rung 1, 429 is
  account-level, and it is the manager/critic model. Measured 429 on it twice on 2026-07-29.
- Rejected: **lowering `MIN_EVIDENCE_ROWS` or inventing a mission** to produce a "four-skill"
  promotion pass — structural ceiling is 2 active missions × `MAX_CANDIDATES_PER_MISSION=1`.
- Rejected: **gitignoring `.claude/`** to stop the guard tripping over it (F46) — that makes the
  blind spot explicit rather than closing it, and `.claude/` is the one tree whose contents steer the
  supervising agent.

---

## 2. Fix registry

F1–F42 reconstructed from `docs/HARDENING.md` on disk; F43–F45 from the 2026-07-30 overnight session;
F46–F52 from this session.

- **F1** Concurrent runs cause silent data loss + false security alarms → no single-writer discipline → run-lock (`orchestrator/runlock.py`) + provenance-based integrity via `run_id`. P0 · PROVEN
- **F2** Power loss orphans tasks permanently → no code path ever read/reset `status='running'` → lease-based crash recovery, `reconcile_interrupted_tasks()`. P0 · PROVEN by absence
- **F3** The critic validates FORM, not TRUTH → tool-free critic cannot verify a cited URL exists → mechanical `citecheck.py` evidence table fed to the critic. P1 · REASONED
- **F4** Brittle verdict parse silently inverts good verdicts → `.startswith("PASS")` broke on markdown/think-blocks → tolerant regex; unparseable ⇒ `needs_review`, never a silent fail. P1
- **F5** Retry loop makes the critic self-anchoring → manager == critic (same model, by locked choice) → critic kept blind to its own prior notes; citecheck is the independent signal. P1 · REASONED
- **F6** Head-of-line blocking starves later seeds → run queue in fixed seed order, so a parked seed kept first crack → order by `(started_at IS NOT NULL, task_id)`. P1 · FIXED 2026-07-27
- **F7** Metric integrity inverts under scarcity → `stale`/`queued` rows left out of the denominator → completion measured against everything SCHEDULED. P1 · PROVEN
- **F8** The cost cap is decorative → nothing read `policy.yaml` cost caps → token-based daily cap enforced; USD half deferred (Ollama reports $0). P1 · PROVEN
- **F9** Cross-provider failover is config-only → `fallback_chain` declared but no code read it → `worker_with_failover()` / `synthesis_with_failover()`. P2 · FIXED 2026-07-27
- **F10** Indirect prompt-injection into all future prompts → hostile page → lesson → skill note → every future prompt → closed by **H7** (see registry entry below / HARDENING). P2 · REASONED
- **F11** Default SQLite busy timeout (5s), no retry → every `connect()` omitted `timeout=` → `timeout=30` + retry. P2
- **F12** `_conn(db=LEDGER_DB)` default-arg binding → import-time binding made the DB path unpatchable → tests can redirect. P2
- **F13** `policy.yaml` is enforced by ZERO code → declared but unread → `orchestrator/policy.py` block-level enforcement + `validate_paths()` drift check. P1 · PROVEN
- **F14** The workspace "container" does not exist in code → §2.6 staked WIDE autonomy on a guard that was never written → `fs_integrity_snapshot/check` over `PROTECTED_PATHS`. P0 · PROVEN
- **F15** `promote.py` commits are not isolated → bare `git commit` swept the whole staged index → explicit pathspec on approve/rollback. P2 · FIXED 2026-07-27
- **F16** The "source of truth" has no second copy → no backup existed at all → `orchestrator/backup.py` via sqlite3 `.backup()` + `AGI_M1_backup` daily. P0 · PROVEN
- **F17** Python-local vs SQLite-UTC clock mismatch → lease computed in Python-local, compared in SQL-UTC (2h skew on this box) → compute boundaries inside SQLite. P1 · found 2026-07-24
- **F18** Task status ignored critic verdict; a REJECTED deliverable read as complete → every resolved task wrote `status='done'` → `status = "done" if verdict=="pass" else "failed"`. P0 · found 2026-07-24
- **F19** F17's clock-domain bug recurred in 3 more call sites → also a `'T'` vs `' '` separator mismatch; live-measured dropping 4 of 7 in-window tasks → `ledger.window_start_sql()`. P1 · fixed 2026-07-27
- **F20** The worker was graded against a spec it was never shown → critic got the full done-definition, worker got one objective line → `deliverable_requirements()` injected into worker prompts. P0 · fixed 2026-07-27
- **F21** A retry erased the previous attempt's accounting AND its review history → consumption columns defaulted to 0/0 and overwrote → `COALESCE(?, col)` + `append_note=True`. P1 · fixed 2026-07-28
- **F22** The daily token cap measured the wrong day → filtered `created_at` (tokens *belonging to* tasks created today) not spend → switched to `finished_at`. P1 · fixed 2026-07-28
- **F23** The citation checker falsely accused correct work of fabrication → `MAX_BYTES=20_000` read 9% of large pages + bare substring match broke on formatting → cap 400_000 + format-tolerant compare. P0 · fixed 2026-07-28
- **F24** The cap could gate but never refuse → post-hoc gate only; a hermes subprocess cannot be halted mid-flight → `budget_insufficient_for()` admission control. P1 · 2026-07-28
- **F22b** Two correct fixes composed into a wrong one → F21+F22 together let *parking* re-stamp `finished_at`, re-dating old spend to today (counter jumped 7,219,268 → 15,743,736 with nothing run) → stamp only on `TERMINAL_STATUSES`. P1 · fixed 2026-07-28
- **F25** Substring matching verified claims that were false → F23's own normalisation let `"19"` match inside `"$194"` → numeric token-boundary regex + confidence-level semantics in the worker prompt. P1 · found by spot-check 2026-07-28
- **F26** Structured (JSON-LD) values invisible to the literal check → only visible text was searched → `_jsonld_text()` merged into the search body. P2 · 2026-07-28
- **F27** Raw substring matching can confirm the right digits for the wrong reason → 4 unrelated matches for "129" on one real page (SVG path, analytics score, pixel dims) → **FOUND, NOT FIXED**; no live wrong verdict yet to design against. P2
- **F28** A spot-check performed by the assistant is schema-identical to an operator's → `human_verdict` cannot express who checked → `"AI-PERFORMED CHECK"` marker + `spot_checked_ai` surfaced in scorecard/Telegram. P1 · 2026-07-28
- **F29** The URL regex bug, third instance: trailing backticks → `_URL_RE` excluded `<>` but not `` ` ``; all 8 of task 30's citations ended in a backtick → excluded every structural markdown delimiter as a class + `_clean_url()`. Measured `dead_frac 0.50 → 0.12`. P0 · fixed 2026-07-29
- **F30** Synthesis seeds silently routed to the browser worker → `seed_is_synthesis()` required the spec to *start with* "synthesis"; mission 002's reads "Cross-channel synthesis: …" → match `synthesi[sz]` in the leading clause. Every 002 synthesis (tasks 14/22/30) had failed for this. P0 · fixed 2026-07-29
- **F31** Every task graded against the whole mission's spec → done-definition describes the COMBINED weekly brief; tool-free synthesis was graded on per-competitor research it may not perform → `task_scope_note()` shared by worker AND critic. Task 27 re-judged `fail → pass` on unchanged bytes. P0 · fixed 2026-07-29
- **F32** A *successful* retry overwrote the failed attempt's token accounting → F21 only covered *omitted* counts → accumulate onto the row's prior total in `run_task()`. P1 · fixed 2026-07-29
- **F33** Synthesis token spend was never recorded, at all → `run_synthesis()` passed no tokens and `ollama_chat()` discarded Ollama's top-level `prompt_eval_count`/`eval_count` → `usage_out` out-param threaded through. Counter moved 4,640,719 → 4,655,381 (+14,662). P1 · fixed 2026-07-29
- **F34** Approving a skill in a week with no canaries silently disarmed its rollback → `_current_canary_green()` returned bare `0`, and rollback fires on `week_green < baseline`, so a baseline of 0 can never trigger → fall back to the last week that genuinely ran canaries; returns `(green, week)`. Both live skills re-stamped to 3. P1 · fixed 2026-07-29
- **F35** Never-attempted work unrunnable forever AND invisible to the score → `expire_stale_parked()` covered `quota_wait` only, so previous-week `queued` rows were reachable by no code path and `dropped` read 0 despite 5 abandoned seeds → expire `queued` too, with a distinct `NEVER ATTEMPTED` note. P1 · fixed 2026-07-29
- **F36** The filesystem guard cannot tell the worker from the operator, and destroys uncommitted work → `git checkout -- *PROTECTED_PATHS` discarded every dirty tracked file on any single violation, unrecoverably → revert scoped to changed paths + originals preserved to `runs/reverted_<ts>/` + detection strengthened to content hashes (catches a re-modified already-dirty file, which porcelain diffing cannot see). **Attribution half deliberately NOT fixed.** P1 · 2026-07-29
- **F37** Infrastructure failure scored as the analyst being wrong, in the one path that deletes skills → `run_canaries()` never called `worker_failed()`, so gemma's `HTTP 500 llama-server startup fail` text was graded as a wrong answer; and F9's failover meant quota exhaustion no longer *parked* (so `week_pending==0` opened the rollback gate on degraded data) → classify infra in the canary path + gate on `week_unjudged == 0`. Green fell 5→3 vs baseline 3; **missed deleting a skill by one canary.** P0 · fixed 2026-07-29
- **F38** The failover chain's last rung had never once worked → not weights-vs-VRAM but the **KV cache**, sized from gemma's default 262,144-token context; neither hermes nor `ollama_chat()` passes `num_ctx` → `config/gemma4-12b-ctx4k.Modelfile` bakes `num_ctx 4096`. Base FAILS with no caller options; variant loads in 103s at 1.5 tok/s. P1 · fixed 2026-07-29
- **F39** "429 is account-level" was a comment no code could read → chain called every cloud rung after the account was already refusing (~30s per canary wasted on `glm-5.2:cloud` right after `kimi` 429'd) → opt-in `quota_group`; a dead group's siblings are skipped. P1 · 2026-07-29
- **F40** The local rung must not grade the system that can delete its own skills → canary green count is the only signal that auto-deletes a skill, and was scored from whichever model was reachable (3/3 cloud passed, 0/2 gemma passed) → `allow_local=False` on the canary path only; quota-exhausted canaries park. P1 · 2026-07-29
- **F41** Locality inferred from a naming convention, not from where the model runs → `_is_local_model()` tested `":cloud" not in model`, so `anthropic/claude-sonnet-5` counted as local → key on `provider` (`LOCAL_PROVIDERS={"ollama"}`) then the suffix. Latent until F40 made it a correctness bug; **found by a test that simulated adding the Anthropic rung.** P1 · fixed 2026-07-29
- **F42** The repo root was outside the containment surface → `PROTECTED_PATHS` listed no root files, so `.gitignore` (which `git status` *honours*, i.e. a self-hiding attack) and `IDENTITY.md` were unprotected, and a brand-new root file matched nothing → added both files + a depth-0 scan for new root entries. P1 · fixed 2026-07-30
- **F43** An infra-failed task could never be retried → the dedup/resume gate in `queue_mission_tasks()` and `run_canaries()` listed `("quota_wait","queued","interrupted")` and omitted `infra_failed`; harmless until F37 started classifying correctly, which made those rows permanently un-retryable → single `RESUMABLE_STATUSES` constant including `infra_failed`, both sites pointed at it. Found concretely: quota reset, operator asked to re-run canaries, **all five would have been skipped**. P1 · fixed 2026-07-30 · `orchestrator/batch_runner.py:1459` · ~~NOT yet written up in docs/HARDENING.md~~ **written up 2026-07-30 in commit `7d453a4`; the registry gap is closed**
- **F44** The daily budget counted a UTC day against local timestamps → `tokens_used_today()` compared `finished_at` (Python-local, `T`-separated) against `datetime('now','start of day')` (UTC, space); its own comment cited F17's lesson while applying it to the wrong column → `replace(datetime('now','localtime','start of day'),' ','T')`, exposed as `policy.today_start()`. Reported **11,390,219 tokens spent on a day that had spent nothing**; third recurrence of the F17/F19 class, and **F22 introduced it**. P1 · fixed 2026-07-30
- **F45** The scorecard's canary line divided by the number that RAN → `canaries_green()` selected `status='done'` only and renderers used `ran or CANARY_TOTAL`, so W31 would publish **`Canaries green: 3/3`** for 3 passed / 2 parked out of 5 → return `(passed, ran, unjudged)`, denominator always `CANARY_TOTAL`. Same vanishing-denominator dishonesty H5/F7 fixed for mission tasks. P1 · fixed 2026-07-30
- **F46** An untracked *directory* reopened F42's hole one level deeper, aimed at the supervising agent → `_root_untracked()` used `git status --porcelain` in its DEFAULT untracked mode, which collapses an untracked directory to one line (`?? .claude/`), and the depth-0 filter made the collapse permanent since any expanded path contains a separator. Measured: before `['.claude/']`, after planting `.claude/evil.py` `['.claude/']`, **delta empty**; an in-place rewrite of already-untracked `.claude/HANDOFF.md` was equally invisible because `??` reprints identically. `.claude/` is Claude Code's own config tree (settings, hooks, skills), so a payload there targets the **supervising agent**, not the worker → `_untracked_files()` with `--untracked-files=all` + sha256 per file + depth-0 filter deleted + policy-writable paths excluded via `policy.is_path_writable()`. P1 · fixed 2026-07-30 · `orchestrator/batch_runner.py` · **`test_f42`'s failing assertion was ASSERTING THE BUG** — "root snapshot holds no path separators" could only pass while no untracked directory existed
- **F47** The guard's field of view was defined partly by files outside version control → `git status`/`ls-files` honour THREE ignore sources and only `.gitignore` is tracked; `.git/info/exclude` (in-repo, never committed) and the global file (`core.excludesFile`, else git's XDG default `~/.config/git/ignore`, honoured **even when the config key is unset**) were unprotected. Proven: plant a file under `orchestrator/`, guard sees it, append one line to `.git/info/exclude`, **guard goes blind** — no commit, no diff, no trace. The global file already carried `**/.claude/settings.local.json`, masking a Claude Code settings file from the guard → fixed on three axes: enumeration via `ls-files --others --exclude-per-directory=.gitignore` (version-controlled sources only), snapshot carries the exact **BYTES** of each unversioned source so an edit is detected and written back (bytes not hashes: `git checkout` is useless on an untracked file, so the snapshot is the only source of truth; absent records as `None` so *creating* one counts), and `_masked_under_protected()` diffs `--exclude-standard` against `.gitignore`-only to name any protected path an unversioned rule is hiding — catching a mask planted BEFORE the snapshot, which the byte comparison structurally cannot see. P1 · fixed 2026-07-30
- **F48** Canary token spend was measured, then dropped on the floor → `run_canaries()` called `ledger.finish_task()` with no `tokens_in`/`tokens_out`, though `worker_with_failover()` returns `usage` and the line above consumes it via `worker_failed(out, usage)`. **All 6/6 resolved canary rows read 0/0** while mission rows carried millions (001: 23.5M in, 002: 12.3M), so `policy.tokens_used_today()` under-counted by exactly the canary spend and `tokens_per_day_hard_stop` protected less than it claimed — the sentence F21/F22b/F32 were each written to stop being true. **F33's bug in a path F33 never checked** (third instance: mission retry F32, synthesis F33, canaries F48) → accumulate arithmetic consolidated into one `accumulated_tokens()` called by BOTH `run_task()` and `run_canaries()`, rather than a fourth copy; all three post-call canary paths record (done, quota_wait, infra_failed), dedup query widened to fetch prior totals so resumes accumulate. Residual: a `TimeoutExpired` canary still records nothing — no `usage` exists to record. Found by reading the ledger after a canary run showed `3/5 green` over rows reading `tok=0/0`. P1 · fixed 2026-07-30 · `tests/test_f48.py` (19 assertions; reverting only the fix turns exactly 3 red)
- **F49** Synthesis silently receives a truncated brief and reports the missing part as a data gap → `run_synthesis()` builds its input with `p.read_text()[:6000] for p in briefs[:6]` — two silent caps, no marker, so neither the synthesis model nor the critic (which never sees the prompt) can tell a complete document from a bisected one. Measured: task 29's brief is 12,464 chars, 6,464 dropped, and `## Topic Opportunity 3` begins at char **6,060** — the cut misses it by **60 characters**. Task 30 therefore declared a data gap that was literally TRUE of the material it received, and grading that as an analyst error would have repeated F37; it was spot-checked pass and the defect recorded instead. Task 27 is hit harder and invisibly: all three of its input briefs (10,994/11,388/8,279) were cut, ~18KB of researched material never reached the synthesis meant to consolidate it, and the output still looks complete. F20/F31 family (judged on material never shown) but worse — those withheld REQUIREMENTS and produced visibly wrong output; this withholds EVIDENCE and produces well-formed output wrong only in what it omits, whose failure mode is an operator told to go source what the harness already has. Adjacent latent instance **now fixed as F51**: `_recent_fact_lines` was 12 rows from truncating. → **FIXED with the marker** (operator's call): `build_brief_block()` states every omission — `[TRUNCATED BY THE HARNESS: n of m characters ... researched and exists ... NOT a data gap]` plus a named section for briefs dropped by the count cap; caps promoted to `SYNTHESIS_BRIEF_CHARS`/`SYNTHESIS_MAX_BRIEFS` so raising them stays a separate one-line decision. **Crucially the prompt also had to change** — the model reasoned CORRECTLY from "absent"; nothing distinguished it from "withheld" — so it now carries "truncation is not a data gap ... do not tell the operator to research it, they already have it". Does NOT recover the omitted text; it makes the loss reportable instead of converting a harness limit into operator make-work. **Found by the spot-check, which is precisely the job F3 says only a human pass can do.** P1 · found + fixed 2026-07-30 · `tests/test_f49.py` (22 assertions, incl. the real task-29 brief now emitting `6464 of 12464`, and the pre-fix expression shown emitting nothing)
- **F51** The fact ledger truncated silently, and dropped the alphabetical tail → `_recent_fact_lines(days=14, cap=120)` ended `rows[:cap]`. Third member of F49's family (F49 briefs, F50 model context, F51 facts) and the closest to firing: measured **108 facts in the 14-day window against a cap of 120** — twelve rows of headroom, while week W30 alone produced **70**. Worse than lossy, the ordering was `ORDER BY entity, id`, so overflow always dropped the alphabetical **tail** — deterministically the same entities (today `ai-productivity`/`dark-academia`/`modern-stoicism`, the whole onboarding set), chosen by name rather than age or relevance → cap 120 → **300**; F49's marker in the same words; and the newest `cap` rows now SELECTED (`id DESC LIMIT`) then PRESENTED grouped by entity, so truncation drops the oldest while reading order stays grouped. `db` made injectable (F12's lesson) so the suite never opens the live ledgerbook. Behaviour today is byte-identical (108 < 300). P1 · found + fixed 2026-07-30 · `tests/test_f51.py` (20 assertions; validated on a case where the two orderings DISAGREE — fix keeps `zzz*` newest, pre-fix keeps `aaa*` oldest)
- **F52** Committing the handoff removed it from the containment surface → `.claude/HANDOFF.md` was covered by `_untracked_files()`'s hashes (F46/F47) **precisely because it was untracked**; `git add` moved it out of that set without moving it into `_tracked_hashes()`, which walks only `PROTECTED_PATHS` — and `.claude` was not listed. Measured a minute after the commit: a simulated tamper produced an EMPTY delta on all four channels (tracked hashes, new untracked, tampered untracked, porcelain). The blinded tree is Claude Code's own config dir — the one place a file steers the SUPERVISING agent, which is why F46 refused to *gitignore* it; tracking achieved the same blindness by the opposite route → `.claude` added to `PROTECTED_PATHS`; tamper now caught on TWO channels, protected tracked files 51→53, worktree still collapsed. **General lesson: changing a file's VCS status silently changes which guard covers it** — `git add` is not usually thought of as security-relevant. F42→F46→F47→F52 is one hole reappearing per layer. Knock-ons handled not suppressed: `test_f47`'s at-rest baseline legitimately moved (now asserts the ONLY at-rest mask is the DECIDED worktree), and its section 7 was re-pointed from the untracked channel to the tracked one, which is *stronger* since `git ls-files` ignores exclude rules entirely. Residual: F47 now WARNs on every snapshot about the worktree — `git worktree remove` clears it at source. **Residual CLOSED same day**: worktree removed, `_masked_under_protected()` → `[]`, `test_f52.py` §3/§5 rewritten to branch on live git state instead of assuming the worktree's presence (16/16 still green). P1 · found + fixed 2026-07-30 · `tests/test_f52.py` (10 assertions incl. the pre-F52 surface shown not to cover the file at all)

- **F53** 25% of the fitness score was awarded unconditionally, on every task ever run → **two independent defects, both required for either fix to matter**: (1) `escalate()` appended to `workspace/ESCALATIONS.md`, logged, pushed to Telegram — and never touched the ledger row, so ten task-scoped escalation sites all died in a markdown file (**F33/F48 class, third instance**: a real measurement that never reaches the column that scores it); (2) `finish_task()` wrote `interventions=?` defaulting to `0`, unconditionally overwriting — **the one consumption column F21 missed** when it moved cost/tokens/critic_verdict to `COALESCE`, latent and invisible precisely *because* the value was already always 0, so the clobber destroyed nothing observable until (1) was fixed. Measured across the whole ledger: `SELECT DISTINCT interventions FROM tasks` → exactly `[0]`, **all 32 rows, every mission, every week**; `cost_usd` likewise `0.0` on all 32, so `cost_eff` takes its `else 1.0` branch every time. **Live range of F is 0.35–1.00, not 0–1**, and the smoking gun was already in this project's own `scorecards` table: two rows reading `fitness: 0.35` with `completion_rate: 0.0, accuracy: None` — weeks where *nothing completed and nothing was verified* still scored 0.35. It also made an M1 acceptance criterion **structurally unprovable**: HARNESS_DESIGN §7's "interventions −30% vs baseline by week 8" is undefined for 0 → 0, so one of four criteria could never have been evaluated → `ledger.record_intervention(task_id, kind)`; `finish_task` → `interventions=COALESCE(?, interventions)` with the default moved to `None` (F21's own pattern finally applied to the column it skipped — explicit caller values still win, so existing callers are unaffected); `escalate()` gains optional `task_id`, wired at **8 task-scoped call sites**, with the 2 run-scoped ones deliberately left uncounted. Reporting added rather than the number corrected: `intervention_measured` / `cost_measured` / `fitness_floor` + scorecard lines in markdown **and** Telegram. `cost_eff` deliberately NOT faked. NOT backfilled. **W untouched — LOCKED**, asserted by the test. **Found by measuring the ledger while rating the harness, not by any test failing** — every suite was green throughout, because nothing was *broken*; the metric was simply measuring less than it claimed. P0 · found + fixed 2026-07-31 · `tests/test_f53.py` (17 assertions, incl. the pre-F53 clobber reproduced explicitly and the 0.35 floor rebuilt from a clean schema)

**H-items** (blueprint, tracked separately in HARDENING.md): H1–H6, H8, H9 done; **H7 CLOSED 2026-07-29**
(skill-note sanitiser at both draft and approval gates, provenance in `promote.py list`, injection
capped + logged) — note it was built *after* the gate had already been used, which is the wrong order.

---

## 3. Active skill states

- **This session ran under `/goal`** (F48 + spot-checks + DR), then four follow-on fixes: F49 marker,
  F49 cap raise, F50, F51. No `/rigor` phase, no `/loop` running, no background tasks pending.
- **Standing caps, all now stated or cleared** — `SYNTHESIS_BRIEF_CHARS=24000`,
  `SYNTHESIS_MAX_BRIEFS=6`, `FACT_LEDGER_CAP=300`. Every one emits a
  `[TRUNCATED BY THE HARNESS: …]` marker when it bites; none bites today (measured).
- **Cron battery — 5 tasks, all `Ready`, all `S4U` LogonType, all battery-agnostic** (re-checked
  2026-07-30 ~08:22, live):

| Task | Next run | Last result |
|---|---|---|
| `AGI_M1_backup` | 2026-07-31 02:00 | `0` — ran successfully tonight |
| `AGI_M1_canaries` | 2026-08-02 03:30 | `2147946720` (stale pre-S4U-fix refusal) |
| `AGI_M1_scorecard` | 2026-08-02 04:00 | `2147946720` (stale pre-S4U-fix refusal) |
| `AGI_M1_shopify` | 2026-08-03 04:00 | `0` |
| `AGI_M1_content` | 2026-08-05 04:00 | `0` |

- **These keep firing after the session ends.** **Sunday 2026-08-02 03:30/04:00 is the first
  scheduler-driven canary + scorecard fire since the S4U fix** — those two have never completed an
  automated run, and their `LastResult` is still the Win32 4320 refusal from before the fix. If either
  returns nonzero again, re-check `Principal.LogonType` first.
- **Two promoted skills are live and injecting on every mission run** (346 and 430 chars):
  `skills_analyst/001-shopify-competitor-intel/20260729_001-…_verify-cited-values-exist-on-their-source-pages.md`
  and `skills_analyst/002-content-niche-research/20260729_002-…_use-exact-spec-defined-evidence-types-for-validati.md`,
  both `canary_baseline: 3` (now sourced from **2026-W31**, per `promote._current_canary_green()`).
- **Regression suites now run from the repo:** `python tests/run_all.py` (all, serially) or
  `python tests/run_all.py f42 f47` (substring filter). It **refuses with exit 2** if
  `runs/.batch.lock` exists. **16 suites, 16/16 green** (re-measured 2026-07-30 11:45, post-F52).
- **Standing rule (F36, extended by F42):** commit before triggering a fire, and do not edit tracked
  files — **including `.gitignore`** — while one runs. It cost work three times on 2026-07-29/30.

---

## 4. Production status

| Thing | State | Evidence |
|---|---|---|
| F29–F51 fixes | **shipped + verified** | `python tests/run_all.py` → **16/16 suites green** from the repo, re-measured after F52 |
| Regression suites in-repo | **shipped + verified** | **16** suites + `run_all.py` + `README.md` at `S:\AGI_like\tests\`; auto-discovered by glob, so a new `test_*.py` needs no registration |
| `tests` inside containment | **verified** | `git ls-files PROTECTED_PATHS` → **51** tracked files, **17 under `tests/`** |
| Lock refusal in `run_all.py` | **verified** | planted a fake `runs/.batch.lock` → refused, `exit=2` |
| F46 untracked-dir detection | **shipped + verified** | plant inside `.claude/` DETECTED; in-place rewrite DETECTED by hash; depth-0 plant still detected; `memory/` write correctly ignored; pre-F46 set snapshot degrades to `{path: None}` |
| F47 exclude-source hardening | **shipped + verified** | 15 assertions in `tests/test_f47.py`; attack caught on all 3 axes; `.git/info/exclude` restored byte-identical; global ignore left untouched |
| F43 HARDENING write-up | **CLOSED** | was the one documentation gap; written in `7d453a4` |
| W31 fitness | **verified, improved** | F **0.8 → 0.914** after 4 in-window spot-checks; accuracy **33% → 71%**, completion 100%, 7/7 attempted, 0 dropped |
| W31 accuracy independence | **⚠ ZERO** | all **7/7** in-window spot-checks are AI-performed; scorecard + Telegram both carry the F28 caveat. The number is real but entirely self-assessed |
| F49 synthesis truncation | **shipped + verified, both halves** | marker states every omission, AND cap raised 6000 → **24000** after measuring that **11 of 13** briefs overflowed the old one. Task 29's brief no longer truncates at all; Topic 3 + the metaintro evidence now reach the model |
| F50 local rung vs synthesis | **shipped + verified** | fixed by testing declared context vs prompt size, NOT locality — `allow_local=False` was rejected as the wrong cause. Opt-in `context_tokens` per F39; 21 assertions; removing the guard reproduces the stall |
| F51 fact-ledger cap | **shipped + verified** | was **12 rows** from firing (108 vs cap 120, W30 alone made 70). Cap → 300, marker added, and ordering fixed so overflow drops the OLDEST not the alphabetical tail |
| W31 canaries | **verified, incomplete data** | 3 green / 2 quota-parked out of 5; rollback gate SHUT by design (F37) |
| Skill promotion gate | **shipped + exercised** | 2 skills approved 2026-07-29, isolated single-file commits (F15 held) |
| H7 sanitiser | **shipped + verified** | 16 unit assertions + end-to-end tamper test: `approve` refused a poisoned candidate, exit 1, no file, no commit |
| Injection logging (H7) | **verified in production** | `task 18: injecting 1 approved skill(s), 346/2000 chars: [...]` |
| Telegram spot-check push | **verified** | 2 real messages delivered 2026-07-29 |
| On-device DB backup | **verified restorable** | `--restore-test` → `RESTORE VERIFIED OK` both DBs; `AGI_M1_backup` LastResult `0` tonight |
| Offsite replication | **CONFIGURED + DR-drilled 2026-07-30** | OneDrive. Drill against the OneDrive copies ONLY rebuilt 81 commits from the bundle, dropped in the OneDrive DBs, and matched live row-for-row (tasks 32, scorecards 15, facts 108, entities 47) with all 7 spot-check verdicts and F28 markers intact. Recovered system runs **12/15** suites |
| Bundle-only vs full restore | **measured** | bundle alone → **5/15** suites (no data); bundle + OneDrive DBs → **12/15**. The 3 remaining need un-versioned fixtures (`extensive_research.md`, `workspace/` deliverables, `.claude/HANDOFF.md`), not code |
| `gemma4:12b-ctx4k` rung | **shipped + verified loadable** | 103s / 1.5 tok/s with no caller options |
| Local rung answer quality | **UNVERIFIED for tool-driven work** | tool-free it answered C1 as 2004/2013 vs true 2006 |
| Baseline-of-3 rollback test | **NOT concluded** | needs a canary week with zero unjudged; **4 attempts**, all 429 (latest 2026-07-30 02:31 — C2/C5 both parked in 48s) |
| Canary token accounting (F48) | **shipped + verified** | was 6/6 rows at `0/0`; `tests/test_f48.py` 19 assertions, and reverting only the fix turns exactly 3 red |
| Nested-worktree content churn | **residual, DECIDED not to fix** | recorded as `<nested-repo>`; appearance detected, internals not |
| F27 (coincidental digit match) | **FOUND, NOT FIXED** | no live wrong verdict to design against |
| Offsite/off-machine durability | **NOT started** | single physical disk: C: and S: are both partitions of Disk 0 |
| Repo history in the backup set | **shipped + restore-drilled** | `backup.py --bundle` writes+verifies `backups/repo_<ts>.bundle`; drill: `git clone` from it rebuilt every commit with `test_f48.py` and F48's registry entry intact. 7 bundles held; newest re-verified in the final sweep |
| Git remote | **shipped + verified 3x** | `origin` → `github.com/rkeerthi22/AGI_inspired`, SSH key-based; tip-hash+tree-diff identical after every push; **94 commits**, `git rev-list --count HEAD` |
| GitHub default branch | **NOT fixed — checked 4x** | still `main` (README-only), not `master` (real history); operator-reported fix did not take |
| Repo README.md | **shipped + committed** | `613c950`; project description, loop, fitness formula, directory map, safety posture — no numbers that go stale |
| CLAUDE.md / HARNESS_DESIGN.md accuracy refresh | **shipped + committed** | `c5b54b2`; fixed a real bug (fallback model documented as plain `gemma4:12b`, which has never once completed a task per F38 — corrected to `gemma4:12b-ctx4k`), added missing directory-map entries, corrected HARNESS_DESIGN's stale "Draft"/"currently W29" status. 16/16 suites green before and after |
| `orchestrator/simulate.py` (prediction layer) | **⚠ UNCOMMITTED, NOT this session's work, NOT reviewed** | exists on disk, untracked; see §5 item 0 — needs an operator decision, not a status |
| `memory/ledgerbook.db` `experiences` table | **written outside containment, integrity checked clean** | 4 rows added by the parallel session bypassing `run_task()`'s guard entirely; all OTHER tables' row counts match expected exactly, so additive-only, not corrupting — but the containment invariant itself was bypassed, which is the point regardless of whether the payload was benign |
| Hermes-native cron jobs | **newly observed this session, not previously tracked in this handoff** | `hermes cron list` shows 2: `919d7323dd0e` "Daily Intelligence Brief" (pre-existing, runs fine, last run 2026-07-30 ok) and `e6e05b1d2e8a` "Vaibhav Sisinty weekly video tracker" (new, first fire 2026-08-03) — distinct from the 4 `AGI_M1_*` Windows Task Scheduler jobs this handoff has tracked until now |
| Vaibhav cron Telegram delivery | **shipped + verified live** | `deliver: local → telegram:6173105867`; before/after `jobs.json` diff showed exactly ONE changed field on ONE job, prompt byte-identical, other job `NO CHANGE`; real test message delivered |
| `scorecard.send_telegram()` (bare `--to telegram`) | **verified working 2026-07-31** | called the function directly → returned `True` and delivered. Sunday 04:00 scorecard delivery is live |
| `TELEGRAM_HOME_CHANNEL` mechanism | **verified by probe; `hermes doctor` is WRONG about it** | doctor says the key "will be ignored" — false. `send_cmd.py:_load_hermes_env()` bridges every top-level `config.yaml` scalar into `os.environ`; probe: absent before, `'6173105867'` after. **Deleting the key to satisfy doctor would break scorecard delivery.** CLAUDE.md annotated (`e81f7e3`) |
| `orchestrator/simulate.py` prediction layer | **exists + operator-authorized; ACCURACY CLAIMS UNVALIDATED** | video "actuals" match no real video; task accuracy is median-vs-same-mission; skill model hardcodes `regressed: False` despite rollback `c7b5721`. Architecture sound, validation is not — see HARNESS_DESIGN.md §5 |
| **F53 intervention plumbing** | **shipped + verified** | `tests/test_f53.py` **17/17**, incl. the live-ledger premise (`DISTINCT interventions` → `[0]`) and the 0.35 floor rebuilt from a clean schema. **17/17 suites green** overall |
| **F53 fitness-honesty reporting** | **shipped + verified against live data** | live `weekly_fitness()` → `intervention_measured: False, cost_measured: False, fitness_floor: 0.35`; Telegram line renders `⚠ 0.35 of F unearned (interv/cost not measured)`; **`fitness` itself unchanged at 0.914** — honesty added, number preserved |
| Harness self-rating | **measured 6/10** | self-hardening 8–9/10 (54 registry entries, 17 suites, guards proven); *evidence of improvement* 2–3/10 (32 tasks total, W30 ran 3 vs ≥10/wk target, accuracy 100% self-graded) |
| Execution-only directive W4–W8 | **LOCKED + committed** | `CLAUDE.md` commit `afc67b6`; 2026-07-29 directive header marked SUPERSEDED IN PART so the contradiction cannot mislead a future session |
| Spot-check queue push | **verified delivered 2026-07-31** | `python orchestrator/spotcheck.py notify` → `0 awaiting spot-check, 10 AI-performed awaiting confirmation — telegram: sent` |
| **Git remote currency** | **⚠ 5 COMMITS BEHIND** | `local master afc67b6` vs `origin/master 613c950`. F53 + directive + this handoff exist on one disk only |

---

## 5. Unresolved action items

**Blocked on user:**

0. **A parallel Hermes session added a new subsystem to this repo, outside git, outside the
   containment guard, and contradicting a locked design decision — needs your call before anything
   else happens here.** Discovered 2026-07-31 03:33 while running the final verification sweep for
   this handoff; not this session's work, not reviewed or approved by this session.

   **What exists, verified by direct inspection (not by trusting the file's own claims):**
   - `orchestrator/simulate.py` (567 lines, untracked) — a "prediction/simulation layer" with three
     models (task-outcome prediction, video-engagement prediction, skill-safety prediction).
   - `docs/HANDOFF_SIMULATION.md` (untracked) — its own handoff note, self-identifying as
     `Author: Claude (glm-5.2:cloud) session`, dated 2026-07-30.
   - `memory/ledgerbook.db` — **modified directly**, 4 new rows in the `experiences` table
     (ids 1–4, timestamps 2026-07-31 01:22:44–01:23:09). Checked this table against corruption:
     all other tables (`facts` 108, `entities` 47, `relations` 18, `decisions` 8) match expected
     counts exactly, so this was additive-only, not damaging — but it bypassed
     `orchestrator/batch_runner.py` entirely, which is the ONLY sanctioned ledgerbook writer.
   - A **new Hermes-native cron job**, confirmed via `hermes cron list`: `e6e05b1d2e8a`, "Vaibhav
     Sisinty weekly video tracker", `0 9 * * 1` (every Monday 09:00), next run **2026-08-03 09:00**,
     local delivery (no Telegram). This will autonomously browse YouTube and download transcripts
     via yt-dlp, unattended, starting Saturday.
   - Files also written to `S:\AI videos\` (a separate, clearly-legitimate, long-running project of
     yours — `channel_concept.md`, `brand_kit.md`, production scripts going back to 2026-07-20 — this
     is NOT the concern; that project is real and yours). The concern is narrower: the same session
     also reached INTO `S:\AGI_like\` specifically.

   **Why this matters, concretely, against what's actually written down in this repo:**
   - CLAUDE.md, verbatim: *"The worker never writes to `ledger.db`/`ledgerbook.db` directly, by
     construction, not by trust."* This write did exactly that — via an interactive/oneshot Hermes
     session that never went through `run_task()`'s containment guard, because that guard only
     brackets subprocess calls the orchestrator itself launches.
   - HARNESS_DESIGN.md §5, verbatim verdict: **"DEFER"** — simulation is explicitly named as an
     M3-only feature, gated on M1 actually passing first and on "decisions with real cost" existing
     (e.g. ad-budget allocation). We are in M1, week 3 of 8. `simulate.py` was built anyway; its own
     handoff doc says "ahead of schedule" as if that were a virtue.
   - CLAUDE.md's 2026-07-29 operator directive, verbatim: *"This directive expands throughput on
     existing active missions, not scope."* A weekly YouTube-tracking cron job for a channel-research
     side project is a scope addition, not throughput on missions 001/002.
   - I edited HARNESS_DESIGN.md's §5/§7 status THIS session (commit `c5b54b2`) reaffirming the DEFER
     verdict and citing current W31 state — **without knowing this already existed**, since it was
     untracked and I had no reason to check for it until the final sweep. That edit is not wrong (it
     accurately reflects what's committed), but it's now sitting next to an uncommitted contradiction.

   **What I did NOT do, deliberately:** delete either file, revert the ledgerbook write, or cancel
   the cron job. This may be work you specifically asked for in that other session — I have zero
   visibility into what was actually requested there, and unilaterally discarding uncommitted work
   without knowing that is exactly the "investigate before deleting" rule I operate under. Flagging,
   not acting, is the correct move until you weigh in.

   **ALL THREE QUESTIONS ANSWERED BY THE OPERATOR 2026-07-31 — resolved, do not re-raise:**
   1. **Yes, requested.** Authorized work from a parallel session, not a containment breach.
      HARNESS_DESIGN.md §5's DEFER verdict is **overridden by operator decision**, recorded there as
      an AS-BUILT DEVIATION with the operator's own reasoning: *"an AGI would likely predict human
      behavior more accurately."* That reframes simulation from cost-optimization (§5's original
      framing) to a capability claim, under which the M3 gate never applied. **Do not delete
      `simulate.py` as out-of-spec.**
   2. **Leave uncommitted.** Operator: *"For now, let's not worry about committing for a while."*
      Both files stay untracked and inert. (Note: an untracked file under `orchestrator/` — a
      PROTECTED_PATH — will keep showing in `_untracked_files()`. That is the guard working, not a
      fault; it is now expected, so do not chase it.)
   3. **Keep the cron.** Operator set it up deliberately: *"I was also the one who set up the Sunday
      loop because I tend to drift off once a week."* First fire **2026-08-03 09:00**.
      **CHANGED 2026-07-31:** delivery switched `local` → `telegram:6173105867`, because a weekly
      update that lands silently on disk fails precisely for someone who drifts. Verified: `jobs.json`
      diffed before/after (exactly one field changed on one job; prompt byte-identical; the other job
      `NO CHANGE`), and a real test message delivered. Backup of the pre-edit `jobs.json` is in this
      session's scratchpad.

   **What I verified about the module itself, since the accuracy claims in its own handoff do not
   hold up** (full detail now in HARNESS_DESIGN.md §5, kept there because that is where the design
   decision lives):
   - **Video accuracy is not evidence** — recorded "actuals" (180,000 / 95,000 views) match **no
     video** in the 17-record dataset (nearest 186,000 / 94,000) and both predictions were for
     videos never published.
   - **Task accuracy is a near-tautology** — predicted a mission's median cost, scored against
     another run of that same scripted mission (4,518,017 vs actual 4,501,536).
   - **Skill-safety training data is factually wrong** — `predict_skill_safety()` hardcodes
     `"regressed": False` for every skill, but rollback `c7b5721` really happened, on mission 001,
     the same mission it scored "low risk".

   **Architecture sound, validation not — both true at once.** The fix is cheap (record outcomes only
   from real published videos / real completed tasks) and the Vaibhav cron is the mechanism that will
   supply genuine data. **Until then, treat every accuracy number this module reports as
   unvalidated.** Not fixed this session: out of scope, and the operator asked for no commits.

1. ~~**C: is at 1.6 GB free and falling.**~~ **LARGELY RESOLVED 2026-07-30** — operator approved
   clearing Temp; 10.7 GB reclaimed (439 entries, 31 skipped as in-use, `claude\` scratchpad
   excluded), verified by re-running the full suite and confirming the repo untouched.
   **Residual, and it is drifting back:** C: read 14.42 GB right after the clear and **12.74 GB**
   at the final sweep six hours later — still **7.3 GB under** CLAUDE.md's 20 GB floor, and the
   downward trend has resumed. Temp no longer holds an obvious next target, so meeting the floor
   needs a different reclaim source. Not urgent for Sunday's fire.
2. ~~**Spot-checks — THE #1 ITEM FOR WEEKS 4–8**~~ **FIRST TWO INDEPENDENT VERDICTS LANDED
   2026-08-01.** The operator personally opened the three cited YouTube URLs (#28) and the
   blog.google post (#29), confirmed titles and both verbatim quotes, and the verdicts were
   recorded. **In-window independent spot-checks: 0 → 2. `spot_checked_ai`: 7 → 5.**
   `accuracy` and `fitness` unchanged at **0.714 / 0.914** — this changed *provenance*, not
   the score, which is exactly right: the number was never wrong, it was unverified.

   **It took a P0 fix to make it register (F54).** The first attempt recorded both verdicts
   and `spot_checked_ai` stayed at 7/7, because `cmd_verdict()` APPENDS and every classifier
   grepped the whole notes field — so one historical AI segment flagged the row forever, and
   the assistant's own note saying *"supersedes the earlier AI-PERFORMED CHECK"* re-flagged
   the row it was clearing. Classification now reads only the latest `HUMAN(...)` segment.
   Registry F54; the scorecard cron at **2026-08-02 04:00** would otherwise have published
   7/7 self-graded hours after two stopped being so.

   **Still self-graded, in-window (5):** #24, #25, #26, #27, #30. Same 2-minute treatment
   each. Out-of-window: #1, #2, #5, #18 (audit trail only, cannot move the metric).

   **Historical note on the count:**
   It is 10 AI-performed verdicts, not 7. The "7" carried by earlier handoffs counted only the
   tasks a previous session was explicitly asked about; `spotcheck.pending_rows()` correctly reports
   **10**, and the extra three — **#24, #25, #26** — are all IN-WINDOW and were never mentioned.
   Measured 2026-07-31 (window opens `2026-07-24 08:10:49`):
   - **In-window (7, these move W31 accuracy):** #24, #25, #26, #27 (001-shopify) · #28, #29, #30
     (002-content)
   - **Out of window (3, audit trail only):** #18, #5, #1
   `spotcheck.py notify` delivered this to Telegram 2026-07-31 (`telegram: sent`). Note the message
   lists only `ai_done[:8]`, so #5 and #1 do not appear in it — both out-of-window, so nothing that
   moves the metric is hidden, but the message does not say it truncated.

   **`0 awaiting spot-check` does NOT mean the queue is clear.** Every deliverable has a verdict;
   what is missing is independence. ~~All 10 were written by the assistant.~~ **8 of 10 now** — #28
   and #29 carry genuine operator reads as of 2026-08-01. Re-running `spotcheck.py pass|fail <id>`
   ~~overwrites a row~~ **appends a new verdict segment** (the old one is kept for audit; the
   "overwrites" wording came from the tool's own docstring and was **false** — see F54), and
   classification reads only the latest segment. Accuracy is **30% of fitness** and the one term the
   system structurally cannot produce for itself.

   **The remaining gap is not effort, it is independence.** ~~All **7/7** in-window checks are now
   AI-performed, so W31's accuracy term is entirely self-assessed~~ — **superseded 2026-08-01: 5/7,
   with #28 and #29 now operator-verified.** The accuracy term is no longer *entirely* self-assessed,
   which is the first time that has been true. The scorecard still carries the F28 caveat for the
   remaining five, in both the file and the Telegram line.
   Question: will you re-run `python orchestrator/spotcheck.py pass|fail <id>` on even **two** of
   **27, 28, 29, 30**? Re-running overwrites the row with a genuine independent read, which is the
   only thing that converts this from a self-graded number into evidence.

   **The two cheapest to check genuinely, ~2 minutes each — this is the recommended next action,
   and it is time-boxed by the scorecard cron at Sun 2026-08-02 04:00:**
   - **#28** — open the three IDs and confirm the titles: `youtube.com/watch?v=5Lpc2nmziEQ` →
     *"Backrooms Movie Breakdown and Ending Explained"*; `…v=SaPaNc3dY9M` → *"OBSESSION Movie
     Breakdown: Every Hidden Detail You Missed!"*; `…v=UpJgzNeNnU0` → *"Disclosure Day is Not an
     Alien Movie - Ending Explained"*.
   - **#29** — open `https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/`
     and Ctrl-F for `30 million` and `secure cloud computer`. Both should appear verbatim.
   ```
   python orchestrator/spotcheck.py pass 28 "checked the three YouTube titles myself"
   python orchestrator/spotcheck.py pass 29 "checked both quotes on blog.google myself"
   ```
   **Fail them if they do not check out** — a `fail` from the operator is worth more than a `pass`
   from the assistant, and is the only route by which we would learn the critic is wrong. And note
   the trap: running those commands *without* opening the links does not make the number
   independent, it launders it. The value is entirely in the two minutes of looking.

   What was actually verified, so you can spot-audit my work rather than repeat it:
   - **#18** — Notion Marketplace live: 4.9/5 on **54 ratings**, count exact; web search independently
     confirms **\$129** and the **SPRING50** code. Gumroad citations unverifiable (client-rendered).
   - **#28** — all three YouTube IDs resolve to their **exact** claimed titles.
   - **#29** — three quotes verified **character-for-character** on blog.google and metaintro.com.
     One Goldman Sachs line is labelled verbatim but paraphrases.
   - **#27** — "Pick any 10 each month" and "1,000 generation credits every month" exact. Trustpilot
     403s. Pricing discrepancy explained by #5 below.
   - **#30** — the apparent error was **F49, a harness bug**; the analyst was right.
   - **#5** — four homepage figures + page title exact; `/upgrade` now **404s**, which explains #27's
     \$19-vs-\$14 discrepancy as a restructured page, not a fabrication.
   - **#1** — simulation, no external claims; honestly labelled MODEL-ESTIMATED, 5 personas, winner
     consistent with its own probabilities.
3. ~~**Offsite backup destination.**~~ **DONE 2026-07-30 — OneDrive, both halves**, and DR-drilled
   end to end. Residual risks, stated rather than assumed away: (a) OneDrive is a *sync* target, so
   the copy is correct on disk but whether Microsoft finished uploading is **not verifiable from
   this tool** — check the sync icon; (b) `KEEP_OFFSITE_N = 7`, so offsite is a rolling last-7, not
   an archive; (c) the ledger now leaves the machine, a privacy change made knowingly after the DB
   secret scan came back clean.
4. **⚠ PUSH THE 5 UNPUSHED COMMITS — the one live durability gap.** `local master afc67b6` vs
   `origin/master 613c950`. Unpushed: `9416aca`, `e81f7e3`, `f185d27`, `d1effba` (**F53**),
   `afc67b6` (**the execution-only directive**). Plus this handoff once committed. The remote is
   configured and was verified three times earlier today, so this is drift, not breakage:
   ```
   git push origin master
   ```
   **The assistant cannot run this** — auto-mode's classifier blocks `git push` outright (observed
   this session: *"Permission for this action was denied by the Claude Code auto mode classifier"*).
   It is an operator command, or add a Bash permission rule to unblock it. Verify after with
   `git ls-remote origin` — tip should read `afc67b6…` or later.

   **Also still open, unchanged after 4 independent checks:** GitHub's default branch is `main`
   (1 commit, README-only), not `master` (99 commits). `git ls-remote --symref origin HEAD` →
   `ref: refs/heads/main` every time, including after two operator reports of switching it. GitHub
   needs both a dropdown selection *and* a separate green confirm button; the second is easy to miss.
   Cosmetic — affects what a browser visitor or bare `git clone` lands on, nothing else.

   ~~**Git remote — the ONLY step left to actually close the single-disk risk.**~~ **CONFIGURED
   2026-07-30/31.** `origin` → `git@github.com:rkeerthi22/AGI_inspired.git` (SSH, ed25519 key
   generated this session, passphrase-less by design since nightly cron push needs to run
   unattended — `~/.ssh/id_ed25519`, added to the GitHub account by the operator). Pushed and
   verified THREE separate times, each with tip-hash + root-commit + full tree-diff comparison, not
   just exit code: after the initial 92-commit push, after the README commit (93), and after the
   doc-accuracy-refresh commit (94, current HEAD `c5b54b2`). All three: `git diff master
   origin/master --stat` empty, i.e. byte-identical.

   **One residual, unresolved, checked 4 separate times with the same result:** GitHub's default
   branch is still `main` (a 1-commit, README-only branch GitHub auto-created when the repo was
   made), not `master` (the 94-commit real history). `git ls-remote --symref origin HEAD` →
   `ref: refs/heads/main` every time, including after the operator twice reported having switched
   it in Settings → Branches. Whatever was clicked did not save — GitHub requires both selecting
   the branch AND a separate green confirm button, and it's easy to miss the second step. Low
   urgency (doesn't affect the push/backup mechanism, only what a browser visitor or a bare
   `git clone` lands on) but worth re-checking.
5. **Second provider (F39/F41 payoff).** The chain is cross-*model*, not cross-*provider*, so it
   cannot survive an account-level 429. Uncommenting `{ provider: anthropic, model: claude-sonnet-5 }`
   in `config/models.yaml` needs a key in Hermes `.env`. Currently **LOCKED** to Ollama-only.

**Doable now (assistant) — BUT read the execution-only directive first (§1): items that are
*building* are PAUSED for weeks 4–8, not merely deprioritised.**

5b. **Under the directive, the assistant's whole job is now: run batches, retry failures, re-run
   parked canaries when quota allows, push the spot-check queue, deliver scorecards, keep the ledger
   honest.** Items 7 and 8 below survive because they are probes/diagnosis, not construction.
   `simulate.py`'s broken validation (§5 item 0) is **NOT** to be fixed — it is building, it is
   uncommitted, and it feeds nothing. Revisit only if it starts writing into the live loop.

6. **Re-run the two parked canaries** (C2, C5 — `quota_wait`, resumable) whenever Ollama quota is
   actually available, to settle the baseline-of-3 test. **4 attempts so far, all instant 429s**
   (01:08, 01:13, and 02:31 on 2026-07-30 — the last parked both in 48s). The window is **not**
   local-midnight based and had not reset after 78 minutes, so this is opportunistic, not
   schedulable. Sunday's 03:30 cron fire is the next scheduled chance.
7. **F27** — still needs a live wrong-verdict example before a fix can be designed against it.
8. **`gemma4:12b-ctx4k` tool-driven quality** — untested with browser tools. The A3 probe once got
   C1 right that way; the tool-free test did not. Worth one real probe next time the box is idle.
   Note F50 now excludes it from *synthesis* on size grounds, so this question only concerns the
   browser-worker path.
9. ~~**Optional, on request:** `git worktree remove .claude/worktrees/jolly-gauss-8e52cb`~~
   **DONE 2026-07-30.** Removed + pruned; `_masked_under_protected()` now `[]`;
   `tests/test_f52.py` §3/§5 rewritten to branch on live state rather than assuming presence.
   16/16 green.
10. ~~**Canary token spend is never recorded.**~~ **FIXED 2026-07-30 as F48** (see registry). One
    residual, stated rather than papered over: a canary killed by `subprocess.TimeoutExpired` still
    records nothing, because that path has no `usage` to record — the same limitation `finish_task()`'s
    F21 comment describes. **Historical rows were NOT backfilled:** the six existing canary rows still
    read 0/0. `runs/canary_<name>.usage.json` may hold some of that spend, but `runs/` is gitignored
    and periodically cleaned, so a backfill would be partial and would rewrite an append-only ledger —
    not done without an explicit call.
11. ~~**Two skill candidates drafted for mission 002, awaiting your `promote.py approve|reject`.**~~
    **APPROVED 2026-07-30** (operator instruction, both). Sourced from real defects the #28/#29
    spot-check notes caught but never acted on:
    - `20260730_002-content-niche-research_reconcile-every-repeated-metric-within-a-delivera.md` —
      task 28's self-contradiction (544K views vs. "587K-view follow-up", same video).
    - `20260730_002-content-niche-research_never-label-a-paraphrase-as-a-verbatim-quote.md` —
      task 29's Goldman Sachs line marked "(verbatim)" while paraphrasing the live page.

    Each cited exactly **one** lesson row (ids 13, 14) — below `promote.py`'s own
    `MIN_EVIDENCE_ROWS=2` corroboration bar for the automatic weekly review, stated at draft time so
    the approval was an informed call, not a rubber stamp. Both now ACTIVE, stamped
    `canary_baseline: 3` (from a real 2026-W31 observation, not a disarmed 0 — see F34), committed in
    two scoped commits (`ef6e7d1`, `b9f80c3`), `lesson_candidates` ids 13/14 marked `promoted_to`,
    `memory/ledgerbook.db.decisions` rows 7/8 record the approval provenance. `002-content-niche-
    research`'s injected skill text is now **1217/2000 chars** (was 430) — still under the H7 cap, no
    truncation. **16/16 suites green** after promotion.

---

## 6. Environment facts

All checked **2026-07-29/30**, most re-measured in the final sweep at **2026-07-30 08:22**.
Per-machine, per-session — re-verify before relying on them.

- **ONE physical disk.** `C:` and `S:` are both partitions of Disk 0, a single 476.9 GB
  `NVMe SAMSUNG MZVL2512HCJQ-00B07`. No local path survives hardware loss.
- **C: free space: 1.6 GB → 14.42 GB → 12.74 GB.** Reclaimed 2026-07-30 ~02:45 by clearing
  `C:\Users\moham\AppData\Local\Temp` with operator approval — **10.7 GB freed**, 439 entries
  removed, 31 skipped as in-use (not forced), and the `claude\` scratchpad tree excluded
  deliberately. The bulk was Visual Studio Installer / .NET workload manifest caches
  (`tl0gxj0e` 4.4 GB, `zikl4kdb` 1.65 GB), idle since 29 Jul — **not** harness data.
  ~~It is drifting back down… a recurring consumer rather than a one-off~~ — **that reading was
  WRONG and is corrected here.** Three points measured 2026-07-30: 14.42 GB (02:45) → 12.74 (08:22)
  → **12.44 (11:45)**. The 0.30 GB/h implied by the first interval was **this session's own
  activity** — restore drills cloning into `C:\…\Temp`, DB copies, repeated bundle writes. The
  genuine idle rate over the last measured 3.4 h is **0.09 GB/h**, i.e. ~118 h to a 2 GB floor.
  Sunday's 03:30 fire is ~2 days out and is **not** at risk. Do not re-raise this as an alarm
  without three points; two points during heavy I/O say nothing.
  Still **7.6 GB under CLAUDE.md's 20 GB floor**; Temp holds no obvious next 5 GB, so meeting the
  floor needs a different target. S: **98.04 GB free**.
  **Free space on this box also swings ~2 GB minute-to-minute** — C: read 1.61 GB then 3.72 GB
  eleven minutes apart with nothing deleted between. Quote a delta, not a single reading.
- **GPU:** `NVIDIA GeForce RTX 3050 Laptop GPU`, 4096 MiB total, ~929 MiB used, ~3034 MiB free.
- **RAM:** 15.7 GB total; free swung between **2.8 GB and 7.9 GB within one hour** — this is why F38
  chose `num_ctx 4096` over 8192.
- **Ollama models installed:** `llama3.1:latest` (4.9 GB), `gemma4:12b` (7.6 GB),
  `gemma4:12b-ctx4k` (7.6 GB, created 2026-07-29), `glm-5.2:cloud`, `kimi-k2.7-code:cloud`.
  Ollama API at `http://127.0.0.1:11434/api/chat`.
- **Uncapped local models CANNOT load.** `gemma4:12b` → `CUDA error: out of memory`;
  `llama3.1:latest` → `failed to allocate CPU buffer of size 16642998272` (15.5 GB, KV cache).
  Both load fine with `num_ctx` capped: llama3.1 4.2–4.5 tok/s, gemma 1.1–1.5 tok/s.
- **Ollama Cloud quota EXHAUSTED** as of 2026-07-30 01:13 — instant 429 on `kimi-k2.7-code:cloud`
  *and* `glm-5.2:cloud` (same account). A 13-token probe succeeded at 01:11 while a real canary
  429'd two minutes later: **a trivial call proves reachability, not headroom.**
  **Still exhausted at 02:31** (78 minutes later, 4th attempt) — so the window is neither
  local-midnight nor hourly. F39's `quota_group` skip fired live here for the first time: after
  `kimi` 429'd, `glm-5.2:cloud` was **not called at all**, cutting each canary to ~24s.
- **Token budget:** `policy.yaml tokens_per_day_hard_stop: 20,000,000`. Spent 2026-07-29:
  **11,390,219**. `policy.tokens_used_today()` on 2026-07-30 → **0** (the day's only model calls
  were 429s, which cost nothing), `today_start()` → `2026-07-30T00:00:00` (F44 reading correctly).
- **Clock skew:** Python local (CEDT, UTC+2) runs 2h ahead of SQLite `datetime('now')` (UTC).
  `created_at` is UTC/space-separated (SQLite-written); `finished_at` is local/`T`-separated
  (Python `isoformat()`). This distinction is load-bearing — see F17, F19, F44.
- **Three ignore sources feed the fs-guard** (F47). `.gitignore` is tracked and protected;
  `S:\AGI_like\.git\info\exclude` (contains `.claude/worktrees/`, now masking nothing — see below)
  and `C:\Users\moham\.config\git\ignore` (contains `**/.claude/settings.local.json`) are
  **unversioned** — `core.excludesFile` is unset, yet git honours that XDG path anyway.
- **Untracked non-ignored set is now 0 entries.** `.claude/HANDOFF.md` is tracked (F52), and
  `.claude/worktrees/jolly-gauss-8e52cb/` was removed 2026-07-30 (`git worktree remove` + `prune`).
  `_masked_under_protected()` → `[]`. The `.claude/worktrees/` line in `.git/info/exclude` is now
  stale (masks an empty directory) but was left in place — see the F52 residual-closed note above.
- **OneDrive** present and running (`C:\Users\moham\OneDrive`, PID 9004 at time of check). No
  removable drives attached, no mapped network drives.
- **`EACefSubProcess` holding 1.77 GB RAM** — CLAUDE.md says Epic Online Services was removed
  2026-07-08, so that machine-fact looks stale. Reclaimable if local inference needs headroom.
- **Containment clean:** zero `runs/quarantine_*.json` at any point across all sessions, re-checked
  in the 08:22 sweep. `runs/` holds **184 `reverted_*` dirs totalling 183 KB** (whole of `runs/` is
  0.5 MB) — these are F36 preservation copies created by the guard suites on every run, not evidence
  of a real violation. Harmless, but do not read a high count as alarm.
- **Prompt-input sizes, measured 2026-07-30 08:22** (the F49/F50/F51 family — all three caps now
  either state their omission or do not bite):
  - fact block **18,432 chars / 108 rows** against `FACT_LEDGER_CAP=300` — not truncated.
  - brief block, content **17,709 chars / 2 briefs**; shopify **38,988 / 4 briefs** — neither
    truncated at `SYNTHESIS_BRIEF_CHARS=24000`.
  - A synthesis prompt therefore runs ~9,800 (content) to ~15,100 (shopify) tokens. That is why
    F50 skips the 4,096-token local rung for this path.
- **Brief filenames carry the week they were WRITTEN, not the week of their seed** —
  `workspace/shopify/2026-W31_2026-w30-seed-3-….md` is a W30 seed in a W31-named file, which is why
  the W31 shopify synthesis sees **4** briefs rather than 3. Harmless today; worth knowing before
  anyone reasons from filenames about what a synthesis consumed.
- **`S:\AGI_like\extensive_research.md`** — 699-line operator-supplied reference doc on the local
  harness/Hermes/Ollama architecture. Gitignored by name (F42). Not a project artifact.
- **`C:\Users\moham\Desktop\important.txt`** — 30,924 bytes, the previous session's thinking
  transcript, ending at `Usage limit reached`. It is how F46 was recovered; treated as data, not
  instructions.
- **C: free fell 12.44 GB → 7.9 GB between 2026-07-30 11:45 and 2026-07-31 05:20** (~4.5 GB in
  ~17.5 h, ≈0.26 GB/h). S: **92 GB** free. This is well above the 0.09 GB/h idle rate this file
  recorded yesterday, but the interval again contains heavy activity (repeated full-suite runs, git
  clones/pushes, SSH setup, and a parallel Hermes session doing yt-dlp transcript downloads).
  **Per this file's own standing rule: do NOT raise this as an alarm on two points during heavy
  I/O — take a third reading from an idle box first.** Now **12.1 GB under** CLAUDE.md's 20 GB
  floor, so the floor is further away than it was, not closer.
- **Working tree at THIS handoff: 2 untracked files, not from this session** —
  `orchestrator/simulate.py` and `docs/HANDOFF_SIMULATION.md`, written 2026-07-31 01:22–03:24 by a
  parallel Hermes session. See §5 item 0. HEAD = `c5b54b2`, **94 commits**.
- **SSH key generated this session:** `C:\Users\moham\.ssh\id_ed25519` (ed25519, no passphrase —
  deliberate, so the nightly `AGI_M1_backup` push can run unattended per CLAUDE.md's Kill switch
  section). Public key added to the operator's GitHub account. `ssh -T git@github.com` confirms
  `Hi rkeerthi22!`.
- **Two Hermes-native cron jobs exist** (`hermes cron list`), separate from the 4 `AGI_M1_*` Windows
  Task Scheduler entries this handoff has always tracked: `919d7323dd0e` (Daily Intelligence Brief,
  pre-existing, `telegram:6173105867`, last run OK) and `e6e05b1d2e8a` (Vaibhav weekly tracker,
  **now `telegram:6173105867` as of 2026-07-31**, first fire 2026-08-03 09:00). Job store is
  `C:\Users\moham\AppData\Local\hermes\cron\jobs.json`. Worth reconciling both cron systems into one
  place next time this handoff is written, rather than tracking them separately.
- **Telegram: BOTH delivery forms verified live 2026-07-31.** Bare `--to telegram` (what
  `scorecard.send_telegram()` uses) resolves via the config→env bridge and delivers; explicit
  `telegram:6173105867` delivers and does **not** depend on the bridge. Prefer the explicit form for
  new work — it survives a config migration that drops the key.
- **⚠ `hermes doctor` emits a FALSE POSITIVE that can break delivery if acted on.** It reports
  `Unknown top-level config key 'TELEGRAM_HOME_CHANNEL' — it will be ignored`. Proven false by probe
  (env absent before `_load_hermes_env()`, `'6173105867'` after). Doctor validates the config
  *schema*; the key is consumed through a bridge doctor does not model. **The CLAUDE.md rule about
  this key is CORRECT and load-bearing — do not "clean up" the key.** Annotated in `e81f7e3`.
  - **Process lesson, recorded because it nearly cost real damage:** I read that warning and told the
    operator the CLAUDE.md rule was stale, offering to "fix" it. It was not stale — following my own
    recommendation would have deleted a key that `scorecard.send_telegram()` depends on, three days
    before the Sunday 04:00 fire. **A tool's own diagnostic is a code-read, not a measurement.** It
    was caught only because the operator said "check it" rather than "do it". Same verification-ladder
    failure the global rules name as the single most common one; it recurs under time pressure and at
    the end of long sessions.
- **`python tests/run_all.py` timed out at the 2-minute foreground cap TWICE this session** (after
  the README commit, and after the doc-accuracy-refresh commit) with zero output before the kill.
  Both times, running the identical command in the background instead completed cleanly at
  **16/16 green**, and process inspection mid-run showed active child PIDs actually progressing
  (not deadlocked) — so this reads as system load (stdout block-buffering when redirected to a
  file, plus whatever the parallel Hermes session was doing on this same box around 01:00–03:30),
  not a real hang. Noted here in case the pattern recurs — if it does, background + `TaskOutput`
  is the fast diagnostic, already proven twice.

---

## 7. Final verification sweep — 2026-07-30 08:22, re-run 11:45

Every line below was measured in one pass; **zero problems**.

| check | result |
|---|---|
| HEAD / commits | `a9771f4` · **81** on master |
| working tree | clean apart from untracked `.claude/` |
| git remote | **none** — the one open durability gap |
| orchestrator modules compile | 11/11 |
| `policy.validate_paths()` | consistent (no drift vs `PROTECTED_PATHS`) |
| regression suites | **15/15 green** |
| tracked files under protection | 51, of which **17** under `tests/` |
| `runs/quarantine_*.json` | **0** |
| HARDENING registry | 52 entries, **F1..F51 + F22b**, no gaps, no duplicates |
| fact block | 18,432 chars / 108 rows, cap 300 — not truncated |
| brief blocks | content 17,709 · shopify 38,988 — neither truncated at cap 24,000 |
| W31 fitness | F **0.914** · completion 100% · accuracy **71%** · 7/7 scheduled |
| W31 spot-checks | 7 recorded, **7 AI-performed** (independence still zero) |
| W31 canaries | 3/5 green, 2 parked — rollback gate shut by design (F37) |
| canary rows at 0 tokens | 6 (historical; F48 fixes forward, no backfill) |
| cron battery | 5 tasks; **canaries Sun 2026-08-02 03:30**, scorecard 04:00, backup nightly 02:00 |
| C: / S: free | **12.74 GB** / 98.04 GB |
| DB snapshots | 9 each for ledger + ledgerbook, newest 2026-07-30 02:51 |
| repo bundle | 7 held; newest `repo_20260730_082205.bundle` **verifies OK** |

**The one thing this sweep could not certify — since CLOSED, 2026-07-30 ~10:55.** At sweep time
everything lived on one physical disk. The operator then authorised OneDrive for the bundle and, on
seeing that a bundle-only restore recovers no data, for the databases too.

**Post-sweep delta (HEAD `2947c24`, 82 commits):**

- `config/offsite_backup.path` → `C:\Users\moham\OneDrive\AGI_like_backups`; the nightly
  `AGI_M1_backup` now replicates both DB snapshots and the repo bundle.
- **DR drill against the OneDrive copies only**, pretending S: is gone: clone from the bundle → 81
  commits; OneDrive DBs dropped in byte-identical; row counts match live exactly (tasks 32,
  scorecards 15, lesson_candidates 12, facts 108, entities 47, relations 18, decisions 6); all 7
  spot-check verdicts and their F28 `AI-PERFORMED CHECK` markers intact.
- **Restore completeness measured, not assumed:** bundle alone → **5/15** suites; bundle + OneDrive
  databases → **12/15**. The 3 that still fail assert against deliberately un-versioned fixtures
  (`extensive_research.md`, `workspace/` deliverables, `.claude/HANDOFF.md`) — verified individually,
  not code defects.
- Secret scans ran **before** anything left the machine and are the precondition for repeating this:
  490 git objects across all history, and 1,537 text cells across both live DBs. Nothing found.

**What is still not certified:** the destination is class `sync`. The files are correct on disk;
whether Microsoft has finished uploading them is not observable from here. And `KEEP_OFFSITE_N = 7`
makes offsite a rolling last-7, not an archive. A git remote remains the stronger answer, and is
still not configured.

### Re-run at 11:45, after F52 and the OneDrive work

| check | result |
|---|---|
| commits | **86** on master |
| working tree | **fully clean** — zero untracked entries (this file is now tracked, F52) |
| regression suites | **16/16 green** |
| `policy.validate_paths()` | consistent |
| `PROTECTED_PATHS` entries | **12** (`.claude` added by F52) |
| HARDENING registry | **53 entries, F1..F52 + F22b**, no gaps, no duplicates |
| `runs/quarantine_*.json` | **0** |
| C: free | **12.44 GB**, idle rate 0.09 GB/h — Sunday not at risk |
| offsite | OneDrive carries bundle + both DBs; restore rebuilt 86 commits **with `.claude/HANDOFF.md` present (440 lines)** |
| git remote | still **none** |

**The gap the 08:22 sweep named is now closed.** At that point everything lived on one disk. It now
lives on two, and the restore has been drilled — including this document, which the first drill
recovered *absent*.

### Final sweep — 2026-07-31 05:20 (this handoff)

| check | result |
|---|---|
| HEAD / commits | `afc67b6` · **99** on master |
| **unpushed** | **⚠ 5 commits** — `origin/master` still `613c950` (§5 item 4) |
| working tree | 2 untracked files, both deliberate (`simulate.py`, `HANDOFF_SIMULATION.md`) |
| regression suites | **17/17 green** (`test_f53` added this session) |
| HARDENING registry | **54 entries, F1..F53 + F22b**, no gaps, no duplicates |
| W31 fitness | F **0.914** · completion 100% · accuracy **71%** · 7/7 scheduled |
| W31 fitness honesty | `intervention_measured: False` · `cost_measured: False` · **`fitness_floor: 0.35`** |
| spot-checks | **10 AI-performed, 0 independent** — 7 of them in-window |
| `policy.tokens_used_today()` | **0** · `today_start()` → `2026-07-31T00:00:00` |
| C: / S: free | **7.9 GB** / 92 GB |
| GitHub default branch | still `main`, not `master` (4th check, unchanged) |

**The one thing this sweep leaves open is durability, and it is one command.** F53 and the
execution-only directive — the two most consequential artefacts of this session — exist on a single
physical disk until `git push origin master` runs.

### Re-run at 2026-07-31 03:33 (earlier in this session)

| check | result |
|---|---|
| commits | **94** on master, HEAD `c5b54b2` |
| working tree | **NOT clean** — 2 untracked files, not from this session (§5 item 0) |
| git remote | **configured**, `origin → github.com/rkeerthi22/AGI_inspired`, push verified 3x tip-hash+tree-diff |
| GitHub default branch | still `main`, not `master` — open, low urgency |
| regression suites | **16/16 green** (via background run; foreground timed out twice, confirmed false alarm both times) |
| `policy.validate_paths()` | consistent |
| `PROTECTED_PATHS` entries | **12**, unchanged |
| HARDENING registry | **53 entries, F1..F52 + F22b**, unchanged this session |
| `runs/quarantine_*.json` | **0** |
| `memory/ledgerbook.db` integrity | all tables checked; `experiences` +4 rows (out-of-band, see §5); every other table's count matches expected exactly |
| Hermes cron jobs | 2 (`919d7323dd0e` pre-existing OK, `e6e05b1d2e8a` new, unreviewed) — distinct from the 4 `AGI_M1_*` Task Scheduler jobs |

**This sweep found something the previous one couldn't have: work from outside this session sitting
in the same repo.** Every other line above is routine continuation. That one is not, and is
deliberately the first thing this document says (see the banner at the top and §5 item 0).

### Post-sweep delta — 2026-07-31 ~04:00 (operator answered, Telegram work)

- **§5 item 0 RESOLVED.** Operator confirmed `simulate.py` + the Vaibhav cron were requested; they
  stay **uncommitted** by instruction; the cron stays. HARNESS_DESIGN.md §5's DEFER verdict is now
  formally overridden there as an AS-BUILT DEVIATION carrying the operator's reasoning, so a future
  session cannot delete the module as out-of-spec.
- **Vaibhav cron switched to Telegram** (`local` → `telegram:6173105867`), verified by before/after
  `jobs.json` diff **and** a real delivered message. Rationale is the operator's own: they drift
  weekly, and a disk-only update fails exactly that person.
- **`hermes doctor` false positive found and recorded** — see §6. I had wrongly called a correct
  CLAUDE.md rule stale on the strength of that warning; the probe reversed it. Committed as
  `e81f7e3`.
- **Commits this delta:** `e81f7e3` (CLAUDE.md doctor note), plus this handoff + HARNESS_DESIGN.md
  §5 update. **97 commits**, 16/16 suites green, working tree carries only the 2 deliberately
  uncommitted simulation files.
- **No new F-numbers.** Nothing here was an AGI_like harness defect: the doctor bug is upstream
  Hermes, the cron change is configuration, and the simulation-validation problems are in an
  uncommitted module. Registry stays **F1..F52 + F22b**. Resisting the urge to mint F53 for a
  non-fix is the point of an append-only registry.

---

## Post-handoff delta — 2026-08-26 (operator resumed work after 26 days)

The handoff above is from 2026-07-31. The harness ran unattended until 2026-08-26, with no
agent-written updates to this file. This block records the deltas a future session needs to
read this doc correctly, without rewriting the 818 lines above.

**Commits this delta, on `master` (was 99 → now 107):**
- `146f9af` F54: an operator re-verification could never clear the AI-performed flag (fixes
  spot-check classification; spot_checked_ai is read from the latest `HUMAN(...)` segment,
  not the whole notes field).
- `8ab05f7` F55: worker produces partial work on tool failure instead of empty deliverable
  (prompt-side instruction for 503/403/timeouts; does NOT address `loop_web_search_cap` —
  see finding below).
- `bb202f6` Fix prediction system bottlenecks: unify CLI, fix MIKS engine, deprecate v1
  (~8,700 lines, 47 files; lands the `prediction_machine/` subsystem you will see in
  `git log` but not in the body of this handoff).
- `bc86d35` test_f44: use empty schema instead of copying live ledger.db **(this session)**.
- `f6b58c1` canary prediction gate: remove `mission_id != canaries` skip **(this session)**.
- All 5 commits pushed to `origin/master` (was 4 unpushed; now 0 unpushed).

**This session landed 4 separate items the operator asked for, in order:**

1. **F44 test fix (Option 1).** `tests/test_f44.py` was failing on data-pollution, not a code
   bug. The test copied the live `ledger/ledger.db` (which had 67 real task rows including
   today's W35 spend of ~3.08M tokens) and only DELETEd `task_id >= 9700` — which left
   real W35 task IDs 65/66/68/69/70 in the test DB. Result: `tokens_used_today()` was
   returning `planted_value + 3_089_362` while the test asserted it was just `planted_value`.
   `policy.py:126-145` (the F44 fix shipped 2026-07-30) was always correct. The fix uses
   `sqlite3.connect(tmp).executescript(CREATE TABLE tasks ...)` to get the same column
   layout without any data the test did not plant. F12's affordance (`LEDGER_DB` is
   injectable) is the right tool here, just wasn't being used. **11/11 assertions green
   after the fix.** This is the same "test asserted the bug" trap `test_f52` hit (see
   "still-red suites" below).
2. **Canary prediction gate removal.** `orchestrator/batch_runner.py:1620-1635` was gated
   on `mission.get("id") != "canaries"`, which excluded the system's own pass/fail signal
   from `predictions.db`. The 25 canary task IDs (7..11, 31..35, 39..43, 47..51, 59..63)
   ran every week, hit the critic, recorded verdicts in `ledger.db` — and never appeared
   in the prediction store. Daily report's 0/67 training-data ratio is directly downstream
   of this gate. The canary path is exactly the right training signal: short deterministic
   specs, no live web tool, the same prompt every week, the same critic. Live probe before
   commit: `before_task_runs(61, 'C1 canary spec', 'canaries')` returned a real prediction
   ID. Safety: the hook is fault-tolerant — failures return `None`, harness continues
   normally. **Live next canary run (Sun 03:30) will seed the store for the first time.**
3. **Phantom task_id 88898 (read-only finding, no code change).** The 2026-08-25 daily
   report's "data problems discovered" line is a *correct* report, not a *fix-needed*
   defect. The row in `prediction_machine/data/predictions.db` with `prediction_id
   =23b9e58b-373f-4924-8e7e-35743f8d1990` and `target='88888'` is already correctly
   excluded from training: `valid_for_training=0`, `invalid_reason='test cleanup'`. The
   `input_features` show `mission_id='test-mission'`, `spec='test synthesis spec'` —
   this is test pollution from 2026-08-20T04:31:32, not production data. The
   `task_outcome_integration.py:233-238` `record_task_outcome()` correctly returns `None`
   when the ledger row is missing, and the evaluator filters on `valid_for_training=1`.
   **Do not chase this in a future session.** A post-week-8 hardening (separate from the
   execution-only window) would be to write `valid_for_training=0` at predict time when
   `task_id` is a clearly-fake value like 88888.
4. **Pushed all unpushed commits to `origin/master`.** Was 4 unpushed (F55, prediction_machine
   bottlenecks, plus the 2 from this session); now 0 unpushed. Tip `f6b58c1` on both local
   and remote. The 8,700-line prediction machine subsystem is now durable off-disk.

**The `python orchestrator/spotcheck.py notify` command was run after the handoff
update landed — see §5 item 2 below for the W32–W35 queue state.**

**Still-red suites (3 of 18) — these are test-data drift, not code bugs. Per the
execution-only directive, I did NOT touch them; recording here so the next session
does not re-chase:**
- `test_baseline`: 4 of 6 assertions fail because the W29/W30/W33 canary data the test
  asserts against has moved on. The F34 fix in `_current_canary_green()` is correct;
  the test fixtures are stale.
- `test_f49`: 1 assertion ("the old cap really was overflowed by most briefs") fails
  because the F49 cap raise 6000 → 24000 invalidated the assumption. The test asserts
  a *historical* property that no longer holds.
- `test_f52`: 1 assertion ("PRE-F52 surface did not cover it at all") fails because
  the test is asserting the *pre-fix* surface state, but post-F52 the surface does
  cover the file. The fix worked; the test was never updated. Same "test-of-the-fix"
  pattern as F54's predecessor.
- All three want either a fixture refresh or a "pre-Fn state" split, not a code change.

**Misclassified claim from `SELF_IMPROVEMENT.md` (2026-08-13):** the doc said
`AGI_M1_shopify` was DISABLED since W31 and the single biggest contributor to low
output. **It was re-enabled sometime between 2026-08-13 and 2026-08-24, and mission
001 has been running normally since.** Verified live: `schtasks /Query
/TN "AGI_M1_shopify" /V` → `Status: Ready, Scheduled Task State: Enabled, Last Run
2026-08-24 4:00:00, Last Result 0`. The job is **scheduled Mondays 4:00 AM only**
(`Days: MON`), not daily, so the per-week ceiling for mission 001 is structurally
~3–7 tasks, not 3–7/day. Ledger confirms: tasks 52–55 on 2026-08-17, 64–67 on
2026-08-24. The "low output" complaint in the doc is real but misattributed: it
reflects weekly mission cadence, not a disabled cron.

**`predictions.db` is tracked in git (new finding, not fixed).** The bottlenecks
commit `bb202f6` (2026-08-25) accidentally tracked `prediction_machine/data/predictions.db`
in git, but `.gitignore` explicitly excludes `ledger/*.db`, `memory/*.db`, `backups/`
as "Live binaries — backed up separately, not version-controlled." The prediction
store DB is the same class of artifact and should be in `.gitignore` too. A diagnostic
probe row from this session is left unstaged to avoid expanding the oversight. The
correct fix is one `.gitignore` line (`prediction_machine/data/*.db` plus the
`-shm`/`-wal` siblings), and a `git rm --cached` to stop tracking. **Deferred to
post-week-8 hardening per the execution-only directive.** Until then, every commit
that mutates the store will diff a binary.

**`bash` permission to `git push` was granted in this session.** The handoff above
(§5 item 4) said auto-mode blocks it. This session attempted and succeeded:
`git push origin master` → `146f9af..f6b58c1 master -> master`. If a future session
sees the rule restored, the `.claude/HANDOFF.md` text above is now historical, not
load-bearing.

**Operator decisions since 2026-07-31, in priority order:**

1. **F44 fix landed as Option 1** (test isolation, not UTC switch). The UTC switch
   would have re-introduced the F44 bug — `finished_at` is local time, UTC boundary
   would mis-attribute for 22 hours a day. Operator confirmed.
2. **No new wiring in `batch_runner.py` for the prediction machine.** The wiring
   landed in `bb202f6` via `prediction_machine/integrations/batch_runner_hook.py`
   and is already called at `batch_runner.py:1620-1635` (before) and
   `batch_runner.py:1654-1661` + `batch_runner.py:1882-1888` (after, twice for
   synthesis and main flow). Injecting direct `prediction_store` calls would
   have caused `TypeError` (signature mismatch), bypassed the predictor, and
   duplicated predictions on retry. Operator confirmed.
3. **The 5-file split of `batch_runner.py` is deferred.** The execution-only
   directive (W4–W8) stands. Refactor will be revisited after week 8 closes,
   tests are green, and the spot-check queue is cleared. Operator confirmed.
4. **Operator transitioning out of execution-only for week 9** — drafting
   `OSINT_INTEGRATION_PLAN.md` (separate document, this file's sibling). Awaits
   operator override of the directive before commit. The plan proposes a
   measured integration of modern OSINT frameworks and GitHub scraping into
   the worker pool, with explicit containment rules to prevent the
   `batch_runner.py` 4,000-line-mess outcome. **See that file when it lands.**
5. **Operator processed the spot-check queue** — `python orchestrator/spotcheck.py
   notify` was run after the handoff update committed. The W32–W35 independent
   verdicts (if the operator graded any) feed back into the next scorecard.
   **W32–W35 accuracy term in the next scorecard is the first signal whether
   the data quality backlog is now clearing.**

**Suites green this session:** was 14/18, now 15/18 (test_f44 fixed).
Suites red: 3 (test_baseline, test_f49, test_f52) — all test-data drift, deferred.
**Suites red that are NOT test-data drift:** 0.

**Total commits on master:** 107 (was 99 in the 2026-07-31 handoff; +5 from this
delta, +3 from the silent Aug run).
**Total tasks in ledger:** 67 (was 32 in the 2026-07-31 handoff; +35 from the
silent Aug run, mostly W32–W35 with a few 002-content failures on tool calls).
**W32–W35 scorecards on disk:** 4 (W32: F=0.467, W33: F=0.35, W34: F=0.5, W35: pending
this Sunday's 04:00 fire).
**Containment status:** zero `runs/quarantine_*.json`, `PROTECTED_PATHS` unchanged at
12 entries (53 tracked files under protection), `fs-guard` still catching what it
should, `_untracked_files()` still flagging the uncommitted `simulate.py` as expected.
