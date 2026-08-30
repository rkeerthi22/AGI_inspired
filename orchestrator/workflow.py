"""workflow.py — cross-task orchestration: synthesis, canaries, retry, repeated-failure.

Move 5c' extraction target. The four functions owned here orchestrate the
LATER stages of a fire (Evaluate -> next-attempt / canary / escalation),
not the per-task work itself -- run_task() stays in batch_runner.

Module rules (locked 2026-08-27, F58 §9 + §10):

  workflow.py MUST NOT import batch_runner or task_runner.
    Importing either would re-introduce the cycle that this module exists
    to break (workflow -> run_task -> ... -> batch_runner -> workflow).
    Composition lives in batch_runner.main(), which is the ONE place that
    knows about both run_task and the orchestration functions.

  retry_failed_this_fire requires run_task_fn explicitly.
    No fallback to a module-local run_task: workflow.py does not own
    run_task. The compatibility shim `run_task_fn=None` default exists
    ONLY so unit tests can pass a stub; production callers must always
    supply `run_task_fn=run_task`. A missing argument raises RuntimeError
    loudly rather than silently failing.

  Module-qualified dependency calls throughout.
    `execution.synthesis_with_failover(...)`, `evaluation.run_critic(...)`,
    etc. resolve at call time, so test monkey-patches on the canonical
    module object in sys.modules are honoured without capturing a stale
    reference (F58's lessons applied here too).

  Runtime context is module-qualified.
    ROOT, RUNS, and log are resolved through runtime_context at call time so
    run-scoped rebinding and the shared logger proxy remain truthful.
"""
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import runtime_context as rc

# Module-qualified dependencies. Listed in the order the operator locked in
# (REFACTOR_PLAN.md §5c'). Each name is imported as a MODULE, not as a flat
# symbol, so test patches on the canonical module hit the right binding.
import execution  # noqa: F401 -- used via execution.<name>(...)
import evaluation  # noqa: F401 -- used via evaluation.<name>(...)
import integrity  # noqa: F401 -- used via integrity.<name>(...)
import ledger  # noqa: F401 -- used via ledger.<name>(...)
import policy  # noqa: F401 -- used via policy.<name>(...)
import prompts  # noqa: F401 -- used via prompts.<name>(...)
import scheduler  # noqa: F401 -- used via scheduler.<name>(...)


def _status_for_critic_verdict(verdict: str) -> str:
    """Keep infrastructure unavailability distinct from content rejection."""
    return ("done" if verdict == "pass" else
            "infra_failed" if verdict == "infra_failed" else "failed")

# ── Constants owned by workflow.py ─────────────────────────────────────
# (Operator lock: only CANARIES / MAX_RETRIES_PER_FIRE /
#  REPEATED_FAILURE_THRESHOLD move here. SYNTHESIS_MAX_BRIEFS, FACT_LEDGER_CAP,
#  WORKER_TIMEOUT_S, MAX_WORKER_CALLS_PER_RUN, etc. stay in their owning
#  module (prompts / execution / batch_runner).)

CANARIES = [
    ("C1", "In what year was Shopify founded? Use web search. Reply: the year, then the source URL.",
     lambda t: "2006" in t and "http" in t),
    ("C2", "What does HTTP status code 429 mean? Use web search. Reply: the meaning, then the source URL.",
     lambda t: "too many requests" in t.lower() and "http" in t),
    ("C3", "What is the capital city of Australia? Use web search. Reply: the city, then the source URL.",
     lambda t: "canberra" in t.lower() and "http" in t),
    ("C4", "Who wrote the paper introducing the Transformer architecture and what is its title? "
           "Use web search. Reply: title, at least one author, source URL.",
     lambda t: "attention is all you need" in t.lower() and "http" in t
               and any(a in t.lower() for a in ("vaswani", "shazeer", "parmar"))),
    ("C5", "Answer these four, each with a source URL, as a 4-row markdown table "
           "(question | answer | source): Shopify founding year; meaning of HTTP 429; "
           "capital of Australia; title of the Transformer paper. Use web search.",
     lambda t: t.count("http") >= 4 and t.count("|") >= 12),
]

MAX_RETRIES_PER_FIRE = 3

REPEATED_FAILURE_THRESHOLD = 3


# ── run_synthesis ──────────────────────────────────────────────────────

def run_synthesis(tid: int, row: dict, mission: dict, roles: dict, out_dir: Path,
                  wk: str, baseline: bool, baseline_note: str) -> str:
    """Synthesis seeds derive from THIS WEEK's briefs + the fact ledger — tool-free
    (no browser worker; the material is supplied, inventing new facts is forbidden)."""
    briefs = sorted(p for p in out_dir.glob(f"{wk}_*.md") if "synthesis" not in p.name)
    brief_block = prompts.build_brief_block(briefs)
    facts_block = prompts._recent_fact_lines()
    # F20, extended to synthesis 2026-07-28. This path was deliberately left out of the
    # original fix because there was no failure evidence for it -- there is now: task 27
    # failed the same night with the exact F20 signature ("omits the required per-fact
    # retrieval dates and confidence scores, lacks a dedicated top 'Changes since last
    # week' diff section"), i.e. graded against a spec it was never shown. The
    # work-only-from-supplied-material rule below is what keeps this safe: the prompt
    # already instructs the model to report an absent item as a data gap rather than
    # invent it, so stating the requirements cannot license fabrication.
    requirements = prompts.deliverable_requirements(mission)
    requirements_block = (
        "\n\nREQUIRED SHAPE OF THE DELIVERABLE — a reviewer checks your output against "
        "exactly these points, and a missing one is a FAIL even when the analysis itself "
        "is sound. Where the supplied material cannot support one of them, say so "
        f"explicitly as a data gap rather than inventing it:\n{requirements}"
        if requirements else "")
    # F31: same note the critic is given, from the same function -- see task_scope_note().
    scope_note = prompts.task_scope_note(row["spec"], mission)
    scope_block = f"\n\nSCOPE OF THIS TASK: {scope_note}"
    prompt = (
        f"You are a research analyst. Objective: {prompts.mission_objective(mission)}\n\n"
        f"YOUR TASK (one task only):\n{row['spec']}{requirements_block}{scope_block}"
        f"{baseline_note}\n\n"
        "Work ONLY from the material below — this week's research briefs and the fact ledger "
        "(current + prior week). Cite the source URLs already present in the material. Do NOT "
        "invent facts or sources that are not in the material. If a requested item is absent "
        "from the material (e.g. a market-pulse addendum that was never researched), state "
        "that plainly as a data gap instead of fabricating it.\n\n"
        # F49: the marker is useless unless the model is told what it means. The original
        # failure was not the model reasoning badly -- it was reasoning correctly from
        # 'absent' when the truth was 'withheld', because nothing distinguished the two.
        "IMPORTANT — TRUNCATION IS NOT A DATA GAP. A brief may carry a "
        "'[TRUNCATED BY THE HARNESS: ...]' marker, or a '[BRIEFS OMITTED BY THE HARNESS]' "
        "section may appear. Those mean the material WAS researched and exists, and was "
        "withheld from you only by a size or count cap. Report that situation as "
        "'supplied material truncated', naming the brief and the omitted amount, and say "
        "which parts of your task it prevented you from covering. Do NOT call it a data "
        "gap, do NOT conclude the topic does not exist, and do NOT tell the operator to go "
        "research it — they already have it. A data gap means nobody researched it; a "
        "truncation means you were not shown it.\n\n"
        f"## THIS WEEK'S BRIEFS\n{brief_block}\n\n## FACT LEDGER\n{facts_block}\n\n"
        "Reply with ONLY the deliverable markdown.")
    if policy.token_budget_breached():
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="policy.yaml tokens_per_day_hard_stop reached — parked",
                           append_note=True)
        integrity.escalate(f"task {tid}: daily token budget exhausted, parked (synthesis)",
                           trigger="cost_cap_breach", task_id=tid)
        rc.log(f"task {tid}: quota_wait (token budget)"); return "quota_wait"
    worker_cfg = roles["worker"]
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']} (tool-free synthesis)")
    import urllib.error
    try:
        # F9: synthesis_with_failover() consumes every 429 internally (trying the next
        # candidate) and only ever re-raises a NON-429 HTTPError, so the branch below
        # no longer needs its own e.code==429 case -- that path is handled before it
        # could reach here.
        syn_usage: dict = {}
        synthesis_result = execution.synthesis_with_failover(
            prompt, worker_cfg, log_prefix=f"task {tid} (synthesis)",
            usage_out=syn_usage)
        out, model_used_cfg, exhausted = synthesis_result
    except urllib.error.HTTPError as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis HTTP {e.code}",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed (HTTP {e.code})"); return "infra_failed"
    except Exception as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis call failed: {e}",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed ({e})"); return "infra_failed"

    if exhausted:
        reason = getattr(synthesis_result, "exhaustion_reason", "quota")
        quota_only = reason == "quota"
        status = "quota_wait" if quota_only else "infra_failed"
        note = ("quota/usage limit on every eligible model in the fallback chain"
                if quota_only else
                f"synthesis fallback unavailable ({reason}); at least one model was "
                "ineligible for the prompt context")
        ledger.finish_task(tid, artifacts=[], status=status,
                           critic_notes=note, append_note=True)
        rc.log(f"task {tid}: synthesis_exhausted ({reason})")
        return "chain_exhausted" if quota_only else "capacity_exhausted"
    if model_used_cfg != worker_cfg:
        ledger.update_model_used(
            tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']} (tool-free synthesis)")
        integrity.escalate(f"task {tid}: synthesis completed via failover to "
                           f"{model_used_cfg['provider']}/{model_used_cfg['model']} after quota "
                           f"exhaustion on the primary worker", trigger="model_failover", task_id=tid)
        worker_cfg = model_used_cfg  # so the deliverable footer below is truthful too

    (rc.RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = execution._strip_tool_chatter(out)
    if len(out.strip()) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars)")
        rc.log(f"task {tid}: failed (short output)"); return "failed"

    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {worker_cfg['model']} (synthesis, tool-free)_\n",
                    encoding="utf-8")
    critic_usage: dict = {}
    verdict, verdict_text = evaluation.run_critic(
        row, out, roles, baseline, scope_note=scope_note, usage_out=critic_usage)
    mission_usage = evaluation.build_mission_usage(tid, syn_usage, critic_usage)
    if verdict == "needs_review":
        integrity.escalate(f"task {tid}: critic verdict ambiguous -- {verdict_text[:200]}",
                           trigger="pass_criteria_ambiguous", task_id=tid)
    # F18 (docs/HARDENING.md): status must reflect the verdict, not just "a call
    # returned." Previously EVERY resolved synthesis landed status='done' regardless
    # of verdict -- weekly_fitness() and is_first_run_for_mission() both read status
    # only, so a critic-REJECTED deliverable was silently indistinguishable from a
    # pass anywhere except the separate critic_verdict column nobody was filtering on.
    status = _status_for_critic_verdict(verdict)
    # No fact extraction for synthesis — it derives from facts already in the ledger;
    # re-extracting would duplicate them.
    # F33 (docs/HARDENING.md): this call used to omit tokens entirely, so no synthesis
    # in the project's history ever recorded what it spent and policy.tokens_used_today()
    # was structurally blind to the whole task type. Measured 2026-07-29 by re-running
    # task 30: the daily counter sat at exactly 4,640,719 before AND after a real
    # synthesis. Accumulated onto the row's prior total for the same reason as F32 --
    # this path is retried like any other.
    tok_in = int(mission_usage.get("input_tokens") or 0) + int(row.get("tokens_in") or 0)
    tok_out = int(mission_usage.get("output_tokens") or 0) + int(row.get("tokens_out") or 0)
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(rc.ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out,
                       critic_verdict=("needs_review" if verdict == "infra_failed" else verdict),
                       critic_notes=verdict_text[:500], status=status)
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")
    rc.log(f"task {tid}: {status} verdict={verdict} (synthesis, {dest.name})")
    return status


# ── retry_failed_this_fire ─────────────────────────────────────────────

def retry_failed_this_fire(ids: list[int], mission: dict, roles: dict,
                            *, run_task_fn) -> list[str]:
    """Directive-2 (2026-07-29): re-attempt this fire's CONTENT failures immediately.

    `run_task_fn` is REQUIRED (Move 5c' extraction contract).
    workflow.py does not own run_task -- it lives in batch_runner. Composition
    layer (batch_runner.main) supplies the actual implementation via
    `retry_failed_this_fire(..., run_task_fn=run_task)`. Tests pass a stub.
    A missing argument raises RuntimeError immediately -- the previous
    fallback (`_task_runner = run_task_fn if run_task_fn is not None else
    run_task`) is intentionally removed because workflow.py has no `run_task`
    in its namespace.

    Until now a critic-rejected task was simply left failed. Next week's fire does not
    pick it up either -- queue_mission_tasks() dedups on a spec containing the ISO week,
    so a new week creates a NEW row and the rejected one is never revisited. The
    Evaluate -> next-attempt edge of the loop (HARNESS_DESIGN §2.1) therefore existed in
    code (run_task() has built prior_feedback from critic_notes all along) but had no
    path that ever exercised it. This is that path.

    Deliberately NOT retried: infra_failed. A worker timeout re-run costs another full
    WORKER_TIMEOUT_S (1800s) to most likely time out again -- that is a budget decision
    for the operator, not an automatic one. Content failures are what the critic's
    objections can actually steer.

    Synthesis retries go LAST so they rebuild from whatever briefs the research retries
    have just corrected, rather than from the versions that failed.
    """
    if run_task_fn is None:
        raise RuntimeError(
            "retry_failed_this_fire requires `run_task_fn` to be passed "
            "explicitly. workflow.py does not own run_task -- it lives in "
            "batch_runner.py. Production callers must supply "
            "`retry_failed_this_fire(..., run_task_fn=br.run_task)`. "
            "If you are seeing this from a test, your test must pass a stub."
        )
    if not ids:
        return []
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"SELECT task_id, spec FROM tasks WHERE task_id IN "
            f"({','.join('?' * len(ids))}) AND status='failed' AND critic_verdict='fail'",
            ids).fetchall()
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (evaluation.seed_is_synthesis(r["spec"]), r["task_id"]))
    picked = ordered[:MAX_RETRIES_PER_FIRE]
    rc.log(f"retry pass: {len(rows)} content failure(s) this fire, retrying "
        f"{len(picked)} with the critic's objections attached"
        + (f" ({len(rows) - len(picked)} over the {MAX_RETRIES_PER_FIRE}/fire cap)"
           if len(rows) > len(picked) else ""))
    out = []
    for r in picked:
        st = run_task_fn(r["task_id"], mission, roles)
        rc.log(f"retry task {r['task_id']}: {st}")
        out.append(st)
        if st == "chain_exhausted":
            rc.log("fallback chain exhausted — ending retry pass")
            break
    return out


# ── _check_repeated_failure ────────────────────────────────────────────

def _check_repeated_failure(mission_id: str) -> None:
    """policy.yaml's repeated_task_failure trigger (escalation.triggers): a mission
    accumulating this many content-FAILED tasks in the current week is a real signal
    the operator should see, independent of any single task's outcome."""
    wk = scheduler.week_key()
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        n = c.execute(
            "SELECT count(*) FROM tasks WHERE mission_id=? AND status='failed' "
            "AND critic_verdict='fail' AND spec LIKE ?", (mission_id, f"[{wk}]%")).fetchone()[0]
    if n == REPEATED_FAILURE_THRESHOLD:  # fire once, at the exact threshold crossing
        integrity.escalate(f"mission {mission_id}: {n} content-failed tasks this week ({wk})",
                           trigger="repeated_task_failure")


# ── run_canaries ───────────────────────────────────────────────────────

def run_canaries(roles: dict) -> None:
    # dedup/resume like queue_mission_tasks() -- found 2026-07-18: this used to call
    # queue_task() unconditionally, so re-running --canaries duplicated any already-
    # parked C-row instead of resuming it.
    worker_cfg = roles["worker"]
    green = 0
    wk = scheduler.week_key()
    for name, prompt, grade in CANARIES:
        spec = f"[{wk}] {name}"
        with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
            dup = c.execute("SELECT task_id, status, tokens_in, tokens_out FROM tasks "
                           "WHERE mission_id='canaries' AND spec=?", (spec,)).fetchone()
        if dup and dup[1] not in scheduler.RESUMABLE_STATUSES:   # H3 + F43 (infra recovers)
            rc.log(f"{name}: already {dup[1]} this week — skipping"); continue
        tid = dup[0] if dup else ledger.queue_task("canaries", spec, "deterministic grade")
        # F48: a resumed canary must ADD to what the row already spent, not replace it --
        # the same reason run_task() reads `row` before the attempt starts (F32). A canary
        # is resumable by RESUMABLE_STATUSES, so this is a live case, not a theoretical one.
        prior_in, prior_out = (dup[2], dup[3]) if dup else (0, 0)
        # F8/F13: canaries draw from the same daily token budget as mission work.
        if policy.token_budget_breached():
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               critic_notes="policy.yaml tokens_per_day_hard_stop reached",
                           append_note=True)
            integrity.escalate(f"canary {name}: daily token budget exhausted, parked",
                               trigger="cost_cap_breach")
            rc.log(f"{name}: quota_wait (token budget)"); continue
        ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
        fs_snapshot = integrity.fs_integrity_snapshot()
        try:
            # F40 (docs/HARDENING.md): canaries NEVER run on a local model. Their green
            # count is the only signal that automatically deletes an operator-approved
            # skill, so it has to measure the analyst, not whichever model happened to be
            # reachable. Measured 2026-07-29: the three canaries that ran on cloud all
            # passed and the two that failed over to gemma both failed; asked tool-free,
            # the local models answer C1's question 2004 and 2013 against a true 2006. With
            # the F38 cap making that rung actually loadable, those would have become
            # scoreable content failures and cost a skill. Excluded, a quota-exhausted
            # canary parks instead — week_pending rises, the rollback gate stays shut
            # (F37), and the skill survives to be judged on real data.
            with integrity.DatabaseMutationGuard(f"canary {name}"):
                out, usage, model_used_cfg, exhausted = execution.worker_with_failover(
                    prompt, worker_cfg, rc.RUNS / f"canary_{name}.usage.json",
                    log_prefix=f"canary {name}", allow_local=False)
        except subprocess.TimeoutExpired:
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               critic_notes="canary timeout",
                           append_note=True)
            rc.log(f"{name}: infra_failed (timeout)"); continue
        except integrity.DatabaseMutationViolation as exc:
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               critic_notes=f"database containment violation: {exc}",
                               append_note=True)
            rc.log(f"{name}: infra_failed (database containment violation)"); continue
        integrity.fs_integrity_check(fs_snapshot, context=f"canary {name}")
        if exhausted:
            tok_in, tok_out = scheduler.accumulated_tokens(usage, prior_in, prior_out)
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               tokens_in=tok_in, tokens_out=tok_out,
                               critic_notes="quota on every model in the fallback chain "
                                            "— canary parked (F9)",
                           append_note=True)
            rc.log(f"{name}: quota_wait (fallback chain exhausted)"); continue
        if model_used_cfg != worker_cfg and not execution.worker_failed(out, usage):
            ledger.update_model_used(tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']}")
            integrity.escalate(f"canary {name}: completed via failover to {model_used_cfg['provider']}/"
                               f"{model_used_cfg['model']} after quota exhaustion on the primary worker",
                               trigger="model_failover")
        # F37 (docs/HARDENING.md), 2026-07-29: run_task() has always classified an API/model
        # failure as infra_failed before judging content; this path went straight to grade()
        # and scored the error TEXT as a wrong answer. Measured live the same night: with
        # cloud quota exhausted, canaries C2 and C5 failed over to local gemma4:12b, which
        # never started ("API call failed after 3 retries: HTTP 500: llama-server startup
        # fail"). The grader looked for a year/city in that string, missed, and recorded
        # critic_verdict='fail' -- infrastructure flakiness written into the ledger as the
        # analyst being wrong, in the one path that gates deletion of approved skills.
        if execution.worker_failed(out, usage):
            tok_in, tok_out = scheduler.accumulated_tokens(usage, prior_in, prior_out)
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               tokens_in=tok_in, tokens_out=tok_out,
                               critic_notes=f"model/API failure, NOT a content miss "
                                            f"(excluded from the green count): {out[:150]}",
                               append_note=True)
            rc.log(f"{name}: infra_failed ({out[:80]})"); continue
        ok = bool(grade(out))
        green += ok
        # F48 (docs/HARDENING.md), 2026-07-30: this call recorded NO tokens. `usage` was
        # returned by worker_with_failover() and consumed one line above by worker_failed(),
        # then dropped -- so all 6/6 resolved canary rows read 0/0 while mission rows carried
        # millions. policy.tokens_used_today() sums this column, so the daily hard stop
        # under-counted by exactly the canary spend.
        tok_in, tok_out = scheduler.accumulated_tokens(usage, prior_in, prior_out)
        ledger.finish_task(tid, artifacts=[], status="done",
                           tokens_in=tok_in, tokens_out=tok_out,
                           critic_verdict="pass" if ok else "fail",
                           critic_notes=f"deterministic: {'ok' if ok else 'MISS'} | {out[:150]}")
        rc.log(f"{name}: {'PASS' if ok else 'FAIL'} (in={tok_in} out={tok_out})")
    # Report the WEEK's actual state, not just this invocation's count -- found
    # 2026-07-18: a resume pass that only re-attempts quota-parked canaries printed
    # "0/5 green" and fired a false regression escalation, ignoring canaries that
    # already passed earlier this week and weren't touched by this pass.
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        rows = c.execute("SELECT status, critic_verdict FROM tasks WHERE mission_id='canaries' "
                         "AND spec LIKE ?", (f"[{wk}]%",)).fetchall()
    week_green = sum(1 for s, v in rows if s == "done" and v == "pass")
    week_pending = sum(1 for s, _ in rows if s in ("quota_wait", "queued", "interrupted"))
    # F37, second half: a canary that could not RUN is not a canary that answered wrongly.
    # Both are "not green", but only one is evidence about a skill.
    week_infra = sum(1 for s, _ in rows if s == "infra_failed")
    week_unjudged = week_pending + week_infra
    week_content_fail = len(CANARIES) - week_green - week_unjudged
    rc.log(f"canaries this week: {week_green}/{len(CANARIES)} green"
        f"{f', {week_pending} quota-parked' if week_pending else ''}"
        f"{f', {week_infra} infra-failed (model/API, not content)' if week_infra else ''}")
    if week_content_fail > 0:
        integrity.escalate(f"canary regression: {week_green}/{len(CANARIES)} green "
                           f"({week_content_fail} answered incorrectly) this week")
    elif week_unjudged:
        rc.log(f"{week_unjudged} canary(ies) never produced a content judgement "
            f"({week_pending} parked, {week_infra} infra) — not a regression, retry later")

    # Promoted-skill protection (§2.4): if this week's green count fell below the baseline
    # recorded when a skill was approved, auto-rollback the newest such skill.
    #
    # Gated on COMPLETE data. The original gate was `week_pending == 0`, on the sound
    # principle that quota-starved data is not evidence about a skill -- but F9 quietly
    # voided it: after cross-provider failover, quota exhaustion no longer PARKS a canary,
    # it completes one on a degraded model, so week_pending is 0 and the gate opens on data
    # that is exactly as unrepresentative as a park. Measured live 2026-07-29 (F37): cloud
    # quota exhausted, C2 and C5 failed over to a gemma4:12b that would not start, both
    # scored 'fail', green fell 5 -> 3 against a baseline of 3. `3 < 3` is False, so the
    # rollback missed deleting an operator-approved skill by exactly one canary -- for a
    # VRAM problem. Counting infra failures as unjudged closes that: partial data now skips
    # the judgement entirely, which is what the gate was always meant to do.
    if week_unjudged == 0:
        try:
            import promote  # late import: only used when the gate opens
            culprit = promote.newest_skill_below_baseline(week_green)
            if culprit:
                promote.cmd_rollback(culprit,
                                     reason=f"canary auto-rollback: week green {week_green} "
                                            f"fell below the skill's approval baseline")
                integrity.escalate(f"AUTO-ROLLBACK: skill {culprit} removed — canaries dropped to "
                                   f"{week_green}/{len(CANARIES)} while it was active")
        except Exception as e:
            rc.log(f"skill-protection check failed ({e}) — manual review advised")
