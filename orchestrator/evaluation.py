"""orchestrator/evaluation.py -- evaluation and memory service (Move 5c).

This module owns the orchestrator's grading and memory-update surface:
parsing the worker's JSON output, running the tool-free critic that
judges a deliverable against the mission spec, and extracting typed
facts into ``memory/ledgerbook.db``. It is described as a service
rather than pure evaluation because both core functions perform
retrieval and durable writes:

  - ``run_critic`` performs mechanical citation retrieval (via
    ``citecheck``), may invoke the LLM, and parses the verdict.
  - ``extract_facts`` spends a manager-call budget and writes
    SQLite state.

The module's dependency surface is locked:

    runtime_context  ->  integrity, execution, prompts
                                       \\
                                        +---> evaluation (this file)
                                       /
                       citecheck, ledger, policy, stdlib

Allowed imports:

  - ``runtime_context``: ROOT, RUNS, log
  - ``execution`` -- imported as a module so ``execution.ollama_chat(...)``
    can be monkey-patched truthfully by tests and future capability
    injection. DO NOT use ``from execution import ollama_chat`` -- that
    captures the reference at import time and the patch stops working.
  - ``citecheck`` -- mechanical citation verification
  - ``ledger`` -- RUN_ID (used as the source-process marker on rows
    inserted by this module)
  - ``policy`` -- manager_call_budget_breached, record_manager_call
  - Standard library only otherwise

What lives here (Move 5c scope):

  - ``seed_is_synthesis`` (Move 4 leaf; F30)
  - ``retract_facts`` (Move 4 leaf; HARNESS_DESIGN §1.2)
  - ``ENTITY_TYPES`` (the canonical set of valid entity_type values)
  - ``_parse_json_array`` (lenient array extraction from worker JSON)
  - ``extract_facts`` (memory-update stage; F33, F49)
  - ``run_critic`` (verdict stage; H4, F3, F4, F5, F20, F31)

What does NOT live here:

  - ``run_synthesis``, ``run_canaries`` -- orchestration mixing
    scheduler state with grading; goes to ``workflow.py`` (Move 5c').
  - ``retry_failed_this_fire``, ``_check_repeated_failure`` -- also
    workflow orchestration.
  - ``run_task`` -- per-task execution; goes to ``task_runner.py``
    (Move 5d).
  - ``prompt building``, ``pass_criteria_for``, ``deliverable_requirements``,
    ``task_scope_note``, ``mission_objective``, ``_recent_fact_lines``,
    ``build_brief_block`` -- prompt layer; stays in ``prompts.py``.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

import citecheck
import execution
import provider_chat
import ledger
import policy
import trajectory

from runtime_context import ROOT, RUNS, log

# Move 4 leaf functions (unchanged). Kept here because they share the
# entity/ledgerbook contract with the rest of this module.

def seed_is_synthesis(spec: str) -> bool:
    """Does this seed describe a synthesis (tool-free, works only from material already
    gathered) rather than fresh research?

    F30 (docs/HARDENING.md), 2026-07-29: this used to require the seed to literally START
    with "synthesis". Mission 002's seed 3 reads "Cross-channel synthesis: ..." -- one word
    off -- so it was routed to the full browser worker every week and did fresh web
    research instead of synthesising the two channel briefs it was written to combine.
    Confirmed in task 30's deliverable: it invented a channel ("AI News Recap", not one of
    the mission's two) and cited corticallabs.com, bbc.com and a Google blog post about
    self-healing roads -- generic AI news, no connection to the operator's channels. Every
    002 synthesis has failed since the mission went active (tasks 14, 22, 30); this is why.

    Match on the seed's LEADING CLAUSE only (to the first colon, capped), so a research
    seed that happens to mention synthesis in its body is not misrouted into the tool-free
    path where it could not do the lookups it needs."""
    body = re.sub(r"^\[[^\]]*\]\[seed \d+\]\s*", "", spec).lower()
    return bool(re.search(r"synthesi[sz]", body.split(":", 1)[0][:80]))


def retract_facts(task_id: int) -> int:
    """Close validity windows on all facts produced by a given task. Called when
    a spot-check FAILS a task the critic had passed -- the facts already extracted
    are tainted and must not persist as current truths. Uses supersede-not-delete
    semantics per HARNESS_DESIGN §1.2."""
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        cur = c.execute(
            "UPDATE facts SET valid_until=datetime('now'), status='retracted' "
            "WHERE source_task_id=? AND valid_until IS NULL",
            (task_id,),
        )
        return cur.rowcount


# Move 5c additions ─────────────────────────────────────────────────────────

ENTITY_TYPES = {"competitor", "product", "channel", "keyword", "trend", "niche", "other"}


def _parse_json_array(text: str) -> list:
    """Lenient array extraction (adapted from onboarding_autonomy.parse_json):
    strip think-blocks and code fences, take the outermost [...] block."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    t = re.sub(r"```(?:json)?|```", "", t)
    m = re.search(r"\[.*\]", t, flags=re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _provider_call_options(config: dict, purpose: str) -> dict:
    return provider_chat.options_from_config(config, purpose)


def extract_facts(tid: int, deliverable: str, manager_model: str,
                  manager_provider: str = "ollama",
                  manager_config: dict | None = None) -> int:
    """The loop's Memory-update stage: ONE tool-free manager call turns a PASSED
    deliverable into typed facts; the ORCHESTRATOR validates and writes them.
    Workers never touch ledgerbook.db (docs/INCIDENTS.md) — the extractor model
    only returns JSON text; every write below is this process. Returns rows written."""
    prompt = (
        "Extract every concrete, sourced fact from this research brief as a JSON array.\n"
        'Each item: {"entity": "<short subject name>", "entity_type": '
        '"competitor|product|channel|keyword|trend|other", "statement": "<one self-contained '
        'factual sentence, keep any number/price/date>", "source_url": "<the URL cited for it>", '
        '"retrieval_date": "YYYY-MM-DD", "confidence": 1-3}.\n'
        "ONLY include facts explicitly present in the brief WITH a cited source URL. "
        "No inference, no summarizing multiple facts into one. Output ONLY the JSON array.\n\n"
        f"BRIEF:\n{deliverable[:12000]}")
    if policy.manager_call_budget_breached():
        log(f"task {tid}: manager-call budget exhausted for today — memory update skipped")
        return 0
    policy.record_manager_call()
    try:
        config = manager_config or {"provider": manager_provider}
        raw = execution.ollama_chat(
            manager_model, prompt,
            **_provider_call_options(config, "fact_extraction"))
    except Exception as e:
        log(f"task {tid}: fact-extraction call failed ({e}) — memory update skipped")
        return 0
    written = 0
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        for it in _parse_json_array(raw)[:40]:
            if not isinstance(it, dict):
                continue
            entity = str(it.get("entity", "")).strip()[:80]
            stmt = str(it.get("statement", "")).strip()
            url = str(it.get("source_url", "")).strip()
            if not entity or not stmt or not url.startswith("http"):
                continue
            etype = str(it.get("entity_type", "other")).strip().lower()
            etype = etype if etype in ENTITY_TYPES else "other"
            conf = it.get("confidence", 1)
            conf = conf if isinstance(conf, int) and 1 <= conf <= 3 else 1
            date = str(it.get("retrieval_date", "")).strip() or str(datetime.now().date())
            c.execute("INSERT OR IGNORE INTO entities (type, name) VALUES (?,?)",
                      (etype, entity))
            existing = c.execute(
                "SELECT 1 FROM facts WHERE statement=? AND source_task_id=?",
                (stmt, tid),
            ).fetchone()
            if not existing:
                c.execute("INSERT INTO facts (entity, statement, provenance_url, provenance_date,"
                          " confidence, status, source_task_id, run_id) "
                          "VALUES (?,?,?,?,?,'candidate',?,?)",
                          (entity, stmt, url, date, conf, tid, ledger.RUN_ID))
                written += 1
    tw = trajectory.active()
    if tw:
        tw.facts_extracted(written, model=manager_model,
                           provider=config.get("provider", manager_provider)
                           if manager_config else manager_provider)
    return written


def critic_is_independent(worker_config: dict | None,
                          critic_config: dict | None) -> bool:
    """Return whether a critic is isolated from the worker's provider failure domain.

    Different models behind one provider still share credentials, quota, routing,
    and often the same upstream outage. The production boundary is provider
    separation, not a cosmetic model-name difference.
    """
    if not isinstance(worker_config, dict) or not isinstance(critic_config, dict):
        return False
    worker_provider = str(worker_config.get("provider") or "").strip().lower()
    critic_provider = str(critic_config.get("provider") or "").strip().lower()
    return bool(worker_provider and critic_provider and worker_provider != critic_provider)


def run_critic(row: dict, out: str, roles: dict, baseline: bool,
               scope_note: str = "", usage_out: dict | None = None,
               worker_config: dict | None = None) -> tuple[str, str]:
    """Tool-free critic judging deliverable CONTENT, now backed by a mechanical,
    non-LLM truth signal (H4, docs/HARDENING.md — fixes F3, F4). Returns
    (verdict, text) where verdict is 'pass' | 'fail' | 'needs_review' — the third
    value means "could not be judged, do not treat as pass or a confirmed fail,
    escalate for a human" (never a silent auto-fail, H4's stated fix for F4's
    brittle-parse bug that used to invert good verdicts unnoticed).

    F66: when ``usage_out`` is supplied, persist critic accounting
    (api_calls, input_tokens, output_tokens, total_tokens, citation_fetches,
    citation_unique_urls) on the dict itself and write the critic usage file
    at ``runs/task<task_id>_critic.usage.json``. The same call also persists
    the citecheck evidence table at ``runs/task<task_id>_citation_evidence.json``.
    A write failure must never convert a real verdict into a silent auto-fail,
    so persistence exceptions are logged and swallowed -- the verdict path
    stays trustworthy regardless of disk state.

    The production caller passes the actual worker configuration. A critic on
    the same provider is not independent, even when its model string differs:
    provider quota, credentials, routing, and outages are shared. In that case
    the result is ``needs_review`` before any critic call. Mechanical citecheck
    remains independent and runs before this routing guard."""
    usage = usage_out if usage_out is not None else {}
    usage["api_calls"] = 0
    usage["input_tokens"] = 0
    usage["output_tokens"] = 0
    usage["total_tokens"] = 0
    usage["citation_fetches"] = 0
    usage["citation_unique_urls"] = 0

    def _finish(verdict: str, text: str) -> tuple[str, str]:
        usage["total_tokens"] = int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0)
        try:
            (RUNS / f"task{row['task_id']}_critic.usage.json").write_text(
                json.dumps(usage, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            log(f"critic usage not persisted for task {row['task_id']} ({e})")
        return verdict, text

    evidence_error: str | None = None
    try:
        evidence = citecheck.verify(out)
    except Exception as e:
        log(f"citation check failed ({e}) -- proceeding without mechanical evidence")
        evidence = []
        evidence_error = str(e)
    summary = citecheck.summarize(evidence)
    usage["citation_fetches"] = len(evidence)
    usage["citation_unique_urls"] = len({e.get("url") for e in evidence if e.get("url")})
    try:
        (RUNS / f"task{row['task_id']}_citation_evidence.json").write_text(
            json.dumps({"task_id": row["task_id"], "fetch_attempts": len(evidence),
                        "unique_urls": usage["citation_unique_urls"],
                        "summary": summary, "error": evidence_error,
                        "evidence": evidence}, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        log(f"citation evidence not persisted for task {row['task_id']} ({e})")
    tw = trajectory.active()
    if tw:
        tw.citecheck_completed(summary["checked"], summary["dead"],
                               summary["dead_frac"],
                               hard_fail=citecheck.is_hard_fail(summary))
    if citecheck.is_hard_fail(summary):
        dead = [e["url"] for e in evidence if citecheck.is_dead(e)][:5]
        return _finish("fail", (f"MECHANICAL FAIL: {summary['dead']}/{summary['checked']} cited "
                                f"URLs dead or fabricated (dead_frac={summary['dead_frac']}): {dead}"))

    critic_cfg = roles.get("critic") if isinstance(roles, dict) else {}
    if not isinstance(critic_cfg, dict):
        critic_cfg = {}
    if worker_config is not None and not critic_is_independent(worker_config, critic_cfg):
        worker_provider = str(worker_config.get("provider") or "").strip() \
            if isinstance(worker_config, dict) else ""
        critic_provider = str((critic_cfg or {}).get("provider") or "").strip()
        if tw:
            tw.critic_evaluated("needs_review",
                                model=str((critic_cfg or {}).get("model") or ""),
                                provider=critic_provider)
        return _finish(
            "needs_review",
            "critic independence unavailable: worker and critic must use different "
            f"providers (worker={worker_provider or 'missing'}, "
            f"critic={critic_provider or 'missing'})")

    if policy.manager_call_budget_breached():
        if tw:
            tw.critic_evaluated("needs_review", model="manager_budget_breached", provider="")
        return _finish("needs_review", "manager-role call budget exhausted for today (policy.yaml "
                                       "cost_caps.manager_calls_per_day) -- critic skipped, not judged")
    policy.record_manager_call()
    model_usage: dict = {}
    usage["api_calls"] = 1
    try:
        if not critic_cfg:
            raise KeyError("critic")
        call_options = _provider_call_options(critic_cfg, "critic")
        verdict_text = execution.ollama_chat(
            critic_cfg["model"],
            "You are a strict critic judging a research analyst's TEXT deliverable.\n"
            "The criteria below are the mission's full spec, written for the system as a "
            "whole -- some lines describe things a SEPARATE orchestrator process handles "
            "automatically after your review (exact file paths/naming, saving facts to a "
            "database, logging your verdict). Do NOT fail the deliverable for missing those "
            "-- they are not the analyst's job and are not present in the text by design. "
            "Judge ONLY the CONTENT: does it cover the required topics, is every fact backed "
            "by a real source URL + date, is it well-sourced and substantive."
            # F31: the spec below is the mission's COMBINED brief; this deliverable is one
            # task's share of it. Without this the critic demands work of a task that is
            # structurally unable to do it (task 27, 2026-07-28) -- see task_scope_note().
            + (f"\n\nSCOPE -- READ BEFORE JUDGING: {scope_note} Grade this deliverable ONLY "
               f"on the share of the spec that is actually its own. Do NOT fail it for "
               f"omitting work that belongs to a sibling task."
               if scope_note else "")
            + ("\n\nThis is the mission's BASELINE (first-ever) run -- there is no prior week "
               "to diff against. Do NOT fail it for lacking a week-over-week diff or NEW-vs-"
               "last-week flags; correct behavior for a baseline run is marking everything as "
               "an initial observation, which is what you should look for instead."
               if baseline else "") +
            "\n\nA mechanical citation check already ran against the URLs in this "
            "deliverable (fetched live, independent of you) -- weigh it, but it does not "
            "replace your own judgment of substance and coverage:\n"
            f"{citecheck.evidence_block(evidence)}\n"
            "\n\nReply with a line reading exactly 'VERDICT: PASS' or 'VERDICT: FAIL'.\n"
            "If FAIL, your VERY NEXT lines must be a parseable, concrete list of exactly "
            "what the deliverable is missing or got wrong -- one item per line, each "
            "prefixed with '- '. The analyst's next attempt is given ONLY this list, so "
            "each item must name a fixable gap (e.g. '- source URL for the Notion pricing "
            "claim', '- competitor pricing for Shopify Basic', '- weekly traffic figure "
            "for week 2'), not a general impression. Use this format:\n"
            "MISSING:\n"
            "- <concrete missing/wrong item>\n"
            "- <concrete missing/wrong item>\n"
            "After the list you may add ONE optional sentence of context.\n\n"
            f"MISSION SPEC (for context only, see instructions above):\n{row['pass_criteria']}\n\n"
            # 24k cap: at 8k the critic factually mis-judged a real deliverable, marking
            # its later sections "absent" when they sat past the truncation (2026-07-18,
            # task 5 — Notion section at ~9.5k was called missing). Models here have 262k
            # context; the cap only guards against pathological outputs.
            f"DELIVERABLE:\n{out[:24000]}",
            # Persist WHY, not just the verdict: today's three 001 failures (24/25/26)
            # were only diagnosable because the one-sentence reason happened to name a
            # missing section. The full trace makes that reliable instead of lucky.
            trace_path=RUNS / f"task{row['task_id']}_critic_reasoning.txt",
            usage_out=model_usage,
            **call_options)
    except Exception as e:
        if tw:
            tw.critic_evaluated("infra_failed", model=critic_cfg.get("model", ""),
                                provider=critic_cfg.get("provider", ""))
        return _finish("infra_failed", f"critic model call failed ({e})")
    usage["input_tokens"] = int(model_usage.get("input_tokens") or 0)
    usage["output_tokens"] = int(model_usage.get("output_tokens") or 0)

    # Tolerant parse (H4, fixes F4): the old `.startswith("PASS")` check silently
    # inverted any reply with markdown bold, a "VERDICT:" prefix, or a leading
    # think-block into a false FAIL, indistinguishable from a real one in the
    # ledger. An unparseable reply is now 'needs_review', never a silent fail.
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", verdict_text, re.I)
    if not m:
        if tw:
            tw.critic_evaluated("needs_review", model=critic_cfg.get("model", ""),
                                provider=critic_cfg.get("provider", ""))
        return _finish("needs_review", verdict_text[:500] + " [UNPARSEABLE VERDICT]")
    parsed_verdict = m.group(1).lower()
    if tw:
        tw.critic_evaluated(parsed_verdict, model=critic_cfg.get("model", ""),
                            provider=critic_cfg.get("provider", ""))
    return _finish(parsed_verdict, verdict_text)


def build_mission_usage(tid: int, worker_usage: dict, critic_usage: dict) -> dict:
    """F66: merge worker/finalizer, critic, and citation retrieval accounting
    into one mission usage file. The arithmetic is direct, no guesswork:

        total_tokens     = worker_in + worker_out + critic_in + critic_out
        api_calls        = worker_api_calls + critic_api_calls
        executed_retrieval_calls  read from JSONL research_finished row
        rejected_agent_retrieval_attempts  read from JSONL research_finished row
        citation_fetches          = number of citecheck.verify() results
        citation_unique_urls      = distinct URLs in citecheck evidence
        total_external_retrieval_calls
                                  = executed_agent_retrieval + citation_fetches
            (covers the apples-to-apples number across runs that
             have varying tool strategies and citation needs)

    The worker/finalizer split is preserved verbatim from the worker's usage
    file (``api_calls`` includes the finalizer). The critic block includes
    its own api_calls + in/out tokens + total_tokens so the mission total
    reconciles exactly across the three roles.
    """
    worker_in = int(worker_usage.get("input_tokens") or 0)
    worker_out = int(worker_usage.get("output_tokens") or 0)
    critic_in = int(critic_usage.get("input_tokens") or 0)
    critic_out = int(critic_usage.get("output_tokens") or 0)
    executed_retrieval = 0
    rejected = 0
    audit_path = RUNS / f"task{tid}_worker.usage.retrieval.jsonl"
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("event") == "research_finished":
                executed_retrieval = int(event.get("executed_retrieval_calls") or 0)
                rejected = int(event.get("rejected_calls") or 0)
    except (OSError, ValueError, TypeError):
        pass
    citation_fetches = int(critic_usage.get("citation_fetches") or 0)
    citation_unique = int(critic_usage.get("citation_unique_urls") or 0)
    merged = {
        "input_tokens": worker_in + critic_in,
        "output_tokens": worker_out + critic_out,
        "total_tokens": worker_in + worker_out + critic_in + critic_out,
        "api_calls": int(worker_usage.get("api_calls") or 0)
                     + int(critic_usage.get("api_calls") or 0),
        "research_and_finalization_api_calls": int(worker_usage.get("api_calls") or 0),
        "critic_api_calls": int(critic_usage.get("api_calls") or 0),
        "critic_input_tokens": critic_in,
        "critic_output_tokens": critic_out,
        "critic_total_tokens": critic_in + critic_out,
        "retrieval_finalization_calls": int(worker_usage.get("retrieval_finalization_calls") or 0),
        "executed_agent_retrieval_calls": executed_retrieval,
        "rejected_agent_retrieval_attempts": rejected,
        "citation_fetches": citation_fetches,
        "citation_unique_urls": citation_unique,
        "total_external_retrieval_calls": executed_retrieval + citation_fetches,
    }
    (RUNS / f"task{tid}_mission.usage.json").write_text(
        json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged
