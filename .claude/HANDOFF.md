# Handoff — AGI_like (M1 research/BI analyst harness) · updated 2026-07-30

Registry source of truth: `S:\AGI_like\docs\HARDENING.md` (F1–F52 all written up there as of this
session — **the F43 gap noted in the previous handoff is now CLOSED**). F1–F42 were originally
reconstructed from HARDENING.md entries on disk, not from memory; F43–F51 were written as they landed.

HEAD = `11037da`. **Working tree fully clean** — this file is now tracked (F52), so there are no
untracked entries at all.

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
- **The nested worktree stays COLLAPSED in the untracked scan** — `.claude/worktrees/jolly-gauss-8e52cb/`
  is recorded as `<nested-repo>`: appearance/disappearance detected, internal churn not. Expanding it
  would point a delete-on-sight remediation at a live git checkout whose `.git` internals and
  `__pycache__` change for legitimate reasons, buying visibility into a directory the harness never
  executes from. **DECIDED 2026-07-30 (operator).** If the residual ever needs closing, the move is
  `git worktree remove .claude/worktrees/jolly-gauss-8e52cb` — delete the blind spot rather than
  police it.
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
F46–F51 from this session.

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
- **F52** Committing the handoff removed it from the containment surface → `.claude/HANDOFF.md` was covered by `_untracked_files()`'s hashes (F46/F47) **precisely because it was untracked**; `git add` moved it out of that set without moving it into `_tracked_hashes()`, which walks only `PROTECTED_PATHS` — and `.claude` was not listed. Measured a minute after the commit: a simulated tamper produced an EMPTY delta on all four channels (tracked hashes, new untracked, tampered untracked, porcelain). The blinded tree is Claude Code's own config dir — the one place a file steers the SUPERVISING agent, which is why F46 refused to *gitignore* it; tracking achieved the same blindness by the opposite route → `.claude` added to `PROTECTED_PATHS`; tamper now caught on TWO channels, protected tracked files 51→53, worktree still collapsed. **General lesson: changing a file's VCS status silently changes which guard covers it** — `git add` is not usually thought of as security-relevant. F42→F46→F47→F52 is one hole reappearing per layer. Knock-ons handled not suppressed: `test_f47`'s at-rest baseline legitimately moved (now asserts the ONLY at-rest mask is the DECIDED worktree), and its section 7 was re-pointed from the untracked channel to the tracked one, which is *stronger* since `git ls-files` ignores exclude rules entirely. Residual: F47 now WARNs on every snapshot about the worktree — `git worktree remove` clears it at source. P1 · found + fixed 2026-07-30 · `tests/test_f52.py` (10 assertions incl. the pre-F52 surface shown not to cover the file at all)

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
  `runs/.batch.lock` exists. **15 suites, 15/15 green** (measured 2026-07-30 08:22).
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
| Git remote | **NOT started** | `git remote -v` empty; **81 commits** (an earlier handoff said 22 — wrong; measured `git rev-list --count HEAD`) exist only on this laptop |

---

## 5. Unresolved action items

**Blocked on user:**

1. ~~**C: is at 1.6 GB free and falling.**~~ **LARGELY RESOLVED 2026-07-30** — operator approved
   clearing Temp; 10.7 GB reclaimed (439 entries, 31 skipped as in-use, `claude\` scratchpad
   excluded), verified by re-running the full suite and confirming the repo untouched.
   **Residual, and it is drifting back:** C: read 14.42 GB right after the clear and **12.74 GB**
   at the final sweep six hours later — still **7.3 GB under** CLAUDE.md's 20 GB floor, and the
   downward trend has resumed. Temp no longer holds an obvious next target, so meeting the floor
   needs a different reclaim source. Not urgent for Sunday's fire.
2. **Spot-checks — WORK DONE 2026-07-30, INDEPENDENCE STILL MISSING.** All 7 requested tasks
   (1, 5, 18, 27, 28, 29, 30) were verified against live sources and recorded `pass`, each note
   prefixed `AI-PERFORMED CHECK` per F28. W31 accuracy **33% → 71%**, fitness **0.8 → 0.914**.
   Only 4 of the 7 could move the metric — **#1, #5 and #18 predate the 7-day window** (starts
   2026-07-23) and were recorded for the audit trail only.

   **The remaining gap is not effort, it is independence.** All **7/7** in-window checks are now
   AI-performed, so W31's accuracy term is entirely self-assessed — exactly the condition F28
   exists to make visible, and the scorecard says so in both the file and the Telegram line.
   Question: will you re-run `python orchestrator/spotcheck.py pass|fail <id>` on even **two** of
   **27, 28, 29, 30**? Re-running overwrites the row with a genuine independent read, which is the
   only thing that converts this from a self-graded number into evidence. What was actually
   verified, so you can spot-audit my work rather than repeat it:
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
4. **Git remote — the ONLY step left to actually close the single-disk risk.** **81** commits (both
   skills, the whole F1–F52 record, the regression suites) exist on one physical disk.
   **Half-closed 2026-07-30:** `backup.py` now writes and verifies a `git bundle` of the full
   history into `backups/` on every nightly run, and a restore drill (`git clone` from the bundle)
   rebuilt every commit intact. That makes the history a **single portable file** — but
   `backups/` is on the same disk, so it protects against repo corruption and mistakes, **not**
   disk failure. Two ways to finish, either of which is one operator action:
   - **Cheapest, no account:** copy the newest `backups/repo_*.bundle` to any USB stick or other
     machine. Verify anywhere with `git bundle verify <file>`; restore with `git clone <file> <dir>`.
   - **Proper remote:** create an empty private repo, then `git remote add origin <url>` and
     `git push -u origin master`. Tell me when the remote exists and I will verify the push
     matches (`git rev-list --count`, ref comparison). **I will not create the repo, add the
     remote, or enter credentials** — that is a prohibited action class, not a preference.

   Configuring `config/offsite_backup.path` (item 3) also gets the bundle off-disk automatically,
   since `replicate_bundle_offsite()` uses the same destination and the same same-disk refusal.
5. **Second provider (F39/F41 payoff).** The chain is cross-*model*, not cross-*provider*, so it
   cannot survive an account-level 429. Uncommenting `{ provider: anthropic, model: claude-sonnet-5 }`
   in `config/models.yaml` needs a key in Hermes `.env`. Currently **LOCKED** to Ollama-only.

**Doable now (assistant):**

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
9. **Optional, on request:** `git worktree remove .claude/worktrees/jolly-gauss-8e52cb` would delete
   the F47 residual rather than police it. Not done — it is your worktree.
10. ~~**Canary token spend is never recorded.**~~ **FIXED 2026-07-30 as F48** (see registry). One
    residual, stated rather than papered over: a canary killed by `subprocess.TimeoutExpired` still
    records nothing, because that path has no `usage` to record — the same limitation `finish_task()`'s
    F21 comment describes. **Historical rows were NOT backfilled:** the six existing canary rows still
    read 0/0. `runs/canary_<name>.usage.json` may hold some of that spend, but `runs/` is gitignored
    and periodically cleaned, so a backfill would be partial and would rewrite an append-only ledger —
    not done without an explicit call.

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
  **It is drifting back down: 12.74 GB at the 08:22 sweep, ~1.7 GB lost in six idle hours**, so
  this is a recurring consumer rather than a one-off, and the reclaim bought time rather than a
  fix. Still **7.3 GB under CLAUDE.md's 20 GB floor**; Temp holds no obvious next 5 GB, so meeting
  the floor needs a different target. S: **98.04 GB free**.
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
  `S:\AGI_like\.git\info\exclude` (md5 `83316bf39d231e3d954929e9f606a762`, contains
  `.claude/worktrees/`) and `C:\Users\moham\.config\git\ignore` (contains
  `**/.claude/settings.local.json`) are **unversioned** — `core.excludesFile` is unset, yet git
  honours that XDG path anyway.
- **Untracked non-ignored set is 2 entries:** `.claude/HANDOFF.md` (hashed) and
  `.claude/worktrees/jolly-gauss-8e52cb/` (`<nested-repo>`, unenumerable by git). Stable across
  consecutive calls — no spurious deltas.
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
- **Working tree clean at handoff apart from untracked `.claude/`.** HEAD = `36f8f18`.

---

## 7. Final verification sweep — 2026-07-30 08:22

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
