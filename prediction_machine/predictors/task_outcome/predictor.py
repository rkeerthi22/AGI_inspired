"""Task outcome predictor — predicts whether a harness task will pass/fail
critic review and token cost.

Model version: task_outcome_v1

Training data: S:/AGI_like/ledger/ledger.db (tasks table).
Uses historical tasks with status IN ('done', 'failed') and mission_id != 'canaries'.
NEVER uses tasks with status='stale' or 'infra_failed' — stale means the task
was never completed (not a quality signal), and infra_failed means the failure
was an infrastructure issue, not a task quality problem.

Stdlib only — no numpy, no sklearn, no frameworks.
"""

import re
import sqlite3
from pathlib import Path
from statistics import median

# ── paths ─────────────────────────────────────────────────────────────────────

LEDGER_DB = Path("S:/AGI_like/ledger/ledger.db")

MODEL_VERSION = "task_outcome_v1"
MODEL_NAME = "historical_median_by_mission"

# ── helpers ───────────────────────────────────────────────────────────────────


def _ledger_conn():
    """Open a read-only connection to the ledger database."""
    conn = sqlite3.connect(str(LEDGER_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _task_features(spec: str, mission_id: str) -> dict:
    """Extract structured features from a task spec string.

    Parses the seed number, week tag, and synthesis flag from the spec text,
    plus the spec length as a complexity proxy.
    """
    seed_match = re.search(r"seed (\d+)", spec)
    seed_num = int(seed_match.group(1)) if seed_match else 0
    week_match = re.search(r"\[(\d{4}-W\d{2})\]", spec)
    week = week_match.group(1) if week_match else ""
    is_synthesis = bool(re.search(r"synthesi[sz]", spec, re.I))
    return {
        "mission_id": mission_id,
        "seed_num": seed_num,
        "week": week,
        "is_synthesis": is_synthesis,
        "spec_len": len(spec),
    }


def _load_training_tasks(mission_id: str | None = None) -> list[dict]:
    """Load terminal-quality tasks from the ledger.

    Only status IN ('done', 'failed') and mission_id != 'canaries'.
    Excludes 'stale' (never completed) and 'infra_failed' (infrastructure issue).
    If mission_id is given, filters to that mission first; falls back to all
    if <2 examples are found.
    """
    with _ledger_conn() as c:
        if mission_id:
            rows = c.execute(
                "SELECT * FROM tasks "
                "WHERE status IN ('done','failed') "
                "AND mission_id != 'canaries' "
                "AND mission_id = ? "
                "ORDER BY task_id",
                (mission_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM tasks "
                "WHERE status IN ('done','failed') "
                "AND mission_id != 'canaries' "
                "ORDER BY task_id"
            ).fetchall()
    return [dict(r) for r in rows]


# ── predictor class ────────────────────────────────────────────────────────────


class TaskOutcomePredictor:
    """Predict task outcome (pass/fail + token cost) from historical ledger data.

    This is deliberately simple — category median / pass-rate by mission, not
    regression.  The point is the closed loop (predict → measure → learn), not
    model sophistication.  A simple model with honest error measurement beats a
    complex model that nobody checks.
    """

    def __init__(self):
        self.model = MODEL_NAME
        self.model_version = MODEL_VERSION

    # -- public API ----------------------------------------------------------

    @staticmethod
    def get_model_version() -> str:
        """Return the model version string."""
        return MODEL_VERSION

    @staticmethod
    def get_description() -> str:
        """Return a human-readable description of the model."""
        return (
            "Task outcome predictor (task_outcome_v1): predicts whether a "
            "harness task will pass or fail critic review, and the token cost. "
            "Uses historical pass-rate and median token cost from the ledger, "
            "filtered by mission_id with fallback to all tasks. "
            "Confidence scales with the number of similar training examples."
        )

    def predict(self, spec: str, mission_id: str) -> dict:
        """Predict task outcome.

        Args:
            spec:        Task spec text (used for feature extraction only).
            mission_id:  Mission identifier for filtering historical data.

        Returns:
            dict with predicted_verdict, pass_probability, predicted_tokens,
            token_range, confidence, n_training_examples, model, model_version,
            and features.
        """
        features = _task_features(spec, mission_id)

        # Load same-mission tasks first; fall back to all if too few
        tasks = _load_training_tasks(mission_id)
        if len(tasks) < 2:
            tasks = _load_training_tasks(None)

        # Further filter to similar tasks (same mission OR same synthesis flag)
        similar = [
            t
            for t in tasks
            if t.get("mission_id") == mission_id
            or features["is_synthesis"]
            == bool(re.search(r"synthesi[sz]", t.get("spec", ""), re.I))
        ]
        if not similar:
            similar = tasks

        # Pass/fail prediction
        passed = [
            t for t in similar
            if t["status"] == "done" and t.get("critic_verdict") == "pass"
        ]
        failed = [
            t for t in similar
            if t["status"] == "failed" and t.get("critic_verdict") == "fail"
        ]
        n_total = len(passed) + len(failed)
        pass_probability = len(passed) / n_total if n_total > 0 else 0.5

        # Token cost prediction (median of tokens_in + tokens_out)
        token_costs = [
            (t["tokens_in"] or 0) + (t["tokens_out"] or 0)
            for t in similar
            if (t["tokens_in"] or 0) + (t["tokens_out"] or 0) > 0
        ]
        if token_costs:
            predicted_tokens = int(median(token_costs))
            token_min = min(token_costs)
            token_max = max(token_costs)
        else:
            predicted_tokens = 0
            token_min = 0
            token_max = 0

        # Confidence from sample size
        n_similar = len(similar)
        if n_similar >= 5:
            confidence = "high"
        elif n_similar >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "predicted_verdict": "pass" if pass_probability >= 0.5 else "fail",
            "pass_probability": round(pass_probability, 3),
            "predicted_tokens": predicted_tokens,
            "token_range": [token_min, token_max],
            "confidence": confidence,
            "n_training_examples": n_similar,
            "model": self.model,
            "model_version": self.model_version,
            "features": features,
        }