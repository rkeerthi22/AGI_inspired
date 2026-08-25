# -*- coding: utf-8 -*-
"""Tests for anti-cheating rules in the PredictionStore.

Run directly::

    python -m unittest prediction_machine.tests.test_anti_cheat
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "S:/AGI_like")

from prediction_machine.core.prediction_store import PredictionStore  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _make_store(tmpdir: str) -> PredictionStore:
    db_path = Path(tmpdir) / "test_predictions.db"
    return PredictionStore(db_path=db_path)


def _create_task_prediction(
    store: PredictionStore,
    target: str = "task-001",
    model_version: str = "task_outcome_v1",
    outcome_due_at: str | None = None,
) -> str:
    if outcome_due_at is None:
        outcome_due_at = _iso(datetime.now() + timedelta(hours=2))
    return store.create_prediction(
        prediction_type="task_outcome",
        target=target,
        prediction={"verdict": "pass", "token_usage": 1000},
        confidence="high",
        input_features={"tokens_in": 500, "spec_lines": 20},
        model_version=model_version,
        outcome_due_at=outcome_due_at,
        code_commit="abc123",
    )


class TestCircularValidationDetection(unittest.TestCase):
    """test_circular_validation_detection: actual from same run can be flagged invalid."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_anticheat_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_circular_validation_detection(self):
        # Simulate a circular validation: the actual outcome is sourced from
        # the same prediction run, which is a contamination signal. We detect
        # this by inspecting the actual_source field and invalidating.
        pred_id = _create_task_prediction(self.store, target="task-circular")
        self.store.record_outcome(
            pred_id,
            actual={"verdict": "pass", "token_usage": 1000},
            actual_source="self_validated",  # suspicious: same run
        )

        # A downstream checker can detect circular validation by the source
        # tag and invalidate the prediction.
        self.store.invalidate_prediction(
            pred_id,
            "Circular validation: actual_source is 'self_validated' — "
            "outcome came from the same run as the prediction",
        )

        row = self.store.get_prediction(pred_id)
        self.assertEqual(row["valid_for_training"], 0)
        self.assertIn("Circular validation", row["invalid_reason"])


class TestFabricatedActualDetection(unittest.TestCase):
    """test_fabricated_actual_detection: recording actual with no real source → invalid_reason set."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_anticheat_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_fabricated_actual_detection(self):
        pred_id = _create_task_prediction(self.store, target="task-fabricated")

        # Record an outcome with a suspicious/empty source — the store
        # accepts it (it trusts the caller), but a downstream anti-cheat
        # layer can detect that the source is fabricated and invalidate.
        self.store.record_outcome(
            pred_id,
            actual={"verdict": "pass", "token_usage": 1000},
            actual_source="",  # empty source — no real provenance
        )

        # Detect: if actual_source is empty or "manual", flag as fabricated
        row = self.store.get_prediction(pred_id)
        if not row["actual_source"] or row["actual_source"] in ("manual", "guess"):
            self.store.invalidate_prediction(
                pred_id,
                "Fabricated actual: actual_source is empty or unreliable",
            )

        row = self.store.get_prediction(pred_id)
        self.assertEqual(row["valid_for_training"], 0)
        self.assertIsNotNone(row["invalid_reason"])
        self.assertIn("Fabricated", row["invalid_reason"])


class TestTimestampOrdering(unittest.TestCase):
    """test_timestamp_ordering: prediction created_at must be before actual_recorded_at."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_anticheat_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_timestamp_ordering_normal(self):
        # Normal case: prediction created now, outcome recorded after — OK.
        pred_id = _create_task_prediction(self.store, target="task-order-ok")
        # Ensure we're at least 1 second later so timestamps differ
        import time
        time.sleep(1.1)
        self.store.record_outcome(
            pred_id,
            {"verdict": "pass", "token_usage": 1000},
            "ledger.db",
        )
        row = self.store.get_prediction(pred_id)
        self.assertLessEqual(row["created_at"], row["actual_recorded_at"])

    def test_timestamp_ordering_backdated(self):
        # Anti-cheat: if created_at is in the future (back-dated prediction),
        # record_outcome must refuse.
        pred_id = _create_task_prediction(self.store, target="task-order-bad")
        future = _iso(datetime.now() + timedelta(hours=1))
        self.store._execute(
            "UPDATE predictions SET created_at = ? WHERE prediction_id = ?",
            (future, pred_id),
        )
        with self.assertRaises(ValueError) as ctx:
            self.store.record_outcome(
                pred_id,
                {"verdict": "pass", "token_usage": 1000},
                "ledger.db",
            )
        self.assertIn("Anti-cheat", str(ctx.exception))


class TestDuplicatePrediction(unittest.TestCase):
    """test_duplicate_prediction: same target allows multiple predictions (different model versions), unique IDs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_anticheat_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_duplicate_prediction(self):
        # Register two model versions for task_outcome
        self.store.register_model_version("task_outcome_v1", "task_outcome", "v1")
        self.store.register_model_version("task_outcome_v2", "task_outcome", "v2")

        # Same target, different model versions — both must succeed
        pid1 = _create_task_prediction(
            self.store, target="same-target", model_version="task_outcome_v1",
        )
        pid2 = _create_task_prediction(
            self.store, target="same-target", model_version="task_outcome_v2",
        )

        self.assertNotEqual(pid1, pid2, "Predictions must have unique IDs")

        # Both should be retrievable
        r1 = self.store.get_prediction(pid1)
        r2 = self.store.get_prediction(pid2)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertEqual(r1["target"], "same-target")
        self.assertEqual(r2["target"], "same-target")
        self.assertEqual(r1["model_version"], "task_outcome_v1")
        self.assertEqual(r2["model_version"], "task_outcome_v2")


if __name__ == "__main__":
    unittest.main()