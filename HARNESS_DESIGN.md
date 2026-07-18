# Cognitive AI Harness — Design Document (Phase 2)

**Date:** 2026-07-17 · **Status:** Draft for approval · **Author:** Claude (Fable 5) with user decisions locked 2026-07-17
**Milestone 1:** one autonomous research/BI analyst that measurably improves at its job. **Milestone 2:** content-ops agent.
**Locked decisions:** hybrid model strategy on Ollama Pro · WIDE autonomy (all autonomous except money, credentials, irreversible deletions) · model-agnostic (LLM swap = config change).

---

## 0. Target capability — the precise definition (gap 1)

Not "AGI." The target is:

> **An agent that, given a standing mission and a task queue in its domain (market/competitor/product research for the user's ventures), completes ≥70% of tasks end-to-end without human help at ≥90% accuracy on human spot-checks, with an intervention rate that declines ≥30% over 8 weeks, at ≤$0.50 average cost per task.**

Every term is measurable and every measurement has a home (the task ledger, §3). "Improvement" means the 4-week moving trend of the fitness function (§3.2) is positive AND the canary set (§3.3) doesn't regress.

---

## 1. Research findings

### 1.1 Environment ground truth (measured live 2026-07-17)

| Fact | Value | How verified |
|---|---|---|
| Hermes Agent | v0.18.2 (2026.7.7.2), git install, actively maintained | `hermes --version`, repo inspection |
| Hermes subsystems present | memory (MEMORY.md/USER.md + 7 pluggable external providers), skills + **curator** (auto-backup before every run, archive/rollback, never auto-deletes), cron, sessions w/ FTS5 search, subagent delegation, oneshot mode with `--usage-file` cost reports, MCP client/server, Telegram **already configured** | help texts, `hermes status` |
| OpenClaw | 2026.5.22, stale on this machine since 2026-05-24, one WhatsApp-bound agent on gemma4:12b | config inspection |
| Ollama | 0.32.1; local model gemma4:12b (7.6 GB); cloud models glm-5.2:cloud / kimi-k2.7-code:cloud referenced in configs; user has **Ollama Pro ($20/mo)** | `ollama list`, configs, user |
| API keys | GitHub only. No Anthropic/OpenAI/Tavily/Firecrawl | `hermes status` |
| Hardware | 15.7 GB RAM, RTX 3050 4 GB VRAM; C: 18.7 GB free (below user's 20 GB floor), S: 112.3 GB free | live measurement |
| gemma4:12b speed | **PENDING — benchmark running at time of writing; see §1.6 addendum** | `ollama run --verbose` |
| glm-5.2:cloud access | **PENDING — probe running; see §1.6 addendum** | `ollama run` probe |

### 1.2 Memory architectures (≥3 alternatives)

| System | Approach | Strengths | Weaknesses for this project |
|---|---|---|---|
| **Mem0** | 3-tier scopes (user/session/agent), hybrid vector+graph+KV | Easiest managed adoption; ECAI-2025 paper baseline | Service dependency; 49.0% on LongMemEval (GPT-4o) — weak temporal reasoning |
| **Zep / Graphiti** | Temporal knowledge graph; facts carry validity windows (true from X until Y), superseded not overwritten | 63.8% LongMemEval — best-in-class temporal accuracy; the right *model* of changing facts | Server + graph DB to run locally; overkill ops burden for one laptop |
| **Letta (MemGPT)** | OS-style: context = RAM, archival = disk, agent self-manages via memory tools | Right philosophy for long-running agents | Own server runtime; duplicates what Hermes already does |
| **Hermes built-in** | MEMORY.md/USER.md, agent-curated with periodic nudges; FTS5 session search; external providers pluggable (incl. mem0, honcho) | Zero new infra; already writes/curates; searchable past sessions | Freeform — no typed schema, no validity windows, no provenance |

**Chosen design: Hermes-native memory for identity/preferences + a custom typed store ("the Ledgerbook") for domain knowledge.** SQLite + markdown views in `S:\AGI_like\memory\`, schema borrowed from Zep's key insight (temporal validity windows, supersede-don't-overwrite) and from SOAR/ACT-R's declarative-vs-procedural split (facts vs skills). Justification vs the three alternatives: no server dependencies on a 4 GB-VRAM laptop, plain-file auditability (WIDE autonomy demands a reviewable memory), and portability — if the backbone ever changes (§4 abandonment criteria), SQLite + markdown moves with us; a Mem0/Zep tenancy doesn't. External-provider escape hatch stays open via `hermes memory setup`.

**Write policy (the part every existing product punts on):**
- Memory types: `fact` (temporal validity + provenance + confidence), `experience` (task outcome + what worked/failed), `decision` (with rationale, immutable), `failure` (with hypothesis + test), `procedure` (promoted to Hermes skill only through the gate in §2.4).
- Temporary vs permanent: everything enters as `candidate` with a 14-day TTL; the weekly consolidation pass (manager + curator) promotes, merges, or expires. Nothing is silently permanent.
- Conflicts: new evidence closes the old fact's validity window and opens a new fact linked to it — history preserved, staleness impossible to confuse with truth.

### 1.3 Multi-agent orchestration (≥3 alternatives)

| Framework | Model | Strengths | Weaknesses for this project |
|---|---|---|---|
| **LangGraph** | Directed graph, conditional edges, checkpointing/time-travel | Largest 2026 production footprint; durable execution; first-class human-in-loop | Heavy new dependency; duplicates Hermes runtime; our flows are simple pipelines |
| **CrewAI** | Role-based crews | Fastest prototyping | Trails on production observability/error recovery; framework lock-in |
| **AutoGen** | Conversational GroupChat | Research pedigree | **Maintenance mode** (Microsoft moved to Agent Framework) — disqualifying for a long-lived system |
| **Hermes native** | Subagent delegation + `-z` oneshot (scriptable, per-run JSON usage report) + cron + kanban | Already installed; usage accounting built in; zero new frameworks | No graph semantics; orchestration logic is ours to write |

**Chosen: Hermes native + a thin Python orchestrator (~300 lines, stdlib only).** The manager is a cron-scheduled Hermes session; workers are `hermes -z` oneshot invocations with `--usage-file` (cost per task lands in the ledger automatically); the critic is a second oneshot with a different prompt and, when affordable, a different model. Justification: smallest thing that works, every run leaves an auditable artifact, and the ledger — not framework state — is the source of truth, so the orchestrator is replaceable. LangGraph is the designated upgrade path if flows outgrow pipelines (trigger in §4).

### 1.4 Skill learning / self-improvement (≥3 alternatives)

| System | Approach | Lesson taken |
|---|---|---|
| **Voyager** (Minecraft) | Ever-growing library of executable skills, retrieval before writing new code, self-verification | Skill = named, reusable, *retrieved first* |
| **Agent Workflow Memory / Agent Skill Induction** | Induce reusable workflows from completed trajectories | Extract after real completions, not from imagination |
| **SkillAudit / SkillsVote (2026)** | Paired-trajectory auditing, lifecycle governance of skills | Skills need lifecycle governance, not just accumulation |
| **Hermes curator** | Background review: prunes stale, consolidates overlaps, archives (recoverable); auto-backup before every run; pin/rollback | Already implements safe custody — versioning, testing hooks, rollback = gap 4's hard parts |

**Chosen: Hermes skills + curator as the mechanism, with a promotion gate we add** (§2.4). The failure mode of every self-improvement loop is confidently wrong lessons compounding; the gate makes lessons enter as hypotheses with attached tests and get promoted only after passing on a real task. One rule the OpenClaw incident record makes non-negotiable: **hub/marketplace skill installs always require human approval** — ~12% of OpenClaw's ClawHub registry (341 of 2,857 skills) was confirmed malicious in 2026.

**AS-BUILT DEVIATION (2026-07-18):** the actual promotion target is **repo-versioned markdown
notes** (`skills_analyst/<mission_id>/*.md`, injected into worker prompts), NOT `hermes skills`
install. Reasoning that changed the decision: rollback needs to be trivial and auditable (git
rm + commit beats `hermes curator rollback` for a system this young); zero supply-chain surface
(the hub-install risk this section itself flags simply doesn't apply if nothing is installed);
model-agnostic (a note is provider-independent text, not a Hermes-specific artifact); every
promotion is a visible diff. Hermes curator's custody properties (auto-backup, archive, pin) are
real strengths but weren't needed once git does the same job for free. See `orchestrator/promote.py`
and §2.4 below for the built mechanism.

### 1.5 Evaluation (≥3 alternatives)

| Benchmark | What it measures | What we take |
|---|---|---|
| **τ-bench (Sierra)** | Tool-agents under policy; **pass^k** = solves same task all k tries | pass^k → our canary-set reliability metric |
| **GAIA** | Real-world assistant questions with verifiable answers | Task style for the analyst's spot-checks |
| **OSWorld / Terminal-Bench** | Desktop/terminal task completion, objective verification | Verification-first task design |
| **AgentBench** | Broad agent suite | Largely superseded in 2026; skip |

**Chosen: a custom fitness ledger** (§3) — public benchmarks measure models, not *this employee on this job*. The user's metrics (tasks completed, interventions, accuracy, improvement) map exactly onto a per-task ledger + weekly scorecard; pass^k on a fixed canary set adds the reliability dimension benchmarks taught us matters.

### 1.6 Kill-assumption measurements — RESULTS (measured 2026-07-17 via Ollama REST API)

| Probe | Result | Design consequence |
|---|---|---|
| gemma4:12b generation speed | **1.54 tok/s** (34.1 s model load; 307 tok in 199.9 s). Output quality fine (correct Rayleigh-scattering answer) | Local 12B is **disqualified from all live agent roles** — a 1,000-token worker output ≈ 11 minutes. Demoted to emergency-fallback/offline-batch only. If a local tier is wanted later, pull a 3–4B model (fits 4 GB VRAM) and re-measure |
| glm-5.2:cloud | **HTTP 429 Too Many Requests** | Pro-plan quota was already exhausted by ordinary daytime use, with zero agent load. This is not a tail risk — it is the steady state to design for |
| kimi-k2.7-code:cloud | **HTTP 429** — account-level, not per-model | Fallback-to-sibling-model does NOT survive quota exhaustion; the fallback chain must cross **providers**, not models |

**UPDATE 2026-07-17 (live onboarding smoke run):** the 429s are a **weekly** cap, now
**exhausted** — the endpoint returns `"you (abishekvr09) have reached your weekly usage limit,
upgrade for higher limits"` for BOTH glm-5.2 and kimi-k2.7. So the manager+worker brain is
fully offline until the weekly reset or a Max upgrade — not a per-minute throttle. This makes
the Anthropic-key-for-manager recommendation load-bearing, not optional, if M1 must run this
week. Two bugs the smoke run also surfaced and fixed: (a) the local Ollama server must be
RUNNING (killing it → connection-refused, which the orchestrator wrongly logged as a completed
task); (b) the orchestrator now classifies quota_wait / infra_failed distinctly from a real
task attempt, so outages never pollute the fitness metric (verified: re-run parks with
tasks_attempted=0).

**Consequences applied to this design:**
1. The orchestrator treats 429 as a normal state: tasks park in `quota_wait`, retry with backoff, resume next window. Overnight batches (cron) exploit idle quota — another reason the laptop stays awake.
2. The daily cost/quota budget in §2.6 gains a quota-burn tracker: manager plans batch sizes against remaining window, not against wishes.
3. **Recommendation (user's money, user's call):** an Anthropic API key for the manager/critic only. Measured evidence: the sole model supply for the entire harness rate-limited during its very first probe. Manager traffic is low-volume/high-value (~10–20 calls/day) — likely a few dollars a month at Haiku/Sonnet-class pricing — and it makes the improvement loop's judge independent of the workers' quota pool. Workers stay on Ollama Cloud. Without it, M1 still proceeds; expect quota-parked evenings.

---

## 2. Architecture proposal

### 2.1 Component diagram (text)

```
                        ┌────────────────────────────────────────────┐
                        │  HUMAN (Telegram — already configured)      │
                        │  approvals · escalations · weekly scorecard │
                        └───────────────▲────────────────────────────┘
                                        │
┌───────────────────────────────────────┴───────────────────────────────────┐
│ MANAGER (cron-scheduled Hermes session, strongest cloud model)             │
│ mission → plan → task specs w/ PRE-WRITTEN pass criteria → assign → review │
│ writes: decisions, priorities, memory promotions                           │
└──────┬──────────────────────┬───────────────────────┬─────────────────────┘
       │ task spec            │ task spec              │ verdicts
┌──────▼──────┐        ┌──────▼──────┐          ┌──────▼──────┐
│ WORKER:     │        │ WORKER:     │          │ CRITIC      │
│ researcher  │        │ analyst /   │          │ (separate   │
│ (hermes -z, │        │ ops         │          │ prompt +    │
│  web ONLY — │        │ (hermes -z) │          │ model, no   │
│  no file/db │        │             │          │  tools)     │
│  by design, │        │             │          │             │
│  see below) │        │             │          │             │
└──────┬──────┘        └──────┬──────┘          └──────┬──────┘
       │ artifacts + usage.json │                      │ pass/fail vs criteria
┌──────▼────────────────────────▼───────────────────────▼──────┐
│ TASK LEDGER (SQLite, append-only)  ←— single source of truth  │
│ + LEDGERBOOK memory (typed facts w/ validity windows)         │
│ + workspace S:\AGI_like\workspace (all writes land here)      │
│ ORCHESTRATOR is the ONLY writer to ledger/ledgerbook — a      │
│ db_integrity_check() snapshots row counts around every worker │
│ call and quarantines+reverts anything else (see docs/         │
│ INCIDENTS.md — an unrestricted worker once wrote its own      │
│ rows and self-graded its own task; this is the real fix)      │
└──────────────────────────┬───────────────────────────────────┘
                           │ Sundays: promote.py review (rides scorecard cron)
                ┌──────────▼──────────┐
                │ SKILL LIBRARY       │  lessons → drafted candidate → OPERATOR
                │ (skills_analyst/    │  approves/rejects → injected into worker
                │  <mission>/*.md,    │  prompts. Rollback = git rm+commit.
                │  git-versioned)     │  Canary-drop → auto-rollback.
                └─────────────────────┘
```

Operating loop (user's spec, mapped): Mission (`missions/*.md`) → Planning (manager) → Execution (workers) → Evaluation (critic vs pre-written criteria) → Memory update (ledger + Ledgerbook) → Skill improvement (gated promotion) → Next batch (cron).

### 2.2 Model routing (model-agnostic — gap: LLM swap = config)

| Role | Default | Swap mechanism |
|---|---|---|
| Manager / critic | `glm-5.2:cloud` (Ollama Pro); **recommended upgrade per §1.6: Anthropic key** | `hermes model` or `-m` flag — no code |
| Workers (bulk) | `kimi-k2.7-code:cloud` (shares the manager's quota pool — see §1.6) | per-invocation `-m` |
| Local tier | gemma4:12b **disqualified for live roles** (1.54 tok/s measured); optional 3–4B model later | config |
| Embeddings (deferred) | FTS5 first; `nomic-embed-text` local if recall demands | config |
| Cross-provider fallback | `hermes fallback` chain must include a second provider, not just a second Ollama model (429 is account-level, measured) | `hermes fallback add` |

The orchestrator passes models as parameters and never hardcodes provider APIs — Hermes is the abstraction layer. This satisfies the model-agnostic constraint by construction.

### 2.3 World model (gap 2) — entities, relations, cause/effect, uncertainty

Not a neural world model (see §5 simulation verdict). A **domain entity store** inside the Ledgerbook: entities (competitor, product, channel, supplier, keyword, trend), typed relations, and *facts about them with provenance (URL/source + retrieval date), confidence (source-count-based, 3 levels), and validity windows*. Cause/effect is recorded as `experience` entries ("action X in context Y → outcome Z"), which is what the manager consults when planning — cheap, honest causal knowledge from own history rather than pretended physics. Uncertainty is explicit: facts below confidence threshold are flagged in reports, never silently asserted.

### 2.4 Safe learning mechanism (gap 4) — BUILT 2026-07-18 (`orchestrator/promote.py`)

Original design (above) targeted `hermes skills`; see the AS-BUILT note in §1.4 for why the
actual mechanism moved to repo-versioned notes. What's actually built and round-trip-verified:

1. On every critic FAIL, the orchestrator calls `ledger.add_lesson()` — `lesson_candidates`
   accumulates real evidence automatically, no separate worker step.
2. Sundays (`promote.py review`, riding the existing scorecard cron — no new schedule): a
   manager call drafts **at most one candidate note per mission** from unpromoted lessons,
   written to `skills_analyst/_candidates/`. An evidence bar (≥2 corroborating lessons) skips
   the model call entirely below it — deliberately conservative, not eager to generalize from one.
3. **Human gate, not an automated pass/fail test:** the operator runs `promote.py list` and
   `approve <file>` / `reject <file>`. This IS the "gated" in gated promotion for M1 — no
   auto-promotion in weeks 1–8; that's a decision to revisit with real data, not before.
4. Approve → note moves into `skills_analyst/<mission_id>/`, committed to git with the
   CURRENT week's canary green-count recorded as that skill's baseline. Every approved note is
   appended (capped ~2k chars) to that mission's worker prompts (`run_task()`).
5. **Automatic protection, not just human veto:** after every canary run with COMPLETE data
   (never judged while any canary is quota-parked — a park is not a regression), if the week's
   green count has dropped below a skill's approval baseline, the newest such skill is
   auto-rolled-back (`git rm` + commit, lesson rows returned to the pool) and the operator is
   escalated via Telegram. Manual `promote.py rollback <path>` also always available.

Versioning = git on `S:\AGI_like` directly (every promotion/rollback is its own commit — no
separate curator snapshot layer needed). Rollback = git revert, verified byte-identical after a
live round-trip test. Self-modification of the orchestrator itself is out of scope for M1
(explicitly deferred — highest-risk, lowest-value at this stage).

### 2.5 Identity & goal management (gap 5)

- `IDENTITY.md`: role, scope, tone; `missions/NNN-*.md`: goal, priority (single ordered list — no weights to game), constraints, done-definition.
- Refusals: compliance floor is hard-coded into the system prompt and the orchestrator's deny-list — official APIs only, no bot-posting, no scraping behind logins, no trending copyrighted audio in commercial work.
- Escalation: anything matching the deny-list, any pass-criteria ambiguity, any spend beyond quota → Telegram message to user; task parks in `blocked` state, loop continues with next task (never idles behind a question).

### 2.6 Security & control (gap 6) — designed for WIDE autonomy

WIDE autonomy makes the *container* the safety mechanism, not per-action prompts:

| Control | Implementation |
|---|---|
| Blast radius | All writes confined to `S:\AGI_like\workspace` + ledger; orchestrator refuses paths outside it. Deletions outside workspace = never (user's own exclusion) |
| Money / credentials | No payment tools, no credential reads (`.env` deny), no purchases — user's exclusions, enforced by tool config not by politeness |
| Audit | Append-only SQLite ledger (every task: spec, criteria, artifacts, usage.json, verdict) + Hermes session logs + git history. Answer to "what did it do while I slept" is a query, not an interrogation |
| Network posture | Gateway loopback-only; **no public exposure** (40,214 exposed OpenClaw instances, 35.4% flagged vulnerable — the cautionary tale). No inbound tunnels in v1 |
| Prompt injection | Web content is data, never instructions (system-prompt rule + critic checks outputs for instruction-following anomalies). Link previews / URL generation to unknown domains restricted — PromptArmor showed link previews exfiltrating data from agent chats |
| Supply chain | Hub skills: human approval. Hermes `security` command (OSV.dev audit of venv/plugins/MCP) runs in the weekly cron |
| Kill switch | `hermes gateway` stop + cron pause = full halt; documented in README |
| Loop runaway | Hermes tool-loop guardrails currently warn-only → M0 sets `hard_stop_enabled: true` (config change) |

**Consolidation decision:** OpenClaw is retired at M0. Rationale: stale install (2026-05-24), duplicate memory/gateway/skills create two sources of truth, and OpenClaw's 2026 record (CVE-2026-25253 CVSS 8.8 one-click RCE, command injection, SSRF, path traversal, prompt-injection-driven RCE) is a liability under WIDE autonomy. **AS-DECIDED (see `docs/MIGRATION.md` for the full record):** `hermes claw migrate --dry-run` showed OpenClaw's data was a near-empty husk — its `USER.md` was a blank template that would have *clobbered* Hermes's real profile — so the data import was deliberately SKIPPED, not run. OpenClaw was left dormant (package still installed, zero running processes/services/tasks, per operator choice) rather than uninstalled; `npm uninstall -g openclaw` remains available whenever wanted. Escalation channel is Telegram — configured, and delivery confirmed LIVE 2026-07-18 (`TELEGRAM_HOME_CHANNEL` set; see `docs/INCIDENTS.md` for why that took real digging).

### 2.7 Data ingestion (gap 7)

- **Inbox pattern:** `S:\AGI_like\inbox\` watched folder — user drops CSVs/exports/PDFs; nightly cron ingests → typed facts + provenance into Ledgerbook.
- **Scheduled pulls:** per-mission cron jobs (competitor pages via official APIs/RSS, marketplace data via Shopify Admin API once user provides token).
- **Web research:** Hermes `web` toolset routed through Ollama's search capability under Pro (already how OpenClaw was configured); Tavily/Firecrawl keys are optional upgrades, not blockers.
- **Feedback:** user's Telegram replies + spot-check verdicts are first-class ledger rows — human feedback is training data for the weekly consolidation.

Formats: everything normalizes to markdown + SQLite rows; no format zoo in v1.

---

## 3. Evaluation framework — the fitness function

### 3.1 Ledger schema (logged automatically, per task)

`task_id, mission_id, spec, pass_criteria (WRITTEN BEFORE RUN), started/finished, model_used, tokens+cost (from --usage-file), artifacts[], critic_verdict, human_verdict (when spot-checked), interventions (count + type), lesson_candidates[]`

### 3.2 Weekly fitness function

```
F = 0.35·completion_rate + 0.30·accuracy + 0.25·(1 − intervention_rate_norm) + 0.10·cost_efficiency
```
- completion_rate = tasks fully done without human help / tasks attempted
- accuracy = human spot-check pass rate (≥3 random tasks/week re-verified by user, GAIA-style verifiable questions preferred)
- intervention_rate_norm = interventions per task, capped at 1
- cost_efficiency = min(1, $0.50 / avg_cost_per_task)

Weights fixed for 8 weeks (no post-hoc rationalization); scorecard auto-generated Sundays by cron, delivered via Telegram, archived in git.

### 3.3 Improvement is proven, not felt

1. **Trend:** 4-week moving average of F strictly increasing.
2. **Reliability (pass^k, τ-bench-inspired):** 5 fixed canary tasks re-run weekly; a promoted skill that breaks a canary is auto-rolled back (curator snapshot) and logged as a failure memory.
3. **Intervention decline:** ≥30% drop from weeks 1–2 baseline by week 8.

If the numbers can't be produced, the improvement claim is not made — the ledger is the only witness that counts.

---

## 4. Risk analysis, failure modes, abandonment criteria

| Risk | Likelihood | Mitigation |
|---|---|---|
| Manager model too weak (cloud open-weights plan/eval quality) | Medium | Measured weekly via accuracy metric; escape hatch = Anthropic key (minutes to switch) |
| Memory rot / confidently wrong lessons | High (the classic failure) | Candidate TTL, gated promotion with attached tests, canary regression, supersede-not-overwrite |
| Prompt injection via researched web content | Medium-high (WIDE autonomy) | Data-not-instructions rule, workspace confinement, no credential access to steal, critic anomaly check, restricted link generation |
| Runaway loops / cost blowout | Medium | Hermes hard-stop enabled, per-mission task quota, daily cost cap in orchestrator (halt + Telegram alert), --usage-file accounting on every run |
| Ollama Cloud quota exhaustion mid-mission | **Confirmed — occurred during the first probe (429 on both cloud models, account-level)** | `quota_wait` task state + backoff; overnight batches in idle quota windows; cross-provider fallback chain; recommended Anthropic key for manager/critic (§1.6) |
| Hermes project risk (abandonment/breaking changes) | Low-medium | Plain-file ledger/memory = portable; quarterly export; see abandonment criteria |
| Laptop is a single point of failure | Certain (it's one laptop) | Nightly `hermes backup` + git push of S:\AGI_like; Hermes supports SSH/Modal/Daytona backends later — design doesn't assume the laptop forever |
| C: drive below 20 GB floor | Present now | M0 housekeeping: Hermes lives on C:; caches/models stay on S:; reclaim vm_bundles per standing rule |

### Abandon / redesign triggers (explicit, falsifiable)

- **Abandon Hermes backbone if:** no upstream release for 90 days, or an OpenClaw-class security advisory lands, or >20% of task failures are orchestration-layer (not model-layer) after 2 weeks of tuning. → Port to Claude Agent SDK or LangGraph; ledger/memory/missions are plain files precisely so this port is days, not months.
- **Abandon Ollama-cloud manager if:** accuracy <80% for 2 consecutive weeks after prompt iteration, or quota exhaustion halts missions twice in a month. → Anthropic key for manager/critic only (workers stay cheap).
- **Redesign memory if:** retrieval misses cause ≥2 wrong decisions/week (→ add embeddings, or graduate to Zep/Graphiti), or curation degrades past ~5k facts.
- **Abandon the harness premise itself if:** by week 8, intervention rate hasn't declined ≥30% AND accuracy hasn't held ≥85% — i.e., the employee isn't learning the job. Then the honest conclusion is that this job needs on-demand assistance, not a standing loop, and the project pivots to scheduled workflows without the self-improvement apparatus.

---

## 5. Simulation layer — verdict: **DEFER** (thin critic now, decision-sim only when actions get expensive)

Evidence reviewed: LATS (MCTS over agent actions; ~2× ReAct on HotPotQA but LLM calls at every node — cost-prohibitive on a $20/mo plan), RAP/world-model prompting, GATS (2026: decoupled world models beat LLM-in-the-loop search, but on synthetic domains), Reflexion (cheap, effective self-reflection — adopted in our lesson pipeline), Dreamer-class RL world models (wrong tool: no dense reward, no cheap rollouts here), classical cognitive architectures SOAR/ACT-R (their durable contribution — declarative/procedural memory split and impasse-driven learning — is absorbed into the memory schema and skill gate, not their symbolic planners).

Reasoning: simulation pays when **acting is expensive or irreversible relative to simulating**. A research analyst's actions (search, read, draft) are cheap and reversible — *the world itself is the cheapest simulator*, and the critic-vs-pre-written-criteria loop captures most of Reflexion's measured benefit at a fraction of LATS's cost. What v1 keeps: (a) manager plan-review before execution (one-step lookahead), (b) critic pass before delivery, (c) failure memories as counterfactual knowledge. **Revisit trigger:** when Milestone 3 introduces decisions with real cost (e.g., ad-budget allocation for adforge), add *decision simulation* — explicit spreadsheet-style models with uncertainty ranges, evaluated by the manager — not neural world models. That is the evidence-matched version of "MiroFish-style simulation" for a business-ops agent.

---

## 6. Competitive landscape

**Commercial:** Lindy (no-code AI-employee platform, 5,000+ customers, $54M raised — strength: integrations breadth; weakness: your data lives in their cloud, per-task pricing compounds), Devin/Cognition (deep vertical autonomy in software engineering — strength: end-to-end depth; weakness: single vertical, enterprise pricing), 11x / Artisan (single-function GTM employees), Vellum (broad workflows). Mid-market deployments run $30K–$150K year one. **Gap they all share:** none runs hybrid-local on user hardware with user-owned memory at subscription-only cost — which is exactly this project's lane ($20/mo, all data on S:).
**Open source:** Hermes Agent (chosen — only OSS agent with a built-in closed learning loop: skills-from-experience + curator), OpenClaw (huge ecosystem, but 2026 security record + stale local install), GPT-Researcher (#1 on CMU's DeepResearchGym; the benchmark for our researcher worker — M1 compares Hermes's research skillpack against it and adopts whichever wins on our canaries), STORM (wiki-style synthesis), LangGraph/CrewAI/AutoGen (§1.3).
**Academic:** CoALA (the taxonomy this architecture instantiates: working/episodic/semantic/procedural memory + decision loop), Voyager, LATS/RAP/GATS (§5), SOAR/ACT-R (memory taxonomy absorbed; symbolic planners not).
**Remaining gap nobody has closed** (and our honest exposure too): trustworthy autonomous *memory curation* over months — everyone, including this design, still needs the human-reviewed weekly consolidation. We mitigate rather than solve.

---

## 7. Roadmap

### M0 — Consolidation & scaffolding — **PASSED 2026-07-17/18**
`hermes claw migrate --dry-run` → migrate → retire OpenClaw · enable tool-loop hard stop · git init S:\AGI_like + CLAUDE.md · create ledger/missions/workspace/inbox scaffolding · verify Telegram escalation round-trip · record §1.6 benchmark numbers · C: housekeeping.
**Acceptance:** one end-to-end hand-run task flows mission → worker → critic → ledger → Telegram scorecard line. Single instance proven before any batching (per standing rule). — MET: onboarding autonomy run (ledger task 1) passed 2026-07-17; Telegram delivery confirmed live 2026-07-18.

### M1 — Research analyst (weeks 1–8) — **IN PROGRESS, currently W29 (baseline week 1)**
Weeks 1–2 baseline (no self-improvement active; measure floor). Weeks 3–8: full loop with gated skill promotion. Concretely: W29–W30 = baseline (no promotion); **W31 (Mon 2026-07-27) = promotion goes live** — the mechanism is already built and round-trip-verified ahead of that date (§2.4).
**Acceptance:** ≥10 tasks/week attempted · completion ≥70% · accuracy ≥90% on spot-checks · interventions −30% vs baseline · cost ≤$0.50/task · scorecard auto-delivered 8/8 Sundays · zero deny-list breaches · canaries green 4 consecutive weeks. — status as of end of W29: batch engine + 4 crons live, memory-update stage writing real facts, spot-check workflow in use (1 recorded), scorecard delivered W29 via Telegram; acceptance is measured over the full 8 weeks, not yet evaluable.

### M2 — Content-ops employee (after M1 passes, not before)
Same harness, new mission pack + skills (topic research → prompt packs → assembly QC around the manual Krea step). Reuses ledger, memory, critic unchanged — this is the test that the harness generalizes.

### M3 — Optional expansions (each gated on demonstrated need)
Decision-simulation for ad spend (§5 trigger) · embeddings/Zep upgrade (§4 trigger) · off-laptop backend (Hermes SSH/Modal) · dashboard.

### Open items still required from user — UPDATED 2026-07-18 (most of the original list resolved)
Resolved: Telegram confirmed + LIVE · overnight cron consent given (crons running) · §1.6
benchmarks measured and acted on (gemma4 demoted, Ollama-only routing accepted) · example tasks
superseded by the actual mission seeds (001/002 built directly rather than waiting on examples)
· Shopify Admin API token turned out NOT required for M1 scope (001 re-scoped to the employee's
own selected niche, competitive-landscape research only — see `missions/001-*.md` notes).

Actually still open:
1. **`YOUTUBE_API_KEY`** — mission 002 runs in degraded mode (web-search evidence, capped
   confidence) without it; add to Hermes `.env` whenever, the mission upgrades itself on the
   next run, no code change needed.
2. **Mission 003 (adforge) needs a real, paying client** before its operator slots get filled —
   standing instruction, not a one-time gap: do not fabricate a client to unblock this.
3. Anthropic key for the manager/critic remains optional-but-recommended (§1.6) — operator has
   explicitly chosen to stay Ollama-only for now, accepting quota-parked stretches as normal
   operation; not blocking, revisit only if quota pain becomes limiting.

---

## Sources
Memory: [Mem0 state of memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) · [Zep vs Mem0 LongMemEval](https://blog.getzep.com) via [comparison roundups](https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026) · [Graphlit survey](https://www.graphlit.com/blog/survey-of-ai-agent-memory-frameworks)
Orchestration: [LangGraph/CrewAI/AutoGen 2026 comparisons](https://pecollective.com/blog/ai-agent-frameworks-compared/) · [DataCamp](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen) · [framework showdown](https://qubittool.com/blog/ai-agent-framework-comparison-2026)
Planning/simulation: [LATS](https://arxiv.org/pdf/2310.04406) · [GATS 2026](https://arxiv.org/html/2607.08894) · [RAP](https://arxiv.org/pdf/2305.14992) · [text world models](https://arxiv.org/pdf/2606.09032)
Skills: [Voyager lineage & 2026 skill-library survey](https://arxiv.org/html/2602.12430v4) · [SkillAudit](https://arxiv.org/pdf/2606.14239) · [SoK: Agentic Skills](https://arxiv.org/pdf/2602.20867)
Evals: [τ-bench guide](https://qaskills.sh/blog/tau-bench-agent-evaluation-guide-2026) · [2026 benchmark landscape](https://benchmarkingagents.com/agent-benchmarks/)
Security: [The Hacker News on OpenClaw CVEs](https://thehackernews.com/2026/03/openclaw-ai-agent-flaws-could-enable.html) · [IBM X-Force](https://www.ibm.com/think/x-force/what-openclaw-reveals-about-agentic-ai-security-risks) · [Reco on ClawHub malicious skills](https://www.reco.ai/blog/openclaw-the-ai-agent-security-crisis-unfolding-right-now)
Commercial: [Vellum AI-employee roundup](https://www.vellum.ai/blog/best-ai-employees) · [Lindy guide](https://www.lindy.ai/blog/ai-employee)
Research workers: [GPT-Researcher](https://gptr.dev/) · [open deep-research roundup](https://blog.gatsbi.com/wordsmith/best-open-source-ai-research-agents/)
Runtime: [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/) · local install inspection (primary source)
