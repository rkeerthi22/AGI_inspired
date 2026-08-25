"""Prediction evaluation engine.

Computes per-prediction error metrics and aggregate accuracy/calibration
statistics from mature prediction/outcome pairs.  Stdlib-only.

Usage::

    from prediction_machine.evaluation.evaluator import (
        PredictionEvaluator,
        compute_error,
    )

    errors = compute_error("task_outcome", pred_dict, actual_dict)
    report = PredictionEvaluator().evaluate(mature_rows)
"""

from __future__ import annotations

import json
import statistics
from typing import Any


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce *val* to float; return *default* on any failure."""
    try:
        if val is None:
            return default
        f = float(val)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _pct_error(predicted: float, actual: float) -> float:
    """Absolute percentage error: |pred - actual| / max(actual, 1) * 100."""
    denom = max(abs(actual), 1.0)
    return abs(predicted - actual) / denom * 100.0


def _signed_pct_error(predicted: float, actual: float) -> float:
    """Signed percentage error (positive = overprediction)."""
    denom = max(abs(actual), 1.0)
    return (predicted - actual) / denom * 100.0


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _ratio(numer: int, denom: int) -> float:
    return numer / denom if denom else 0.0


def _parse_json(raw: Any) -> dict:
    """Parse a JSON string to dict; return {} on failure.  Pass-through dicts."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _empty_calibration() -> dict:
    return {
        "high": {"n": 0, "accuracy": 0.0},
        "medium": {"n": 0, "accuracy": 0.0},
        "low": {"n": 0, "accuracy": 0.0},
    }


def _calibration_update(calib: dict, confidence: str, correct: bool) -> None:
    """Increment a calibration bucket in place."""
    key = confidence if confidence in ("high", "medium", "low") else "low"
    bucket = calib[key]
    bucket["n"] += 1
    # accuracy running mean
    n = bucket["n"]
    bucket["accuracy"] = bucket["accuracy"] * (n - 1) / n + (1.0 if correct else 0.0) / n


def _calibration_finalize(calib: dict) -> dict:
    """Round accuracy to 4 dp."""
    out = {}
    for key in ("high", "medium", "low"):
        n = calib[key]["n"]
        acc = calib[key]["accuracy"]
        out[key] = {"n": n, "accuracy": round(acc, 4)}
    return out


# ---------------------------------------------------------------------------
# per-prediction error computation
# ---------------------------------------------------------------------------

def compute_error(prediction_type: str, prediction: dict, actual: dict) -> dict:
    """Compute per-prediction error metrics.

    Parameters
    ----------
    prediction_type : str
        One of ``task_outcome``, ``video_engagement``, ``skill_safety``,
        ``miks_campaign``.
    prediction : dict
        Parsed prediction payload (not a JSON string).
    actual : dict
        Parsed actual-outcome payload (not a JSON string).

    Returns
    -------
    dict
        Error metrics specific to *prediction_type*.  Always includes a
        ``prediction_type`` key.  Returns a minimal error dict if the type is
        unknown.
    """
    if not isinstance(prediction, dict):
        prediction = {}
    if not isinstance(actual, dict):
        actual = {}

    if prediction_type == "task_outcome":
        return _error_task_outcome(prediction, actual)
    if prediction_type == "video_engagement":
        return _error_video_engagement(prediction, actual)
    if prediction_type == "skill_safety":
        return _error_skill_safety(prediction, actual)
    if prediction_type == "miks_campaign":
        return _error_miks_campaign(prediction, actual)
    return {"prediction_type": prediction_type, "error": "unknown_type"}


def _error_task_outcome(pred: dict, actual: dict) -> dict:
    # Accept both prediction format (predicted_verdict) and outcome format (verdict)
    pred_verdict = str(
        pred.get("predicted_verdict") or pred.get("verdict") or ""
    ).strip().lower()
    actual_verdict = str(actual.get("verdict", "")).strip().lower()
    verdict_correct = pred_verdict == actual_verdict and pred_verdict != ""

    pred_tokens = _safe_float(
        pred.get("predicted_tokens") or pred.get("token_usage") or pred.get("tokens")
    )
    actual_tokens = _safe_float(actual.get("tokens") or actual.get("token_usage"))
    token_error_pct = _pct_error(pred_tokens, actual_tokens)
    signed_token_pct = _signed_pct_error(pred_tokens, actual_tokens)

    # Directional: did we correctly predict pass vs fail?
    pred_pass = pred_verdict in ("pass", "passed", "success", "ok", "true", "1", "yes")
    actual_pass = actual_verdict in ("pass", "passed", "success", "ok", "true", "1", "yes")
    directional_correct = pred_pass == actual_pass and pred_verdict != ""

    return {
        "prediction_type": "task_outcome",
        "verdict_correct": verdict_correct,
        "token_error_pct": round(token_error_pct, 4),
        "token_signed_error_pct": round(signed_token_pct, 4),
        "directional_correct": directional_correct,
        "predicted_verdict": pred_verdict,
        "actual_verdict": actual_verdict,
        "predicted_tokens": pred_tokens,
        "actual_tokens": actual_tokens,
    }


def _error_video_engagement(pred: dict, actual: dict) -> dict:
    # Extract view counts for 24h / 3d / 7d windows
    def _views(d: dict, key: str) -> float:
        # accept nested e.g. d["views_7d"] or d["7d"]["views"]
        if key in d:
            return _safe_float(d[key])
        window_map = {
            "views_24h": ("24h", "views_24h"),
            "views_3d": ("3d", "views_3d"),
            "views_7d": ("7d", "views_7d"),
        }
        if key in window_map:
            window, alt = window_map[key]
            if isinstance(d.get(window), dict):
                return _safe_float(d[window].get("views"))
            return _safe_float(d.get(alt))
        return 0.0

    pred_24h = _views(pred, "views_24h")
    actual_24h = _views(actual, "views_24h")
    pred_3d = _views(pred, "views_3d")
    actual_3d = _views(actual, "views_3d")
    pred_7d = _views(pred, "views_7d")
    actual_7d = _views(actual, "views_7d")

    err_24h = _pct_error(pred_24h, actual_24h)
    err_3d = _pct_error(pred_3d, actual_3d)
    err_7d = _pct_error(pred_7d, actual_7d)

    signed_7d = _signed_pct_error(pred_7d, actual_7d)

    # Directional: above/below category median
    category_median = _safe_float(pred.get("category_median") or actual.get("category_median"))
    if category_median > 0:
        pred_above = pred_7d > category_median
        actual_above = actual_7d > category_median
        directional_correct = pred_above == actual_above
    else:
        directional_correct = False

    return {
        "prediction_type": "video_engagement",
        "view_error_pct_24h": round(err_24h, 4),
        "view_error_pct_3d": round(err_3d, 4),
        "view_error_pct_7d": round(err_7d, 4),
        "view_signed_error_pct_7d": round(signed_7d, 4),
        "directional_correct": directional_correct,
        "predicted_views_7d": pred_7d,
        "actual_views_7d": actual_7d,
        "predicted_views_24h": pred_24h,
        "actual_views_24h": actual_24h,
        "predicted_views_3d": pred_3d,
        "actual_views_3d": actual_3d,
    }


def _error_skill_safety(pred: dict, actual: dict) -> dict:
    predicted_high_risk = bool(pred.get("high_risk", pred.get("risk_level") == "high"))
    regressed = bool(actual.get("regressed", actual.get("regression_occurred")))

    risk_correct = (predicted_high_risk and regressed) or (not predicted_high_risk and not regressed)
    false_positive = predicted_high_risk and not regressed
    false_negative = not predicted_high_risk and regressed

    return {
        "prediction_type": "skill_safety",
        "risk_correct": risk_correct,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "predicted_high_risk": predicted_high_risk,
        "actual_regressed": regressed,
    }


def _error_miks_campaign(pred: dict, actual: dict) -> dict:
    pred_views = _safe_float(pred.get("total_views") or pred.get("views"))
    actual_views = _safe_float(actual.get("total_views") or actual.get("views"))
    pred_engagement = _safe_float(pred.get("engagement") or pred.get("total_engagement"))
    actual_engagement = _safe_float(actual.get("engagement") or actual.get("total_engagement"))
    pred_revenue = _safe_float(pred.get("revenue") or pred.get("total_revenue"))
    actual_revenue = _safe_float(actual.get("revenue") or actual.get("total_revenue"))

    views_error_pct = _pct_error(pred_views, actual_views)
    engagement_error_pct = _pct_error(pred_engagement, actual_engagement)
    revenue_error_pct = _pct_error(pred_revenue, actual_revenue)

    signed_views = _signed_pct_error(pred_views, actual_views)

    return {
        "prediction_type": "miks_campaign",
        "views_error_pct": round(views_error_pct, 4),
        "engagement_error_pct": round(engagement_error_pct, 4),
        "revenue_error_pct": round(revenue_error_pct, 4),
        "views_signed_error_pct": round(signed_views, 4),
        "predicted_total_views": pred_views,
        "actual_total_views": actual_views,
        "predicted_engagement": pred_engagement,
        "actual_engagement": actual_engagement,
        "predicted_revenue": pred_revenue,
        "actual_revenue": actual_revenue,
    }


# ---------------------------------------------------------------------------
# aggregate evaluator
# ---------------------------------------------------------------------------

class PredictionEvaluator:
    """Aggregate evaluation of mature predictions.

    A *mature prediction* is a row dict from :class:`PredictionStore` with
    keys including ``prediction_type``, ``prediction`` (JSON string),
    ``actual`` (JSON string), ``error`` (JSON string), ``confidence``,
    ``model_version`` and ``valid_for_training``.
    """

    # ---- public API -------------------------------------------------------

    @staticmethod
    def compute_error(prediction_type: str, prediction: dict, actual: dict) -> dict:
        """Thin wrapper around :func:`compute_error`."""
        return compute_error(prediction_type, prediction, actual)

    def evaluate(self, predictions: list[dict]) -> dict:
        """Evaluate a list of mature prediction rows.

        Returns the aggregate report dict described in the module docstring.
        Handles empty / malformed input gracefully.
        """
        if not predictions:
            return self._empty_report()

        total = len(predictions)
        valid_rows = [r for r in predictions if self._is_valid(r)]
        invalid_rows = [r for r in predictions if not self._is_valid(r)]
        n_valid = len(valid_rows)
        n_invalid = len(invalid_rows)

        # group valid rows by type
        by_type_raw: dict[str, list[dict]] = {}
        for row in valid_rows:
            ptype = row.get("prediction_type", "unknown")
            by_type_raw.setdefault(ptype, []).append(row)

        by_type: dict[str, dict] = {}
        all_error_pcts: list[float] = []

        for ptype in ("task_outcome", "video_engagement", "skill_safety", "miks_campaign"):
            rows = by_type_raw.get(ptype, [])
            if ptype == "task_outcome":
                stats, err_pcts = self._eval_task_outcome(rows)
            elif ptype == "video_engagement":
                stats, err_pcts = self._eval_video_engagement(rows)
            elif ptype == "skill_safety":
                stats, err_pcts = self._eval_skill_safety(rows)
            elif ptype == "miks_campaign":
                stats, err_pcts = self._eval_miks_campaign(rows)
            else:
                stats, err_pcts = {}, []
            by_type[ptype] = stats
            all_error_pcts.extend(err_pcts)

        # include any extra unknown types as a no-op so they're counted
        for ptype, rows in by_type_raw.items():
            if ptype not in by_type:
                by_type[ptype] = {
                    "n": len(rows),
                    "n_valid": len(rows),
                    "sample_size_warning": True,
                    "note": f"Unknown prediction type: {ptype}",
                }

        # by_version
        by_version = self._eval_by_version(valid_rows)

        # overall
        overall = {
            "total_valid": n_valid,
            "mean_error_pct": round(_mean(all_error_pcts), 4),
            "types_represented": sorted(by_type_raw.keys()),
        }

        return {
            "total_predictions": total,
            "valid_predictions": n_valid,
            "invalid_predictions": n_invalid,
            "by_type": by_type,
            "by_version": by_version,
            "overall": overall,
        }

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _is_valid(row: dict) -> bool:
        val = row.get("valid_for_training", 1)
        if isinstance(val, str):
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = 1
        return bool(val)

    @staticmethod
    def _get_error(row: dict) -> dict:
        """Return the parsed error dict for a row.

        Prefers the stored ``error`` column; recomputes from prediction/actual
        if the stored error is missing or unparseable.
        """
        err = _parse_json(row.get("error"))
        if err:
            return err
        # try to recompute
        ptype = row.get("prediction_type", "")
        pred = _parse_json(row.get("prediction"))
        actual = _parse_json(row.get("actual"))
        if pred and actual and ptype:
            return compute_error(ptype, pred, actual)
        return {}

    @staticmethod
    def _get_confidence(row: dict) -> str:
        conf = str(row.get("confidence", "")).strip().lower()
        return conf if conf in ("high", "medium", "low") else "low"

    # -- per-type evaluators --

    def _eval_task_outcome(self, rows: list[dict]) -> tuple[dict, list[float]]:
        n = len(rows)
        if n == 0:
            return self._empty_type("task_outcome"), []

        verdict_correct: list[bool] = []
        token_errs: list[float] = []
        signed_errs: list[float] = []
        directional: list[bool] = []
        calib = _empty_calibration()

        for row in rows:
            err = self._get_error(row)
            vc = bool(err.get("verdict_correct", False))
            te = _safe_float(err.get("token_error_pct"))
            se = _safe_float(err.get("token_signed_error_pct", _signed_pct_error(
                _safe_float(err.get("predicted_tokens")),
                _safe_float(err.get("actual_tokens")),
            )))
            dc = bool(err.get("directional_correct", False))
            verdict_correct.append(vc)
            token_errs.append(te)
            signed_errs.append(se)
            directional.append(dc)
            _calibration_update(calib, self._get_confidence(row), vc)

        result = {
            "n": n,
            "n_valid": n,
            "verdict_accuracy": round(_ratio(sum(verdict_correct), n), 4),
            "mean_token_error_pct": round(_mean(token_errs), 4),
            "median_token_error_pct": round(_median(token_errs), 4),
            "min_token_error_pct": round(min(token_errs), 4) if token_errs else 0.0,
            "max_token_error_pct": round(max(token_errs), 4) if token_errs else 0.0,
            "directional_accuracy": round(_ratio(sum(directional), n), 4),
            "calibration": _calibration_finalize(calib),
            "bias": round(_mean(signed_errs), 4),
            "sample_size_warning": n < 10,
        }
        if n < 10:
            result["note"] = f"Small sample (n={n}); metrics are not reliable."
        return result, token_errs

    def _eval_video_engagement(self, rows: list[dict]) -> tuple[dict, list[float]]:
        n = len(rows)
        if n == 0:
            return self._empty_type("video_engagement"), []

        mae_7d_vals: list[float] = []
        mape_7d_vals: list[float] = []
        signed_7d: list[float] = []
        directional: list[bool] = []
        calib = _empty_calibration()

        for row in rows:
            err = self._get_error(row)
            pred_7d = _safe_float(err.get("predicted_views_7d"))
            actual_7d = _safe_float(err.get("actual_views_7d"))
            abs_err = abs(pred_7d - actual_7d)
            mae_7d_vals.append(abs_err)
            pct_err = _safe_float(err.get("view_error_pct_7d"))
            mape_7d_vals.append(pct_err)
            signed_7d.append(_safe_float(err.get("view_signed_error_pct_7d", _signed_pct_error(pred_7d, actual_7d))))
            dc = bool(err.get("directional_correct", False))
            directional.append(dc)
            # calibration based on directional correctness
            _calibration_update(calib, self._get_confidence(row), dc)

        result = {
            "n": n,
            "n_valid": n,
            "mae_views_7d": round(_mean(mae_7d_vals), 4),
            "mape_views_7d": round(_mean(mape_7d_vals), 4),
            "median_error_pct_7d": round(_median(mape_7d_vals), 4),
            "directional_accuracy": round(_ratio(sum(directional), n), 4),
            "calibration": _calibration_finalize(calib),
            "bias": round(_mean(signed_7d), 4),
            "sample_size_warning": n < 10,
        }
        if n < 10:
            result["note"] = f"Small sample (n={n}); metrics are not reliable."
        return result, mape_7d_vals

    def _eval_skill_safety(self, rows: list[dict]) -> tuple[dict, list[float]]:
        n = len(rows)
        if n == 0:
            return self._empty_type("skill_safety"), []

        risk_correct: list[bool] = []
        false_positives = 0
        false_negatives = 0
        predicted_high = 0
        actual_regressions = 0
        true_positives = 0  # predicted high AND regressed

        for row in rows:
            err = self._get_error(row)
            rc = bool(err.get("risk_correct", False))
            risk_correct.append(rc)
            fp = bool(err.get("false_positive", False))
            fn = bool(err.get("false_negative", False))
            phr = bool(err.get("predicted_high_risk", False))
            ar = bool(err.get("actual_regressed", False))
            if fp:
                false_positives += 1
            if fn:
                false_negatives += 1
            if phr:
                predicted_high += 1
            if ar:
                actual_regressions += 1
            if phr and ar:
                true_positives += 1

        precision = _ratio(true_positives, predicted_high)
        recall = _ratio(true_positives, actual_regressions)

        result = {
            "n": n,
            "n_valid": n,
            "risk_accuracy": round(_ratio(sum(risk_correct), n), 4),
            "false_positive_rate": round(_ratio(false_positives, n), 4),
            "false_negative_rate": round(_ratio(false_negatives, n), 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "sample_size_warning": n < 10,
        }
        if n < 10:
            result["note"] = f"Small sample (n={n}); metrics are not reliable."
        # error pct proxies: use false_positive/negative rates as the error measure
        err_pcts = [
            _ratio(false_positives, n) * 100,
            _ratio(false_negatives, n) * 100,
        ]
        return result, err_pcts

    def _eval_miks_campaign(self, rows: list[dict]) -> tuple[dict, list[float]]:
        n = len(rows)
        if n == 0:
            return self._empty_type("miks_campaign"), []

        views_errs: list[float] = []
        eng_errs: list[float] = []
        rev_errs: list[float] = []
        signed_errs: list[float] = []

        for row in rows:
            err = self._get_error(row)
            ve = _safe_float(err.get("views_error_pct"))
            ee = _safe_float(err.get("engagement_error_pct"))
            re = _safe_float(err.get("revenue_error_pct"))
            se = _safe_float(err.get("views_signed_error_pct"))
            views_errs.append(ve)
            eng_errs.append(ee)
            rev_errs.append(re)
            signed_errs.append(se)

        all_errs = views_errs + eng_errs + rev_errs
        result = {
            "n": n,
            "n_valid": n,
            "views_mape": round(_mean(views_errs), 4),
            "engagement_mape": round(_mean(eng_errs), 4),
            "revenue_mape": round(_mean(rev_errs), 4),
            "bias": round(_mean(signed_errs), 4),
            "sample_size_warning": n < 10,
        }
        if n < 10:
            result["note"] = f"Small sample (n={n}); metrics are not reliable."
        return result, all_errs

    # -- by version --

    def _eval_by_version(self, valid_rows: list[dict]) -> dict:
        groups: dict[str, list[dict]] = {}
        for row in valid_rows:
            ver = row.get("model_version", "unknown")
            groups.setdefault(ver, []).append(row)

        out: dict[str, dict] = {}
        for ver, rows in sorted(groups.items()):
            # Determine the dominant prediction type for this version
            type_counts: dict[str, int] = {}
            for r in rows:
                t = r.get("prediction_type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            dominant_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"

            n = len(rows)
            # compute a generic accuracy / error for this version
            accuracy_vals: list[float] = []
            error_pcts: list[float] = []
            for r in rows:
                err = self._get_error(r)
                # pick a correctness signal depending on type
                ptype = r.get("prediction_type", "")
                if ptype == "task_outcome":
                    accuracy_vals.append(1.0 if err.get("verdict_correct") else 0.0)
                    error_pcts.append(_safe_float(err.get("token_error_pct")))
                elif ptype == "video_engagement":
                    accuracy_vals.append(1.0 if err.get("directional_correct") else 0.0)
                    error_pcts.append(_safe_float(err.get("view_error_pct_7d")))
                elif ptype == "skill_safety":
                    accuracy_vals.append(1.0 if err.get("risk_correct") else 0.0)
                elif ptype == "miks_campaign":
                    error_pcts.append(_safe_float(err.get("views_error_pct")))

            entry: dict[str, Any] = {
                "n": n,
                "dominant_type": dominant_type,
                "accuracy": round(_ratio(int(sum(accuracy_vals)), len(accuracy_vals)), 4) if accuracy_vals else 0.0,
                "mean_error_pct": round(_mean(error_pcts), 4),
                "sample_size_warning": n < 10,
            }
            if n < 10:
                entry["note"] = f"Small sample (n={n}); metrics are not reliable."
            out[ver] = entry
        return out

    # -- empty states --

    @staticmethod
    def _empty_type(ptype: str) -> dict:
        return {
            "n": 0,
            "n_valid": 0,
            "sample_size_warning": True,
            "note": f"No valid predictions for type '{ptype}'.",
        }

    @staticmethod
    def _empty_report() -> dict:
        return {
            "total_predictions": 0,
            "valid_predictions": 0,
            "invalid_predictions": 0,
            "by_type": {
                "task_outcome": PredictionEvaluator._empty_type("task_outcome"),
                "video_engagement": PredictionEvaluator._empty_type("video_engagement"),
                "skill_safety": PredictionEvaluator._empty_type("skill_safety"),
                "miks_campaign": PredictionEvaluator._empty_type("miks_campaign"),
            },
            "by_version": {},
            "overall": {
                "total_valid": 0,
                "mean_error_pct": 0.0,
                "types_represented": [],
            },
        }