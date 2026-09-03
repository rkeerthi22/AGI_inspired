# Codex Handoff + Red-Team Blueprint — 2026-09-04

**To:** Codex (implementer, resuming after usage-limit block)
**From:** Claude Code (red-team / independent reviewer)
**Role split (operator-set, 2026-09-04):** Codex takes over forward implementation. Claude
Code acts as red-team / reviewer, invoked by the operator on demand. This document is
Claude Code's handoff of (a) what it did to Codex's interrupted work and (b) a grounded,
red-teamed blueprint for the next phase. The goal: a world-class harness — measured, not
asserted.

**Master HEAD at write time:** `170161b` (`origin/master`, clean tree, model-free gate
61/61 green, continuity rev 56 recover-clean). ESTOP engaged. Model-free only — nothing
here clears live execution.

---

## 0. TL;DR

Codex's security/preflight batch was interrupted by a usage limit before it reached
`master`. Claude Code independently verified it (own measurements, not the transcript),
FF-merged it to `master`, bumped continuity, and pushed. State is solid. **The much bigger
finding is upstream of that work:** the 2026-09-03 validation cohort passed only 1 of 6
missions, but red-team review of the run artifacts shows **3 of those 5 failures were
caused or contributed to by one gate defect (RC-1), not by model quality.** The harness's
own citecheck was fighting the model. Fixing RC-1 is the single highest-value next step
and is model-free. A red test proving it already exists at `tests/red/`.

---

## 1. What happened to your work (independently verified)

Your two commits (`351104e` security, `d8037f3` preflight path) were on branch
`claude-code/telemetry-truth-fixes-2026-09-03`, unmerged to `master`, when the usage limit
hit. Claude Code verified them from the repository state — not from any transcript — then
integrated.

| Check | Result (Claude Code's own measurement) | How |
|---|---|---|
| Commits exist | `351104e`, `d8037f3` confirmed | `git log` on the branch |
| Operator-auth 5 trust properties | All hold | diff of `operator_auth.py`, `execution_pause.py`, `secrets.py`, `provider_chat.py`, `operator_cli.py` |
| Unsigned markers fail closed | `verify_marker()` returns None for `{`-prefixed JSON | diff |
| Foreign self-signed rejected | `hmac.compare_digest(embedded, trusted)`; verifier rebuilt from trusted key | diff |
| Purpose-bound | `action` + `use` checked in `clear_is_authorized()` and `consume_canary_authorization()` | diff |
| Signing fails closed | `_signed_marker()` no try/except→JSON fallback | diff |
| UTF-16LE Credential Manager | write passes `str` to `CredentialBlob`; read tries UTF-16LE on null-byte blobs | diff + live `credential_manager_has_api_key('byteplus_coding')=True` |
| Model-free gate | **61/61 green, exit 0, 0 `[FAIL]`** | `python tests/run_all.py` at `170161b` |
| No tracked secrets | `git grep` exit 1 (only deliberate test fixtures) | own grep |
| ESTOP engaged | `pause_engaged()=True` | `execution_pause.pause_engaged()` |
| `ARK_API_KEY` out of `.env` | 0 lines in Hermes `.env` | `grep -c` |
| `ARK_API_KEY` in Credential Manager | present (value never printed) | `secrets.credential_manager_has_api_key` |

**No discrepancy with your report.** Integration proceeded.

**Master lineage now:**
```
170161b chore(continuity): trim rev 56 brief under the 4096-byte cap   <- HEAD, pushed
e7bcd16 chore(continuity): bump brief to rev 56 after security/preflight integration  <- over-cap intermediate
f9a07fe docs(integration): verify Codex security batch, sync canonical docs, carry forward failover gap
d8037f3 fix(preflight): preserve first porcelain path
351104e fix(security): anchor operator trust and add release preflight
```
`e7bcd16` is a known over-cap intermediate (4891 > `MAX_BRIEF_BYTES=4096`); `170161b`
supersedes it with the trimmed 4030-byte valid brief. `--amend`/force-push were
classifier-blocked, so `e7bcd16` stays in history as an intermediate where HEAD itself is
recover-clean. **Do not be alarmed that `recover` fails at `e7bcd16`** — it does, by
construction; recover at `170161b` is fully clean (0 discrepancies, all 6 reference shas
match).

The operator-trust + preflight work is **landed and verified**. The open items below are
the *next* phase, not a re-do of this one.

---

## 2. Red-team: the cohort truth (1/6 is mostly a gate problem)

The 2026-09-03 cohort (specs in `workspace/validation/cohort_missions.json`) passed only
M4. The handoff narrative treated this as "the model mostly failed." Red-team review of
the actual run artifacts (`runs/task{114,116,117,118,119}_*`) says that framing is wrong:

| Mission / task | Stated failure | Actual root cause (from artifacts) | Stale? |
|---|---|---|---|
| **M3 / 114** | failed spec | **Spec-strict + mild instruction-following.** Model found PromptBase reviews + a 4.9 rating, declared Trustpilot blocked — but didn't explicitly declare G2/Chrome-Web-Store status or enumerate ≥3 sources with per-source status (`task114_critic_reasoning.txt`). Closest to a legitimate fail, but pedantic. | No |
| **M4 / 115** | — | **PASSED.** | — |
| **M5 / 116** | failed citecheck | **RC-1 false negative.** 4/8 cited URLs "unreachable" (dead_frac=0.50 > 0.34). But 3 of those 4 were 403/429 (flowgpt.com 403 — the mission's OWN subject site; capterra 403; megatek 429). True dead_frac = 1/8 = 0.125 (only postunreel, 410, is gone). **Should have passed.** (`task116_citation_evidence.json`) | **Yes — would pass with RC-1 fix** |
| **M6 / 117** | worker API failure | **Real harness bug, already fixed.** Pre-`5522926` failover stopped the chain at the Anthropic rung on any non-quota error. `5522926` added `_worker_failover_reason` + continuation. | **Yes — fixed by `5522926`** |
| **M6 / 118 (rerun)** | failed citecheck | **RC-1 false negative.** 10/13 HN item URLs "unreachable" (dead_frac=0.77). Many are 429/403 from news.ycombinator.com bot throttling — live pages, not fabricated. (`task118.trajectory.jsonl`) | **Yes — likely passes with RC-1 fix** |
| **M7 / 119** | failed spec | **Spec-strict + RC-1 artifact.** Model used "Not found in available evidence" not the exact phrase "not publicly disclosed"; ≥2-sources-per-marketplace not met everywhere. **But** the critic's "factual error" — model claimed FlowGPT "loads successfully" while citecheck said `UNREACHABLE (403)` — is itself RC-1: flowgpt.com loads fine in a browser; it 403s the bot. The model told the truth; the gate made it look like a lie (`task119_critic_reasoning.txt`). | **Partly — the "factual error" dissolves with RC-1** |

**The headline:** the model (ark-code-latest via BytePlus) was substantially better than
1/6 suggests. **RC-1 caused or contributed to 3 of the 5 failures.** The core thesis —
"mechanical gates force cheap models to behave reliably" — is not failing because the model
is bad; it is failing because the gate *mis-measures* on bot-protected sites. Fixing the
gate before re-running the cohort is a prerequisite for any honest conclusion about model
quality.

> **Honesty note on provenance:** the M5/M6/M7 citecheck classifications were confirmed
> by Claude Code against primary evidence (`task116_citation_evidence.json`, the
> trajectories, `citecheck.py` source). The M3/M7 spec-strict classifications rest on the
> critic-reasoning files (`task114`/`task119_critic_reasoning.txt`), which Claude Code read
> directly. The failover-bug classification for M6 rests on the prior failover review
> (`docs/reviews/claude-code_FAILOVER_REVIEW_2026-09-03.md`, verified at `8ca4152`).

---

## 3. Open gaps — located, with acceptance criteria

### RC-1 (P0): citecheck treats 403/429 as "dead citation"
**Where:** `orchestrator/citecheck.py:297` (`reachable = 200 <= resp.status < 400`),
`:305-307` (HTTPError → `reachable=False`), `:334` (`dead = sum(not reachable)`), `:38`
(`DEAD_FRAC_HARD_FAIL = 0.34`).
**Root cause:** a server that *responded* (403/429/503) means the page exists and the
citation is real; the bot was refused. That is categorically different from 404/410/DNS
(genuinely gone / fabricated). `summarize` lumps them.
**Red test:** `tests/red/test_citecheck_waf_false_negative.py` — gap case is RED now,
guard case is GREEN. Run it: `python -B tests/red/test_citecheck_waf_false_negative.py`
(exit 1 = gap open).
**Acceptance:** both cases GREEN; the gap case flips when 403/429/503 stop counting toward
`dead`/`dead_frac`. Then promote the file into `tests/` + `tests/tiers.json`. Design
freedom: distinguish a `blocked` bucket (server responded, 4xx/5xx but page exists) from
`dead` (404/410/DNS/timeout), and base `is_hard_fail` on `dead` only. Don't be over-lenient
— the guard case enforces that genuinely-dead citations still hard-fail. A blocked citation
should still surface to the critic as "literal not verified (blocked)" — just not as
"fabricated."
**Heads-up:** `evaluation.py:264-269` formats the dead-URL message from the same evidence;
update the wording there too so the critic sees "blocked (403)" vs "dead (404)"
distinctly.

### A5 (P1): raw worker stdout is 0 bytes on early-abort failure paths
**Where:** `task117_worker_raw.txt` is 0 bytes (the run that hit the failover early-stop).
`task110_worker_raw.txt` likewise. Other failure paths captured raw correctly (114/116/118/
119 are populated). From the failover review, the dump doesn't fire on the early-abort path
(`task_runner.py:417` region, `workflow.py:186`).
**Acceptance:** a test that drives a worker failure which triggers early-abort and asserts
the raw-stdout file is non-empty. Re-use the failover-review evidence as the fixture.

### A3 (P1): `failover_attempted` event reason is hardcoded
**Where:** `execution.py:378-381` — every failover transition is emitted with
`reason="quota_exhausted"` regardless of the actual prior reason. Latent today only because
no non-quota transition has been recorded live. Once RC-1/credentials move and an
auth/provider_unavailable transition fires live, the trajectory's transition reasons become
unreliable.
**Acceptance:** thread the actual classified `failure_reason` into `tw.failover_attempted`.
A test that fakes an authentication-classified failure and asserts the emitted event's
reason is not `quota_exhausted`.

### P1 security gaps (each located, code self-documents the gap)

| Gap | Location / evidence | Acceptance |
|---|---|---|
| **Egress isolation** (engine-independent) | `pty_daemon.py` HAS a Job Object (`_create_job_object`, `SetInformationJobObject`, `AssignProcessToJobObject`, `TerminateJobObject`) — but for *process kill* on PTY teardown, **not** outbound network policy. No egress filter anywhere. | A worker cannot reach a host not on an allowlist; a test asserting a denied-egress attempt is blocked. |
| **Tamper-evident off-machine audit** | `execution_pause.py` has ESTOP-sentinel tamper *detection* (re-engages); `integrity.py` has filesystem tamper checks (F14/F36/F42/F46/F47/F52). All *local*. No hash-chained append-only off-machine audit. | Audit entries are hash-chained to the prior entry and replicated off-machine; a test asserting tamper of a past entry is detected. |
| **Reproducible dependency hashes** | `scripts/requirements.txt` has exact *pins* (`==`) but no hashes; install doesn't use `--require-hashes`. A supply-chain swap of a pinned version passes. | `--require-hashes` enforced; a pinned dep with a tampered hash fails to install. |
| **Independent critic** | `evaluation.py:216-217` literally comments: "manager and critic are still the SAME model (glm-5.2:cloud)" — the design intent ("different critic model when a second provider exists") is "not applied here." | Critic runs on a different provider/model than the manager; a test asserting critic_model != manager_model (or a documented, accepted single-provider fallback). |

---

## 4. Blueprint (prioritized)

1. **RC-1 citecheck (P0, model-free).** Highest leverage: likely flips M5 + M6-rerun from
   fail to pass and dissolves M7's "factual error." Red test exists — make it green.
   *Then* the cohort result means something.
2. **A5 raw stdout on early-abort (P1, model-free).** Closes the evidence gap that made
   M6-first hard to diagnose for a whole session.
3. **A3 failover reason (P1, model-free).** Prevents the next live cohort from recording
   lying trajectory reasons.
4. **P1 security gaps (P2):** egress isolation > tamper-evident audit > reproducible hashes
   > independent critic. These are the "prototype → product" work. They need operator
   architectural decisions (esp. egress: allowlist model; independent critic: which second
   provider under the weak-AI strategy). **Recommend:** land egress + hashes first (no
   provider decision needed); defer independent critic until the cohort actually passes
   (no point second-modeling a gate that's broken).
5. **Re-run the cohort (P2, needs operator `--controlled-window`).** Only after RC-1. This
   is the real test of the thesis. Not Codex's alone to authorize — the Munder boundary
   verdict + credential-rotation item still gate live execution.
6. **Credentials posture decision (operator).** Anthropic/OpenAI creds are absent by design
   (weak-AI strategy). The failover chain walks to local gemma if they stay absent. That
   may be intended. Either way it should be a *stated* decision, not emergent.

### What "world-class" requires before the claim is honest
A passing cohort run (≥5/6) on the *current* model, with the post-fix failover chain
live-exercised past rung 4, and the P1 security gaps closed. Until then this is a strong
*control prototype*, not a finished product — which is exactly what `CURRENT_STATE.md:122`
already says. Don't let the security-batch landing inflate the framing.

---

## 5. Role split + what this does NOT authorize

- **Codex:** implementer for items 1–4 above. Owns the relevant write scopes (record them
  in `docs/ACTIVE_WORK.json` on resume per rule 1).
- **Claude Code:** red-team / reviewer, invoked by the operator on demand. This document is
  Claude Code's current contribution; it does not own forward implementation.

**NOT authorized by this document (model-free repo work only):**
- No BytePlus / Ollama provider calls. No ESTOP change. No canary. No M1–M7 re-runs.
- Nothing here clears live execution — that depends on the Munder boundary verdict and the
  credential-rotation item, neither of which this touches.
- The `ARK_API_KEY` is vaulted in Credential Manager; do not re-export it to `.env`.

---

## 6. Codex resume checklist

1. Run the universal bootstrap in `AGENTS.md` (8 steps): read
   `.harness/continuity/current.json`, `docs/ACTIVE_WORK.json`, `docs/CURRENT_STATE.md`,
   then `python orchestrator/continuity.py recover` (expect 0 discrepancies at `170161b`).
2. `python tests/run_all.py` → expect 61/61 green, exit 0.
3. `python -B tests/red/test_citecheck_waf_false_negative.py` → expect `GAP OPEN (red)`.
   This is RC-1. Start here.
4. Read this doc's §3 acceptance criteria per item; read the red test's header comment for
   the primary evidence.
5. Update `docs/ACTIVE_WORK.json` with your ownership before editing anything (rule 1: one
   agent per write scope).

**Cross-references:** prior security findings/open-items tracker at
`docs/AGENT_HANDOFF_2026-09-03_SECURITY_PREFLIGHT_INTEGRATION.md`; failover review (A3/A5
evidence) at `docs/reviews/claude-code_FAILOVER_REVIEW_2026-09-03.md`; fix registry at
`S:\ObsidianVault\Fix Registry.md` (next fix is **F110**).
