"""M1 batch execution engine — runs a mission's weekly tasks, the canaries, or the scorecard.
Called by Windows scheduled tasks (see missions/_M1_INDEX.md) or by hand.

    python orchestrator/batch_runner.py --mission 001-shopify-competitor-intel [--dry-run]
    python orchestrator/batch_runner.py --canaries
    python orchestrator/batch_runner.py --scorecard
    python orchestrator/batch_runner.py --resume            # only retry parked tasks (all missions)

Built around what the live runs exposed (HARNESS_DESIGN.md §1.6 + ledgerbook):
- workers go through `hermes -z` (web toolset — bare API cannot browse, sources would be fake);
- 429/quota → park quota_wait and continue queue; API/conn failure → infra_failed (cb106ef);
- resume-first: parked tasks retry before new ones queue; weekly dedup key prevents re-queueing;
- utf-8 everywhere (cp1252 crashes); every run appends runs/batch_<ts>.log;
- policy caps enforced: max worker calls per run, escalations to workspace/ESCALATIONS.md.
Stdlib + PyYAML only."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citecheck  # noqa: E402
import ledger  # noqa: E402
import policy  # noqa: E402

from runtime_context import (  # noqa: E402
    ROOT, RUNS, MISSIONS, ESCALATIONS, log, set_log_file,
)

MAX_WORKER_CALLS_PER_RUN = 12          # policy cost cap proxy (Ollama returns no $)
# Raised 900 -> 1800 on 2026-07-28. 900s was calibrated against UNDER-SPECIFIED tasks: before
# F20 the worker never received the mission's done-definition, so it did far less research
# than it was graded on (mission 001 seed 1 finished in ~4.6 min / 35 api_calls that way).
# Handing it the real spec -- >=2 product URLs per competitor, NEW-vs-last-week flags, promo
# check, review sentiment with rating AND recurring theme, plus a diff section, plus "address
# each prior objection" -- multiplies the browser work, and the first post-F20 run of that
# same seed hit the 900s ceiling with zero output (task 24, infra_failed, no usage file).
# CAUSATION IS UNCONFIRMED: no usage file, session dump, or partial output survived the kill,
# so a hermes hang or cloud slowness are still live alternatives. This raise is the cheap
# discriminating test -- if a task now completes in 15-25 min the size hypothesis holds; if it
# still dies at 1800s the cause is elsewhere and the ceiling is not the problem.
# COUPLED: ledger.LEASE_SECONDS must stay > this + ~360s (raised to 2400 in the same commit).
WORKER_TIMEOUT_S = 1800


def load_roles() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["roles"]




# ── model calls ────────────────────────────────────────────────────────────────

from integrity import (  # noqa: E402,F401
    escalate, _db_snapshot, db_integrity_snapshot, db_integrity_check,
    _untracked_files, _local_exclude_sources, _local_exclude_state,
    _masked_under_protected, _untracked_of, _tracked_hashes,
    fs_integrity_snapshot, fs_integrity_check, preflight,
    PROTECTED_PATHS, _PROVENANCE_TABLES,
)
from execution import (  # noqa: E402,F401  -- _strip_tool_chatter added in Move 5c'
    hermes_worker, ollama_chat, _is_local_model, load_fallback_chain,
    _quota_group, _fits_context, _context_skip_note,
    _failover_candidates, worker_with_failover, synthesis_with_failover,
    is_quota_error, worker_failed, _strip_tool_chatter,
    WORKER_TIMEOUT_S, LOCAL_FALLBACK_TIMEOUT_S, LOCAL_PROVIDERS,
    CHARS_PER_TOKEN, RESPONSE_RESERVE_TOKENS,
)
from prompts import (  # noqa: E402,F401
    pass_criteria_for, deliverable_requirements, task_scope_note,
    mission_objective, _recent_fact_lines, build_brief_block,
    SYNTHESIS_BRIEF_CHARS, SYNTHESIS_MAX_BRIEFS, FACT_LEDGER_CAP,
    _INTERNAL_CRITERIA_RE,
)
from evaluation import (  # noqa: E402,F401  -- Move 5c re-exports
    ENTITY_TYPES, _parse_json_array, extract_facts, run_critic,
    seed_is_synthesis, retract_facts,
)
from scheduler import (  # noqa: E402,F401
    parse_mission, week_key, queue_mission_tasks,
    is_first_run_for_mission, expire_stale_parked,
    reconcile_interrupted_tasks, accumulated_tokens, mission_workspace,
    RESUMABLE_STATUSES,
)

# ── Move 5c' compatibility re-exports ──────────────────────────────────
# The four canonical definitions below live in orchestrator/workflow.py.
# batch_runner keeps the same attribute names so existing imports
# (`from batch_runner import run_synthesis`, etc.) keep working without
# a hunt-and-replace pass through the codebase. The identity is asserted
# in tests/test_f58.py §10 (`br.X is workflow.X`) so a future drift --
# e.g. someone re-defining one of these here by accident -- fails the
# gate before it can reach production.
from workflow import (  # noqa: E402,F401  -- Move 5c' re-exports
    run_synthesis, run_canaries, retry_failed_this_fire,
    _check_repeated_failure, CANARIES, MAX_RETRIES_PER_FIRE,
    REPEATED_FAILURE_THRESHOLD,
)



# F49, second half (2026-07-30): raised 6000 -> 24000 after measuring, not guessing.
# At 6000, **11 of the 13 briefs on disk overflowed** -- truncation was the normal case,
# not an edge case, and the largest brief (15,968 chars) lost 9,968 of them. 24000 clears
# every observed brief with ~50% headroom.
#
# Measured cost, against a 20,000,000-token daily cap: a shopify synthesis prompt grows
# ~25,200 -> ~39,000 chars (~+3,400 tok) and a content one ~11,500 -> ~17,700 (~+1,500).
# That is the previously-withheld research finally reaching the model, which is the point.
# Worst case at these caps (6 briefs x 24,000 + the fact block) is ~41k tokens, inside every
# cloud rung's context.
#
# NOT a constraint, though it looks like one: the last fallback rung `gemma4:12b-ctx4k` has
# a 4,096-token context and therefore could never run a synthesis -- measured at the OLD cap
# it already needed 8,226 (content) to 11,662 (shopify) tokens. Raising the cap does not
# break that rung; it has been decorative for this path all along. See the note in
# docs/HARDENING.md F49.
SYNTHESIS_MAX_BRIEFS = 6         # how many of this week's briefs are supplied

def run_task(tid: int, mission: dict, roles: dict) -> str:
    """Execute one queued/parked task through worker→classifier→critic→ledger."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        row = dict(c.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())

    # Prediction Machine: record a prediction BEFORE the task runs (§predict→act→measure→learn).
    # Fault-tolerant: if the prediction machine is unavailable, the harness runs normally.
    # (Was previously gated on `mission["id"] != "canaries"` -- that excluded the system's own
    # pass/fail signal from the prediction store, so the canary rows the rest of the harness
    # uses to detect regressions were never learned from. Removed 2026-08-26: the canary path
    # produces the most stable prediction targets in the system -- short deterministic specs,
    # no live web tool, the same prompt every week -- so they are exactly the rows that should
    # seed a trained model. The hook is fault-tolerant: a canary that fails in the predictor
    # still runs the harness normally, and `after_task_completes` at the bottom of run_task
    # already returns None cleanly when no matching prediction exists.)
    try:
        sys.path.insert(0, str(ROOT.parent))
        from prediction_machine.integrations.batch_runner_hook import before_task_runs
        before_task_runs(tid, row["spec"], mission["id"])
    except Exception:
        pass  # prediction machine is optional — never block the harness

    worker_cfg = roles["worker"]
    wk = week_key()
    out_dir = ROOT / "workspace" / mission_workspace(mission["id"])
    out_dir.mkdir(parents=True, exist_ok=True)

    objective = mission_objective(mission)
    baseline = is_first_run_for_mission(mission["id"])
    # Retry-with-feedback: a re-queued row that previously failed review carries the
    # critic's objections — feed them to the worker so the loop actually learns
    # (Evaluate → next attempt, §2.1). Without this the feedback evaporates.
    prior_feedback = ""
    if row.get("critic_verdict") == "fail" and (row.get("critic_notes") or "").strip():
        prior_feedback = (
            "\n\nPREVIOUS ATTEMPT FAILED REVIEW. The reviewer's exact objections:\n"
            f"{row['critic_notes'][:600]}\n"
            "Address each objection specifically in this attempt.")
    baseline_note = (
        "\n\nBASELINE RUN: this is the first tracked run for this mission — there is no prior "
        "week to compare against. Do not attempt a week-over-week diff. Instead, mark every "
        "finding as the initial baseline (e.g. 'NEW — first tracked observation') so next "
        "week's run has something real to compare against."
        if baseline else "")
    if seed_is_synthesis(row["spec"]):
        synth_status = run_synthesis(tid, row, mission, roles, out_dir, wk, baseline, baseline_note)
        # Prediction Machine: record the actual outcome AFTER the synthesis completes.
        # Synthesis returns early (bypassing the main-flow after hook at the bottom of
        # run_task), so we need our own call here. Fault-tolerant: never block the harness.
        try:
            from prediction_machine.integrations.batch_runner_hook import after_task_completes
            after_task_completes(tid)
        except Exception:
            pass
        return synth_status
    # Promoted technique notes (§2.4): operator-approved, repo-versioned, capped ~2k.
    try:
        import promote
        skill_notes = promote.active_skills_for(mission["id"])
    except Exception:
        skill_notes = ""
    # H7 (docs/HARDENING.md): "log every injection in the run log". This text persists
    # into EVERY future prompt for the mission, which is what makes F10's chain worse
    # than a single poisoned task -- so which skills were live for a given run has to be
    # reconstructable from the log afterwards, not inferred from whatever the files
    # happen to say today. Names + total size, never the note bodies (the run log is not
    # the audit trail for content; git is).
    if skill_notes:
        try:
            names = [p.name for p in sorted(
                (promote.SKILLS / mission["id"]).glob("*.md"))]
        except Exception:
            names = ["<unreadable>"]
        capped = " CAPPED" if len(skill_notes) >= promote.MAX_INJECTED_CHARS else ""
        log(f"task {tid}: injecting {len(names)} approved skill(s), "
            f"{len(skill_notes)}/{promote.MAX_INJECTED_CHARS} chars{capped}: {names}")
    skills_block = (f"\n\nAPPROVED ANALYST TECHNIQUES (from your past reviewed work — "
                    f"apply where relevant):\n{skill_notes}" if skill_notes else "")
    compliance_block = policy.compliance_prompt_block()
    # F20 (docs/HARDENING.md): the critic grades against the mission's done-definition;
    # until now the worker never saw it, so it was judged on a spec it had no access to
    # (mission 001 tasks 24/25/26, 2026-07-27 -- 0/3, every reason a requirement stated
    # only in text the worker was not given). Internal paths/schema are stripped by
    # deliverable_requirements(), so this does NOT reopen the 2026-07-18 containment hole.
    # Ordered BEFORE baseline_note deliberately: on a first-ever run baseline_note's "do
    # not attempt a week-over-week diff" must read as the later, overriding exception to
    # the diff requirement below it.
    requirements = deliverable_requirements(mission)
    requirements_block = (
        "\n\nREQUIRED SHAPE OF THE DELIVERABLE — a reviewer checks your output against "
        "exactly these points, and a missing one is a FAIL even when the research itself "
        f"is sound:\n{requirements}" if requirements else "")
    # F31: same note the critic is given, from the same function -- see task_scope_note().
    scope_note = task_scope_note(row["spec"], mission)
    scope_block = f"\n\nSCOPE OF THIS TASK: {scope_note}"
    prompt = (
        f"You are a research analyst. Objective of this research area: {objective}\n\n"
        f"YOUR TASK THIS RUN (one task only):\n{row['spec']}"
        f"{requirements_block}{scope_block}{baseline_note}{prior_feedback}{skills_block}\n\n"
        f"Use web search for every fact. RULES: every fact needs a source URL + retrieval date "
        f"({datetime.now().date()}) + confidence 1-3. No fact without a live source. Seed names "
        f"are unverified — verify each is real before citing it. Write the deliverable as clean "
        f"markdown.\n\n"
        # F25 (docs/HARDENING.md): the 2026-07-28 spot-check found confidence 3 asserted on
        # values absent from the cited page, and one verbatim-quoted price sentence that does
        # not exist on the page it names. The prompt had never said what the levels MEAN, so
        # "3" was being used to signal conviction rather than verification.
        f"WHAT THE CONFIDENCE LEVELS MEAN — these are claims about EVIDENCE, not about how "
        f"sure you feel:\n"
        f"  3 = you loaded the cited page THIS RUN and read the exact value on it.\n"
        f"  2 = the value comes from a secondary/aggregator source, or the primary page was "
        f"blocked, cached, or rendered incompletely.\n"
        f"  1 = inferred, dated, or otherwise uncertain.\n"
        f"If a page was unreachable, 403/404, or Cloudflare-blocked, the highest honest "
        f"confidence is 1 — say so plainly rather than assigning 3 to a source you could not "
        f"read. Never cite a page you did not successfully open for a value you did not see "
        f"on it.\n"
        f"QUOTATIONS: text in quotation marks must be copied VERBATIM from the cited page. If "
        f"you are paraphrasing or reconstructing pricing/terms, write it as your own summary "
        f"without quote marks. A quoted sentence that does not appear on the page is treated "
        f"as fabrication, even when the underlying number is right.\n\n"
        f"TOOL FAILURES: If a web search or page fetch fails (timeout, 503, 403, or any error), "
        f"do not stop or produce an error message as your output. Note which sources failed, "
        f"continue with available sources, and produce the best deliverable you can with partial "
        f"evidence. Mark any fact that could only be sourced from a failed tool call as "
        f"confidence 1 and note the tool failure. An empty deliverable or one containing only "
        f"error text is a FAIL.\n\n"
        f"If you fail to find sources after exhausting your web_search tool limits, DO NOT give "
        f"up. Immediately fallback to using the requests tool to query known endpoints, or use "
        f"yt-dlp if applicable.\n\n"
        f"IMPORTANT: this is a research-only task. Use ONLY web/browser tools to look things up. "
        f"The only exceptions are the requests and yt-dlp fallbacks above after web_search is "
        f"exhausted. Otherwise, do NOT use any file, terminal, code-execution, or memory tool for "
        f"ANY reason — do not "
        f"create, write, or edit any file, and do not run any command except the requests/yt-dlp "
        f"fallbacks above. A separate system persists "
        f"your output; your job is only to research and reply with the deliverable markdown as "
        f"your final message text, nothing else."
        + (f"\n\n{compliance_block}" if compliance_block else "")
    )
    # F8/F13 (docs/HARDENING.md): Ollama reports no $, so token count is the real
    # daily consumption signal -- check BEFORE spending the call, not after.
    if policy.token_budget_breached():
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="policy.yaml tokens_per_day_hard_stop reached "
                                        "-- parked, retry once the day rolls over",
                           append_note=True)
        escalate(f"task {tid}: daily token budget exhausted, parked",
                 trigger="cost_cap_breach", task_id=tid)
        log(f"task {tid}: quota_wait (token budget)")
        return "quota_wait"
    # F24 (docs/HARDENING.md): admission control. The check above is a pure gate -- it
    # stops the next task only AFTER the cap is blown, which is how 2026-07-27 hit 360%
    # of cap on one uninterruptible 8.5M call. A hermes subprocess cannot be stopped
    # mid-research, so refuse work we can already predict will not fit instead of
    # pretending we can halt it later. Evidence comes from this task's own prior spend
    # (preserved only because F21 stopped retries zeroing it).
    est = policy.estimated_tokens_for(tid, mission["id"])
    if policy.budget_insufficient_for(est):
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes=f"parked by admission control: estimated {est:,} "
                                        f"tokens will not fit today's remaining budget",
                           append_note=True)
        escalate(f"task {tid}: estimated {est:,} tokens exceeds remaining daily budget "
                f"({policy.tokens_used_today():,} already spent) -- parked before starting",
                trigger="cost_cap_breach", task_id=tid)
        log(f"task {tid}: budget_skip (estimated {est:,} won't fit) — trying the next seed")
        return "budget_skip"
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
    usage_path = RUNS / f"task{tid}_worker.usage.json"
    snapshot = db_integrity_snapshot()
    fs_snapshot = fs_integrity_snapshot()
    try:
        out, usage, model_used_cfg, exhausted = worker_with_failover(
            prompt, worker_cfg, usage_path, log_prefix=f"task {tid}")
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes="worker timeout",
                           append_note=True)
        log(f"task {tid}: infra_failed (timeout)")
        return "infra_failed"

    db_integrity_check(snapshot, context=f"task {tid} worker call")
    fs_integrity_check(fs_snapshot, context=f"task {tid} worker call")
    # Persist the FULL raw output regardless of what happens next -- a misclassified
    # task must stay diagnosable. Learned 2026-07-18: a real, substantial brief was
    # nearly lost with only a 200-char snippet surviving in critic_notes.
    (RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = _strip_tool_chatter(out)

    if exhausted:
        # F9: every model in the chain hit quota -- park exactly as before this fix.
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit on every model in the "
                                        "fallback chain — parked (§1.6, F9)",
                           append_note=True)
        log(f"task {tid}: chain_exhausted (every fallback model quota-limited)")
        return "chain_exhausted"
    if model_used_cfg != worker_cfg:
        # F9: keep provenance truthful and flag the degraded-model deliverable for
        # spot-check priority -- a failover completion is not a free pass.
        ledger.update_model_used(tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']}")
        escalate(f"task {tid}: completed via failover to {model_used_cfg['provider']}/"
                f"{model_used_cfg['model']} after quota exhaustion on the primary worker",
                trigger="model_failover", task_id=tid)
        worker_cfg = model_used_cfg  # so the deliverable footer below is truthful too
    if worker_failed(out, usage):
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker API failure (full text in "
                                       f"runs/task{tid}_worker_raw.txt): {out[:200]}",
                           append_note=True)
        log(f"task {tid}: infra_failed ({out[:80]})")
        return "infra_failed"
    if len(out) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars) — no deliverable")
        log(f"task {tid}: failed (short output)")
        return "failed"

    # F13 deny-list (docs/HARDENING.md): heuristic scan of the worker's OWN report
    # for language claiming a hard-excluded action. The worker is never told these
    # tools/actions are off-limits by omission alone -- this makes the deny-list an
    # executed, escalated check rather than an unread document.
    deny_hits = policy.deny_list_scan(out)
    if deny_hits:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"deny-list match: {deny_hits} -- see policy.yaml "
                                        f"hard_exclusions; output not persisted as a deliverable")
        escalate(f"task {tid}: worker output matched deny-list pattern(s) {deny_hits}",
                trigger="deny_list_match", task_id=tid)
        log(f"task {tid}: failed (deny-list match {deny_hits})")
        return "failed"

    # write deliverable
    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {worker_cfg['model']}_\n", encoding="utf-8")

    verdict, verdict_text = run_critic(row, out, roles, baseline, scope_note=scope_note)
    if verdict == "needs_review":
        escalate(f"task {tid}: critic verdict ambiguous -- {verdict_text[:200]}",
                trigger="pass_criteria_ambiguous", task_id=tid)
    # F18 (docs/HARDENING.md): status must reflect the verdict. Previously EVERY
    # resolved task landed status='done' regardless of critic_verdict -- proven live
    # 2026-07-24: task_id 20/21/22 all carry critic_verdict='fail' with status='done',
    # so weekly_fitness() (which reads only status) reported 100% completion on a week
    # where the TRUE pass rate was 0/10. needs_review is also not 'done' -- an
    # unjudged deliverable must not silently count as complete either.
    status = "done" if verdict == "pass" else "failed"

    # F32 (docs/HARDENING.md), 2026-07-29: accumulate, don't replace. F21 made an
    # OMITTED token count preserve the prior attempt's; it does nothing for a retry that
    # SUCCEEDS and passes real numbers, which overwrites them -- so the failed attempt's
    # spend vanishes from tokens_used_today() and the daily guard again protects less
    # than it should. Latent while retries were rare; directive-2 below makes retries
    # routine, so it has to be closed first. `row` was read before this attempt started,
    # so it holds exactly the prior total (0/NULL on a first run -- a no-op there).
    # F48: the arithmetic moved to accumulated_tokens(), now shared with run_canaries().
    tok_in, tok_out = accumulated_tokens(usage, row.get("tokens_in"), row.get("tokens_out"))
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status=status)

    # Lesson capture (baseline weeks: harvest only, promotion stays OFF per §7):
    # critic objections become lesson_candidates so week-3 skill promotion has evidence.
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")
        _check_repeated_failure(mission["id"])

    # Memory-update stage: only PASSED research deliverables become facts.
    facts_n = extract_facts(tid, out, roles["manager"]["model"]) if verdict == "pass" else 0
    log(f"task {tid}: {status} verdict={verdict} facts+{facts_n} "
        f"({dest.name}, in={tok_in} out={tok_out})")

    # Prediction Machine: record the actual outcome AFTER the task completes.
    # Fault-tolerant: if the prediction machine is unavailable, the harness runs normally.
    try:
        from prediction_machine.integrations.batch_runner_hook import after_task_completes
        after_task_completes(tid)
    except Exception:
        pass  # prediction machine is optional — never block the harness

    return status


# Directive-1 (2026-07-29): a task can park for three very different reasons, and the
# batch loop used to treat all of them as "stop the whole fire". They are not the same:
#
#   budget_skip     -- admission control refused THIS task's predicted cost (F24). A
#                      cheaper seed behind it may well fit. Costs zero model calls.
#   quota_wait      -- the daily hard cap is blown. Every remaining task will park too,
#                      but parking them is free and leaves an honest, annotated row
#                      instead of a silent 'queued' with no explanation.
#   chain_exhausted -- every model in the fallback chain returned a quota error. This is
#                      the only one whose retry costs anything real, so it is the only
#                      one that stops the pass, and only after repeating.
#
# Treating budget_skip as a full stop was F6's head-of-line blocking rebuilt one layer
# up: on 2026-07-28 task 26 alone estimated ~8.5M tokens while tasks 28/29 needed 2.4M
# and 1.4M -- the expensive seed parked first and the two affordable ones behind it were
# never attempted.
PARK_STATUSES = ("quota_wait", "budget_skip", "chain_exhausted")
MAX_CONSECUTIVE_CHAIN_EXHAUSTED = 2

# F43 (docs/HARDENING.md), 2026-07-30: statuses a LATER invocation may pick up again.
# `infra_failed` belongs here and was missing, which F37 turned from harmless into blocking:
# once an API/model failure is correctly classified as infra rather than as a content 'fail',
# the row is no longer retryable at all, so a canary that failed because a model would not
# load stayed failed even after the infrastructure recovered. Found immediately -- cloud
# quota reset, the operator asked to re-run the canaries, and all five would have been
# skipped ("already infra_failed this week").
#
# Note this does NOT contradict directive-2's deliberate exclusion of infra_failed from
# retry_failed_this_fire(). That exclusion is about retrying inside the SAME fire, where
# conditions are unchanged and a timeout would just burn another 1800s. This is a later
# invocation, where the whole point is that conditions may have changed.

# ── main ───────────────────────────────────────────────────────────────────────
LOCK_PATH_NAME = ".batch.lock"  # lives under RUNS; see runlock.py for F1 rationale


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission")
    ap.add_argument("--canaries", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deliver", action="store_true",
                    help="with --scorecard: also push the summary line to Telegram (fail-soft)")
    ap.add_argument("--max-tasks", type=int, default=MAX_WORKER_CALLS_PER_RUN)
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    set_log_file(RUNS / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    import runlock
    try:
        with runlock.acquire(RUNS / LOCK_PATH_NAME):
            return _run(args)
    except runlock.AlreadyRunning as e:
        log(f"another batch_runner is already running — skipping this fire ({e})")
        return 0


def _run(args) -> int:
    """Everything that touches shared state (ledger/ledgerbook/workspace). Runs
    ONLY while main() holds the exclusive lock — never call this directly."""
    if args.scorecard:
        import scorecard
        md, line = scorecard.build(deliver=args.deliver)
        log("scorecard written"); print(md); print("SUMMARY:", line)
        # Sunday cadence: promotion review rides the scorecard task (no extra schtask).
        # Fail-soft: a quota-blocked review must never break scorecard delivery.
        try:
            import promote
            promote.cmd_review(notify=args.deliver, dry=False)
        except Exception as e:
            log(f"promotion review skipped ({e}) — retries next Sunday")
        return 0

    if not preflight():
        return 3
    # F13 (docs/HARDENING.md): one-time-per-run consistency check between the
    # fs-guard's PROTECTED_PATHS (H9) and policy.yaml's declared writable roots --
    # catches the two lists silently drifting apart. Warns + escalates, doesn't
    # block the run (a stale doc shouldn't halt real work; it should get fixed).
    path_problems = policy.validate_paths(PROTECTED_PATHS)
    if path_problems:
        log(f"policy/fs-guard path inconsistency: {path_problems}")
        escalate(f"policy.yaml/fs-guard path lists are inconsistent: {path_problems}")
    roles = load_roles()
    expire_stale_parked()
    reconcile_interrupted_tasks()

    if args.canaries:
        run_canaries(roles)
        return 0

    if args.resume:
        import sqlite3
        with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
            # Filter OUT canaries (use --canaries to resume those) BEFORE slicing to
            # max_tasks -- found 2026-07-18: slicing first let canary rows, which sort
            # earlier by task_id, silently consume the whole budget while the mission
            # task the operator actually wanted resumed was never reached (no error,
            # just a quiet no-op).
            # F6 (docs/HARDENING.md): never-attempted (started_at NULL -- hit the
            # pre-start_task() token-budget check, not an actual worker call) go before
            # already-attempted, same fairness rule as queue_mission_tasks() above.
            parked = [r[0] for r in c.execute(
                "SELECT task_id, mission_id FROM tasks WHERE status IN "
                "('quota_wait', 'interrupted') AND mission_id != 'canaries' "
                "ORDER BY (started_at IS NOT NULL), task_id")]  # H3
        log(f"resume mode: {len(parked)} parked/interrupted non-canary task(s)")
        ran = 0
        exhausted_streak = 0
        for tid in parked[:args.max_tasks]:
            with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
                mid = c.execute("SELECT mission_id FROM tasks WHERE task_id=?",
                                (tid,)).fetchone()[0]
            st = run_task(tid, parse_mission(mid), roles)
            ran += 1
            # Directive-1: same rule as the main loop -- a task that could not fit the
            # budget must not cancel the resume attempt of every task behind it.
            if st == "chain_exhausted":
                exhausted_streak += 1
                if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                    log("every fallback model still quota-limited — stopping resume pass")
                    break
            else:
                exhausted_streak = 0
        return 0

    if not args.mission:
        log("nothing to do (need --mission, --canaries, --scorecard, or --resume)")
        return 1

    mission = parse_mission(args.mission)
    if mission["frontmatter"].get("status") != "active":
        log(f"mission {args.mission} is not active — skipping")
        return 0

    ids = queue_mission_tasks(mission, args.dry_run)
    log(f"{args.mission}: {len(ids)} task(s) to run this pass (dedup week {week_key()})")
    if args.dry_run:
        return 0

    # Directive-1: give EVERY seed its turn. Only a repeatedly-exhausted provider chain
    # (the one park reason whose retry actually costs anything) stops the pass early --
    # see PARK_STATUSES.
    statuses = []
    exhausted_streak = 0
    for tid in ids[:args.max_tasks]:
        st = run_task(tid, mission, roles)
        statuses.append(st)
        if st == "chain_exhausted":
            exhausted_streak += 1
            if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                log(f"every fallback model quota-limited on {exhausted_streak} consecutive "
                    f"tasks — stopping this pass, remaining seeds stay queued")
                break
        else:
            exhausted_streak = 0

    # Move 5c' (workflow.py extraction): composition layer supplies the
    # task runner explicitly. workflow.retry_failed_this_fire raises if
    # run_task_fn is omitted -- this is the load-bearing wiring that
    # prevents a workflow -> batch_runner cycle. Before Move 5c' the
    # function used a module-local fallback; the extraction removed
    # that fallback so the seam is enforced here.
    statuses += retry_failed_this_fire(ids, mission, roles, run_task_fn=run_task)
    done = statuses.count("done")
    parked = sum(statuses.count(s) for s in PARK_STATUSES)
    log(f"run complete: {done}/{len(statuses)} done, {parked} parked, "
        f"{statuses.count('infra_failed')} infra, {statuses.count('failed')} failed")
    # Directive-5: a fire that produced new deliverables pushes the spot-check queue
    # instead of waiting for the operator to think of running `spotcheck.py list`.
    # Fail-soft on purpose -- an undeliverable notification must never fail the batch.
    if done:
        try:
            import spotcheck
            if spotcheck.notify_pending():
                log("spot-check queue pushed to Telegram")
        except Exception as e:
            log(f"spot-check notification skipped ({e})")
    print("FITNESS:", json.dumps(ledger.weekly_fitness(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
