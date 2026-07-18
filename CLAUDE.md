# AGI_like — Cognitive AI Harness (project instructions)

One autonomous AI employee that measurably improves at one job. Full design: [HARNESS_DESIGN.md](HARNESS_DESIGN.md).
Milestone 1 = research/BI analyst. Runtime backbone = **Hermes Agent** (`%LOCALAPPDATA%\hermes`). This is the harness's own project dir; the deliverable is a shipped, verified, self-improving employee — not an exploration report.

## Model hierarchy (locked 2026-07-17; CURRENT routing verified 2026-07-18)
- **Manager / critic brain** = `glm-5.2:cloud` (Ollama) right now — operator chose to stay Ollama-only (accepting quota-parked stretches) over adding an Anthropic key. The Anthropic path stays PREFERRED and pre-wired (commented block in `config/models.yaml`) for whenever that changes; not a re-decision, just flip the comment.
- **Worker** = `kimi-k2.7-code:cloud` (Ollama Cloud) for bulk research/analysis — runs with the DEFAULT Hermes toolset (browser/web tools; real research needs `browser_*`, not a bare "web" toolset — see Ground-truth rules).
- **Fallback** = local `gemma4:12b` (MEASURED 1.54 tok/s — emergency/offline batch only, never a live role).
- All routing lives in `config/models.yaml`. Swapping a model = editing that file, never code. Keep it that way (model-agnostic is a hard constraint).

## Ground-truth rules (verified, do not relitigate)
- Ollama Cloud under Pro **rate-limits (HTTP 429) under ordinary load** — treat quota exhaustion as normal: park tasks, retry with backoff, batch overnight. Cross-provider fallback, not just cross-model.
- Never enter credentials or move money (WIDE autonomy excludes: money, credentials, irreversible deletions outside workspace).
- All agent writes confined to `workspace/`; all outcomes logged to `ledger/ledger.db` (append-only). If it's not in the ledger, it didn't happen.
- **The worker never writes to `ledger.db`/`ledgerbook.db` directly, by construction, not by trust.** An unrestricted worker once did exactly that (wrote its own rows + self-graded its own task — see `docs/INCIDENTS.md` 2026-07-18). Root cause was handing the worker our own internal paths/schema as context; fix was a minimal, path-free prompt PLUS `db_integrity_check()` in `orchestrator/batch_runner.py`, which snapshots row counts before/after every worker call and quarantines+reverts anything unauthorized. Toolset flags (`-t web`) do NOT reliably restrict Hermes tool access — don't rely on them for containment; the integrity guard is the real boundary.
- **Telegram delivery needs `TELEGRAM_HOME_CHANNEL` explicitly set** — `hermes send --to telegram` (bare platform) does NOT infer it from the discovered channel directory, even once a chat exists. Set via `hermes config set TELEGRAM_HOME_CHANNEL <chat_id>` (plain config, not a credential). Also: if a platform integration silently drops messages with zero trace anywhere (no session, no pairing request), check `hermes doctor` first — an unmigrated config schema (`v0` vs current) can break dispatch with no error output at all.
- Secrets live in `.env` only — never in MEMORY.md, missions, or committed files. (An ElevenLabs key was found in Hermes MEMORY.md 2026-07-17 — that class of leak is the exact prompt-injection exfil surface we defend against.)
- Compliance floor: official APIs only, no scraping behind logins, no bot-posting, no trending copyrighted audio in commercial work.

## Loop (per HARNESS_DESIGN §2.1) — BUILT, all stages live
Mission (`missions/*.md`) → Plan (manager) → Execute (workers, `hermes -z` + `--usage-file`) → Evaluate (critic vs pass-criteria WRITTEN BEFORE the run) → Memory update (`orchestrator/batch_runner.py:extract_facts()` → `memory/ledgerbook.db`) → Skill improvement (`orchestrator/promote.py` — gated by OPERATOR approval, not auto; skills are repo-versioned markdown in `skills_analyst/<mission>/`, NOT Hermes-installed skills — rollback is a `git rm`+commit, zero supply-chain surface) → Next batch (cron, `schtasks`, 4 tasks named `AGI_M1_*`).

## Fitness (prove improvement, don't claim it)
`F = 0.35·completion + 0.30·accuracy + 0.25·(1−intervention) + 0.10·cost_eff`, logged per task, weekly scorecard (`orchestrator/scorecard.py`, delivered via Telegram — LIVE since 2026-07-18). Weights fixed 8 weeks. 5 fixed canary tasks re-run weekly (`missions/_CANARIES.md`); a promoted skill whose canary green-count drops below its approval baseline auto-rolls-back (judged only on complete data — never while a canary is quota-parked).

## Kill switch
`hermes gateway stop` + pause cron = full halt. Nightly `hermes backup` + `git -C S:\AGI_like push` = recovery.

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
  `MIGRATION.md` (OpenClaw→Hermes decision record)

## When editing here
Smallest change that works. Prove it by running it. A measurement beats a code-reading. Report failures verbatim.
