"""orchestrator/scheduler.py -- scheduler state service (Move 5b).

Extracted from batch_runner.py as Move 5b of the W9 5-file split (see
REFACTOR_PLAN.md). This module owns task-lifecycle state in `ledger.db`:
queueing, expiration, crash recovery, token accounting, and workspace selection.

Dependency direction (per the W9 plan, section 3):
    runtime_context  →  integrity, execution, prompts
                    ↘
                  scheduler (this file; pure state)
                    ↑
                  workflow (5c')

Module category: SCHEDULER STATE SERVICE.
    Reads + writes SQLite state in `ledger.db`. No LLM calls, no
    evaluation. Allowed dependencies:
    - `runtime_context` (paths, log)
    - `ledger`
    - `policy`
    - `prompts` (`pass_criteria_for`)

It is described as a "scheduler state service" rather than "pure"
because it reads and writes SQLite state in `ledger.db`: queueing,
expiration, crash recovery, and resume dedup are durable operations
(F2, F6, F35, F43). The only "pure" surface is `accumulated_tokens()`,
which is an arithmetic helper over its inputs.

NOT in scope (the operator's pre-5b gate):
    - No model calls, no model routing, no failover.
    - No integrity enforcement (no fs/db guards).
    - No retry decisions or repeat-failure heuristics.
    - No CLI surface.
    - No evaluation imports -- the scheduler/evaluation cycle stays broken.

What does NOT live here:
    - `run_task` and `run_canaries`: stay in `batch_runner.py` until 5d
      (task_runner.py decomposition).
    - `extract_facts`, `run_critic`, `run_synthesis`: pure evaluation,
      goes to `evaluation.py` (5c).
    - `retry_failed_this_fire`, `_check_repeated_failure`: orchestration,
      goes to `workflow.py` (5c').
"""
from __future__ import annotations

import re
from datetime import datetime

import yaml

from runtime_context import MISSIONS, log

import ledger  # noqa: E402  -- orchestrator sibling; lazy-importable
from prompts import pass_criteria_for  # noqa: E402  -- only pass_criteria_for is needed


def parse_mission(mission_id: str) -> dict:
    path = MISSIONS / f"{mission_id}.md"
    text = path.read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---", 2)[1])
    seeds = []
    m = re.search(r"## Task seeds.*?\n(.*?)(?=\n## |\Z)", text, re.S)
    if m:
        seeds = [re.sub(r"^\d+\.\s*", "", ln).strip()
                 for ln in m.group(1).strip().splitlines()
                 if re.match(r"^\d+\.", ln.strip())]
    # merge numbered continuation lines (seeds wrap across lines in the files)
    return {"id": mission_id, "frontmatter": fm, "body": text, "seeds": seeds, "path": path}

def week_key() -> str:
    return datetime.now().strftime("%Y-W%V")

def queue_mission_tasks(mission: dict, dry: bool) -> list[int]:
    """Queue this week's tasks (dedup on mission+seed#+week). Returns task_ids to run,
    ordered so NEVER-ATTEMPTED seeds go before any seed already attempted this week.

    F6 (docs/HARDENING.md): this used to return ids in fixed seed order every call. On a
    retry fire, a seed that already parked (started_at IS SET -- it reached start_task()
    and hermes_worker() actually ran before hitting quota) was still first in line, hit
    the same quota/budget wall again, and the caller's `break`-on-quota_wait meant the
    seeds behind it were never even tried. Live evidence 2026-07-24: mission 001 seed 1
    (task 16) sat quota_wait since 2026-07-20 while seeds 2-4 (tasks 17-19) sat 'queued'
    with started_at=NULL -- structurally unable to ever run as long as seed 1 kept
    getting first crack at a scarce daily budget. Sorting never-attempted (started_at
    NULL) ahead of already-attempted gives every seed one try before any seed gets a
    second -- a fairness/rotation fix, not a scheduling rewrite. On a mission's first
    fire of the week all rows are equally untried, so ties break on task_id and the
    order is unchanged from before (seed 1,2,3,4)."""
    import sqlite3
    wk = week_key()
    rows = []  # (task_id, started_at) in seed-encounter order; sorted before returning
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        for i, seed in enumerate(mission["seeds"], 1):
            spec = f"[{wk}][seed {i}] {seed}"
            dup = c.execute("SELECT task_id, status, started_at FROM tasks WHERE "
                            "mission_id=? AND spec=?", (mission["id"], spec)).fetchone()
            if dup:
                if dup[1] in RESUMABLE_STATUSES:          # H3 + F43 (infra recovers)
                    rows.append((dup[0], dup[2]))     # resume it
                continue                               # done/failed this week → skip
            if dry:
                log(f"DRY: would queue: {spec[:100]}")
                continue
            tid = ledger.queue_task(mission["id"], spec,
                                    pass_criteria_for(mission))
            rows.append((tid, None))                  # brand new row, never started
    rows.sort(key=lambda r: (r[1] is not None, r[0]))
    return [tid for tid, _ in rows]

def is_first_run_for_mission(mission_id: str) -> bool:
    """True if this mission has never completed a task in an earlier week. A mission's
    week-1 run structurally cannot satisfy a 'changes since last week' criterion -- there
    is no prior week. Confirmed 2026-07-18: an unguided worker correctly self-identified
    this ('no prior brief to diff against, treat as baseline') while a guided one, told
    nothing, got marked FAIL for not producing a diff that cannot exist yet."""
    import sqlite3
    wk = week_key()
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        row = c.execute(
            "SELECT 1 FROM tasks WHERE mission_id=? AND status='done' AND spec NOT LIKE ? LIMIT 1",
            (mission_id, f"[{wk}]%")).fetchone()
    return row is None

def expire_stale_parked() -> None:
    """Previous-ISO-week rows that can never run again are marked 'stale', which
    weekly_fitness() counts as `dropped` — the honest record of scheduled work that
    did not happen.

    F35 (docs/HARDENING.md), 2026-07-29: this used to cover `quota_wait` only, and the
    omission of `queued` left NEVER-ATTEMPTED work permanently stranded. No code path
    could reach such a row: queue_mission_tasks() matches only specs carrying the CURRENT
    week, `--resume` selects only quota_wait/interrupted, reconcile_interrupted_tasks()
    touches only `running`, and this function skipped it. Five rows sat in exactly that
    state (tasks 4, 13, 14, 17, 19 -- W29/W30 seeds, four with started_at NULL and zero
    tokens), unrunnable forever.

    The honesty cost was the worse half. weekly_fitness() reports a `queued` row as
    `pending` only while it is inside the 7-day window; once it ages out it is counted
    nowhere, and `dropped` read 0 despite five abandoned seeds -- the same vanishing-work
    failure H5/F7 was written to close, recurring at a boundary that fix did not reach.

    Never-attempted rows get a distinct note and their own count in the log line:
    `started_at IS NULL` means the seed was starved before it ever reached a worker (the
    F6 signature), a different operational signal from work that ran and then parked.
    `interrupted` is deliberately left alone -- `--resume` can still reach it, so it is
    not stranded, and expiring it would break H3's crash-recovery path. Current-week rows
    are untouched; they are still legitimately waiting for this week's fire."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        wk = f"[{week_key()}]%"
        never = c.execute(
            "SELECT count(*) FROM tasks WHERE status IN ('quota_wait','queued') "
            "AND started_at IS NULL AND spec NOT LIKE ?", (wk,)).fetchone()[0]
        cur = c.execute(
            "UPDATE tasks SET status='stale', critic_notes=TRIM(COALESCE(critic_notes,'') || "
            "CASE WHEN started_at IS NULL "
            "     THEN ' | expired: NEVER ATTEMPTED, superseded by the new week' "
            "     ELSE ' | expired: superseded by new week' END) "
            "WHERE status IN ('quota_wait','queued') AND spec NOT LIKE ?", (wk,))
        if cur.rowcount:
            log(f"expired {cur.rowcount} task(s) from previous weeks "
                f"({never} never attempted) — they now count as dropped, not invisible")

def reconcile_interrupted_tasks() -> int:
    """H3 (docs/HARDENING.md, fixes F2): on every process start, before any new queueing,
    find 'running' rows whose lease has expired -- the owning process crashed, was killed,
    or the machine lost power. Previously these were orphaned FOREVER: no code path ever
    read or reset status='running', so the task was never retried, never counted (fitness
    counts only done/failed), and its seed was blocked for the rest of the week by dedup.

    Recovered rows go to 'interrupted' (dedup-resumable, see queue_mission_tasks) with an
    incremented attempt_count. Past MAX_TASK_ATTEMPTS, mark 'failed' instead of retrying
    forever -- an honest give-up beats a silent crash-loop. Always logged, never silent;
    surfaced on the scorecard so the operator sees it happened."""
    import sqlite3
    n_recovered = n_gave_up = 0
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        expired = c.execute(
            "SELECT task_id, attempt_count FROM tasks WHERE status='running' "
            "AND (lease_expires_at IS NULL OR lease_expires_at < datetime('now'))"
        ).fetchall()
        for row in expired:
            tid, attempts = row["task_id"], (row["attempt_count"] or 0) + 1
            if attempts >= ledger.MAX_TASK_ATTEMPTS:
                c.execute(
                    "UPDATE tasks SET status='failed', attempt_count=?, "
                    "critic_notes=COALESCE(critic_notes,'') || "
                    "' | gave up after ' || ? || ' interruptions (crash/power-loss recovery cap)' "
                    "WHERE task_id=?", (attempts, attempts, tid))
                n_gave_up += 1
            else:
                c.execute(
                    "UPDATE tasks SET status='interrupted', attempt_count=?, "
                    "critic_notes=COALESCE(critic_notes,'') || "
                    "' | recovered from an orphaned running state (attempt ' || ? || ')' "
                    "WHERE task_id=?", (attempts, attempts, tid))
                n_recovered += 1
    if n_recovered or n_gave_up:
        log(f"crash recovery: {n_recovered} task(s) recovered for retry, "
           f"{n_gave_up} gave up after {ledger.MAX_TASK_ATTEMPTS} interruptions")
    return n_recovered + n_gave_up

def accumulated_tokens(usage: dict, prior_in, prior_out) -> tuple[int, int]:
    """This attempt's consumption ADDED to whatever the row already carried.

    F32 (docs/HARDENING.md) established the rule for run_task(): `finish_task()` writes
    these columns via COALESCE, so omitting them preserves the prior value (F21) but
    PASSING one replaces it -- and a retry that succeeds passes real numbers, silently
    erasing the failed attempt's spend from `tokens_used_today()`.

    F48, 2026-07-30: promoted from an inline expression to a shared function because the
    canary path never had it at all, and the whole reason it never had it is that this
    logic lived in exactly one function's body. Two call sites computing the same thing
    from two copies of the same three lines is the failure shape of F33 (synthesis missed
    when the mission path was fixed) and F43 (two status tuples). One definition, both
    callers. `prior_*` may be None on a first attempt -- that is a no-op, not a special
    case."""
    return (int(usage.get("input_tokens") or 0) + int(prior_in or 0),
            int(usage.get("output_tokens") or 0) + int(prior_out or 0))

RESUMABLE_STATUSES = ("quota_wait", "queued", "interrupted", "infra_failed")

def mission_workspace(mission_id: str) -> str:
    return {"001-shopify-competitor-intel": "shopify",
            "002-content-niche-research": "content",
            "003-adforge-local-market": "adforge"}.get(mission_id, "onboarding")


