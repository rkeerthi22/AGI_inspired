# -*- coding: utf-8 -*-
"""Tests for PredictionStore (core/prediction_store.py).

Every test uses a **temporary** database created inside ``tempfile.mkdtemp()``
so the production database under ``prediction_machine/data/predictions.db``
is never touched.

Run directly::

    python -m unittest prediction_machine.tests.test_prediction_store
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta

# Ensure the repository root is on sys.path so `prediction_machine` is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prediction_machine.core.prediction_store import PredictionStore  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    """Format a datetime as ISO string truncated to seconds (matching store)."""
    return dt.isoformat(timespec="seconds")


def _make_store(tmpdir: str) -> PredictionStore:
    """Create a PredictionStore backed by a temp DB inside *tmpdir*."""
    db_path = Path(tmpdir) / "test_predictions.db"
    return PredictionStore(db_path=db_path)


def _create_task_prediction(
    store: PredictionStore,
    target: str = "task-001",
    model_version: str = "task_outcome_v1",
    outcome_due_at: str | None = None,
    confidence: str = "high",
) -> str:
    """Create a minimal task_outcome prediction and return its id."""
    if outcome_due_at is None:
        outcome_due_at = _iso(datetime.now() + timedelta(hours=2))
    return store.create_prediction(
        prediction_type="task_outcome",
        target=target,
        prediction={"verdict": "pass", "token_usage": 1000},
        confidence=confidence,
        input_features={"tokens_in": 500, "spec_lines": 20},
        model_version=model_version,
        outcome_due_at=outcome_due_at,
        code_commit="abc123",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreatePrediction(unittest.TestCase):
    """test_create_prediction: create a prediction, verify all fields stored."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_create_prediction(self):
        pred_id = self.store.create_prediction(
            prediction_type="task_outcome",
            target="task-100",
            prediction={"verdict": "pass", "score": 0.9},
            confidence="high",
            input_features={"tokens_in": 800, "spec_lines": 15},
            model_version="task_outcome_v1",
            outcome_due_at="2025-12-31T23:59:59",
            code_commit="deadbeef",
        )
        self.assertTrue(pred_id, "Expected a non-empty prediction_id")

        row = self.store.get_prediction(pred_id)
        self.assertIsNotNone(row, "Prediction not found after create")
        self.assertEqual(row["prediction_id"], pred_id)
        self.assertEqual(row["prediction_type"], "task_outcome")
        self.assertIsNotNone(row["created_at"])
        self.assertEqual(row["target"], "task-100")
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["model_version"], "task_outcome_v1")
        self.assertEqual(row["code_commit"], "deadbeef")
        self.assertEqual(row["outcome_due_at"], "2025-12-31T23:59:59")
        self.assertIsNone(row["actual"])
        self.assertIsNone(row["actual_recorded_at"])
        self.assertIsNone(row["actual_source"])
        self.assertIsNone(row["error"])
        self.assertEqual(row["valid_for_training"], 1)
        self.assertIsNone(row["invalid_reason"])

        # Verify JSON-serialised fields parse correctly
        pred = json.loads(row["prediction"])
        self.assertEqual(pred, {"verdict": "pass", "score": 0.9})
        feats = json.loads(row["input_features"])
        self.assertEqual(feats, {"tokens_in": 800, "spec_lines": 15})


class TestPredictionImmutability(unittest.TestCase):
    """test_prediction_immutability: immutable fields don't change via record_outcome."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_prediction_immutability(self):
        pred_id = _create_task_prediction(self.store, target="task-imm")
        original = self.store.get_prediction(pred_id)

        # Record an outcome — should not change prediction/confidence/model_version
        self.store.record_outcome(
            pred_id,
            actual={"verdict": "fail"},
            actual_source="ledger.db",
            error={"mae": 0.5},
        )
        after = self.store.get_prediction(pred_id)

        # Immutable fields must be identical
        self.assertEqual(after["prediction"], original["prediction"])
        self.assertEqual(after["confidence"], original["confidence"])
        self.assertEqual(after["model_version"], original["model_version"])
        self.assertEqual(after["prediction_id"], original["prediction_id"])
        self.assertEqual(after["prediction_type"], original["prediction_type"])
        self.assertEqual(after["created_at"], original["created_at"])
        self.assertEqual(after["target"], original["target"])
        self.assertEqual(after["outcome_due_at"], original["outcome_due_at"])
        self.assertEqual(after["code_commit"], original["code_commit"])
        self.assertEqual(after["input_features"], original["input_features"])

        # Outcome fields should now be set
        self.assertIsNotNone(after["actual"])
        self.assertIsNotNone(after["actual_recorded_at"])
        self.assertEqual(after["actual_source"], "ledger.db")
        self.assertIsNotNone(after["error"])


class TestRecordOutcome(unittest.TestCase):
    """test_record_outcome: create prediction, record outcome, verify fields set."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_record_outcome(self):
        pred_id = _create_task_prediction(self.store, target="task-rec")
        actual = {"verdict": "fail", "token_usage": 1200}
        error = {"mae": 200, "verdict_correct": False}

        result = self.store.record_outcome(pred_id, actual, "ledger.db", error=error)

        self.assertIsNotNone(result["actual"])
        self.assertIsNotNone(result["actual_recorded_at"])
        self.assertEqual(result["actual_source"], "ledger.db")
        self.assertIsNotNone(result["error"])

        # Parse stored actual JSON
        stored_actual = json.loads(result["actual"])
        self.assertEqual(stored_actual, actual)

        # Parse stored error JSON
        stored_error = json.loads(result["error"])
        self.assertEqual(stored_error, error)


class TestRecordOutcomeTwice(unittest.TestCase):
    """test_record_outcome_twice: second record_outcome must raise ValueError."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_record_outcome_twice(self):
        pred_id = _create_task_prediction(self.store, target="task-2x")
        self.store.record_outcome(pred_id, {"verdict": "pass"}, "ledger.db")

        with self.assertRaises(ValueError) as ctx:
            self.store.record_outcome(pred_id, {"verdict": "fail"}, "ledger.db")
        self.assertIn("already has an outcome", str(ctx.exception))


class TestInvalidatePrediction(unittest.TestCase):
    """test_invalidate_prediction: verify valid_for_training=0 and invalid_reason set."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_invalidate_prediction(self):
        pred_id = _create_task_prediction(self.store, target="task-inv")
        ok = self.store.invalidate_prediction(pred_id, "contaminated data")
        self.assertTrue(ok, "invalidate_prediction should return True for existing prediction")

        row = self.store.get_prediction(pred_id)
        self.assertEqual(row["valid_for_training"], 0)
        self.assertEqual(row["invalid_reason"], "contaminated data")

        # Non-existent prediction returns False
        ok2 = self.store.invalidate_prediction("nonexistent-uuid", "test")
        self.assertFalse(ok2)


class TestGetPendingOutcomes(unittest.TestCase):
    """test_get_pending_outcomes: only past-due predictions with no actual returned."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_get_pending_outcomes(self):
        # Prediction 1: past due, no outcome → pending
        pid1 = _create_task_prediction(
            self.store, target="task-past",
            outcome_due_at="2020-01-01T00:00:00",
        )
        # Prediction 2: future due, no outcome → NOT pending
        pid2 = _create_task_prediction(
            self.store, target="task-future",
            outcome_due_at=_iso(datetime.now() + timedelta(days=1)),
        )
        # Prediction 3: past due, but has outcome → NOT pending
        pid3 = _create_task_prediction(
            self.store, target="task-past-done",
            outcome_due_at="2020-01-01T00:00:00",
        )
        self.store.record_outcome(pid3, {"verdict": "pass"}, "ledger.db")

        pending = self.store.get_pending_outcomes()
        pending_ids = [p["prediction_id"] for p in pending]
        self.assertIn(pid1, pending_ids)
        self.assertNotIn(pid2, pending_ids)
        self.assertNotIn(pid3, pending_ids)

        # Filter by prediction_type
        pending_filtered = self.store.get_pending_outcomes(prediction_type="task_outcome")
        self.assertEqual(len(pending_filtered), 1)
        self.assertEqual(pending_filtered[0]["prediction_id"], pid1)


class TestGetMaturePredictions(unittest.TestCase):
    """test_get_mature_predictions: predictions with recorded outcomes returned."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_get_mature_predictions(self):
        # Prediction with outcome → mature
        pid1 = _create_task_prediction(self.store, target="task-mat-1")
        self.store.record_outcome(pid1, {"verdict": "pass"}, "ledger.db")

        # Prediction without outcome → NOT mature
        pid2 = _create_task_prediction(self.store, target="task-mat-2")

        mature_all = self.store.get_mature_predictions(valid_only=False)
        mature_ids = [m["prediction_id"] for m in mature_all]
        self.assertIn(pid1, mature_ids)
        self.assertNotIn(pid2, mature_ids)

        # Default valid_only=True — pid1 is still valid → included
        mature_valid = self.store.get_mature_predictions(valid_only=True)
        self.assertIn(pid1, [m["prediction_id"] for m in mature_valid])

        # Invalidate pid1 → valid_only=True excludes it
        self.store.invalidate_prediction(pid1, "bad data")
        mature_after = self.store.get_mature_predictions(valid_only=True)
        self.assertNotIn(pid1, [m["prediction_id"] for m in mature_after])

        # valid_only=False still includes it
        mature_all2 = self.store.get_mature_predictions(valid_only=False)
        self.assertIn(pid1, [m["prediction_id"] for m in mature_all2])


class TestAntiCheatTimestamp(unittest.TestCase):
    """test_anti_cheat_timestamp: cannot record outcome if created_at > now (back-dated)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_anti_cheat_timestamp(self):
        # We simulate a back-dated prediction by manually inserting a row
        # whose created_at is in the future, then calling record_outcome.
        pred_id = _create_task_prediction(self.store, target="task-cheat")

        # Overwrite created_at to be in the future so the anti-cheat check
        # (created_at > actual_recorded_at) triggers.
        future = _iso(datetime.now() + timedelta(hours=1))
        self.store._execute(
            "UPDATE predictions SET created_at = ? WHERE prediction_id = ?",
            (future, pred_id),
        )

        with self.assertRaises(ValueError) as ctx:
            self.store.record_outcome(pred_id, {"verdict": "pass"}, "ledger.db")
        self.assertIn("Anti-cheat", str(ctx.exception))


class TestModelVersionRegistration(unittest.TestCase):
    """test_model_version_registration: register, activate, get_active_version."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_model_version_registration(self):
        self.store.register_model_version(
            "task_outcome_v1", "task_outcome", "baseline",
        )
        self.store.activate_model_version("task_outcome_v1")
        active = self.store.get_active_version("task_outcome")
        self.assertEqual(active, "task_outcome_v1")

        # Register and activate a second version → first should be deactivated
        self.store.register_model_version(
            "task_outcome_v2", "task_outcome", "improved",
            parent_version="task_outcome_v1",
        )
        self.store.activate_model_version("task_outcome_v2")
        active2 = self.store.get_active_version("task_outcome")
        self.assertEqual(active2, "task_outcome_v2")

        # Duplicate registration raises ValueError
        with self.assertRaises(ValueError):
            self.store.register_model_version(
                "task_outcome_v1", "task_outcome", "dupe",
            )

        # Activate non-existent raises ValueError
        with self.assertRaises(ValueError):
            self.store.activate_model_version("nonexistent_version")


class TestExperimentLifecycle(unittest.TestCase):
    """test_experiment_lifecycle: create experiment, update with results, verify decision stored."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="predstore_test_")
        self.store = _make_store(self.tmpdir)

    def tearDown(self):
        self.store.close()

    def test_experiment_lifecycle(self):
        exp_id = self.store.create_experiment(
            prediction_type="task_outcome",
            observed_failure="Model overestimates pass rate",
            hypothesis="Training data biased toward success",
            proposed_change="Add failure examples",
            previous_metric=0.72,
        )
        self.assertTrue(exp_id, "Expected a non-empty experiment_id")

        # Verify initial state (PENDING)
        experiments = self.store.get_experiments()
        self.assertEqual(len(experiments), 1)
        self.assertEqual(experiments[0]["decision"], "PENDING")
        self.assertEqual(experiments[0]["previous_metric"], 0.72)
        self.assertIsNone(experiments[0]["new_metric"])
        self.assertIsNone(experiments[0]["sample_size"])

        # Update with results
        self.store.update_experiment(
            exp_id,
            new_metric=0.81,
            sample_size=150,
            decision="ACCEPT",
            backtest_details={"baseline_mae": 0.28, "new_mae": 0.19},
        )

        experiments = self.store.get_experiments()
        self.assertEqual(len(experiments), 1)
        exp = experiments[0]
        self.assertEqual(exp["decision"], "ACCEPT")
        self.assertEqual(exp["new_metric"], 0.81)
        self.assertEqual(exp["sample_size"], 150)
        bt = json.loads(exp["backtest_details"])
        self.assertEqual(bt, {"baseline_mae": 0.28, "new_mae": 0.19})

        # Filter by decision
        accepted = self.store.get_experiments(decision="ACCEPT")
        self.assertEqual(len(accepted), 1)
        rejected = self.store.get_experiments(decision="REJECT")
        self.assertEqual(len(rejected), 0)

        # Non-existent experiment raises ValueError
        with self.assertRaises(ValueError):
            self.store.update_experiment(
                "nonexistent-uuid",
                new_metric=0.5,
                sample_size=10,
                decision="REJECT",
            )

        # Invalid decision raises ValueError
        with self.assertRaises(ValueError):
            self.store.update_experiment(
                exp_id,
                new_metric=0.5,
                sample_size=10,
                decision="MAYBE",
            )


if __name__ == "__main__":
    unittest.main()
