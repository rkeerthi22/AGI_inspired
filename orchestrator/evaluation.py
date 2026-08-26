"""orchestrator/evaluation.py -- grading, memory-update, and synthesis stages.

Extracted from batch_runner.py as Move 4 (Leaf Extraction) of the W9 5-file
split (see REFACTOR_PLAN.md).

Scope of this module:
  - seed_is_synthesis  (F30: classify a spec as synthesis vs per-subject)
  - retract_facts      (HARNESS_DESIGN §1.2: invalidate facts on task retraction)

These two are the LEAF functions of what will eventually become the full
evaluation layer. They have zero internal cross-module calls -- they only
use stdlib (re, sqlite3) and a single module-level constant (ROOT) -- so
they can be cleanly extracted without dragging in 25+ cross-module
dependencies.

What does NOT live here (yet):
  - run_critic / run_synthesis / run_canaries / extract_facts: each calls
    10+ functions across execution / integrity / prompts / ledger / policy
    AND ~10 batch_runner.py helpers (week_key, accumulated_tokens,
    queue_mission_tasks, run_task, etc.) that are themselves Move 5
    territory. Attempted in pre-Move4 audit 2026-08-26; full extraction
    would require ~200 lines of intentional helper duplication (rejected:
    adds technical debt, defeats the refactor's purpose) OR a re-think of
    the dependency chain (better handled when Move 5 lands and scheduler
    can move together with evaluation).
  These four stay in batch_runner.py for now and will roll into Move 5
  (Scheduler/Glue) as a single combined extraction when scheduler.py is
  built.

Dependency direction (per the W9 plan, section 1):
    integrity.py -> execution.py -> prompts.py -> evaluation.py -> scheduler.py

This module depends on:
  - stdlib only (re, sqlite3, datetime)
  - ROOT (pathing) -- this module's own definition, mirroring batch_runner.py

No sibling-module imports. No internal calls between the two functions.
The shim pattern from Moves 1, 2, and 3 applies cleanly.
"""


import re
import sqlite3
from pathlib import Path

# Mirrors batch_runner.ROOT: the repo root, computed from this file's location.
ROOT = Path(__file__).resolve().parent.parent


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
    import sqlite3
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        cur = c.execute(
            "UPDATE facts SET valid_until=datetime('now'), status='retracted' "
            "WHERE source_task_id=? AND valid_until IS NULL",
            (task_id,))
        return cur.rowcount