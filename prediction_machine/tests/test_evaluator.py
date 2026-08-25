# -*- coding: utf-8 -*-
"""Tests for the evaluation engine (evaluation/evaluator.py).

Run directly::

    python -m unittest prediction_machine.tests.test_evaluator
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, "S:/AGI_like")

from prediction_machine.evaluation.evaluator import (  # noqa: E402
    compute_error,
    PredictionEvaluator,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _make_task_row(
    pred_verdict: str = "pass",
    actual_verdict: str = "pass",
    pred_tokens: float = 1000,
    actual_tokens: float = 1000,
    confidence: str = "high",
    model_version: str = "task_outcome_v1",
    valid: bool = True,
):
    """Build a mature prediction row dict as the evaluator expects."""
    prediction = {"verdict": pred_verdict, "token_usage": pred_tokens}
    actual = {"verdict": actual_verdict, "token_usage": actual_tokens}
    error = compute_error("task_outcome", prediction, actual)
    return {
        "prediction_type": "task_outcome",
        "prediction": json.dumps(prediction),
        "actual": json.dumps(actual),
        "error": json.dumps(error),
        "confidence": confidence,
        "model_version": model_version,
        "valid_for_training": 1 if valid else 0,
    }


# ---------------------------------------------------------------------------
# compute_error tests
# ---------------------------------------------------------------------------

class TestComputeErrorTaskOutcome(unittest.TestCase):
    """test_compute_error_task_outcome: verify error computation for task_outcome."""

    def test_compute_error_task_outcome(self):
        pred = {"verdict": "pass", "token_usage": 1000}
        actual = {"verdict": "fail", "token_usage": 1200}
        err = compute_error("task_outcome", pred, actual)

        self.assertEqual(err["prediction_type"], "task_outcome")
        self.assertFalse(err["verdict_correct"])
        self.assertTrue(err["directional_correct"] is False)
        self.assertEqual(err["predicted_verdict"], "pass")
        self.assertEqual(err["actual_verdict"], "fail")
        self.assertEqual(err["predicted_tokens"], 1000.0)
        self.assertEqual(err["actual_tokens"], 1200.0)
        # |1000-1200| / max(1200,1) * 100 = 16.6667
        self.assertAlmostEqual(err["token_error_pct"], 16.6667, places=3)

    def test_compute_error_task_outcome_correct(self):
        pred = {"verdict": "pass", "token_usage": 500}
        actual = {"verdict": "pass", "token_usage": 500}
        err = compute_error("task_outcome", pred, actual)
        self.assertTrue(err["verdict_correct"])
        self.assertAlmostEqual(err["token_error_pct"], 0.0, places=4)


class TestComputeErrorVideoEngagement(unittest.TestCase):
    """test_compute_error_video_engagement: verify error computation for video."""

    def test_compute_error_video_engagement(self):
        pred = {
            "views_24h": 1000,
            "views_3d": 5000,
            "views_7d": 10000,
            "category_median": 8000,
        }
        actual = {
            "views_24h": 1200,
            "views_3d": 4800,
            "views_7d": 9000,
            "category_median": 8000,
        }
        err = compute_error("video_engagement", pred, actual)

        self.assertEqual(err["prediction_type"], "video_engagement")
        self.assertEqual(err["predicted_views_7d"], 10000.0)
        self.assertEqual(err["actual_views_7d"], 9000.0)
        self.assertEqual(err["predicted_views_24h"], 1000.0)
        self.assertEqual(err["actual_views_24h"], 1200.0)
        # Directional: pred 10000 > median 8000, actual 9000 > median 8000 → both above → correct
        self.assertTrue(err["directional_correct"])
        # 24h error: |1000-1200| / max(1200,1) * 100 = 16.6667
        self.assertAlmostEqual(err["view_error_pct_24h"], 16.6667, places=3)
        # 7d error: |10000-9000| / max(9000,1) * 100 = 11.1111
        self.assertAlmostEqual(err["view_error_pct_7d"], 11.1111, places=3)


class TestComputeErrorSkillSafety(unittest.TestCase):
    """test_compute_error_skill_safety: verify error computation for skill."""

    def test_compute_error_skill_safety(self):
        # Correctly predict high risk
        pred = {"high_risk": True}
        actual = {"regressed": True}
        err = compute_error("skill_safety", pred, actual)
        self.assertEqual(err["prediction_type"], "skill_safety")
        self.assertTrue(err["risk_correct"])
        self.assertFalse(err["false_positive"])
        self.assertFalse(err["false_negative"])
        self.assertTrue(err["predicted_high_risk"])
        self.assertTrue(err["actual_regressed"])

    def test_compute_error_skill_safety_false_positive(self):
        pred = {"high_risk": True}
        actual = {"regressed": False}
        err = compute_error("skill_safety", pred, actual)
        self.assertFalse(err["risk_correct"])
        self.assertTrue(err["false_positive"])
        self.assertFalse(err["false_negative"])

    def test_compute_error_skill_safety_false_negative(self):
        pred = {"high_risk": False}
        actual = {"regressed": True}
        err = compute_error("skill_safety", pred, actual)
        self.assertFalse(err["risk_correct"])
        self.assertFalse(err["false_positive"])
        self.assertTrue(err["false_negative"])


class TestComputeErrorMiksCampaign(unittest.TestCase):
    """test_compute_error_miks_campaign: verify error computation for miks."""

    def test_compute_error_miks_campaign(self):
        pred = {"total_views": 10000, "engagement": 500, "revenue": 200}
        actual = {"total_views": 8000, "engagement": 600, "revenue": 180}
        err = compute_error("miks_campaign", pred, actual)

        self.assertEqual(err["prediction_type"], "miks_campaign")
        self.assertEqual(err["predicted_total_views"], 10000.0)
        self.assertEqual(err["actual_total_views"], 8000.0)
        self.assertEqual(err["predicted_engagement"], 500.0)
        self.assertEqual(err["actual_engagement"], 600.0)
        self.assertEqual(err["predicted_revenue"], 200.0)
        self.assertEqual(err["actual_revenue"], 180.0)
        # views error: |10000-8000| / max(8000,1) * 100 = 25.0
        self.assertAlmostEqual(err["views_error_pct"], 25.0, places=4)
        # engagement error: |500-600| / max(600,1) * 100 = 16.6667
        self.assertAlmostEqual(err["engagement_error_pct"], 16.6667, places=3)


# ---------------------------------------------------------------------------
# PredictionEvaluator.evaluate tests
# ---------------------------------------------------------------------------

class TestEvaluateEmpty(unittest.TestCase):
    """test_evaluate_empty: pass empty list, verify graceful handling."""

    def test_evaluate_empty(self):
        evaluator = PredictionEvaluator()
        report = evaluator.evaluate([])
        self.assertEqual(report["total_predictions"], 0)
        self.assertEqual(report["valid_predictions"], 0)
        self.assertEqual(report["invalid_predictions"], 0)
        self.assertEqual(report["overall"]["total_valid"], 0)
        self.assertEqual(report["overall"]["mean_error_pct"], 0.0)
        self.assertEqual(report["by_version"], {})
        # by_type should have all four types with zero counts
        for ptype in ("task_outcome", "video_engagement", "skill_safety", "miks_campaign"):
            self.assertIn(ptype, report["by_type"])
            self.assertEqual(report["by_type"][ptype]["n"], 0)
            self.assertTrue(report["by_type"][ptype]["sample_size_warning"])


class TestEvaluateTaskOutcome(unittest.TestCase):
    """test_evaluate_task_outcome: pass list with a few task predictions, verify metrics."""

    def test_evaluate_task_outcome(self):
        rows = [
            _make_task_row("pass", "pass", 1000, 1000, confidence="high"),
            _make_task_row("fail", "fail", 500, 500, confidence="high"),
            _make_task_row("pass", "fail", 800, 1200, confidence="medium"),
        ]
        report = PredictionEvaluator().evaluate(rows)
        stats = report["by_type"]["task_outcome"]
        self.assertEqual(stats["n"], 3)
        self.assertEqual(stats["n_valid"], 3)
        # 2 of 3 verdicts correct
        self.assertAlmostEqual(stats["verdict_accuracy"], 0.6667, places=4)
        # directional: pred pass==actual pass (✓), fail==fail (✓), pass≠fail (✗) → 2/3
        self.assertAlmostEqual(stats["directional_accuracy"], 0.6667, places=4)
        # Sample size < 10 → warning
        self.assertTrue(stats["sample_size_warning"])


class TestEvaluateSampleSizeWarning(unittest.TestCase):
    """test_evaluate_sample_size_warning: < 10 valid predictions → warning True."""

    def test_evaluate_sample_size_warning(self):
        rows = [_make_task_row(confidence="high") for _ in range(5)]
        report = PredictionEvaluator().evaluate(rows)
        stats = report["by_type"]["task_outcome"]
        self.assertEqual(stats["n"], 5)
        self.assertTrue(stats["sample_size_warning"])

    def test_evaluate_sample_size_no_warning(self):
        rows = [_make_task_row(confidence="high") for _ in range(10)]
        report = PredictionEvaluator().evaluate(rows)
        stats = report["by_type"]["task_outcome"]
        self.assertEqual(stats["n"], 10)
        self.assertFalse(stats["sample_size_warning"])


class TestEvaluateCalibration(unittest.TestCase):
    """test_evaluate_calibration: verify calibration buckets by confidence level."""

    def test_evaluate_calibration(self):
        # 3 high-confidence correct, 1 high-confidence wrong
        rows = [
            _make_task_row("pass", "pass", confidence="high"),
            _make_task_row("pass", "pass", confidence="high"),
            _make_task_row("pass", "pass", confidence="high"),
            _make_task_row("pass", "fail", confidence="high"),
            # 2 medium correct
            _make_task_row("fail", "fail", confidence="medium"),
            _make_task_row("fail", "fail", confidence="medium"),
            # 1 low wrong
            _make_task_row("pass", "fail", confidence="low"),
        ]
        report = PredictionEvaluator().evaluate(rows)
        calib = report["by_type"]["task_outcome"]["calibration"]

        self.assertEqual(calib["high"]["n"], 4)
        # 3/4 correct → 0.75
        self.assertAlmostEqual(calib["high"]["accuracy"], 0.75, places=4)

        self.assertEqual(calib["medium"]["n"], 2)
        self.assertAlmostEqual(calib["medium"]["accuracy"], 1.0, places=4)

        self.assertEqual(calib["low"]["n"], 1)
        self.assertAlmostEqual(calib["low"]["accuracy"], 0.0, places=4)


class TestEvaluateBias(unittest.TestCase):
    """test_evaluate_bias: systematic overprediction → positive bias."""

    def test_evaluate_bias(self):
        # All predictions overestimate token usage by +200
        rows = [
            _make_task_row("pass", "pass", pred_tokens=1200, actual_tokens=1000),
            _make_task_row("fail", "fail", pred_tokens=700, actual_tokens=500),
            _make_task_row("pass", "pass", pred_tokens=300, actual_tokens=100),
        ]
        report = PredictionEvaluator().evaluate(rows)
        stats = report["by_type"]["task_outcome"]
        # signed error: (1200-1000)/1000*100=20, (700-500)/500*100=40, (300-100)/100*100=200
        # mean = (20+40+200)/3 = 86.6667 → positive bias
        self.assertGreater(stats["bias"], 0.0)
        self.assertAlmostEqual(stats["bias"], 86.6667, places=3)


if __name__ == "__main__":
    unittest.main()