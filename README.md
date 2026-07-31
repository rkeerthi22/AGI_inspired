# AGI_like — Cognitive AI Harness

An autonomous AI "employee" designed to measurably improve at one job over time, rather
than being re-prompted from scratch on every run. Milestone 1 is a research / BI analyst.

Full design and rationale: [HARNESS_DESIGN.md](HARNESS_DESIGN.md). Operating rules and
current project state: [CLAUDE.md](CLAUDE.md).

## How it works

The harness runs a fixed loop, per mission:

```
Mission (missions/*.md)
  -> Plan (manager model)
  -> Execute (worker models)
  -> Evaluate (critic, against pass-criteria written BEFORE the run)
  -> Memory update (facts extracted into a typed, provenance-tracked store)
  -> Skill improvement (operator-approved technique notes, injected into future prompts)
  -> Next batch (scheduled)
```

Model routing is entirely config-driven (`config/models.yaml`) — swapping which model
plays manager, worker, or fallback never requires a code change.

## Fitness

Every task is scored against a fixed formula (weights locked for the milestone's full
duration):

```
F = 0.35*completion + 0.30*accuracy + 0.25*(1 - intervention) + 0.10*cost_eff
```

A weekly scorecard tracks the trend, and a fixed set of canary tasks re-runs on a
schedule to catch regressions before they compound. Skill promotion (a technique note
that gets injected into every future prompt for a mission) is gated on operator
approval — never automatic.

## Directory map

| Path | Purpose |
|---|---|
| `config/` | Model routing, policy (deny-list, cost caps, autonomy scope) |
| `missions/` | One file per standing goal |
| `ledger/` | Append-only task ledger (source of truth for what actually ran) |
| `memory/` | Typed facts with provenance and validity windows |
| `orchestrator/` | The harness itself — execution, containment, fitness math, scoring |
| `skills_analyst/` | Operator-approved technique notes, versioned per mission |
| `tests/` | Regression suite guarding the harness's own safety and correctness |
| `docs/` | Incident write-ups and the fix registry, with root causes |
| `workspace/` | Agent scratch space (all agent writes are confined here) |

## Safety posture

- All agent writes are confined to `workspace/`; every outcome is logged to an
  append-only ledger — if it's not logged, it didn't happen.
- Workers never write to the ledger directly, by construction, not by trust.
- Official APIs only. No scraping behind logins, no bot-posting, no unofficial platform
  automation.
- Secrets live outside version control, always.

## Verifying the harness

```
python tests/run_all.py
```

Runs the full regression suite. See `docs/` for the fix registry and incident history
behind each guard.
