# AGI_like — Cognitive AI Harness (project instructions)

One autonomous AI employee that measurably improves at one job. Full design: [HARNESS_DESIGN.md](HARNESS_DESIGN.md).
Milestone 1 = research/BI analyst. Runtime backbone = **Hermes Agent** (`%LOCALAPPDATA%\hermes`). This is the harness's own project dir; the deliverable is a shipped, verified, self-improving employee — not an exploration report.

## Model hierarchy (locked 2026-07-17)
- **Manager / critic brain** = frontier model (Claude via Anthropic key, or best available). Low-volume, high-value: planning, evaluation, memory promotion.
- **Workers** = Ollama Cloud models (`glm-5.2:cloud`, `kimi-k2.7-code:cloud`) for bulk research/analysis.
- **Fallback** = local `gemma4:12b` (MEASURED 1.54 tok/s — emergency/offline batch only, never a live role).
- All routing lives in `config/models.yaml`. Swapping a model = editing that file, never code. Keep it that way (model-agnostic is a hard constraint).

## Ground-truth rules (verified, do not relitigate)
- Ollama Cloud under Pro **rate-limits (HTTP 429) under ordinary load** — treat quota exhaustion as normal: park tasks, retry with backoff, batch overnight. Cross-provider fallback, not just cross-model.
- Never enter credentials or move money (WIDE autonomy excludes: money, credentials, irreversible deletions outside workspace).
- All agent writes confined to `workspace/`; all outcomes logged to `ledger/ledger.db` (append-only). If it's not in the ledger, it didn't happen.
- Secrets live in `.env` only — never in MEMORY.md, missions, or committed files. (An ElevenLabs key was found in Hermes MEMORY.md 2026-07-17 — that class of leak is the exact prompt-injection exfil surface we defend against.)
- Compliance floor: official APIs only, no scraping behind logins, no bot-posting, no trending copyrighted audio in commercial work.

## Loop (per HARNESS_DESIGN §2.1)
Mission (`missions/*.md`) → Plan (manager) → Execute (workers, `hermes -z` + `--usage-file`) → Evaluate (critic vs pass-criteria WRITTEN BEFORE the run) → Memory update (ledger + `memory/ledgerbook.db`) → Skill improvement (gated promotion, `hermes skills`/`curator`) → Next batch (cron).

## Fitness (prove improvement, don't claim it)
`F = 0.35·completion + 0.30·accuracy + 0.25·(1−intervention) + 0.10·cost_eff`, logged per task, weekly scorecard. Weights fixed 8 weeks. 5 fixed canary tasks re-run weekly; a promoted skill that breaks a canary auto-rolls back.

## Kill switch
`hermes gateway stop` + pause cron = full halt. Nightly `hermes backup` + `git -C S:\AGI_like push` = recovery.

## Directory map
- `config/` — models.yaml (routing), policy.yaml (deny-list, cost caps, autonomy)
- `missions/` — one file per standing goal; `_TEMPLATE.md` is the schema
- `ledger/` — schema.sql, init_ledger.py, ledger.db (source of truth, gitignored binary)
- `memory/` — ledgerbook.db (typed facts w/ validity windows), markdown views (committed)
- `orchestrator/` — thin Python over Hermes oneshots (stdlib only, no framework lock-in)
- `workspace/` — agent scratch (gitignored); `inbox/` — user drops data here for nightly ingest
- `runs/` — per-run usage.json + logs (gitignored)

## When editing here
Smallest change that works. Prove it by running it. A measurement beats a code-reading. Report failures verbatim.
