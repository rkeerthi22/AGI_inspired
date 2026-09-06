# Shared Launch Brief — AGI_like (Multi-Agent)

**Audience:** Gemini CLI, Codex, Hermes, Human Operator
**Author:** Claude Code (independent review session)
**Timestamp:** 2026-09-06
**Git HEAD:** `3b1e1f4` (origin/master == master, tree clean)
**Working Tree:** Clean
**Current Task ID:** SHARED-LAUNCH-BRIEF-2026-09-06
**Task Status:** COMPLETE (documentation; gates no subsystem write scope)

> This is a **coordination brief**, not a single-agent handoff. Every resuming
> agent reads this alongside `docs/CURRENT_STATE.md` and `.harness/continuity/current.json`.
> It states what is verified, what each agent does next, what is held, and when to wake whom.

---

## 1. Verified Current State (measured this session, not trusted from docs)

| Item | State | Evidence |
| :--- | :--- | :--- |
| Model-free gate | **74/74 green, exit 0** | ran `python -B tests/run_all.py` live |
| Git | `origin/master == master == 3b1e1f4`, clean | `git fetch` + `git log` |
| Last landings | F121–F126 (release admission → restricted worker token → deliverable preflight) | git log |
| F126 review corrections | **BOTH held** — citecheck reuse (no 403=dead regression) + no-socket pin + infra-guard | read `orchestrator/deliverable_preflight.py`, `task_runner.py:496`, `test_deliverable_preflight.py` |
| OmniRoute | HELD (not wired; zero refs in `config/models.yaml`) | confirmed in diff |
| Write scope | CLEAR — every `ACTIVE_WORK.json` entry `completed` | read the file |
| ESTOP | engaged (`True`) | CURRENT_STATE.md |
| Live yield | **1/6** (only M4/task 115 passed Sept 3 cohort) | CURRENT_STATE.md lines 80-85 |

**One-line strategy:** Code-side yield work is DONE. F126 + failover/provider-id fixes
should lift yield 1/6 → ~3/6 (M3, M5) — *but that is theory until a live cohort proves it.*
Everything remaining is operator-provisioned (the WFP rule) or live-validation (a cohort
under quota reset + operator-authorized window). **Do not write more code until a live
measurement says you need to.**

---

## 2. Per-Agent Forward Roles

### Gemini CLI — Independent Principal Architect / Implementer
- You just landed F126 with **both** Claude review corrections honored. That is verified.
- **Next code task: NONE is blocking.** Do not start a new feature. The next code task
  emerges ONLY after a live cohort reveals a new bottleneck.
- **Do NOT wire OmniRoute.** See §4 held items. It is a separate architectural change under
  the verify → adversarial-review → comparison governance pause.
- **Your next useful action is review:** when the live cohort lands, independently verify the
  yield claim against the real ledger (`ledger/ledger.db`), not against any agent's summary.

### Codex — Forward Implementer / Integration
- Same standing as Gemini: no blocking code task. Forward-implementation role is paused
  pending a live measurement.
- Do NOT begin OmniRoute, three-identity deployment, or any architectural change without the
  governance gate (§4). F124's restricted token is landed; the full three-account model
  is deferred (lower-ROI today).
- If a Gemini or Claude landing touches the gate, independently re-run
  `python -B tests/run_all.py` before treating any green claim as real.

### Hermes — Backbone Runtime (web toolset, `hermes -z`)
- You are the runtime that serves worker calls during the live cohort. You are NOT a code
  author on this brief's items.
- **Your trigger:** provider quota reset (Ollama Cloud / BytePlus). The Sept 3 cohort's #1
  blocker was 429 exhaustion — do not serve a cohort against a quota wall.
- When the operator opens a controlled window, workers reach providers through you and the
  egress broker. Confirm the broker allowlist (`config/egress_policy.yaml:12-16`) covers the
  four providers before the window opens (it does today).

### Human Operator — the only one who can move the date
- The two actions that most accelerate launch are YOURS, not code:
  1. **WFP / `netsh` deny-direct-egress rule for the worker SID on this host** (§3, Step 2).
  2. **Open a controlled live cohort** when quota resets (§3, Step 1).

---

## 3. Launch Sequence (follow in order; each step has a trigger)

### Step 1 — Controlled live cohort (trigger: quota reset)
1. Probe quota health first: one supervised canary
   (`python -B orchestrator/batch_runner.py --canaries`). Do not run a cohort against a 429.
2. Run targeted revisits of the failed Sept 3 windows, in THIS order:
   - **M5 / task 116** (4/8 dead URLs) — F126's *direct* target. Cleanest proof. **Run first.**
   - **M3 / task 114** (missing spec cells) — F126's *direct* target. Second proof.
   - **M6 rerun / task 118** (graded fail, reason unrecorded) — diagnostic.
   - **M7 / task 119** (fabricated FlowGPT availability) — **honest limit**: preflight catches
     *missing* "not publicly disclosed" cells but CANNOT repair a fabricated claim without
     re-fabrication risk. Expect this to stay failed; that is the model-capability ceiling,
     not a harness gap. Do not "fix" it with more preflight.
3. ESTOP discipline: open controlled window → run → close → re-engage. No live work without
   the window.

### Step 2 — Host hardening (trigger: before the cohort launches workers)
Provision the **WFP / `netsh` deny-direct-egress rule for the worker SID** on this host.
Right now `HARNESS_EGRESS_ATTESTATION` is a *signed claim* the operator makes — it is not
machine-enforced on this box. The first live cohort is the moment that gap stops being
theoretical. This is the *lightweight slice* of Path 2; the full three-account deployment
(§4) can wait. F124 already did the restricted-token part; this rule makes the attestation real.

### Step 3 — Verify the yield claim honestly (trigger: cohort finishes)
Bring to Claude (or independently verify): task IDs + `runs/batch_*.log` + ledger rows.
Verify against the **real ledger**, not any agent's summary. Expected honest outcomes:
- M5/M3 pass → F126 works, yield ~3/6. Proceed to sustained proof.
- M5/M3 still fail → F126 did not transfer to live. Debug, do not assume.
- M7 fails → expected. Model-capability ceiling, not a regression.

### Step 4 — Sustained proof (trigger: Step 3 shows yield moved off 1/6)
Run 2–3 more cohort cycles across different missions over a week+. The launch question is
not "did it pass once" but "is the pass rate stable and the system recoverable." This
separates the **7/10 control-prototype** tier (supervised-windowed operation, ~1-2 weeks of
focused work IF quota holds) from the **3/10 production** tier (unattended autonomy, ~1
month+, needs full Path 2 + your Admin hands).

---

## 4. HELD Items — Do NOT touch until the unblock condition is met

### OmniRoute (architectural change; governance-gated)
**Held because:** as scoped (`npm i -g omniroute`, worker → `127.0.0.1:8000`) it is an
**F124 bypass** — a restricted worker reaching an unrestricted loopback daemon that egresses
as the controller. `deny_direct_egress` becomes a signed lie. It also adds ZERO new
reachability (all four providers already allowlisted in `config/egress_policy.yaml:12-16`)
and is an unaudited black box on the model-call path (provenance risk — see citecheck F10).

**Unblock — ALL four required before any `config/models.yaml` edit or live cohort:**
1. Topology = controller-layer routing (transparent, logged passthrough) OR OmniRoute routes
   THROUGH the egress broker to allowlisted hosts OR worker SID firewalled from `:8000`.
2. Every OmniRoute-routed call lands in the signed trajectory event stream (model, version,
   retries, token counts) — same provenance as today.
3. Double-retry interaction verified: OmniRoute retry + harness `worker_with_failover` must
   not BOTH fire on one 429 (would burn quota faster, the opposite intent).
4. Independently / adversarially reviewed.

### Three-identity host deployment (full Path 2)
**Held because:** heavier, lower-ROI today. F124 did the load-bearing part (restricted token).
Full AGI_Signer / AGI_Controller / AGI_Worker service accounts is defense-in-depth.
**Unblock:** only needed for unattended daily autonomy (the 3/10 production tier), not for
supervised-windowed operation.

### Token-policy reasoning guard
**Deferred:** add the defensive check (hidden thinking tokens counted toward the 20M cap at
`config/policy.yaml:23`; reasoning models fenced to `manager`/`critic` only, ≤40 calls/day) in
the SAME commit that first wires a thinking/reasoning model. No reasoning model is active
today (verified: `config/models.yaml` roles are `glm-5.2:cloud`, `kimi-k2.7-code:cloud`,
`ark-code-latest`, `gemma4:12b-ctx4k` — all non-reasoning).

---

## 5. Hard Invariants (never violate, regardless of quota pressure)

- **ESTOP engaged** between controlled windows. No live run/canary/isolation window without
  operator authorization.
- **Weak-AI strategy LOCKED** — preflight is mechanical, never a second LLM judge.
- **citecheck is the authoritative floor** — preflight rescues, never relaxes citecheck.
- **Single write scope** — claim in `ACTIVE_WORK.json` before editing; reviewer agents
  read-only. Multi-agent concurrent implementation requires ephemeral git worktrees.
- **Gate green with zero `[FAIL]` before handoff** — count is dynamic; read it from
  `tests/run_all.py` output, never a hardcoded number.
- **No OmniRoute on the live path** until the four §4 unblock conditions hold.
- **Provider quota is an operating constraint, not a reason to weaken the gate.**

---

## 6. When to Wake Each Agent (so the operator need not guess)

| Trigger | Wake | Bring |
| :--- | :--- | :--- |
| Quota reset, ready to cohort | Operator (opens window) + Hermes (serves calls) | canary result |
| Cohort finishes | Claude (verify) + Gemini (independent verify) | task IDs + `runs/batch_*.log` + ledger rows |
| M5/M3 still fail after F126 | Claude (debug) | the failing task's trajectory + citecheck evidence |
| M7 fails and tempted to "fix" | Claude (decide model-swap vs preflight limit) | the failing deliverable |
| Want to wire OmniRoute | Claude (check 4 conditions) | the topology proposal |
| A Gemini/Codex landing lands | any other agent (re-run gate) | the commit SHA |
| Want to push | the agent who re-ran the gate | confirmed exit 0 on pushed HEAD |

---

## 7. Quick Reference — Commands & File Pointers

**Commands:**
```bash
python -B tests/run_all.py                                  # verify gate before trusting any green claim
git fetch origin && git log --oneline origin/master..master # check local vs remote
python -B orchestrator/batch_runner.py --canaries           # quota health probe (run FIRST after reset)
python -B orchestrator/batch_runner.py --scorecard          # weekly fitness
python -B orchestrator/operator_cli.py preflight release    # F121 admission contract
```

**Where the truth lives on disk:**
- Live cohort results (the 1/6 truth): `docs/CURRENT_STATE.md` lines 80-85
- Provider config (ONLY place models live): `config/models.yaml`
- Egress allowlist: `config/egress_policy.yaml:12-16`
- Preflight code (must stay citecheck-reusing): `orchestrator/deliverable_preflight.py`
- Token cap: `config/policy.yaml:23` (`tokens_per_day_hard_stop: 20000000`)
- Governance: `docs/ACTIVE_WORK.json` + `AGENTS.md` + `docs/HANDOFF_PROTOCOL.md`
- Recovery brief: `.harness/continuity/current.json`

---

## 8. Live Model Calls Made This Session
- **Live calls made:** NO
- ESTOP remained engaged throughout. This brief is documentation only.

## 9. Exact Next Action
**Operator:** wait for quota reset → run one canary → if green, open a controlled window and
revisit **M5 / task 116 first**. Provision the WFP deny-direct-egress rule before workers
launch. Bring the cohort results to Claude/Gemini for independent ledger verification.

## 10. Explicit Do-Not-Do Directives
- Do NOT wire OmniRoute into `config/models.yaml` or run a live cohort on it.
- Do NOT disengage ESTOP without an authorized `--controlled-window` path.
- Do NOT edit files outside an owned `ACTIVE_WORK.json` scope.
- Do NOT treat any "green" claim as real without re-running `tests/run_all.py` at exit 0.
- Do NOT attempt to auto-repair M7's fabricated claim via preflight (re-fabrication risk).
