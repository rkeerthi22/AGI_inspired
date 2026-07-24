"""Minimal ledger access — append tasks, record verdicts, compute fitness.
Stdlib only. The orchestrator and any hand-run go through here so the ledger stays
the single source of truth (HARNESS_DESIGN.md §3)."""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DB = ROOT / "ledger" / "ledger.db"

# Fitness weights — FIXED for 8 weeks, do not tune mid-window (§3.2)
W = {"completion": 0.35, "accuracy": 0.30, "intervention": 0.25, "cost": 0.10}
COST_TARGET = 0.50


def _conn(db=None):
    # F12 (docs/HARDENING.md): db=LEDGER_DB as a default arg binds the path at
    # IMPORT time, so a test/probe that reassigns ledger.LEDGER_DB to redirect
    # at a copy is silently ignored and writes land in the real DB — this is
    # exactly what happened during the 2026-07-19 audit. Resolve at CALL time.
    c = sqlite3.connect(db if db is not None else LEDGER_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def queue_task(mission_id: str, spec: str, pass_criteria: str) -> int:
    """Create a task with pre-written pass criteria. Returns task_id."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tasks (mission_id, spec, pass_criteria, status) "
            "VALUES (?,?,?,'queued')",
            (mission_id, spec, pass_criteria),
        )
        return cur.lastrowid


def start_task(task_id: int, model_used: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status='running', started_at=?, model_used=? WHERE task_id=?",
            (datetime.now().isoformat(timespec="seconds"), model_used, task_id),
        )


def finish_task(task_id: int, *, artifacts, cost_usd=0.0, tokens_in=0, tokens_out=0,
                critic_verdict=None, critic_notes=None, status="done",
                interventions=0, intervention_types=None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, finished_at=?, artifacts=?, cost_usd=?, "
            "tokens_in=?, tokens_out=?, critic_verdict=?, critic_notes=?, "
            "interventions=?, intervention_types=? WHERE task_id=?",
            (status, datetime.now().isoformat(timespec="seconds"),
             json.dumps(artifacts), cost_usd, tokens_in, tokens_out,
             critic_verdict, critic_notes, interventions,
             json.dumps(intervention_types or []), task_id),
        )


def add_lesson(task_id: int, lesson: str, kind: str = "worked") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO lesson_candidates (task_id, lesson, kind) VALUES (?,?,?)",
            (task_id, lesson, kind),
        )


def weekly_fitness(week_start: str | None = None) -> dict:
    """Compute F over tasks in the 7 days from week_start (default: last 7 days)."""
    start = (datetime.fromisoformat(week_start) if week_start
             else datetime.now() - timedelta(days=7))
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE created_at >= ? AND status IN "
            "('done','failed') AND mission_id != 'canaries'",  # canaries tracked separately
            (start.isoformat(timespec="seconds"),)
        ).fetchall()
    n = len(rows)
    if n == 0:
        return {"tasks_attempted": 0, "fitness": None, "note": "no tasks in window"}
    completed = sum(1 for r in rows if r["status"] == "done")
    spot = [r for r in rows if r["human_verdict"] in ("pass", "fail")]
    accuracy = (sum(1 for r in spot if r["human_verdict"] == "pass") / len(spot)
                if spot else None)
    interventions = sum(r["interventions"] for r in rows)
    avg_cost = sum(r["cost_usd"] for r in rows) / n
    completion_rate = completed / n
    intervention_norm = min(1.0, interventions / n)
    cost_eff = min(1.0, COST_TARGET / avg_cost) if avg_cost > 0 else 1.0
    acc = accuracy if accuracy is not None else 0.0
    fitness = (W["completion"] * completion_rate + W["accuracy"] * acc +
               W["intervention"] * (1 - intervention_norm) + W["cost"] * cost_eff)
    return {
        "tasks_attempted": n, "completion_rate": round(completion_rate, 3),
        "accuracy": round(accuracy, 3) if accuracy is not None else None,
        "intervention_rate": round(intervention_norm, 3),
        "avg_cost_usd": round(avg_cost, 4), "fitness": round(fitness, 3),
        "spot_checked": len(spot),
    }


if __name__ == "__main__":
    # Smoke test the ledger without any model: queue -> start -> finish -> fitness.
    tid = queue_task("000-onboarding", "SMOKE: verify ledger write path",
                     "row exists with verdict")
    start_task(tid, "none/smoke")
    finish_task(tid, artifacts=["workspace/onboarding/_smoke.txt"], cost_usd=0.0,
                critic_verdict="pass", critic_notes="ledger write-path OK",
                status="done")
    add_lesson(tid, "ledger smoke test passes end to end", "worked")
    print(f"queued+finished task_id={tid}")
    print("fitness:", json.dumps(weekly_fitness(), indent=2))
