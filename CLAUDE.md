# AGI_like — Cognitive AI Harness (project instructions)

One autonomous AI employee that measurably improves at one job. Full design: [HARNESS_DESIGN.md](HARNESS_DESIGN.md).
Milestone 1 = research/BI analyst. Runtime backbone = **Hermes Agent** (`%LOCALAPPDATA%\hermes`). This is the harness's own project dir; the deliverable is a shipped, verified, self-improving employee — not an exploration report.

## Model hierarchy (locked 2026-07-17; CURRENT routing verified 2026-07-18)
- **Manager / critic brain** = `glm-5.2:cloud` (Ollama) right now — operator chose to stay Ollama-only (accepting quota-parked stretches) over adding an Anthropic key. The Anthropic path stays PREFERRED and pre-wired (commented block in `config/models.yaml`) for whenever that changes; not a re-decision, just flip the comment.
- **Worker** = `kimi-k2.7-code:cloud` (Ollama Cloud) for bulk research/analysis — runs with the DEFAULT Hermes toolset (browser/web tools; real research needs `browser_*`, not a bare "web" toolset — see Ground-truth rules).
- **Fallback** = local `gemma4:12b-ctx4k` (MEASURED 1.54 tok/s — emergency/offline batch only, never a live role). Plain `gemma4:12b` is NOT what's configured: it OOMs on load every time (262k-token default context vs. 4GB VRAM) and has never once completed a task — the `-ctx4k` Modelfile variant (F38) is the one that actually runs.
- All routing lives in `config/models.yaml`. Swapping a model = editing that file, never code. Keep it that way (model-agnostic is a hard constraint).

## Ground-truth rules (verified, do not relitigate)
- Ollama Cloud under Pro **rate-limits (HTTP 429) under ordinary load** — treat quota exhaustion as normal: park tasks, retry with backoff, batch overnight. Cross-provider fallback, not just cross-model.
- Never enter credentials or move money (WIDE autonomy excludes: money, credentials, irreversible deletions outside workspace).
- All agent writes confined to `workspace/`; all outcomes logged to `ledger/ledger.db` (append-only). If it's not in the ledger, it didn't happen.
- **The worker never writes to `ledger.db`/`ledgerbook.db` directly, by construction, not by trust.** An unrestricted worker once did exactly that (wrote its own rows + self-graded its own task — see `docs/INCIDENTS.md` 2026-07-18). Root cause was handing the worker our own internal paths/schema as context; fix was a minimal, path-free prompt PLUS `db_integrity_check()` in `orchestrator/batch_runner.py`, which snapshots row counts before/after every worker call and quarantines+reverts anything unauthorized. Toolset flags (`-t web`) do NOT reliably restrict Hermes tool access — don't rely on them for containment; the integrity guard is the real boundary.
- **Telegram delivery needs `TELEGRAM_HOME_CHANNEL` explicitly set** — `hermes send --to telegram` (bare platform) does NOT infer it from the discovered channel directory, even once a chat exists. Set via `hermes config set TELEGRAM_HOME_CHANNEL <chat_id>` (plain config, not a credential). Also: if a platform integration silently drops messages with zero trace anywhere (no session, no pairing request), check `hermes doctor` first — an unmigrated config schema (`v0` vs current) can break dispatch with no error output at all.
  - **RE-VERIFIED 2026-07-31, and `hermes doctor` LIES about this one — do not act on its warning.** Doctor reports `⚠ Unknown top-level config key 'TELEGRAM_HOME_CHANNEL' — it will be ignored`. **That is false.** `hermes_cli/send_cmd.py:_load_hermes_env()` bridges EVERY top-level scalar in `config.yaml` into `os.environ` (skipping keys already in the env), and its docstring names this key as exactly where `hermes config set` puts it. Measured: env var absent before the bridge, `'6173105867'` after. Doctor validates the config *schema*, which this key is not part of — but it is consumed via the bridge, so "ignored" is wrong. The rule above stands; deleting the key to satisfy doctor would BREAK `scorecard.send_telegram()` (verified live: returns `True` and delivers today). **Lesson: a tool's own diagnostic is a code-read, not a measurement.**
  - **Prefer the explicit `telegram:<chat_id>` form for anything new** (e.g. cron `--deliver telegram:6173105867`). It resolves without depending on the bridge at all, so it survives a config migration that drops the key. Both forms verified delivering 2026-07-31.
- Secrets live in `.env` only — never in MEMORY.md, missions, or committed files. (An ElevenLabs key was found in Hermes MEMORY.md 2026-07-17 — that class of leak is the exact prompt-injection exfil surface we defend against.)
- Compliance floor: official APIs only, no scraping behind logins, no bot-posting, no trending copyrighted audio in commercial work.

## Loop (per HARNESS_DESIGN §2.1) — BUILT, all stages live
Mission (`missions/*.md`) → Plan (manager) → Execute (workers, `hermes -z` + `--usage-file`) → Evaluate (critic vs pass-criteria WRITTEN BEFORE the run) → Memory update (`orchestrator/batch_runner.py:extract_facts()` → `memory/ledgerbook.db`) → Skill improvement (`orchestrator/promote.py` — gated by OPERATOR approval, not auto; skills are repo-versioned markdown in `skills_analyst/<mission>/`, NOT Hermes-installed skills — rollback is a `git rm`+commit, zero supply-chain surface) → Next batch (cron, `schtasks`, 4 tasks named `AGI_M1_*`).

## Fitness (prove improvement, don't claim it)
`F = 0.35·completion + 0.30·accuracy + 0.25·(1−intervention) + 0.10·cost_eff`, logged per task, weekly scorecard (`orchestrator/scorecard.py`, delivered via Telegram — LIVE since 2026-07-18). Weights fixed 8 weeks. 5 fixed canary tasks re-run weekly (`missions/_CANARIES.md`); a promoted skill whose canary green-count drops below its approval baseline auto-rolls-back (judged only on complete data — never while a canary is quota-parked).

## Kill switch
`hermes gateway stop` + pause cron = full halt. Nightly `hermes backup` + `git -C S:\AGI_like push` = recovery.
Remote configured 2026-07-30: `origin` → `github.com/rkeerthi22/AGI_inspired` (SSH, key-based,
no stored password), default branch `master`. Offsite DB snapshots + a verified `git bundle`
also land in OneDrive on every nightly backup — two independent off-disk copies, not one.

## Directory map
- `config/` — models.yaml (routing), policy.yaml (deny-list, cost caps, autonomy)
- `missions/` — one file per standing goal; `_TEMPLATE.md` is the schema; `_M1_INDEX.md` is the
  live status table + operator duties; `_CANARIES.md` is the fixed regression set
- `ledger/` — schema.sql, init_ledger.py, ledger.db (source of truth, gitignored binary)
- `memory/` — ledgerbook.db (typed facts w/ validity windows, gitignored), `scorecards/*.md`
  (committed weekly views)
- `orchestrator/` — thin Python over Hermes oneshots (stdlib only, no framework lock-in):
  `batch_runner.py` (execution + containment + memory-update), `ledger.py` (fitness math),
  `scorecard.py` (weekly report + Telegram), `spotcheck.py` (operator verdict CLI),
  `promote.py` (skill review/approve/reject/rollback)
- `skills_analyst/<mission_id>/*.md` — ACTIVE promoted technique notes (git-versioned, operator-
  approved, injected into that mission's worker prompts); `_candidates/` awaiting approval,
  `_rejected/` audit trail
- `workspace/` — agent scratch (gitignored); `inbox/` — user drops data here for nightly ingest
- `runs/` — per-run usage.json + raw worker output + logs (gitignored); `quarantine_*.json` here
  would mean the integrity guard caught an unauthorized write (none since the fix)
- `docs/` — `INCIDENTS.md` (real bugs found + fixed, with root cause and lesson),
  `MIGRATION.md` (OpenClaw→Hermes decision record), `HARDENING.md` (the append-only F1…Fn fix
  registry — every hardening fix, its root cause, and its regression test)
- `tests/` — regression suite guarding the harness's own safety/correctness (fs-guard,
  containment, clock-domain, canary accounting); `python tests/run_all.py` runs all of it
- `.claude/HANDOFF.md` — running session-handoff doc: architectural decisions, unresolved
  action items, environment facts — read this first when picking work back up
- `.harness/continuity/current.json` — small current recovery checkpoint. After context
  compaction, model switch, crash recovery, or resume: read and validate this first with
  `python orchestrator/continuity.py recover`; verify live Git/runtime/database state and
  resolve every disagreement in favour of live state; then read only the referenced durable
  records needed for the next action. The brief is a locator, never a source of truth.

## When editing here
Smallest change that works. Prove it by running it. A measurement beats a code-reading. Report failures verbatim.

## Operator directive — expanded budget (2026-07-29)

> **⚠ SUPERSEDED IN PART by the 2026-07-31 directive at the end of this file.** Items 1, 2 and 4
> (batch sizes, faster retries, spot-check backlog) still stand — they are execution. Items 3 and 5
> (diagnose/fix synthesis, draft skill candidates) are **PAUSED**: both are building, and building is
> what the newer directive stops. Read the newer one before acting on this one.

**Context:** token quota increased, time budget expanded, operator is available for more throughput.
The previous conservative posture (park early, single-retry, minimal batch sizes) was correct for
baseline weeks when we were still finding P0 bugs in the harness. That phase is over. The hardening
audit is complete (20 findings, all addressed), cron scheduling is verified, failover chain is
proven, and the integrity guards are live-tested. Push forward.

### What changes

1. **Batch sizes — use the full budget.** Stop parking tasks at the first quota signal when there's
   remaining daily budget. If `tokens_used_today()` says there's room, run the next task. The
   previous behavior of "parking remaining tasks" after a single quota hit was appropriate when
   quota was scarce — it no longer is. Run all seeds per mission per fire, not just until the
   first 429.

2. **Retry failed tasks sooner.** Tasks that fail critic review should be retried in the SAME
   batch run if token budget permits, not deferred to next week's cron fire. The current
   once-per-week cadence means a failed synthesis waits 7 days — that's too slow now.

3. **Synthesis tasks — diagnose and fix.** Synthesis (seed 4) has failed every attempt across
   W27 and W31. This is now a pattern, not a fluke. Investigate why: is it the prompt, the
   done-definition, the context window, or the lack of prior-week data? Fix it rather than
   letting it fail-and-defer indefinitely.

4. **Spot-check backlog — process it.** There are done tasks inside the scoring window (#26,
   #28, #29) that need human verdicts to feed accuracy. Flag these to the operator proactively
   via Telegram, not just on `spotcheck.py list`.

5. **Skill promotion — be less conservative.** The current evidence bar (≥2 corroborating
   lessons) has produced zero candidates in 3 weeks. If mission 002 has 3 lessons in the pool,
   that already exceeds the bar — draft a candidate and surface it for operator review. Don't
   wait for a perfect signal.

6. **Mission 003 (adforge) stays on hold.** More budget does not mean fabricating a client.
   This directive expands throughput on existing active missions, not scope.

### What does NOT change

- **Safety posture is unchanged.** All integrity guards, containment checks, quarantine logic,
  and deny-list rules remain in force. More budget ≠ less caution.
- **Human gate on skill promotion stays.** No auto-promotion. Faster drafting, same approval
  process.
- **Ledger honesty stays.** If a task fails, it's recorded as failed. The point is to have
  MORE attempts, not to inflate the numbers.
- **Cost tracking stays.** Every token is still logged. The budget is larger, not unmetered.

### TL;DR for the agent
You have more fuel. Use it. Run more tasks, retry failures faster, fix the synthesis problem,
draft skill candidates when the evidence is there. The operator is engaged and checking in —
surface things that need human review via Telegram rather than waiting to be asked. The 8-week
clock is ticking and we're in Week 3 — the improvement curve needs data points, and data points
come from attempts, not from parking.

## Operator directive — EXECUTION ONLY, weeks 4–8 (2026-07-31) · **LOCKED**

**Context:** the harness rates ~6/10 against a finished system, and the split is lopsided — the
*self-hardening* machinery is 8–9/10 (53 fixes with root causes, 17 regression suites, containment
guards that have caught real violations, DR drilled), while *evidence the employee is improving* is
2–3/10. Three weeks in, the improvement curve has almost no points on it, and F53 just showed that
some of what it does have was compromised: 35% of every fitness score was awarded unconditionally.
Operator's call, agreed on the evidence: **the bottleneck is no longer system quality. It is data.**

### The rule

**Weeks 4–8 are execution and independent human verdicts. Nothing else.** Do not add features. Do
not add hardening. Do not refactor. The system is now exceptionally good at measuring itself; what
it lacks is uncompromised measurements to take.

### What this means concretely

- **DO:** run batches, retry failures, re-run parked canaries when quota allows, push the spot-check
  queue to the operator, deliver scorecards, keep the ledger honest.
- **DO NOT:** open new F-numbers for anything that is not actively breaking a run; write new
  orchestrator modules; wire `simulate.py` into the loop; expand mission scope; "improve" the
  fitness function (W is LOCKED and F53 is the last word on its data).
- **The one standing exception:** a defect that is actively corrupting the data being collected is
  still a P0 and gets fixed immediately — a harness that silently poisons its own evidence defeats
  the entire point of an execution-only phase. The bar is "this run is producing false numbers right
  now", not "this could be better".

### Why this is the right call, recorded so it is not relitigated

Every additional hardening fix has diminishing returns against a system already at 17/17 green,
while every week without independent verdicts permanently costs a data point that cannot be
recovered later — the 8-week window closes on schedule whether or not it has evidence in it.
Accuracy is 30% of fitness and is **the only term the system cannot produce for itself**. As of
2026-07-31 all in-window spot-checks are AI-performed, so that 30% is currently self-graded.

### The single highest-value action available

`python orchestrator/spotcheck.py notify` pushes the pending queue to Telegram (delivery verified
live 2026-07-31). Two operator verdicts convert the accuracy term from self-assessment into
evidence. That is worth more to this project right now than any code that could be written.
