# Gemini Directive: Frontier-Worker Ablation — Is the Architecture Model-Bound or Architecture-Bound? — 2026-09-13

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI (forward implementer)
**Date:** 2026-09-13
**Baseline:** HEAD `d4a995c` · tree clean · gate **79/79** exit 0 · ESTOP engaged · rev 119 · **origin in sync (0/0)**
**Strategic question this answers:** *The harness is code-final and 7/7-envelope-proven, but single-window yield is 3/7 — twice. Is that 3/7 the worker model's ceiling (architecture is world-class, model-bound) or the architecture's own ceiling (shallow loop, model-independent)?* This directive runs ONE cohort that isolates the worker as the single variable.

---

## 0. Why this is the highest-leverage next action (the framing, do not skip)

The harness is done as engineering: §1 abuse-bound false-fail closed (Task 203), §2 M6 prompt floor landed, all 7 missions have ≥1 live critic-approved pass (envelope complete), gate green, origin synced. What's left is a **strategic question, not an engineering one**: is the 3/7 yield the *model's* ceiling or the *architecture's*?

**The evidence already says it's the model** — and this test confirms or refutes that:

Claude parsed the **entire ledger history** (tasks 83–208) and computed content-only per-mission pass rates: M1 ≈38%, M2 ≈45%, M3 ≈30%, M4 ≈67%, M5 ≈30%, M6 ≈44%, M7 ≈33%. The sum of those probabilities ≈ **2.87 expected passes per 7-mission window**. Both observed windows landed on **exactly 3/7** — the statistical expectation. **There is no gate defect left to find; the cohort yield is the worker's per-mission pass probability, summed.** M6's 206→208 flip (same floor, same model, fail→pass) already proved it's stochastic variance, not a ceiling.

So the prediction this test makes is clean: if the architecture is model-bound, a stronger worker lifts those 7 probabilities and the window yield rises toward 5–7/7. If the architecture is itself the ceiling, even a frontier worker stays at ~3/7. **Either outcome is a valid, valuable result** — it points the next investment (model selection vs. agentic depth) instead of leaving the question open.

---

## 1. The change — swap the COHORT worker to a frontier model (reversible, default-preserving)

### 1.1 The load-bearing fact (Claude verified this session)

`workspace/validation/run_cohort.py:49-66` `validation_roles()` **hardcodes the cohort worker as byteplus**:
```python
roles["worker"] = dict(byteplus)   # run_cohort.py:62 — forced, ignores models.yaml `worker:` role
roles["critic"] = dict(roles["manager"])   # :64 — critic = ollama/glm-5.2:cloud
```
argparse (`:168-178`) has **no `--worker` override**. So swapping the cohort worker is NOT a models.yaml flip (the cohort ignores `models.yaml`'s `worker:` role) — it requires a change to `validation_roles()`. **This is the one code change this directive requires.**

Note the nuance: the cohort worker has been `byteplus_coding/ark-code-latest` (forced), NOT the production worker (`ollama/kimi-k2.7-code:cloud`, models.yaml:24-29). The 3/7 yield is the **byteplus worker's** ceiling. This test replaces *that* variable.

### 1.2 The required change (reversible + default-preserving — pick the exact mechanism)

Add a cohort-worker override that `validation_roles()` honors, defaulting to byteplus (no behavior change when unset). Preferred mechanism — an env var:

```python
def validation_roles() -> dict:
    ...
    frontier_worker = os.environ.get("HARNESS_COHORT_WORKER_PROVIDER")
    if frontier_worker == "openai":
        openai_cfg = config["providers"]["openai"]
        roles["worker"] = {
            "provider": "openai",
            "hermes_provider": openai_cfg["hermes_provider"],
            "model": openai_cfg["routing_model"],   # gpt-4o
            "endpoint": openai_cfg["endpoint"],
            "authentication_reference": openai_cfg["authentication_reference"],
            "quota_group": None,   # no shared quota pool with ollama critic — genuinely separate
        }
    else:
        roles["worker"] = dict(byteplus)   # unchanged default
    roles["critic"] = dict(roles["manager"])   # critic stays ollama/glm-5.2:cloud — F120 independence preserved (openai ≠ ollama)
    ...
```

**Properties this MUST have** (Gemini, verify your implementation against these):
- **Default-preserving:** with the env var unset, `validation_roles()` returns byteplus exactly as today. The 79/79 gate must stay green with no env var set.
- **Critic unchanged:** the critic stays `ollama/glm-5.2:cloud` (roles["manager"]). **Do NOT move the critic to openai** — that would destroy the A/B (we're isolating the worker) and break F120 independence only if both were openai. Critic = ollama, worker = openai = independent. ✓
- **Reversible:** set the env var only for the test cohort; it's gone next session. No models.yaml production change.

Add a hermetic test in `tests/test_cohort_isolation.py` (or equivalent): `validation_roles()` with env var unset → worker.provider == "byteplus_coding"; with `HARNESS_COHORT_WORKER_PROVIDER=openai` → worker.provider == "openai" AND critic.provider == "ollama" (independence holds). Bump the gate count (read the real N/N, never hardcode).

### 1.3 Why openai/gpt-4o (and the stronger option)

`openai/gpt-4o` is the strongest worker **available on this box now**: configured (models.yaml:71-75), key in Credential Manager (`AGI_like/openai`, canary-verified live 2026-09-11), in the fallback chain with no quota_group (models.yaml:90 — genuinely separate from ollama). It's a fair frontier proxy.

**Honest limitation:** gpt-4o is strong but not top-3-frontier in 2026 (Claude Opus 5 / GPT-5 / Gemini-3-Pro era). If you want the *strongest* test — the literal "can this compete with Claude" test — the harness is already wired for it: `models.yaml:65-69` declares the `anthropic` provider with `routing_model: claude-sonnet-5`. It only lacks `ANTHROPIC_API_KEY` in the vault. **Operator decision:** if you provision an Anthropic key, this same directive tests `claude-sonnet-5` as the worker (set `HARNESS_COHORT_WORKER_PROVIDER=anthropic` and add the anthropic branch). That is the true "compete with Claude" ablation. GPT-4o is the immediately-available proxy; Claude-as-worker is the stronger follow-up if GPT-4o already lifts yield (or the decisive one if it doesn't).

---

## 2. Pre-flight (the kill-assumption — verify ALL before dispatch)

```bash
# 1. Ollama UP (critic = ollama/glm-5.2:cloud, reached through the local daemon — THE kill-assumption, same as every prior cohort)
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version',timeout=5); print('Ollama: UP')" 2>&1 | tail -1

# 2. OpenAI key LIVE (the new kill-assumption — the frontier worker must actually work; credman presence + one real call)
python -c "import sys; sys.path.insert(0,'orchestrator'); from secrets import credential_manager_has_api_key as h; print('openai key present:', h('openai'))"
# Then a live single-call canary (the failover canary pattern): a 1-token openai/gpt-4o completion. If it 429s or auth-fails, STOP.

# 3. Gate green (with NO env var set — confirms the default-preserving property)
python -B tests/run_all.py   # N/N, exit 0, zero FAIL lines

# 4. ESTOP engaged + 0 zombies (clean slate)
python -c "import sys; sys.path.insert(0,'orchestrator'); from execution_pause import pause_engaged; print('ESTOP:', pause_engaged())"
python -c "import sqlite3; db=sqlite3.connect('ledger/ledger.db'); print('zombies:', db.execute(\"SELECT COUNT(*) FROM tasks WHERE status='running'\").fetchone()[0])"
```

**Pre-flight pass criteria:** Ollama UP → openai key present AND canary call returns 200 → gate N/N (no env var) → ESTOP True → 0 zombies. If the openai canary fails (429/auth/quota), STOP — the test is meaningless with a broken frontier worker; report the failure. If Ollama is down, STOP (critic has no failover; same lesson as Task 185).

---

## 3. The run — ONE controlled window, same 7 missions, only the worker changes

```bash
# Set the override, run the full cohort under one operator-authorized window
HARNESS_COHORT_WORKER_PROVIDER=openai python workspace/validation/run_cohort.py --controlled-window
```

Same 7 missions (M1–M7, `workspace/validation/cohort_missions.json`), same specs, same critic (ollama/glm-5.2:cloud), same gate (§1 abuse-bound fix + §2 M6 floor both in). **Only the worker changes: byteplus/ark-code-latest → openai/gpt-4o.** This is a clean A/B against the byteplus control (already measured: 3/7, twice).

**Cost note (honest, operator-relevant):** byteplus was on-plan (no marginal $). The openai/gpt-4o worker is pay-per-token. The cohort is ~300k input / ~70k output tokens — at GPT-4o-class pricing that's on the order of a few dollars for the whole window (operator: verify current OpenAI pricing before authorizing; do not act on a from-memory price). Cheap for the strategic answer, but it is real spend the byteplus cohort was not. Flag it to the operator before the window opens.

---

## 4. Honest expectations (do NOT pre-judge the outcome)

| Outcome | Window yield | What it means | Next investment |
|---|---|---|---|
| **Architecture is model-bound** (predicted) | **5–7/7** | The architecture is world-class-grade; the worker was the only ceiling. M5/M7 (hardest, ~30%) should lift most. | Worker-model selection (production swap) + agentic depth + deployment hardening. |
| **Architecture is itself the ceiling** | **~3/7** (unchanged) | A frontier worker doesn't help → the shallow one-shot loop (`controlled_hermes.py`) is the bottleneck, not the model. | Stop model-tuning; build agentic depth (plan→act→observe→replan, critic-feedback re-research). |
| **Mixed** | **4/7** | Some missions are model-bound, some are loop-bound. | Per-mission attribution: which lifted (model-bound) vs. which didn't (loop-bound). |

**The prediction is the first row** (5–7/7), because the per-mission pass-rate math already shows the yield is the worker's probability summed — there's no gate defect masking anything. But **measure, don't assume.** If it lands on 3/7, that is a MORE important result than 7/7 — it says the architecture needs depth work, and no model swap will save it.

---

## 5. Parse-don't-trust on EVERY row (unchanged discipline — the bug class that caught everything)

For each mission: parse the ledger row (real status/verdict), parse the deliverable (real content, not an error shell), parse the M2 broker JSONL (aiprm rows — bug #1 must still hold), verify token provenance (usage file `input_tokens`/`output_tokens` vs ledger `tokens_in`/`tokens_out`), confirm 0 zombies. **Do not cite an artifact without parsing it.** Pay special attention to whether the openai worker actually fired (not a silent failover back to byteplus/ollama — check the usage file's provider/model field per attempt; if the worker rolled to a fallback rung, that mission is NOT a clean openai data point and must be flagged).

---

## 6. Do-not-do (invariants — unchanged)

- Do NOT disengage ESTOP without `--controlled-window`. The cohort runs inside ONE authorized window; ESTOP re-engages after.
- Do NOT dispatch if Ollama is down (critic has no failover) OR if the openai canary fails (broken frontier worker = meaningless test).
- Do NOT change the critic. Critic stays `ollama/glm-5.2:cloud` — isolating the worker is the whole point.
- Do NOT make the override default-on. `validation_roles()` with no env var MUST return byteplus (the 79/79 gate proves it).
- Do NOT relabel content fails as quota/infra cascades. Ledger is ground truth.
- Do NOT trust the gate exit code as green until you grep FAIL/FAILED AND confirm exit 0 (D6).
- Do NOT cite an artifact without parsing it (parse-don't-trust).
- Do NOT set `HARNESS_AUDIT_BACKEND=s3` / `HARNESS_AUDIT_ENFORCE=1` (B deferred).
- Credentials: in Credential Manager, never printed. Read presence via `orchestrator/secrets.py credential_manager_has_api_key()`.

---

## 7. Report back to Claude (the gate)

1. **The override:** the exact mechanism (file:line) added to `validation_roles()`; proof it's default-preserving (gate N/N with no env var); the hermetic test (env unset→byteplus, env=openai→openai worker + ollama critic).
2. **Pre-flight:** Ollama UP, openai canary result (status + latency + tokens), gate N/N, ESTOP, 0 zombies.
3. **The cohort — honest scorecard:** all 7 rows — task_id / status / verdict / tokens / which provider actually served the worker (from the usage file, not assumed) / real-cause attribution. The corrected yield. Per-mission: which flipped vs. the byteplus 3/7 baseline.
4. **The verdict:** model-bound (5–7/7), architecture-bound (~3/7), or mixed (4/7) — with the per-mission evidence.
5. **Cost:** actual openai token spend for the window (from usage files, summed).
6. **Any new bug** found (parse-don't-trust).

Claude will independently: re-parse the override + test, re-run the gate (no env var → still green), re-parse the cohort ledger + deliverables + broker JSONL, confirm the openai worker actually served each mission (no silent failover), and confirm the yield honestly. **Gemini's assertions are the input; Claude's independent verification is the gate.**

---

## 8. What this unlocks

This is the single test that decides the harness's trajectory:
- **If 5–7/7:** the architecture is validated as world-class-grade. You've built a hardened research harness whose only ceiling was the worker model — and you can now point a frontier model at it and get frontier-grade output. The path to "compete" is: swap the production worker to a frontier model, then build agentic depth on top of a proven architecture. The hardening (your real asset) carries forward unchanged.
- **If ~3/7:** the architecture has a depth problem no model fixes. That's a harder, more valuable finding — it says stop spending on model swaps and build the agentic loop. Either way, you stop guessing.

Run the override + hermetic test (no window), then ONE controlled window with the frontier worker, then report. This is the highest-leverage thing to do next — it resolves the one open strategic question about the harness for the cost of one cohort.

---

*Written by Claude Code (final reviewer), 2026-09-13. Baseline `d4a995c` (synced, 79/79, ESTOP engaged, rev 119). Load-bearing facts verified this session: `validation_roles()` hardcodes cohort worker=byteplus (run_cohort.py:62) with no argparse `--worker` override (168-178); models.yaml openai provider configured (71-75, 90) + key canary-verified live; critic=ollama/glm-5.2:cloud (roles["manager"]) so worker→openai keeps F120 independence; anthropic provider block exists (65-69) with routing_model claude-sonnet-5 but no key (the stronger-test option, operator-gated). Per-mission pass rates computed from full ledger parse (tasks 83-208): sum ≈ 2.87 expected passes/window, matching both observed 3/7 windows — no gate defect remains, yield is worker probability. The kill-assumption: Ollama up AND openai canary green before dispatch.*
