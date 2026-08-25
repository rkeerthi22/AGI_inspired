#!/usr/bin/env python
"""Prediction Machine — Daily Learning Loop

Runs once per day to:
1. Collect real outcomes for matured predictions
2. Score all predictions with new outcomes
3. Evaluate overall performance
4. Diagnose systematic errors
5. Generate daily report

Usage: python -m prediction_machine.run_daily
  or:  python prediction_machine/run_daily.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Path setup — make it runnable standalone
# ---------------------------------------------------------------------------
_THIS_FILE = os.path.abspath(__file__)
_PM_DIR = os.path.dirname(_THIS_FILE)          # prediction_machine/
_REPO_DIR = os.path.dirname(_PM_DIR)            # S:/AGI_like/

# Add repo root so `import prediction_machine...` works
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# Also add PM_DIR so `import core...` works if needed
if _PM_DIR not in sys.path:
    sys.path.insert(0, _PM_DIR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REPORTS_DIR = os.path.join(_PM_DIR, "reports", "daily")
_LEDGERBOOK_DB = os.path.join(_REPO_DIR, "memory", "ledgerbook.db")

_MODEL_VERSIONS = [
    # (version, prediction_type, description, parent_version)
    ("task_outcome_v1", "task_outcome",
     "Historical median by mission — pass-rate and token cost from ledger", None),
    ("video_engagement_v1", "video_engagement",
     "Category-median based view prediction for YouTube videos", None),
    ("skill_safety_v1", "skill_safety",
     "Heuristic risk classifier for skill promotion safety", None),
    ("miks_campaign_v1", "miks_campaign",
     "MIKS campaign engagement/revenue predictor (initial heuristic)", None),
]

_PREDICTION_TYPES = ["task_outcome", "video_engagement", "skill_safety", "miks_campaign"]

_MIN_SAMPLE = 10  # below this, we warn about sample size


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _parse_json(raw: Any) -> dict:
    """Parse a JSON string to dict; return {} on failure."""
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


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        f = float(val)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _git_head() -> Optional[str]:
    """Return the current git HEAD commit hash, or None."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", _REPO_DIR, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Step 1 — Initialise store and register model versions
# ---------------------------------------------------------------------------

def init_store():
    """Initialise the PredictionStore and return it."""
    from prediction_machine.core.prediction_store import PredictionStore
    store = PredictionStore()
    store.init_db()
    return store


def register_model_versions(store) -> None:
    """Register all model versions if not already registered."""
    for version, ptype, description, parent in _MODEL_VERSIONS:
        try:
            existing = store.get_active_version(ptype)
            # Try to register; if it already exists, it'll raise ValueError
            store.register_model_version(
                version=version,
                prediction_type=ptype,
                description=description,
                parent_version=parent,
            )
            # Activate the first registered version for this type
            if existing is None:
                store.activate_model_version(version)
        except ValueError:
            # Already registered — fine
            pass
        except Exception as exc:
            sys.stderr.write(f"register_model_versions: {version}: {exc}\n")


# ---------------------------------------------------------------------------
# Step 2 — Run all collectors
# ---------------------------------------------------------------------------

def run_all_collectors(store) -> dict[str, dict]:
    """Run all four collectors and return their summaries."""
    results: dict[str, dict] = {}

    # Task outcome collector
    try:
        from prediction_machine.collectors.task_outcome_collector import TaskOutcomeCollector
        results["task_outcome"] = TaskOutcomeCollector().collect_pending(store)
    except Exception as exc:
        results["task_outcome"] = {"error": str(exc), "checked": 0, "recorded": 0, "skipped": 0, "invalidated": 0, "errors": [str(exc)]}

    # Video engagement collector
    try:
        from prediction_machine.collectors.video_engagement_collector import VideoEngagementCollector
        results["video_engagement"] = VideoEngagementCollector().collect_pending(store)
    except Exception as exc:
        results["video_engagement"] = {"error": str(exc), "checked": 0, "recorded": 0, "skipped": 0, "pending": 0, "errors": [str(exc)]}

    # Skill safety collector
    try:
        from prediction_machine.collectors.skill_safety_collector import SkillSafetyCollector
        results["skill_safety"] = SkillSafetyCollector().collect_pending(store)
    except Exception as exc:
        results["skill_safety"] = {"error": str(exc), "checked": 0, "recorded": 0, "skipped": 0, "invalidated": 0, "errors": [str(exc)]}

    # MIKS campaign collector
    try:
        from prediction_machine.collectors.miks_campaign_collector import MiksCampaignCollector
        results["miks_campaign"] = MiksCampaignCollector().collect_pending(store)
    except Exception as exc:
        results["miks_campaign"] = {"error": str(exc), "checked": 0, "recorded": 0, "skipped": 0, "invalidated": 0, "errors": [str(exc)]}

    return results


# ---------------------------------------------------------------------------
# Step 3 — Evaluate
# ---------------------------------------------------------------------------

def evaluate_all(store) -> dict:
    """Run the evaluator on all mature predictions."""
    from prediction_machine.evaluation.evaluator import PredictionEvaluator
    evaluator = PredictionEvaluator()

    mature = store.get_mature_predictions(valid_only=False)
    report = evaluator.evaluate(mature)
    return report


# ---------------------------------------------------------------------------
# Step 4 — Diagnose systematic errors
# ---------------------------------------------------------------------------

def diagnose(store, eval_report: dict) -> dict:
    """Diagnose systematic errors from mature predictions.

    Returns a diagnosis dict with:
    - large_misses: predictions with error > 50%
    - bias_by_type: systematic over/underprediction per type
    - calibration_check: are high-confidence predictions more accurate?
    - by_version: is the newer version better than the older?
    """
    diagnosis: dict[str, Any] = {
        "large_misses": [],
        "bias_by_type": {},
        "calibration_check": {},
        "by_version": {},
    }

    try:
        mature = store.get_mature_predictions(valid_only=False)
    except Exception:
        return diagnosis

    # --- Large misses (error > 50%) ---
    for row in mature:
        err = _parse_json(row.get("error"))
        ptype = row.get("prediction_type", "")
        target = row.get("target", "")
        pred_id = row.get("prediction_id", "")

        # Determine the primary error percentage for this type
        error_pct = None
        if ptype == "task_outcome":
            error_pct = _safe_float(err.get("token_error_pct"))
        elif ptype == "video_engagement":
            error_pct = _safe_float(err.get("view_error_pct_7d"))
        elif ptype == "skill_safety":
            # Use 100% if wrong, 0% if right
            if not err.get("risk_correct", False):
                error_pct = 100.0
            else:
                error_pct = 0.0
        elif ptype == "miks_campaign":
            error_pct = _safe_float(err.get("views_error_pct"))

        if error_pct is not None and error_pct > 50.0:
            diagnosis["large_misses"].append({
                "prediction_id": pred_id,
                "prediction_type": ptype,
                "target": target,
                "error_pct": round(error_pct, 2),
                "model_version": row.get("model_version", ""),
            })

    # Sort by error descending, keep top 20
    diagnosis["large_misses"].sort(key=lambda x: x["error_pct"], reverse=True)
    diagnosis["large_misses"] = diagnosis["large_misses"][:20]

    # --- Bias by type ---
    for ptype in _PREDICTION_TYPES:
        type_rows = [r for r in mature if r.get("prediction_type") == ptype]
        if not type_rows:
            diagnosis["bias_by_type"][ptype] = {"n": 0, "bias": 0.0, "direction": "none"}
            continue

        signed_errs = []
        for row in type_rows:
            err = _parse_json(row.get("error"))
            if ptype == "task_outcome":
                signed_errs.append(_safe_float(err.get("token_signed_error_pct")))
            elif ptype == "video_engagement":
                signed_errs.append(_safe_float(err.get("view_signed_error_pct_7d")))
            elif ptype == "skill_safety":
                # No signed error for binary; use false_positive/negative
                pass
            elif ptype == "miks_campaign":
                signed_errs.append(_safe_float(err.get("views_signed_error_pct")))

        if signed_errs:
            mean_bias = sum(signed_errs) / len(signed_errs)
            diagnosis["bias_by_type"][ptype] = {
                "n": len(type_rows),
                "bias": round(mean_bias, 2),
                "direction": "over" if mean_bias > 0 else "under" if mean_bias < 0 else "neutral",
            }
        else:
            diagnosis["bias_by_type"][ptype] = {
                "n": len(type_rows),
                "bias": 0.0,
                "direction": "none",
            }

    # --- Calibration check ---
    # Are "high" confidence predictions actually more accurate than "low"?
    for ptype in _PREDICTION_TYPES:
        type_rows = [r for r in mature if r.get("prediction_type") == ptype]
        if len(type_rows) < 2:
            diagnosis["calibration_check"][ptype] = {
                "n": len(type_rows),
                "note": "INSUFFICIENT DATA for calibration check",
            }
            continue

        conf_buckets: dict[str, list[bool]] = {"high": [], "medium": [], "low": []}
        for row in type_rows:
            conf = str(row.get("confidence", "low")).strip().lower()
            if conf not in conf_buckets:
                conf = "low"
            err = _parse_json(row.get("error"))

            # Determine correctness per type
            correct = False
            if ptype == "task_outcome":
                correct = bool(err.get("verdict_correct", False))
            elif ptype == "video_engagement":
                correct = bool(err.get("directional_correct", False))
            elif ptype == "skill_safety":
                correct = bool(err.get("risk_correct", False))
            elif ptype == "miks_campaign":
                # Use < 20% views error as "correct"
                ve = _safe_float(err.get("views_error_pct"))
                correct = ve < 20.0

            conf_buckets[conf].append(correct)

        calib_result = {}
        for level in ("high", "medium", "low"):
            bucket = conf_buckets[level]
            if bucket:
                calib_result[level] = {
                    "n": len(bucket),
                    "accuracy": round(sum(bucket) / len(bucket), 4),
                }
            else:
                calib_result[level] = {"n": 0, "accuracy": 0.0}

        diagnosis["calibration_check"][ptype] = calib_result

    # --- By version comparison ---
    by_version = eval_report.get("by_version", {})
    for ver, stats in by_version.items():
        diagnosis["by_version"][ver] = stats

    return diagnosis


# ---------------------------------------------------------------------------
# Step 5 — Generate the daily report
# ---------------------------------------------------------------------------

def generate_report(
    store,
    collector_results: dict[str, dict],
    eval_report: dict,
    diagnosis: dict,
) -> str:
    """Generate the daily report as markdown."""
    today = _today_str()
    lines: list[str] = []

    # --- Header ---
    lines.append("# Prediction Machine — Daily Report")
    lines.append(f"**Date:** {today}")
    lines.append("")

    # --- Dataset metrics ---
    total_predictions = store.count_predictions()

    # New predictions today
    all_preds = store.get_all_predictions(valid_only=None)
    today_prefix = today
    new_today = sum(
        1 for p in all_preds
        if (p.get("created_at") or "").startswith(today_prefix)
    )

    # Predictions matured today
    all_mature = store.get_mature_predictions(valid_only=False)
    matured_today = sum(
        1 for p in all_mature
        if (p.get("actual_recorded_at") or "").startswith(today_prefix)
    )

    valid_outcomes = len([p for p in all_mature if p.get("valid_for_training") in (1, "1", True)])
    invalid_outcomes = store.count_predictions() - valid_outcomes

    # Training sample size = valid mature predictions
    training_sample = len(store.get_mature_predictions(valid_only=True))

    lines.append("## Dataset")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total predictions | {total_predictions} |")
    lines.append(f"| New predictions today | {new_today} |")
    lines.append(f"| Predictions matured today | {matured_today} |")
    lines.append(f"| Valid real outcomes | {valid_outcomes} |")
    lines.append(f"| Invalid outcomes | {invalid_outcomes} |")
    lines.append(f"| Training sample size | {training_sample} |")
    lines.append("")

    # --- Performance by type ---
    lines.append("## Performance")
    by_type = eval_report.get("by_type", {})

    for ptype in _PREDICTION_TYPES:
        lines.append(f"### {ptype.replace('_', ' ').title()}")
        stats = by_type.get(ptype, {})
        n = stats.get("n", 0)

        if n == 0:
            lines.append(f"- N: 0")
            lines.append("- INSUFFICIENT DATA — no mature predictions for this type")
            lines.append("")
            continue

        lines.append(f"- N: {n}")

        if ptype == "task_outcome":
            verdict_acc = stats.get("verdict_accuracy", 0.0)
            token_err = stats.get("mean_token_error_pct", 0.0)
            bias = stats.get("bias", 0.0)
            bias_dir = "over" if bias > 0 else "under" if bias < 0 else "neutral"
            ss_warn = "yes" if stats.get("sample_size_warning", True) else "no"
            lines.append(f"- Verdict accuracy: {verdict_acc * 100:.1f}%")
            lines.append(f"- Mean token error: {token_err:.1f}%")
            lines.append(f"- Bias: {bias:.1f}% ({bias_dir})")
            lines.append(f"- Sample size warning: {ss_warn}")
            if n < _MIN_SAMPLE:
                lines.append(f"- INSUFFICIENT DATA — sample size {n} < {_MIN_SAMPLE}, metrics not reliable")

        elif ptype == "video_engagement":
            mape_7d = stats.get("mape_views_7d", 0.0)
            dir_acc = stats.get("directional_accuracy", 0.0)
            bias = stats.get("bias", 0.0)
            bias_dir = "over" if bias > 0 else "under" if bias < 0 else "neutral"
            ss_warn = "yes" if stats.get("sample_size_warning", True) else "no"
            lines.append(f"- MAPE views 7d: {mape_7d:.1f}%")
            lines.append(f"- Directional accuracy: {dir_acc * 100:.1f}%")
            lines.append(f"- Bias: {bias:.1f}% ({bias_dir})")
            lines.append(f"- Sample size warning: {ss_warn}")
            if n < _MIN_SAMPLE:
                lines.append(f"- INSUFFICIENT DATA — sample size {n} < {_MIN_SAMPLE}, metrics not reliable")

        elif ptype == "skill_safety":
            risk_acc = stats.get("risk_accuracy", 0.0)
            fpr = stats.get("false_positive_rate", 0.0)
            fnr = stats.get("false_negative_rate", 0.0)
            precision = stats.get("precision", 0.0)
            recall = stats.get("recall", 0.0)
            ss_warn = "yes" if stats.get("sample_size_warning", True) else "no"
            lines.append(f"- Risk accuracy: {risk_acc * 100:.1f}%")
            lines.append(f"- False positive rate: {fpr * 100:.1f}%")
            lines.append(f"- False negative rate: {fnr * 100:.1f}%")
            lines.append(f"- Precision: {precision * 100:.1f}%")
            lines.append(f"- Recall: {recall * 100:.1f}%")
            lines.append(f"- Sample size warning: {ss_warn}")
            if n < _MIN_SAMPLE:
                lines.append(f"- INSUFFICIENT DATA — sample size {n} < {_MIN_SAMPLE}, metrics not reliable")

        elif ptype == "miks_campaign":
            views_mape = stats.get("views_mape", 0.0)
            eng_mape = stats.get("engagement_mape", 0.0)
            rev_mape = stats.get("revenue_mape", 0.0)
            bias = stats.get("bias", 0.0)
            bias_dir = "over" if bias > 0 else "under" if bias < 0 else "neutral"
            ss_warn = "yes" if stats.get("sample_size_warning", True) else "no"
            lines.append(f"- Views MAPE: {views_mape:.1f}%")
            lines.append(f"- Engagement MAPE: {eng_mape:.1f}%")
            lines.append(f"- Revenue MAPE: {rev_mape:.1f}%")
            lines.append(f"- Bias: {bias:.1f}% ({bias_dir})")
            lines.append(f"- Sample size warning: {ss_warn}")
            if n < _MIN_SAMPLE:
                lines.append(f"- INSUFFICIENT DATA — sample size {n} < {_MIN_SAMPLE}, metrics not reliable")

        lines.append("")

    # --- What we learned today ---
    lines.append("## What we learned today")
    learned_lines = _generate_learned_section(
        store, collector_results, matured_today,
    )
    lines.extend(learned_lines)
    lines.append("")

    # --- Largest prediction misses ---
    lines.append("## Largest prediction misses")
    large_misses = diagnosis.get("large_misses", [])
    if large_misses:
        lines.append("| Type | Target | Error % | Model version |")
        lines.append("|---|---|---|---|")
        for miss in large_misses[:10]:
            lines.append(
                f"| {miss['prediction_type']} | {miss['target']} | "
                f"{miss['error_pct']:.1f}% | {miss['model_version']} |"
            )
    else:
        lines.append("No predictions with error > 50% found.")
    lines.append("")

    # --- Systematic bias detected ---
    lines.append("## Systematic bias detected")
    bias_by_type = diagnosis.get("bias_by_type", {})
    any_bias = False
    for ptype in _PREDICTION_TYPES:
        b = bias_by_type.get(ptype, {})
        n = b.get("n", 0)
        bias_val = b.get("bias", 0.0)
        direction = b.get("direction", "none")
        if n > 0 and direction != "neutral" and abs(bias_val) > 5.0:
            any_bias = True
            lines.append(f"- **{ptype}**: {bias_val:.1f}% ({direction}) — n={n}")
    if not any_bias:
        lines.append("No significant systematic bias detected (>5% threshold).")
    lines.append("")

    # --- Changes tested ---
    lines.append("## Changes tested")
    experiments = store.get_experiments(decision="PENDING")
    if experiments:
        for exp in experiments:
            lines.append(f"- **{exp.get('prediction_type', '')}**: {exp.get('hypothesis', '')}")
            lines.append(f"  - Proposed: {exp.get('proposed_change', '')}")
            lines.append(f"  - Previous metric: {exp.get('previous_metric', 'N/A')}")
    else:
        lines.append("No changes currently being tested.")
    lines.append("")

    # --- Accepted changes ---
    lines.append("## Accepted changes")
    accepted = store.get_experiments(decision="ACCEPT")
    if accepted:
        for exp in accepted:
            lines.append(f"- **{exp.get('prediction_type', '')}**: {exp.get('proposed_change', '')}")
            lines.append(f"  - Metric: {exp.get('previous_metric', 'N/A')} → {exp.get('new_metric', 'N/A')}")
            lines.append(f"  - Sample size: {exp.get('sample_size', 'N/A')}")
    else:
        lines.append("No changes accepted yet.")
    lines.append("")

    # --- Rejected changes ---
    lines.append("## Rejected changes")
    rejected = store.get_experiments(decision="REJECT")
    if rejected:
        for exp in rejected:
            lines.append(f"- **{exp.get('prediction_type', '')}**: {exp.get('proposed_change', '')}")
            lines.append(f"  - Metric: {exp.get('previous_metric', 'N/A')} → {exp.get('new_metric', 'N/A')}")
            lines.append(f"  - Reason: backtest did not show improvement")
    else:
        lines.append("No changes rejected yet.")
    lines.append("")

    # --- Data problems discovered ---
    lines.append("## Data problems discovered")
    problem_lines = _generate_data_problems(collector_results, store)
    lines.extend(problem_lines)
    lines.append("")

    # --- Next highest-value action ---
    lines.append("## Next highest-value action")
    next_action = _determine_next_action(store, eval_report)
    lines.append(next_action)
    lines.append("")

    return "\n".join(lines)


def _generate_learned_section(
    store,
    collector_results: dict[str, dict],
    matured_today: int,
) -> list[str]:
    """Generate the 'What we learned today' section."""
    lines: list[str] = []

    if matured_today == 0:
        lines.append("No new mature outcomes today. The system is in data collection phase.")
        lines.append("")

    # Report collector findings
    for ptype in _PREDICTION_TYPES:
        result = collector_results.get(ptype, {})
        recorded = result.get("recorded", 0)
        invalidated = result.get("invalidated", 0)
        errors = result.get("errors", [])

        if recorded > 0:
            lines.append(f"- **{ptype}**: {recorded} new outcome(s) recorded.")

        if invalidated > 0:
            lines.append(f"- **{ptype}**: {invalidated} prediction(s) invalidated.")

        if errors:
            for err in errors[:3]:
                lines.append(f"- **{ptype}** error: {err}")

    # Report invalid predictions
    all_mature = store.get_mature_predictions(valid_only=False)
    invalid_mature = [p for p in all_mature if p.get("valid_for_training") in (0, "0", False)]
    if invalid_mature:
        new_invalid = [
            p for p in invalid_mature
            if (p.get("actual_recorded_at") or "").startswith(_today_str())
        ]
        if new_invalid:
            lines.append(f"- {len(new_invalid)} prediction(s) marked invalid today.")
            for p in new_invalid[:5]:
                reason = p.get("invalid_reason", "unknown")
                ptype = p.get("prediction_type", "unknown")
                lines.append(f"  - {ptype}: {reason}")

    if matured_today == 0 and not any(
        collector_results.get(pt, {}).get("recorded", 0) > 0
        for pt in _PREDICTION_TYPES
    ):
        # Check if there were any errors
        any_errors = any(
            collector_results.get(pt, {}).get("errors")
            for pt in _PREDICTION_TYPES
        )
        if not any_errors:
            lines.append("- Nothing new was learned today — no new data collected.")

    return lines


def _generate_data_problems(collector_results: dict[str, dict], store) -> list[str]:
    """Generate the 'Data problems discovered' section."""
    lines: list[str] = []

    for ptype in _PREDICTION_TYPES:
        result = collector_results.get(ptype, {})
        errors = result.get("errors", [])
        if errors:
            lines.append(f"- **{ptype}**: {len(errors)} error(s) during collection:")
            for err in errors[:3]:
                lines.append(f"  - {err}")

        # Check for invalid outcomes
        all_mature = store.get_mature_predictions(valid_only=False)
        invalid = [p for p in all_mature if p.get("prediction_type") == ptype and p.get("valid_for_training") in (0, "0", False)]
        if invalid:
            lines.append(f"- **{ptype}**: {len(invalid)} invalid prediction(s) in store.")

    if not lines:
        lines.append("No data problems discovered today.")

    return lines


def _determine_next_action(store, eval_report: dict) -> str:
    """Determine the single most impactful next action."""
    # Check task outcome predictions
    task_preds = store.get_all_predictions(prediction_type="task_outcome", valid_only=None)
    n_task = len(task_preds)

    if n_task == 0:
        return "Wire batch_runner integration to auto-predict before each task"

    if n_task < 10:
        return (f"Accumulate more task predictions — current sample too small "
                f"for reliable metrics ({n_task}/10)")

    # Check video engagement
    video_preds = store.get_all_predictions(prediction_type="video_engagement", valid_only=None)
    video_mature = store.get_mature_predictions(prediction_type="video_engagement", valid_only=False)
    if video_preds and not video_mature:
        return "Publish a video and create a prediction before publishing to start collecting real video outcomes"

    # Check skill safety
    skill_preds = store.get_all_predictions(prediction_type="skill_safety", valid_only=None)
    if not skill_preds:
        return "Run skill promotion workflow to generate new skill safety predictions"

    # Check MIKS campaigns
    miks_preds = store.get_all_predictions(prediction_type="miks_campaign", valid_only=None)
    if not miks_preds:
        return "Create MIKS campaign predictions when campaigns are launched"

    # If we have data across types, focus on the weakest performing area
    by_type = eval_report.get("by_type", {})
    weakest_type = None
    weakest_metric = 1.0

    for ptype in _PREDICTION_TYPES:
        stats = by_type.get(ptype, {})
        n = stats.get("n", 0)
        if n < _MIN_SAMPLE:
            continue

        if ptype == "task_outcome":
            acc = stats.get("verdict_accuracy", 0.0)
        elif ptype == "video_engagement":
            acc = stats.get("directional_accuracy", 0.0)
        elif ptype == "skill_safety":
            acc = stats.get("risk_accuracy", 0.0)
        elif ptype == "miks_campaign":
            # Lower MAPE = better, convert to "accuracy" proxy
            mape = stats.get("views_mape", 100.0)
            acc = max(0.0, 1.0 - mape / 100.0)
        else:
            continue

        if acc < weakest_metric:
            weakest_metric = acc
            weakest_type = ptype

    if weakest_type:
        return (f"Improve {weakest_type} predictions — current accuracy "
                f"is {weakest_metric * 100:.1f}%, the weakest performing type with "
                f"sufficient data")

    return "Continue collecting data — all prediction types need more mature outcomes"


# ---------------------------------------------------------------------------
# Migration — old experiences table
# ---------------------------------------------------------------------------

def migrate_old_experiences(store) -> dict:
    """Migrate old experiences from ledgerbook.db into the prediction store.

    Reads the ``experiences`` table from ``S:/AGI_like/memory/ledgerbook.db``
    and creates a prediction in the PredictionStore for each old prediction.
    ALL migrated predictions are marked as invalid for training because they
    may contain circular or fabricated actuals.

    Returns
    -------
    dict
        Summary: ``{"migrated": int, "invalidated": int}``
    """
    summary = {"migrated": 0, "invalidated": 0}

    if not os.path.isfile(_LEDGERBOOK_DB):
        sys.stderr.write(
            f"migrate_old_experiences: ledgerbook.db not found at {_LEDGERBOOK_DB}\n"
        )
        return summary

    try:
        conn = sqlite3.connect(_LEDGERBOOK_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM experiences ORDER BY id"
        ).fetchall()
        conn.close()
    except Exception as exc:
        sys.stderr.write(f"migrate_old_experiences: failed to read experiences: {exc}\n")
        return summary

    code_commit = _git_head()
    now_iso = _now_iso()
    invalid_reason = (
        "migrated from old experiences table — may contain circular/fabricated actuals"
    )

    for row in rows:
        try:
            row_dict = dict(row)
            task_id = row_dict.get("task_id")
            context = row_dict.get("context", "")
            action_raw = row_dict.get("action", "")
            outcome_raw = row_dict.get("outcome", "")
            worked = row_dict.get("worked")

            # Parse the structured action JSON to extract prediction type
            action_parsed = _parse_json(action_raw)
            outcome_parsed = _parse_json(outcome_raw)

            # The old experiences stored predictions as:
            #   action  = {"type": "prediction", "prediction": {...}}
            #   outcome = {"type": "outcome",   "outcome":  {...}, "error": {...}}
            old_prediction = action_parsed.get("prediction", {})
            old_outcome = outcome_parsed.get("outcome", {})
            old_error = outcome_parsed.get("error", {})

            # Determine the prediction_type from the stored domain
            domain = old_prediction.get("domain", "task_outcome")
            if domain not in _PREDICTION_TYPES:
                domain = "task_outcome"  # fallback

            # Build a prediction payload from the old data
            prediction_payload = dict(old_prediction)
            prediction_payload["source"] = "migrated_experiences"
            prediction_payload["original_context"] = context

            # Build the actual from the old outcome
            actual = dict(old_outcome)
            actual["source"] = "old_experiences_table"
            actual["worked"] = worked

            # Build input features from the old prediction's features
            old_features = old_prediction.get("features", {})
            input_features = {
                "task_id": task_id,
                "context": context,
                "domain": domain,
                **old_features,  # merge in the original features
            }

            # Determine target
            if task_id is not None:
                target = str(task_id)
            elif domain == "video_engagement":
                target = context  # video prediction context
            elif domain == "skill_safety":
                target = context  # skill prediction context
            else:
                target = f"migrated_{row_dict.get('id', 'unknown')}"

            # Use a past due date so it's immediately "mature"
            outcome_due_at = now_iso

            # Use the confidence from the old prediction, or default to low
            confidence = old_prediction.get("confidence", "low")
            if confidence not in ("low", "medium", "high"):
                confidence = "low"

            # Use the old model version if available, else migrated_v0
            model_version = old_prediction.get("model_version", "migrated_v0")
            if not model_version:
                model_version = "migrated_v0"

            prediction_id = store.create_prediction(
                prediction_type=domain,
                target=target,
                prediction=prediction_payload,
                confidence=confidence,
                input_features=input_features,
                model_version=model_version,
                outcome_due_at=outcome_due_at,
                code_commit=code_commit,
            )

            # Record the outcome immediately (it's historical data)
            try:
                store.record_outcome(
                    prediction_id=prediction_id,
                    actual=actual,
                    actual_source="old_experiences_table",
                    error=old_error if old_error else None,
                )
            except (ValueError, Exception):
                # May fail if timing check trips; that's OK for migrated data
                pass

            # Mark as invalid for training
            store.invalidate_prediction(prediction_id, reason=invalid_reason)

            summary["migrated"] += 1
            summary["invalidated"] += 1

        except Exception as exc:
            sys.stderr.write(
                f"migrate_old_experiences: failed to migrate experience "
                f"id={row_dict.get('id', '?')}: {exc}\n"
            )

    return summary


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_daily() -> str:
    """Run the full daily loop and return the report text.

    Steps:
    1. Initialise store
    2. Register model versions
    3. Run all collectors
    4. Evaluate mature predictions
    5. Diagnose systematic errors
    6. Generate and save the daily report
    """
    print(f"[{_now_iso()}] Prediction Machine — daily loop starting")
    print()

    # Step 1: Initialise store
    store = init_store()
    print(f"[{_now_iso()}] Store initialised: {store.db_path}")

    # Step 2: Register model versions
    register_model_versions(store)
    print(f"[{_now_iso()}] Model versions registered")

    # Step 3: Run collectors
    print(f"[{_now_iso()}] Running collectors...")
    collector_results = run_all_collectors(store)
    for ptype, result in collector_results.items():
        recorded = result.get("recorded", 0)
        checked = result.get("checked", 0)
        skipped = result.get("skipped", 0)
        print(f"  {ptype}: checked={checked} recorded={recorded} skipped={skipped}")

    # Step 4: Evaluate
    print(f"[{_now_iso()}] Evaluating mature predictions...")
    eval_report = evaluate_all(store)
    n_mature = eval_report.get("total_predictions", 0)
    n_valid = eval_report.get("valid_predictions", 0)
    print(f"  Total mature: {n_mature}, valid: {n_valid}")

    # Step 5: Diagnose
    print(f"[{_now_iso()}] Diagnosing systematic errors...")
    diagnosis = diagnose(store, eval_report)
    n_misses = len(diagnosis.get("large_misses", []))
    print(f"  Large misses (>50% error): {n_misses}")

    # Step 6: Generate report
    print(f"[{_now_iso()}] Generating daily report...")
    report = generate_report(store, collector_results, eval_report, diagnosis)

    # Save to file
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(_REPORTS_DIR, f"{_today_str()}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[{_now_iso()}] Report saved to {report_path}")

    # Print to stdout for cron delivery
    print()
    print("=" * 70)
    print(report)
    print("=" * 70)

    return report


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        run_daily()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)