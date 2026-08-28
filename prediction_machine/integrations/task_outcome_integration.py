"""Task-outcome integration — wires batch_runner into the prediction machine.

This module provides the integration point between the orchestrator's
``batch_runner`` and the prediction machine.  It creates a prediction
*before* a task runs and records the real outcome *after* the task
reaches a terminal status.

Stdlib-only.  Python 3.11.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import traceback
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Path setup — so this file works whether imported as a package or run directly
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PM_DIR = os.path.dirname(_THIS_DIR)          # prediction_machine/
_REPO_DIR = os.path.dirname(_PM_DIR)          # repository root

if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from prediction_machine.timebase import utc_after, utc_iso

# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_store():
    from prediction_machine.core.prediction_store import PredictionStore
    return PredictionStore


def _import_predictor():
    from prediction_machine.predictors.task_outcome.predictor import TaskOutcomePredictor
    return TaskOutcomePredictor


def _import_compute_error():
    from prediction_machine.evaluation.evaluator import compute_error
    return compute_error


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LEDGER_DB = os.path.join(_REPO_DIR, "ledger", "ledger.db")

_TERMINAL_STATUSES = {"done", "failed", "infra_failed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_head(repo_dir: str) -> Optional[str]:
    """Return the current git HEAD commit hash, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _now_iso() -> str:
    return utc_iso()


def _now_plus_hours(hours: float) -> str:
    return utc_after(hours)


# ---------------------------------------------------------------------------
# Integration class
# ---------------------------------------------------------------------------

class TaskOutcomeIntegration:
    """Integration point between batch_runner and the prediction machine.

    Methods are designed to be fault-tolerant — any prediction-machine
    failure is caught and logged so the orchestrator continues normally.
    """

    def __init__(self) -> None:
        self._predictor = None

    # -- lazy predictor ------------------------------------------------------

    def _get_predictor(self):
        if self._predictor is None:
            Predictor = _import_predictor()
            self._predictor = Predictor()
        return self._predictor

    # -- public API ----------------------------------------------------------

    def predict_for_task(
        self,
        task_id: int,
        spec: str,
        mission_id: str,
        store,
    ) -> Optional[str]:
        """Create a prediction for a task *before* it runs.

        Parameters
        ----------
        task_id : int
            The ledger task_id.
        spec : str
            Task spec text (forwarded to the predictor).
        mission_id : str
            Mission identifier (forwarded to the predictor).
        store : PredictionStore
            The store in which to persist the prediction.

        Returns
        -------
        str or None
            The prediction_id on success, or None if the prediction failed.
        """
        try:
            predictor = self._get_predictor()
            prediction_payload = predictor.predict(spec, mission_id)

            if not isinstance(prediction_payload, dict):
                sys.stderr.write(
                    f"TaskOutcomeIntegration: predictor returned non-dict for "
                    f"task {task_id}\n"
                )
                return None

            confidence = prediction_payload.get("confidence", "low")
            model_version = prediction_payload.get("model_version", "task_outcome_v1")
            outcome_due_at = _now_plus_hours(2.0)
            code_commit = _git_head(_REPO_DIR)

            input_features = {
                "task_id": task_id,
                "spec": spec,
                "mission_id": mission_id,
            }

            prediction_id = store.create_prediction(
                prediction_type="task_outcome",
                target=str(task_id),
                prediction=prediction_payload,
                confidence=confidence,
                input_features=input_features,
                model_version=model_version,
                outcome_due_at=outcome_due_at,
                code_commit=code_commit,
            )
            return prediction_id

        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(
                f"TaskOutcomeIntegration.predict_for_task: "
                f"failed for task {task_id}:\n{tb}\n"
            )
            return None

    def record_task_outcome(
        self,
        task_id: int,
        store,
    ) -> Optional[dict]:
        """Record the actual outcome for a task after it completes.

        Finds the prediction for *task_id*, fetches the real outcome from
        ``ledger.db``, computes the error, and persists it.

        Parameters
        ----------
        task_id : int
            The ledger task_id whose outcome should be recorded.
        store : PredictionStore
            The prediction store.

        Returns
        -------
        dict or None
            The error dict on success, or None if no prediction was found
            or recording failed.
        """
        try:
            # Find the prediction for this task_id
            all_preds = store.get_all_predictions(
                prediction_type="task_outcome",
                valid_only=None,
            )
            matching = [
                p for p in all_preds
                if str(p.get("target", "")) == str(task_id)
                and p.get("actual") is None
            ]
            if not matching:
                return None

            pred = matching[0]
            pred_id = pred.get("prediction_id") or pred.get("id")

            # Fetch the real task row from ledger.db
            if not os.path.isfile(_LEDGER_DB):
                sys.stderr.write(
                    f"TaskOutcomeIntegration.record_task_outcome: "
                    f"ledger.db not found at {_LEDGER_DB}\n"
                )
                return None

            with sqlite3.connect(_LEDGER_DB) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()

            if row is None:
                sys.stderr.write(
                    f"TaskOutcomeIntegration.record_task_outcome: "
                    f"task {task_id} not found in ledger.db\n"
                )
                return None

            status = (row["status"] or "").strip().lower()
            if status not in _TERMINAL_STATUSES:
                sys.stderr.write(
                    f"TaskOutcomeIntegration.record_task_outcome: "
                    f"task {task_id} has non-terminal status '{status}'\n"
                )
                return None

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

            # Compute error
            error = None
            try:
                compute_error = _import_compute_error()
                pred_payload = _parse_json_field(pred.get("prediction"))
                error = compute_error("task_outcome", pred_payload, actual)
            except Exception as exc:
                sys.stderr.write(
                    f"TaskOutcomeIntegration.record_task_outcome: "
                    f"compute_error failed for task {task_id}: {exc}\n"
                )

            store.record_outcome(
                prediction_id=pred_id,
                actual=actual,
                actual_source="ledger.db:tasks",
                error=error,
            )
            return error

        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(
                f"TaskOutcomeIntegration.record_task_outcome: "
                f"failed for task {task_id}:\n{tb}\n"
            )
            return None


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def _parse_json_field(raw: Any) -> dict:
    """Parse a JSON string to dict; pass through if already dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def get_predicted_task_ids(store) -> set[str]:
    """Return the set of task_ids (as strings) that already have predictions.

    This allows the batch_runner to avoid creating duplicate predictions.
    """
    try:
        all_preds = store.get_all_predictions(
            prediction_type="task_outcome",
            valid_only=None,
        )
        return {str(p.get("target", "")) for p in all_preds if p.get("target")}
    except Exception:
        return set()
