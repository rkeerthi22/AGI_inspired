"""Canonical single-task execution pipeline (Move 5d).

This module owns run_task; batch_runner only composes and re-exports it.
"""
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime

import evaluation
import execution
import integrity
import ledger
import policy
import promote
import prompts
import runtime_context as rc
import scheduler
import trajectory
import workflow
import citecheck
from retrieval_progress import (DEFAULT_RETRIEVAL_PROFILE,
                                DYNAMIC_BROWSER_PROFILE,
                                retrieval_policy_for_profile)
from health_events import emit as emit_health_event


@dataclass(frozen=True)
class _TaskContext:
    tid: int
    mission: dict
    roles: dict
    row: dict
    retrieval_profile: str = DEFAULT_RETRIEVAL_PROFILE


@dataclass(frozen=True)
class _TaskResult:
    status: str


MAX_RETRY_GAPS = 12
MAX_RETRY_GAP_CHARS = 240


def _extract_missing_list(text: str) -> list:
    """Parse the critic's structured 'MISSING:' block (weak-AI efficiency, F104)
    into a clean list of concrete gaps. Returns [] when no parseable block is
    present, so the caller falls back to injecting the raw prose objections (the
    pre-F104 path) -- the change is strictly an improvement or a no-op.

    The critic is a cheap model (same as manager, per F5) that we PROMPT to emit::

        VERDICT: FAIL
        MISSING:
        - source URL for the Notion claim
        - competitor pricing for Shopify

    but it may not comply. Only explicitly bulleted ('- ' / '* ') or numbered
    ('1.' / '1)') lines after a 'MISSING:' header are collected, so a trailing
    context sentence or the 'VERDICT:' line itself is never misread as a gap.
    This is the spec-compliance counterpart to F103's citation-evidence wiring: a
    dumb model can act on 'fix items 1-3' but not on 'the brief felt incomplete'."""
    m = re.search(r"MISSING:\s*(.*)", text or "", re.S | re.I)
    if not m:
        return []
    items = []
    for line in m.group(1).splitlines():
        stripped = line.strip()
        bm = re.match(r"(?:[-*•]|\d+[\.\)])\s+(.*)", stripped)
        if bm and bm.group(1).strip():
            items.append(bm.group(1).strip()[:MAX_RETRY_GAP_CHARS])
            if len(items) >= MAX_RETRY_GAPS:
                break
    return items


def _load_task(tid: int) -> dict:
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())


def _prepare_task_input(tid: int, mission: dict, roles: dict, row: dict,
                        retrieval_profile: str = DEFAULT_RETRIEVAL_PROFILE
                        ) -> _TaskContext:
    return _TaskContext(tid=tid, mission=mission, roles=roles, row=row,
                        retrieval_profile=retrieval_profile)


def _run_synthesis_task(context: _TaskContext, out_dir, wk, baseline, baseline_note) -> str:
    return workflow.run_synthesis(context.tid, context.row, context.mission,
                                  context.roles, out_dir, wk, baseline, baseline_note)


def _run_research_task(context: _TaskContext) -> str:
    """Execute a prepared research task through worker, critic, and ledger."""
    tid, mission, roles, row = (context.tid, context.mission,
                                context.roles, context.row)
    tw = trajectory.active()

    # Prediction Machine: record a prediction BEFORE the task runs (Â§predictâ†’actâ†’measureâ†’learn).
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
        sys.path.insert(0, str(rc.ROOT.parent))
        from prediction_machine.integrations.batch_runner_hook import before_task_runs
        before_task_runs(tid, row["spec"], mission["id"])
    except Exception as e:
        emit_health_event("prediction", "before_task_runs", e,
                          task_id=tid, mission_id=mission["id"])
        pass  # prediction machine is optional â€” never block the harness

    worker_cfg = roles["worker"]
    wk = scheduler.week_key()
    out_dir = rc.ROOT / "workspace" / scheduler.mission_workspace(mission["id"])
    out_dir.mkdir(parents=True, exist_ok=True)

    objective = prompts.mission_objective(mission)
    baseline = scheduler.is_first_run_for_mission(mission["id"])
    # Retry-with-feedback: a re-queued row that previously failed review carries the
    # critic's objections â€” feed them to the worker so the loop actually learns
    # (Evaluate â†’ next attempt, Â§2.1). Without this the feedback evaporates.
    prior_feedback = ""
    if row.get("critic_verdict") == "fail" and (row.get("critic_notes") or "").strip():
        notes = row["critic_notes"]
        missing = _extract_missing_list(notes)
        if missing:
            # F104: structured spec gaps (weak-AI efficiency). The critic was
            # prompted to emit a 'MISSING:' bullet list; present it as a numbered
            # checklist so a cheap model's retry targets concrete fixes instead of
            # paraphrasing prose objections. Falls through to prose otherwise.
            checklist = "\n".join(f"  [{i + 1}] {item}" for i, item in enumerate(missing))
            prior_feedback = (
                "\n\nPREVIOUS ATTEMPT FAILED REVIEW. The reviewer found these "
                "specific gaps — your new attempt MUST close EVERY one:\n"
                f"{checklist}\n"
                "Do not resubmit until each numbered item above is addressed.")
        else:
            prior_feedback = (
                "\n\nPREVIOUS ATTEMPT FAILED REVIEW. The reviewer's exact objections:\n"
                f"{notes[:600]}\n"
                "Address each objection specifically in this attempt.")
        # Mechanical citation evidence (weak-AI efficiency): the prior attempt's
        # citecheck produced a BLOCKED/DEAD/OK list per URL — that is FACT, not the
        # critic's opinion of it. Feed it to the worker directly so a cheap model
        # can act on "URL X is dead, replace it" instead of only "sourcing felt weak".
        # See citecheck.evidence_block / citecheck.is_dead; persisted at
        # runs/task{tid}_citation_evidence.json by evaluation.run_critic. Fail-soft: a
        # missing/unreadable file adds nothing.
        try:
            import json as _json
            _ev_path = rc.RUNS / f"task{tid}_citation_evidence.json"
            if _ev_path.exists():
                _ev = _json.loads(_ev_path.read_text(encoding="utf-8"))
                if _ev.get("evidence"):
                    _block = citecheck.evidence_block(_ev["evidence"])
                    # RC-1: only genuinely-DEAD citations need replacing. A BLOCKED
                    # URL (403/429) is a live page the bot couldn't fetch -- replacing
                    # it wastes a retry on a URL that wasn't actually broken.
                    _dead = sum(1 for e in _ev["evidence"] if citecheck.is_dead(e))
                    if _dead:
                        prior_feedback += (
                            f"\n\nMECHANICAL CITATION CHECK ({_dead} of "
                            f"{len(_ev['evidence'])} URLs dead or fabricated in the prior "
                            "attempt — this is verified fact, not opinion):\n"
                            f"{_block}\n"
                            "Replace every DEAD URL with a live one you have actually "
                            "fetched. BLOCKED URLs (403/429) are live pages the bot "
                            "could not fetch — keep those, do not replace them.")
        except Exception:
            pass  # never let an audit artifact block a retry
    baseline_note = (
        "\n\nBASELINE RUN: this is the first tracked run for this mission â€” there is no prior "
        "week to compare against. Do not attempt a week-over-week diff. Instead, mark every "
        "finding as the initial baseline (e.g. 'NEW â€” first tracked observation') so next "
        "week's run has something real to compare against."
        if baseline else "")
    if evaluation.seed_is_synthesis(row["spec"]):
        synth_status = _run_synthesis_task(context, out_dir, wk, baseline, baseline_note)
        # Prediction Machine: record the actual outcome AFTER the synthesis completes.
        # Synthesis returns early (bypassing the main-flow after hook at the bottom of
        # run_task), so we need our own call here. Fault-tolerant: never block the harness.
        try:
            from prediction_machine.integrations.batch_runner_hook import after_task_completes
            after_task_completes(tid)
        except Exception as e:
            emit_health_event("prediction", "after_synthesis_completes", e,
                              task_id=tid, mission_id=mission["id"])
            pass
        tw = trajectory.active()
        if tw:
            if synth_status == "done":
                tw.task_completed("pass", synth_status)
            else:
                tw.task_failed(f"synthesis {synth_status}", failure_stage="synthesis")
        return synth_status
    # Promoted technique notes (Â§2.4): operator-approved, repo-versioned, capped ~2k.
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
        rc.log(f"task {tid}: injecting {len(names)} approved skill(s), "
            f"{len(skill_notes)}/{promote.MAX_INJECTED_CHARS} chars{capped}: {names}")
    skills_block = (f"\n\nAPPROVED ANALYST TECHNIQUES (from your past reviewed work â€” "
                    f"apply where relevant):\n{skill_notes}" if skill_notes else "")
    compliance_block = policy.compliance_prompt_block()
    # F20 (docs/HARDENING.md): the critic grades against the mission's done-definition;
    # until now the worker never saw it, so it was judged on a spec it had no access to
    # (mission 001 tasks 24/25/26, 2026-07-27 -- 0/3, every reason a requirement stated
    # only in text the worker was not given). Internal paths/schema are stripped by
    # prompts.deliverable_requirements(), so this does NOT reopen the 2026-07-18 containment hole.
    # Ordered BEFORE baseline_note deliberately: on a first-ever run baseline_note's "do
    # not attempt a week-over-week diff" must read as the later, overriding exception to
    # the diff requirement below it.
    requirements = prompts.deliverable_requirements(mission)
    requirements_block = (
        "\n\nREQUIRED SHAPE OF THE DELIVERABLE â€” a reviewer checks your output against "
        "exactly these points, and a missing one is a FAIL even when the research itself "
        f"is sound:\n{requirements}" if requirements else "")
    # F31: same note the critic is given, from the same function -- see prompts.task_scope_note().
    scope_note = prompts.task_scope_note(row["spec"], mission)
    scope_block = f"\n\nSCOPE OF THIS TASK: {scope_note}"
    dynamic_browser = context.retrieval_profile == DYNAMIC_BROWSER_PROFILE
    if dynamic_browser:
        retrieval_block = (
            "Use the rendered canonical page for every fact. RETRIEVAL PROFILE: "
            "dynamic_browser_required. The external controller starts in browser mode. "
            "Your first retrieval call must be browser_navigate to the exact canonical URL "
            "named in the task. Then use browser snapshots, clicks, and scrolling to capture "
            "the complete table in both monthly and annual states, including seat counts and "
            "any promotion visibly rendered on the page. Do not call web_search, web_extract, "
            "requests, terminal, or code tools; they are outside this profile. Never attempt "
            "to solve or bypass a CAPTCHA, WAF, or access challenge. If one appears, record it "
            "as the blocking browser evidence."
        )
        fallback_block = ""
        # web_extract/web_search are FORBIDDEN in this profile, so the generic
        # web_extract-based citation self-check (defined in the else branch) does
        # not apply -- citations here come from the rendered canonical page.
        citation_selfcheck_block = ""
        tool_scope_block = (
            "IMPORTANT: this is a browser-only research task. Use ONLY browser tools to "
            "render and inspect the canonical page. Do NOT use file, terminal, requests, "
            "code-execution, memory, or search tools for any reason. A separate system "
            "persists your output; your job is only to research and reply with the "
            "deliverable markdown as your final message text, nothing else."
        )
    else:
        retrieval_block = "Use web search for every fact."
        fallback_block = (
            "\n\nIf you fail to find sources after exhausting your web_search tool limits, "
            "DO NOT give up. Immediately fallback to using the requests tool to query known "
            "endpoints, or use yt-dlp if applicable."
        )
        tool_scope_block = (
            "IMPORTANT: this is a research-only task. Use ONLY web/browser tools to look "
            "things up. The only exceptions are the requests and yt-dlp fallbacks above "
            "after web_search is exhausted. Otherwise, do NOT use any file, terminal, "
            "code-execution, or memory tool for ANY reason -- do not create, write, or edit "
            "any file, and do not run any command except the requests/yt-dlp fallbacks above. "
            "A separate system persists your output; your job is only to research and reply "
            "with the deliverable markdown as your final message text, nothing else."
        )
        # F106 (weak-AI efficiency): the post-hoc citecheck already re-fetches every
        # URL after submission and hard-fails on dead ones (F103 feeds that evidence
        # back on retry). The one in-loop lever that needs no new tool and no ESTOP
        # window is to tell the agentic worker -- which already has web_search /
        # web_extract -- to self-confirm each cited URL was opened to a live page THIS
        # run before finalizing. Attacks M5/task-116 (4/8 unreachable URLs cited from
        # search snippets the worker never opened). Strictly additive: a model that
        # ignores it is caught by the post-hoc gate exactly as before; one that
        # complies fixes dead URLs before the gate, saving a failed retry.
        citation_selfcheck_block = (
            "\n\nCITATION SELF-CHECK BEFORE YOU FINALIZE: after you submit, a separate "
            "mechanical check re-fetches every URL in your deliverable, and any "
            "unreachable URL fails the review and triggers a full redo. The most common "
            "review failure is a source URL pasted from a search snippet that was never "
            "actually opened. So before you write the final deliverable, confirm that "
            "EVERY URL you intend to cite is one you fetched to a live, working page THIS "
            "run (via a web_search result you followed, or web_extract). If a URL you want "
            "to cite returned 404, 403, a timeout, a block page, or any error, do NOT cite "
            "it: replace it with a URL you did successfully load, or drop the fact and mark "
            "it confidence 1 with a note that the source could not be reached. Every URL in "
            "your final deliverable must be one you personally opened to a working page this "
            "run."
        )
    prompt = (
        f"You are a research analyst. Objective of this research area: {objective}\n\n"
        f"YOUR TASK THIS RUN (one task only):\n{row['spec']}"
        f"{requirements_block}{scope_block}{baseline_note}{prior_feedback}{skills_block}\n\n"
        f"{retrieval_block} RULES: every fact needs a source URL + retrieval date "
        f"({datetime.now().date()}) + confidence 1-3. No fact without a live source. Seed names "
        f"are unverified â€” verify each is real before citing it. Write the deliverable as clean "
        f"markdown.\n\n"
        # F25 (docs/HARDENING.md): the 2026-07-28 spot-check found confidence 3 asserted on
        # values absent from the cited page, and one verbatim-quoted price sentence that does
        # not exist on the page it names. The prompt had never said what the levels MEAN, so
        # "3" was being used to signal conviction rather than verification.
        f"WHAT THE CONFIDENCE LEVELS MEAN â€” these are claims about EVIDENCE, not about how "
        f"sure you feel:\n"
        f"  3 = you loaded the cited page THIS RUN and read the exact value on it.\n"
        f"  2 = the value comes from a secondary/aggregator source, or the primary page was "
        f"blocked, cached, or rendered incompletely.\n"
        f"  1 = inferred, dated, or otherwise uncertain.\n"
        f"If a page was unreachable, 403/404, or Cloudflare-blocked, the highest honest "
        f"confidence is 1 â€” say so plainly rather than assigning 3 to a source you could not "
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
        f"{fallback_block}\n\n"
        f"{tool_scope_block}"
        + (f"\n\n{citation_selfcheck_block}" if citation_selfcheck_block else "")
        + (f"\n\n{compliance_block}" if compliance_block else "")
    )
    # F8/F13 (docs/HARDENING.md): Ollama reports no $, so token count is the real
    # daily consumption signal -- check BEFORE spending the call, not after.
    if policy.token_budget_breached():
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="policy.yaml tokens_per_day_hard_stop reached "
                                        "-- parked, retry once the day rolls over",
                           append_note=True)
        integrity.escalate(f"task {tid}: daily token budget exhausted, parked",
                 trigger="cost_cap_breach", task_id=tid)
        rc.log(f"task {tid}: quota_wait (token budget)")
        if tw:
            tw.task_failed("token budget exhausted", failure_stage="admission")
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
        integrity.escalate(f"task {tid}: estimated {est:,} tokens exceeds remaining daily budget "
                f"({policy.tokens_used_today():,} already spent) -- parked before starting",
                trigger="cost_cap_breach", task_id=tid)
        rc.log(f"task {tid}: budget_skip (estimated {est:,} won't fit) â€” trying the next seed")
        if tw:
            tw.task_failed("estimated tokens exceed remaining daily budget",
                           failure_stage="admission")
        return "budget_skip"
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
    usage_path = rc.RUNS / f"task{tid}_worker.usage.json"
    fs_snapshot = integrity.fs_integrity_snapshot()
    try:
        with integrity.DatabaseMutationGuard(f"task {tid} worker call"):
            worker_options = {}
            if context.retrieval_profile != DEFAULT_RETRIEVAL_PROFILE:
                worker_options["retrieval_profile"] = context.retrieval_profile
            out, usage, model_used_cfg, exhausted = execution.worker_with_failover(
                prompt, worker_cfg, usage_path, log_prefix=f"task {tid}",
                **worker_options)
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes="worker timeout",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed (timeout)")
        if tw:
            tw.task_failed("worker timeout", failure_stage="execution")
        return "infra_failed"
    except integrity.DatabaseMutationViolation as exc:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"database containment violation: {exc}",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed (database containment violation)")
        if tw:
            tw.task_failed("database containment violation", failure_stage="execution")
        return "infra_failed"
    except Exception as exc:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker launch failure: {exc}",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed (worker launch failure: {exc})")
        if tw:
            tw.task_failed("worker launch failure", failure_stage="execution",
                           detail=str(exc)[:200])
        return "infra_failed"

    integrity.fs_integrity_check(fs_snapshot, context=f"task {tid} worker call")
    # Persist the FULL raw output regardless of what happens next -- a misclassified
    # task must stay diagnosable. Learned 2026-07-18: a real, substantial brief was
    # nearly lost with only a 200-char snippet surviving in critic_notes.
    (rc.RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = execution._strip_tool_chatter(out)

    if exhausted:
        # F9: every model in the chain hit quota -- park exactly as before this fix.
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit on every model in the "
                                        "fallback chain â€” parked (Â§1.6, F9)",
                           append_note=True)
        rc.log(f"task {tid}: chain_exhausted (every fallback model quota-limited)")
        if tw:
            tw.task_failed("every fallback model quota-limited", failure_stage="execution")
        return "chain_exhausted"
    if model_used_cfg != worker_cfg and not execution.worker_failed(out, usage):
        # F9: keep provenance truthful and flag the degraded-model deliverable for
        # spot-check priority -- a failover completion is not a free pass.
        ledger.update_model_used(tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']}")
        integrity.escalate(f"task {tid}: completed via failover to {model_used_cfg['provider']}/"
                f"{model_used_cfg['model']} after quota exhaustion on the primary worker",
                trigger="model_failover", task_id=tid)
        worker_cfg = model_used_cfg  # so the deliverable footer below is truthful too
    if execution.worker_failed(out, usage):
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker API failure (full text in "
                                       f"runs/task{tid}_worker_raw.txt): {out[:200]}",
                           append_note=True)
        rc.log(f"task {tid}: infra_failed ({out[:80]})")
        if tw:
            tw.task_failed("worker API failure", failure_stage="execution",
                           detail=out[:200])
        return "infra_failed"
    if len(out) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars) â€” no deliverable")
        rc.log(f"task {tid}: failed (short output)")
        if tw:
            tw.task_failed("output too short", failure_stage="classification",
                           detail=f"{len(out)} chars")
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
        integrity.escalate(f"task {tid}: worker output matched deny-list pattern(s) {deny_hits}",
                trigger="deny_list_match", task_id=tid)
        rc.log(f"task {tid}: failed (deny-list match {deny_hits})")
        if tw:
            tw.task_failed("deny-list match", failure_stage="classification",
                           detail=str(deny_hits))
        return "failed"

    return _record_outcome(context, out, usage, worker_cfg, scope_note,
                           out_dir, wk, baseline)


def _record_outcome(context: _TaskContext, out: str, usage: dict,
                    worker_cfg: dict, scope_note: str, out_dir, wk: str,
                    baseline: bool) -> str:
    """Persist, grade, account for, and learn from a completed worker output."""
    tid, mission, roles, row = (context.tid, context.mission,
                                context.roles, context.row)
    # write deliverable
    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} Â· {datetime.now().isoformat(timespec='seconds')}"
                          f" Â· {worker_cfg['model']}_\n", encoding="utf-8")

    critic_usage: dict = {}
    verdict, verdict_text = evaluation.run_critic(
        row, out, roles, baseline, scope_note=scope_note, usage_out=critic_usage)
    mission_usage = evaluation.build_mission_usage(tid, usage, critic_usage)
    if verdict == "needs_review":
        integrity.escalate(f"task {tid}: critic verdict ambiguous -- {verdict_text[:200]}",
                trigger="pass_criteria_ambiguous", task_id=tid)
    elif verdict == "infra_failed":
        integrity.escalate(f"task {tid}: critic infrastructure unavailable -- "
                           f"{verdict_text[:200]}",
                trigger="model_infrastructure_failure", task_id=tid)
    # F18 (docs/HARDENING.md): status must reflect the verdict. Previously EVERY
    # resolved task landed status='done' regardless of critic_verdict -- proven live
    # 2026-07-24: task_id 20/21/22 all carry critic_verdict='fail' with status='done',
    # so weekly_fitness() (which reads only status) reported 100% completion on a week
    # where the TRUE pass rate was 0/10. needs_review is also not 'done' -- an
    # unjudged deliverable must not silently count as complete either.
    status = ("done" if verdict == "pass" else
              "infra_failed" if verdict == "infra_failed" else "failed")

    # F32 (docs/HARDENING.md), 2026-07-29: accumulate, don't replace. F21 made an
    # OMITTED token count preserve the prior attempt's; it does nothing for a retry that
    # SUCCEEDS and passes real numbers, which overwrites them -- so the failed attempt's
    # spend vanishes from tokens_used_today() and the daily guard again protects less
    # than it should. Latent while retries were rare; directive-2 below makes retries
    # routine, so it has to be closed first. `row` was read before this attempt started,
    # so it holds exactly the prior total (0/NULL on a first run -- a no-op there).
    # F48: the arithmetic moved to scheduler.accumulated_tokens(), now shared with run_canaries().
    tok_in, tok_out = scheduler.accumulated_tokens(
        mission_usage, row.get("tokens_in"), row.get("tokens_out"))
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(rc.ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out,
                       critic_verdict=("needs_review" if verdict == "infra_failed" else verdict),
                       critic_notes=verdict_text[:500], status=status)

    # Lesson capture (baseline weeks: harvest only, promotion stays OFF per Â§7):
    # critic objections become lesson_candidates so week-3 skill promotion has evidence.
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")
        workflow._check_repeated_failure(mission["id"])

    # Memory-update stage: only PASSED research deliverables become facts.
    facts_n = evaluation.extract_facts(
        tid, out, roles["manager"]["model"], roles["manager"].get("provider", "ollama"),
        roles["manager"]
    ) if verdict == "pass" else 0
    rc.log(f"task {tid}: {status} verdict={verdict} facts+{facts_n} "
        f"({dest.name}, in={tok_in} out={tok_out})")
    tw = trajectory.active()
    if tw:
        if status == "done":
            tw.task_completed(verdict, status, facts_extracted=facts_n)
        else:
            tw.task_failed(verdict_text[:200] if verdict_text else "unknown",
                           failure_stage="evaluation")

    # Prediction Machine: record the actual outcome AFTER the task completes.
    # Fault-tolerant: if the prediction machine is unavailable, the harness runs normally.
    try:
        from prediction_machine.integrations.batch_runner_hook import after_task_completes
        after_task_completes(tid)
    except Exception as e:
        emit_health_event("prediction", "after_task_completes", e,
                          task_id=tid, mission_id=mission["id"])
        pass  # prediction machine is optional â€” never block the harness

    return status



def run_task(tid: int, mission: dict, roles: dict,
             retrieval_profile: str | None = None) -> str:
    """Execute one queued/parked task through worker→classifier→critic→ledger."""
    tw = trajectory.begin(tid, mission["id"])
    try:
        row = _load_task(tid)
        worker_cfg = roles.get("worker", {})
        tw.task_started(
            spec=row.get("spec", ""),
            worker_model=worker_cfg.get("model", ""),
            worker_provider=worker_cfg.get("provider", ""),
        )
        normalized_profile = retrieval_policy_for_profile(retrieval_profile).profile
        if normalized_profile == DEFAULT_RETRIEVAL_PROFILE:
            # Preserve the established four-argument helper contract for all
            # generic callers and test doubles.
            context = _prepare_task_input(tid, mission, roles, row)
        else:
            context = _prepare_task_input(
                tid, mission, roles, row, normalized_profile)
        return _run_research_task(context)
    except Exception as exc:
        tw.task_failed(f"unhandled task runner exception: {exc}", failure_stage="task_runner")
        raise
    finally:
        trajectory.end()
