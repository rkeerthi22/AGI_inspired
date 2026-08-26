# OSINT Integration Plan — Week 9 Transition
**Status: DRAFT, awaiting operator override of execution-only directive**
**Author: AGI_like agent, 2026-08-26**
**Target start: after operator sign-off + W35 scorecard lands (2026-08-31 04:00)**

---

## 0. Why this document exists

The 2026-07-31 operator directive in `CLAUDE.md` locked weeks 4–8 of M1 as
"execution only — no new features, no new hardening, no refactors." That
directive was set for measured reasons: the harness was producing numbers
the operator could not verify independently, and the bottleneck had
shifted from system quality to data quality. W4–W8 was a deliberately
narrow window.

Today is 2026-08-26. W4–W8 closes on schedule. The operator has signalled
it is time to transition out of execution-only. This plan is the proposal
for what the next phase looks like, specifically:

- **Integrate modern OSINT frameworks** (SpiderFoot-class, theHarvester,
  recon-ng) into the worker pool — not as direct subprocess calls, but
  as a tool layer the existing `run_task()` machinery can dispatch to.
- **Add GitHub-aware research** (the platform itself, repos, issues,
  contributors) as a first-class source — distinct from generic web
  search, with its own rate limits, citation rules, and containment
  considerations.
- **Avoid the 4,000-line `batch_runner.py` outcome** by routing new
  capability through new modules, not by adding conditional branches to
  the existing 2,324-line file.
- **Keep every existing F1–F55 fix intact** and every containment guard
  in force — the same posture the operator has held throughout M1.

This is a *plan*, not a code change. Nothing in this document lands
without operator review of the proposal as a whole, plus per-feature
approval as each one is built.

---

## 1. The architectural rule that prevents the 4,000-line mess

`orchestrator/batch_runner.py` is currently 2,324 lines. The user
explicitly said in the prior session: *"It is currently over 2,000 lines
long, which means every time you try to make the AI smarter, you risk
breaking the whole system."* That is correct. The W4–W8 directive
deferred the proposed 5-file split (`execution.py` / `integrity.py` /
`prompts.py` / `evaluation.py` / `scheduler.py`) on the grounds that
refactoring a 2,300-line file while tests are red is a containment
risk. That reasoning still holds.

**The OSINT integration must therefore be additive (new files in new
locations), not subtractive (more branches in `batch_runner.py`).**

The minimum that satisfies this:

1. **No code added to `orchestrator/batch_runner.py` for the OSINT
   capability itself.** All new code lives in `orchestrator/osint/` (or
   equivalent new path), and the only change to `batch_runner.py` is a
   *single dispatch line* — one conditional in the existing tool-routing
   block that says "if the task's mission has an OSINT plugin, also
   expose the plugin's tool set to the worker." That is the same
   pattern `promote.py` already uses for skill injection: a
   mission-scoped file that the orchestrator consults, with a strict
   size/cap budget and a fault-tolerant try/except.
2. **Each OSINT plugin is its own module** with a documented interface
   (`class OsintPlugin: name, description, tool_definitions(), run(query,
   ctx) -> OsintResult`) so adding a new source = adding one file, not
   editing the orchestrator. Plugin discovery is by directory listing
   under `orchestrator/osint/plugins/`, the same auto-discovery
   `tests/run_all.py` already uses for test files.
3. **GitHub scraping is a plugin, not a special case.** A plugin
   implementation can call `gh` CLI (already on this box per the EdgeLab
   reference) or the official GitHub REST API with the operator's
   `GITHUB_TOKEN`. It does *not* get its own orchestrator branch.
4. **The prediction machine is the loop's downstream consumer, not
   its architect.** A new `osint` `prediction_type` gets registered
   alongside `task_outcome` / `video_engagement` / `skill_safety`
   / `miks_campaign` so OSINT runs are scored and learnable, but
   nothing in `prediction_machine/` needs to know about the OSINT
   capability itself.

This rule is non-negotiable for the integration. If a feature cannot
land without adding branches to `batch_runner.py`, that feature is
*postponed until the 5-file split is done*. The split is the
prerequisite for OSINT, not the other way around.

---

## 2. What the operator gets from OSINT — three concrete capabilities

### 2.1  Subdomain and asset enumeration (SpiderFoot-style)

- **Use case:** When a mission's research subject is a company
  (`001-shopify-competitor-intel` is the obvious fit; could extend
  to `003-adforge-local-market` if it ever unsuspends), the worker
  should be able to enumerate subdomains, certificate-transparency
  data, and DNS records to find competitor assets not present in
  their public marketing site.
- **Plugin path:** `orchestrator/osint/plugins/subdomain_enum.py`
- **Source options, in preference order:**
  1. `crt.sh` (Certificate Transparency logs; public, no auth, JSON
     API, has a rate limit but it's generous)
  2. `dnsrecon` / `amass` if operator-installed (these are
     heavy and outside the stdlib-only discipline; gate behind a
     `config/osint.yaml` "extras allowed" flag)
  3. SpiderFoot as a subprocess (the prior `ClawWork` analysis
     in `bb202f6` already explored this; reuse whatever lessons
     came from it)
- **Containment:** read-only by definition. Worker cannot write to
  any OSINT tool's output — it gets the JSON, parses it, and emits
  a `fact` row. No exceptions.
- **Citations:** `crt.sh` results are verbatim JSON. Cite the issuer
  ID, the not_before date, the CN. `crt.sh` has the same content-
  drift class as the F23 citecheck problem; the existing
  `citecheck.py` should be extended to handle CT-style
  `{"id": ..., "name_value": ...}` shapes before this plugin ships.

### 2.2  GitHub repository and contributor research

- **Use case:** A competitor's GitHub presence (orgs, repos, contributor
  velocity, issue backlogs, release cadence) is a first-class research
  source. The user named this explicitly in the prior session
  ("GitHub scraping capabilities").
- **Plugin path:** `orchestrator/osint/plugins/github_research.py`
- **Source preference order:**
  1. **Official GitHub REST API** with a `GITHUB_TOKEN` from the
     operator's `.env` (5,000 req/hr authenticated vs 60 unauth;
     this is not negotiable — unauthenticated scraping gets
     60-req/hr cap, which is 1 query/min, useless for a 4-seed
     research mission)
  2. **`gh` CLI** as a fallback for things the API doesn't cover
     (e.g. `gh search repos` for relevance-ranked results). The
     user has `gh` on the box; check it.
  3. **NO direct HTML scraping of github.com.** That is what
     gets projects banned. Every "scraping GitHub" tutorial on
     the internet is wrong about this.
- **Data shape:** `OsintResult(target, source, fields, citations)` —
  for GitHub, `fields` is `{"stars": ..., "last_commit": ...,
  "open_issues": ..., "contributors": [...], "language_breakdown":
  {...}}`. The plugin returns this; the orchestrator decides what
  becomes a `fact` row.
- **Rate limit handling:** the GitHub API returns `X-RateLimit-Remaining`
  on every response. The plugin MUST read it and refuse to call again
  if `< 100` remaining in the current window. This is the same
  discipline `policy.tokens_used_today()` enforces for token spend.
  Borrow the pattern: a small `rate_guard(remaining, reset_at)` helper
  that raises if called when the budget is gone.
- **Containment:** the plugin does not call `git clone`. A clone is a
  write to `workspace/` and would need its own admission control.
  Operator can opt in to `git clone --depth 1` for specific targets
  by adding them to a `config/osint.yaml` allowlist, but the default
  is metadata-only via the API.
- **Citations:** `https://api.github.com/repos/{owner}/{repo}` is
  citable. `https://github.com/{owner}/{repo}` is the user-facing URL
  and also citable. Both should be in the fact row.

### 2.3  Cross-source entity resolution (the hard one)

- **Use case:** When a single company/person has a presence on their
  own site, on LinkedIn, on GitHub, on YouTube, on X/Twitter, on
  Product Hunt — link them. This is the textbook OSINT-value-add:
  not "gather more data" but "decide which pieces of data are
  the same entity."
- **Plugin path:** `orchestrator/osint/plugins/entity_resolve.py`
- **Approach:** deterministic for stable handles (GitHub username ==
  Twitter handle with high prior), probabilistic for fuzzy matches
  (name + location + employer). The probabilistic path is exactly
  what the prediction machine is built to learn: it can be
  registered as a `prediction_type = "entity_resolution"`.
- **Risk:** false positives. Two unrelated "John Smith" GitHub
  users get merged. Mitigation: every merge must surface its
  evidence and a confidence score; the critic reviews merges like
  any other claim; the spot-check queue gets a special filter for
  entity-resolution verdicts.
- **This is the LAST capability to land.** The other two should
  be in production for at least 2 weeks with real data before
  entity resolution is enabled. Adding it on top of broken
  per-source extraction is the same mistake as "build the
  meta-system before the components work."

---

## 3. Tool-calling fallbacks — the F55 lesson, applied

The W35 04:00 batch log (2026-08-26) showed the existing system
already has the right shape for tool fallbacks: F55 added a prompt
instruction for "switch to `requests` or `yt-dlp` when `web_search`
is exhausted." The OSINT plugins must follow the same pattern,
formally:

| Failure | First retry | Second retry | Final |
|---|---|---|---|
| `crt.sh` 503 | `crt.sh` again with backoff (max 3) | skip plugin, note in fact row | fail the plugin, continue the rest of the task |
| GitHub API 403 (rate limit) | read `X-RateLimit-Reset`, sleep | if reset > 60s away, skip plugin | same as above |
| GitHub API 404 (repo moved/private) | search for the repo at the same org | mark the target "unverifiable" | same as above |
| `gh` CLI not installed | log and fall back to REST API | n/a | n/a |
| DNS resolution fails | skip plugin (DNS is a fallback, not a primary) | n/a | n/a |

The orchestrator must never block on a plugin. Every plugin call
is wrapped in a 30s hard timeout (per the F38 timeout discipline)
plus the per-call budget. If a plugin's 30s is exceeded, the
worker continues with the data it has, marks the missing field as
confidence 1 (F55's pattern), and proceeds.

---

## 4. Specialized agent personas for OSINT

The current worker is a single Ollama model (`kimi-k2.7-code:cloud` with
`gemma4:12b-ctx4k` as the local fallback). For OSINT work, a single
persona is the wrong shape. A research task that needs both creative
angle-suggestion AND rigorous citation-checking benefits from two
personas with different prompts and different critic weights.

The proposal, deferred to after the 5-file split and the W35 scorecard:

- **`osint_collector` persona** — narrow, tool-heavy, fast. Prompt
  template emphasizes "produce a structured JSON of facts you found,
  with verbatim citations, no synthesis." Critic weights: citation
  accuracy high, synthesis quality low. This is the "list all
  subdomains of competitor.com" persona.
- **`osint_synthesizer` persona** — wider, slower, more
  context-tolerant. Takes the collector's output plus a brief and
  produces a narrative deliverable. Critic weights: synthesis
  quality high, citation accuracy still required but already
  vetted upstream.

The orchestrator already has the right shape for this: it's the
same dual-call pattern as `run_synthesis()` (the manager calls the
synthesizer) and `run_task()` (the worker does the collection).
A future refactor that adds `osint_collector` and `osint_synthesizer`
is a *per-mission* specialization, not a new orchestrator module.
It is explicitly out of scope for this plan.

---

## 5. How this data feeds the prediction machine

Three concrete integrations, none of which require new code in
`prediction_machine/`:

1. **Register `osint` as a new `prediction_type` alongside the four
   existing ones** (`task_outcome`, `video_engagement`, `skill_safety`,
   `miks_campaign`). The `VALID_PREDICTION_TYPES` set in
   `prediction_machine/core/prediction_store.py:53-58` adds one
   entry. The `model_versions` table gets a new row for
   `osint_v1`. The daily report already has a generic "Performance
   by type" section, so it will pick this up automatically.

2. **Track OSINT-specific outcomes in `ledger.db`.** New columns
   on the `tasks` table: `osint_plugin` (TEXT, the plugin name),
   `osint_target` (TEXT, the entity investigated), `osint_citations`
   (INTEGER, count of citable facts returned). This is a *schema
   migration*, which is exactly the kind of "hardening" the
   execution-only directive defers. **Not in this plan**; recorded
   for the post-week-8 hardening backlog.

3. **Feed `entity_resolve` confidence back as a prediction signal.**
   When the entity-resolution plugin merges two records, the merge
   event is a `prediction` with `predicted="same_entity"`,
   `actual=verified_by_critic`. The prediction_machine's `compute_error`
   already handles this shape; no new evaluation code.

The principle: every OSINT event that has a clear "predicted vs.
actual" structure becomes a row in `predictions.db`. Events that
do not (e.g. a generic web search returning a fact) do not. The
prediction machine's value is its selectivity, not its
comprehensiveness.

---

## 6. Containment and safety

The same posture as M1, with three OSINT-specific rules:

1. **No OSINT plugin can write outside `workspace/<mission>/`.** The
   existing `fs-guard` and `policy.validate_paths()` already enforce
   this; the new code just has to *not* introduce new writable
   paths.
2. **No OSINT plugin can spend money or commit to external state.**
   The existing `policy.deny_list_scan()` and the hard_exclusions
   in `config/policy.yaml` cover this; the OSINT code should
   extend the deny-list patterns with OSINT-specific phrases
   ("purchased API credits", "subscribed to a service", etc.)
   before the first OSINT task runs. The list lives in
   `orchestrator/policy.py:84-93` (`_DENY_PATTERNS`); adding
   patterns there is a one-line edit per phrase.
3. **OSINT plugins must declare their network reach in a docstring.**
   Each plugin's `class OsintPlugin` is preceded by a YAML frontmatter
   block listing the domains/IPs/APIs it touches. The fs-guard
   surface check (currently a no-op for outbound) gets a sibling
   check: "is the plugin about to call a domain not in its declared
   list?" If yes, refuse. This is a small, scoped addition
   (~50 lines) and the right place to put it is
   `orchestrator/osint/__init__.py` as a decorator.

The last item is the only new code path; the first two reuse
existing guards. This keeps the safety story honest: "we added one
new guard, and it works the way the existing guards work."

---

## 7. Order of operations — week 9 and beyond

Sequenced to minimize risk and let each step prove itself before
the next:

| Week | Action | Why this order |
|---|---|---|
| W9  | **Operator sign-off on this plan.** No code. | Reversible. Confirms the directive override is deliberate. |
| W9  | **5-file split of `batch_runner.py`** (the deferred refactor) | Prerequisite: any OSINT code that touches `batch_runner.py` will be on a 1,500-line file, not 2,300. |
| W9  | **Add `prediction_type = "osint"`** to `VALID_PREDICTION_TYPES`; add `osint_v1` row to `model_versions`. No plugin code yet. | Establishes the schema before the data starts flowing. |
| W10 | **Ship `orchestrator/osint/__init__.py` + `subdomain_enum.py`** as the first plugin. | Read-only, contained, no auth, no rate limits beyond public politeness. The simplest possible plugin, the safest to debug live. |
| W10 | **Live test on a real mission.** Add one OSINT-enabled task to `001-shopify-competitor-intel` for one competitor (a real one, with the operator's awareness). | The first end-to-end data point. Without this, every other OSINT capability is speculative. |
| W11 | **Ship `github_research.py`** with the operator's `GITHUB_TOKEN`. | After subdomain_enum is stable for a week, with the rate-limit guard verified. |
| W11 | **Extend `citecheck.py`** for CT-log JSON and GitHub API JSON. | Both plugins are now producing data; the citecheck needs to be ready for them. |
| W12+ | **`entity_resolve.py`** only after the above two have produced ≥ 50 real facts across ≥ 3 missions. | The "hard" capability lands last, with evidence. |

The W9 W10 W11 numbering assumes a normal cadence. If a canary
regresses or a `runs/quarantine_*.json` appears, halt. The plan is
"ship after prove," not "ship by date."

---

## 8. The 5 things that block this plan, in order of urgency

1. **Operator sign-off.** Reverses the W4–W8 execution-only directive.
   The handoff records that directive as locked; the override must
   be explicit, not assumed. **(NOW)**
2. **5-file split of `batch_runner.py`.** Cannot be deferred past
   W10 — every OSINT plugin that touches the orchestrator needs
   the dispatch line to be in `execution.py` (or whatever the
   post-split equivalent is), not buried in a 2,300-line file.
3. **`citecheck.py` extension for JSON-shaped sources.** Needed by
   W11. The existing `citecheck.py` is HTML-text-shaped; a
   `_jsonld_text()`-style helper for CT logs and GitHub API is
   one method plus a few tests.
4. **`GITHUB_TOKEN` from operator.** The unauthenticated rate limit
   is operationally fatal. Token goes in `.env`, never in code,
   never in `memory/`. The agent that runs the plugin reads it
   via `os.environ`, the same way the existing F8 reads Ollama's
   config.
5. **Three test suites still red (`test_baseline`, `test_f49`,
   `test_f52`).** The plan does not require these to be green —
   they are test-data drift, not code bugs — but if any of them
   is on the OSINT-plugin code path, fix that one first.

---

## 9. What this plan explicitly is NOT

- **Not a research report on OSINT tooling.** The SpiderFoot /
  recon-ng / theHarvester / gh-CLI landscape is surveyed above
  in one paragraph; this is a *plan for the project's integration*,
  not a *literature review*. The handoff already names "research/BI
  analyst" as M1; this stays within that scope.
- **Not a refactor plan for `batch_runner.py`.** The 5-file split
  is a prerequisite, mentioned in §7, not designed here. The split
  plan is a separate document and a separate conversation.
- **Not a coverage of every OSINT use case.** Domain/IP/passive-DNS
  / dark-web / breach-data are mentioned in §2.1–§2.3 only as
  they map to the three concrete capabilities. Capabilities like
  "monitor a Telegram channel for mentions of competitor X" are
  real and valuable but explicitly out of scope for the M1→M2
  transition; they would each be a 4–6 week sub-project.
- **Not a commitment to ship.** Every line in §7 is a *proposal*
  to do work, not a promise. If a step fails, the steps after it
  wait. The `runs/quarantine_*.json` empty count and the
  `fs-guard` zero-violation record are the only evidence-based
  indicators that the system is healthy enough to extend.
- **Not a violation of the M1 fitness measurement.** The fitness
  formula (`F = 0.35·completion + 0.30·accuracy + 0.25·(1−intervention)
  + 0.10·cost_eff`) stays locked for M1's full 8-week window
  even as M2 capabilities ship. New capabilities either improve
  one of the four terms or they do not affect fitness; they do
  not get their own term or their own weight.

---

## 10. What the operator should read first

If you read this whole document, you have the right picture. If
you only read three sections, read these in this order:

1. **§1 (the architectural rule).** "No code added to
   `batch_runner.py` for the OSINT capability itself." If that
   rule is wrong, the rest of the plan is wrong.
2. **§6 (containment and safety).** The three new rules. If
   any of them is wrong, the rest of the plan is unsafe.
3. **§8 (the 5 blockers).** This is the actual ask. The
   sign-off is the only one I cannot do without you.
