"""
TaskOutcomeCollector — collects real task outcomes from ledger.db.

For every pending "task_outcome" prediction, looks up the task row in
the configured repository ledger. If the task has reached a terminal state
(done / failed / infra_failed) with a non-null finished_at, the collector
records the *real* outcome. If the task is still running it is skipped.
If the task went stale it is invalidated. An anti-cheat guard ensures the
prediction's created_at precedes the task's finished_at.

Stdlib-only. Python 3.11.
"""

from __future__ import annotations

import datetime
import sqlite3
import os
import sys
import traceback
from typing import Any

from prediction_machine.paths import LEDGER_DB

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_LEDGER_DB = str(LEDGER_DB)

_TERMINAL_STATUSES = {"done", "failed", "infra_failed"}
_RUNNING_STATUSES = {"queued", "running", "quota_wait", "blocked"}
_STALE_STATUS = "stale"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(raw: Any) -> datetime.datetime | None:
    """Parse a timestamp that may be ISO-8601, epoch seconds, or epoch float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.datetime.fromtimestamp(float(raw), tz=datetime.timezone.utc)
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        # Try ISO-8601 first (with or without timezone)
        try:
            # fromisoformat handles '2025-01-01T12:00:00' and '...+00:00' in 3.11+
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Try epoch
        try:
            return datetime.datetime.fromtimestamp(float(raw), tz=datetime.timezone.utc)
        except (ValueError, OSError):
            return None
    return None


def _import_compute_error() -> Any:
    """Lazily import compute_error from the evaluator module."""
    eval_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "evaluation", "evaluator.py",
    )
    if os.path.isfile(eval_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("prediction_evaluator", eval_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "compute_error", None)
    return None


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class TaskOutcomeCollector:
    """Collects real task outcomes from ledger.db for task_outcome predictions."""

    def __init__(self, ledger_db: str | None = None) -> None:
        self.ledger_db = ledger_db or _LEDGER_DB
        self._compute_error = _import_compute_error()

    # -- DB helpers --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not os.path.isfile(self.ledger_db):
            raise FileNotFoundError(f"Ledger DB not found: {self.ledger_db}")
        conn = sqlite3.connect(self.ledger_db)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_task(self, task_id: int) -> sqlite3.Row | None:
        """Return the task row for *task_id*, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return row

    # -- Main entry point ---------------------------------------------------

    def collect_pending(self, store) -> dict[str, Any]:
        """
        Collect real outcomes for all pending ``task_outcome`` predictions.

        Returns a summary dict:
            {"checked": int, "recorded": int, "skipped": int,
             "invalidated": int, "errors": list[str]}
        """
        summary: dict[str, Any] = {
            "checked": 0,
            "recorded": 0,
            "skipped": 0,
            "invalidated": 0,
            "errors": [],
        }

        try:
            pending = store.get_pending_outcomes(prediction_type="task_outcome")
        except Exception as exc:
            summary["errors"].append(f"get_pending_outcomes failed: {exc}")
            return summary

        for pred in pending:
            summary["checked"] += 1
            pred_id = pred.get("id") or pred.get("prediction_id")
            target = pred.get("target")

            try:
                # --- Parse task_id from target --------------------------------
                if target is None:
                    summary["errors"].append(
                        f"prediction {pred_id}: missing target (task_id)"
                    )
                    continue
                try:
                    task_id = int(str(target).strip())
                except (ValueError, TypeError):
                    summary["errors"].append(
                        f"prediction {pred_id}: target '{target}' is not a valid task_id"
                    )
                    continue

                # --- Fetch the real task row ---------------------------------
                row = self._fetch_task(task_id)
                if row is None:
                    summary["errors"].append(
                        f"prediction {pred_id}: task_id {task_id} not found in ledger.db"
                    )
                    continue

                status = (row["status"] or "").strip().lower()
                finished_at_raw = row["finished_at"]
                created_at_raw = pred.get("created_at")

                # --- Still running? skip -------------------------------------
                if status in _RUNNING_STATUSES:
                    summary["skipped"] += 1
                    continue

                # --- Stale? invalidate (not a real outcome) ------------------
                if status == _STALE_STATUS:
                    try:
                        store.invalidate_prediction(
                            pred_id,
                            reason=f"task {task_id} went stale (never completed)",
                        )
                        summary["invalidated"] += 1
                    except Exception as exc:
                        summary["errors"].append(
                            f"prediction {pred_id}: invalidate failed: {exc}"
                        )
                    continue

                # --- Terminal but missing finished_at? skip -----------------
                if status in _TERMINAL_STATUSES and finished_at_raw is None:
                    summary["skipped"] += 1
                    continue

                # --- Not terminal and not running/stale? unknown — skip -----
                if status not in _TERMINAL_STATUSES:
                    summary["skipped"] += 1
                    continue

                # --- Anti-cheat: prediction must precede outcome -------------
                pred_ts = _parse_ts(created_at_raw)
                fin_ts = _parse_ts(finished_at_raw)
                if pred_ts is not None and fin_ts is not None and pred_ts >= fin_ts:
                    summary["errors"].append(
                        f"prediction {pred_id}: anti-cheat violation — "
                        f"created_at ({pred_ts}) is not before finished_at ({fin_ts})"
                    )
                    continue

                # --- Record the REAL outcome ---------------------------------
                critic_verdict = row["critic_verdict"]
                tokens_in = row["tokens_in"] or 0
                tokens_out = row["tokens_out"] or 0
                model_used = row["model_used"]

                actual: dict[str, Any] = {
                    "verdict": critic_verdict if critic_verdict else "unknown",
                    "tokens": int(tokens_in) + int(tokens_out),
                    "status": status,
                    "model_used": model_used if model_used else "unknown",
                }
                actual_source = "ledger.db:tasks"

                # --- Compute error via evaluator (optional) -----------------
                error = None
                if self._compute_error is not None:
                    try:
                        error = self._compute_error(
                            "task_outcome", dict(pred), actual
                        )
                    except Exception as exc:
                        summary["errors"].append(
                            f"prediction {pred_id}: compute_error failed: {exc}"
                        )

                # --- Persist ------------------------------------------------
                store.record_outcome(pred_id, actual, actual_source, error)
                summary["recorded"] += 1

            except Exception:
                tb = traceback.format_exc()
                summary["errors"].append(
                    f"prediction {pred_id}: unexpected error:\n{tb}"
                )

        return summary
