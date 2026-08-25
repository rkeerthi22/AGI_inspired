"""Prediction and simulation layer for the Cognitive AI Harness.

This module implements the "predict, act, measure, learn" loop that turns the
harness from a reactive system (act -> learn) into a cognitive one (predict ->
act -> measure -> learn). It is the mechanism HARNESS_DESIGN.md section 5
deferred for M1 and names as the M3 trigger: "decision simulation — explicit
spreadsheet-style models with uncertainty ranges, evaluated by the manager."

THREE PREDICTION DOMAINS (each with its own model):

  1. Task outcome prediction (from ledger.db)
     Given a task spec, mission_id, and model, predict:
       - Will the critic verdict be pass or fail?
       - How many tokens will it cost?
     Training data: every task in the ledger with a terminal status.
     Features: mission_id, seed_number, week, prior_attempts, model_used.

  2. Video engagement prediction (from Vaibhav dataset)
     Given video features (type, hook formula, duration, upload day, title
     pattern), predict:
       - Expected views at 7 days
       - Expected engagement rate (likes/views)
     Training data: 17 videos with known views, likes, and metadata.
     Features: content_type, hook_formula, duration_min, upload_dayofweek.

  3. Skill safety prediction (from ledger.db + skills_analyst/)
     Given a skill note's content, predict:
       - Will canaries regress if this skill is promoted?
     Training data: the 2 approved skills with canary baselines + history.
     Features: note length, mission_id, evidence_count, baseline_green.

PREDICTION HONESTY DISCIPLINE (the whole point):

  Every prediction is recorded BEFORE the action, with a timestamp, in the
  experiences table. After the outcome is known, the prediction error is
  computed and stored. The prediction-error history is itself a measurable
  signal: if it is not shrinking over time, the model is not learning.

  This is not a neural network. It is simple statistical regression over
  structured data — mean/median by category, weighted by sample size, with
  uncertainty ranges (min-max, IQR). The point is the closed loop, not the
  model sophistication.

USAGE:

  python orchestrator/simulate.py predict-task <mission_id> <seed_text>
  python orchestrator/simulate.py predict-video <type> <hook> <duration> <day>
  python orchestrator/simulate.py predict-skill <mission_id> <note_length> <evidence_count>
  python orchestrator/simulate.py record <experience_id> <actual_outcome>
  python orchestrator/simulate.py accuracy
  python orchestrator/simulate.py report

Stdlib only — no numpy, no sklearn, no frameworks. Same discipline as the
rest of the orchestrator (HARNESS_DESIGN.md §1.3)."""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DB = ROOT / "ledger" / "ledger.db"
LEDGERBOOK_DB = ROOT / "memory" / "ledgerbook.db"
#: The "AI videos" project was restructured into numbered folders on 2026-07-31 and
#: this dataset moved from the project root into 07_analysis/. That silently emptied
#: load_vaibhav_dataset(), so every video-engagement prediction returned null while
#: sim_gate.py masked it behind a heuristic. Check both layouts, newest first.
_VAIBHAV_CANDIDATES = [
    Path("S:/AI videos/07_analysis/vaibhav_video_dataset.json"),
    Path("S:/AI videos/vaibhav_video_dataset.json"),
]
VAIBHAV_DATASET = next((p for p in _VAIBHAV_CANDIDATES if p.exists()),
                       _VAIBHAV_CANDIDATES[0])


# ── data access ────────────────────────────────────────────────────────────────

def _ledger_conn():
    c = sqlite3.connect(LEDGER_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _ledgerbook_conn():
    c = sqlite3.connect(LEDGERBOOK_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def load_vaibhav_dataset() -> list[dict]:
    """Load the Vaibhav video dataset from JSON (built from transcript analysis).
    Returns [] if the file doesn't exist (e.g., running on a different machine)."""
    if not VAIBHAV_DATASET.exists():
        return []
    with open(VAIBHAV_DATASET, "r", encoding="utf-8") as f:
        return json.loads(f.read())


# ── model 1: task outcome prediction ───────────────────────────────────────────

def _task_features(spec: str, mission_id: str) -> dict:
    """Extract features from a task spec that the model can use."""
    # Extract seed number from spec like "[2026-W29][seed 1] ..."
    seed_match = re.search(r"seed (\d+)", spec)
    seed_num = int(seed_match.group(1)) if seed_match else 0
    # Extract week
    week_match = re.search(r"\[(\d{4}-W\d{2})\]", spec)
    week = week_match.group(1) if week_match else ""
    # Is synthesis?
    is_synthesis = bool(re.search(r"synthesi[sz]", spec, re.I))
    # Spec length (longer specs = more complex tasks)
    spec_len = len(spec)
    return {
        "mission_id": mission_id,
        "seed_num": seed_num,
        "week": week,
        "is_synthesis": is_synthesis,
        "spec_len": spec_len,
    }


def _historical_tasks(mission_id: str | None = None) -> list[dict]:
    """Load terminal tasks from the ledger as training data."""
    with _ledger_conn() as c:
        if mission_id:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status IN ('done','failed','infra_failed') "
                "AND mission_id != 'canaries' AND mission_id = ? ORDER BY task_id",
                (mission_id,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status IN ('done','failed','infra_failed') "
                "AND mission_id != 'canaries' ORDER BY task_id").fetchall()
    return [dict(r) for r in rows]


def predict_task_outcome(spec: str, mission_id: str) -> dict:
    """Predict whether a task will pass critic review and how many tokens it will cost.

    Uses historical data from the same mission. If no history exists for that
    mission, falls back to all missions. The prediction includes:
      - pass_probability: fraction of similar tasks that passed
      - predicted_tokens: median token cost of similar tasks
      - token_range: (min, max) from historical data
      - confidence: 'high' if >=5 similar tasks, 'medium' if >=2, 'low' otherwise

    This is deliberately simple — mean/median by category, not regression.
    The point is the closed loop (predict -> measure -> learn), not model
    sophistication. A simple model with honest error measurement beats a
    complex model that nobody checks.
    """
    features = _task_features(spec, mission_id)
    tasks = _historical_tasks(mission_id)

    # If mission has no history, use all tasks
    if len(tasks) < 2:
        tasks = _historical_tasks(None)

    # Filter to similar tasks (same mission, or synthesis vs non-synthesis)
    similar = [t for t in tasks
               if t.get("mission_id") == mission_id
               or features["is_synthesis"] == bool(
                   re.search(r"synthesi[sz]", t.get("spec", ""), re.I))]

    if not similar:
        similar = tasks

    # Pass/fail prediction
    passed = [t for t in similar if t["status"] == "done"
              and t.get("critic_verdict") == "pass"]
    failed = [t for t in similar if t["status"] == "failed"
              and t.get("critic_verdict") == "fail"]
    n_total = len(passed) + len(failed)
    pass_probability = len(passed) / n_total if n_total > 0 else 0.5

    # Token cost prediction
    token_costs = [(t["tokens_in"] or 0) + (t["tokens_out"] or 0)
                   for t in similar
                   if (t["tokens_in"] or 0) + (t["tokens_out"] or 0) > 0]
    if token_costs:
        predicted_tokens = int(median(token_costs))
        token_min = min(token_costs)
        token_max = max(token_costs)
    else:
        predicted_tokens = 0
        token_min = 0
        token_max = 0

    # Confidence
    n_similar = len(similar)
    if n_similar >= 5:
        confidence = "high"
    elif n_similar >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "domain": "task_outcome",
        "features": features,
        "pass_probability": round(pass_probability, 3),
        "predicted_verdict": "pass" if pass_probability >= 0.5 else "fail",
        "predicted_tokens": predicted_tokens,
        "token_range": [token_min, token_max],
        "n_training_examples": n_similar,
        "confidence": confidence,
        "model": "historical_median_by_mission",
    }


# ── model 2: video engagement prediction ───────────────────────────────────────

# Day-of-week mapping (0=Monday, 6=Sunday)
_DOW_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday"]


def _video_features(content_type: str, hook_formula: str,
                     duration_min: float, upload_day: str) -> dict:
    day_lower = upload_day.lower().strip()
    dow = None
    for i, name in enumerate(_DOW_NAMES):
        if name in day_lower:
            dow = i
            break
    return {
        "content_type": content_type.lower().strip(),
        "hook_formula": hook_formula.lower().strip(),
        "duration_min": float(duration_min),
        "upload_dow": dow if dow is not None else -1,
    }


def predict_video_engagement(content_type: str, hook_formula: str,
                              duration_min: float, upload_day: str) -> dict:
    """Predict video views at 7 days and engagement rate.

    Uses the Vaibhav Sisinty dataset (17 videos with known outcomes).
    Prediction is by category mean with uncertainty ranges.

    Features:
      - content_type: roundup, tutorial, opinion, comparison, list, deep_dive,
        breaking_news, money
      - hook_formula: shocking_fact, contrarian_truth, relatable_problem,
        secret_reveal, timeline_event
      - duration_min: video duration in minutes
      - upload_day: day of week (monday, tuesday, etc.)

    Returns predicted_views, view_range, predicted_engagement_rate, confidence.
    """
    dataset = load_vaibhav_dataset()
    if not dataset:
        return {
            "domain": "video_engagement",
            "error": "Vaibhav dataset not found — cannot predict without training data",
            "predicted_views": None,
            "confidence": "none",
        }

    features = _video_features(content_type, hook_formula, duration_min, upload_day)

    # Filter by content type first (strongest predictor)
    by_type = [v for v in dataset if v["type"] == features["content_type"]]
    if not by_type:
        # Fall back to all videos
        by_type = dataset

    # Further filter by hook formula if we have enough
    by_hook = [v for v in by_type if v.get("hook_formula") == features["hook_formula"]]
    if len(by_hook) >= 3:
        training = by_hook
    else:
        training = by_type

    # Views prediction
    views = [v["views"] for v in training if v.get("views")]
    predicted_views = int(median(views)) if views else 0
    view_min = min(views) if views else 0
    view_max = max(views) if views else 0

    # Engagement rate prediction (likes/views)
    engagement_rates = []
    for v in training:
        if v.get("views") and v.get("likes") and v["views"] > 0:
            engagement_rates.append(v["likes"] / v["views"])
    if engagement_rates:
        predicted_engagement = round(median(engagement_rates), 4)
        eng_min = round(min(engagement_rates), 4)
        eng_max = round(max(engagement_rates), 4)
    else:
        predicted_engagement = 0.05  # 5% fallback
        eng_min = 0.02
        eng_max = 0.10

    # Duration adjustment: shorter videos (< 15 min) tend to get more views
    duration_adj = 1.0
    if features["duration_min"] < 15 and features["duration_min"] > 0:
        duration_adj = 1.15  # 15% boost for short videos
    elif features["duration_min"] > 30:
        duration_adj = 0.85  # 15% penalty for long videos

    adjusted_views = int(predicted_views * duration_adj)

    n_training = len(training)
    if n_training >= 5:
        confidence = "high"
    elif n_training >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "domain": "video_engagement",
        "features": features,
        "predicted_views_7d": adjusted_views,
        "view_range": [view_min, view_max],
        "predicted_engagement_rate": predicted_engagement,
        "engagement_range": [eng_min, eng_max],
        "n_training_examples": n_training,
        "confidence": confidence,
        "model": "category_median_with_duration_adjustment",
    }


# ── model 3: skill safety prediction ───────────────────────────────────────────

def predict_skill_safety(mission_id: str, note_length: int,
                          evidence_count: int) -> dict:
    """Predict whether promoting a skill will cause canary regression.

    .. deprecated::
        This v1 model hardcodes ``"regressed": False`` for all skills, ignoring
        the real rollback at commit c7b5721. Use
        ``prediction_machine.predictors.skill_safety.predictor.SkillSafetyPredictor``
        (v2) instead, which scans git history for actual rollbacks.

    Uses the 2 approved skills with canary baselines as training data.
    This is a very small dataset — the prediction is deliberately conservative.

    Features:
      - mission_id: which mission the skill applies to
      - note_length: character count of the skill note
      - evidence_count: number of lesson rows supporting it
      - baseline_green: canary green count at approval time

    Returns a risk assessment, not a binary prediction.
    """
    # Known skills (from skills_analyst/)
    skills_dir = ROOT / "skills_analyst"
    known_skills = []
    if skills_dir.exists():
        for p in sorted(skills_dir.glob("*/*.md")):
            if p.parent.name in ("_candidates", "_rejected"):
                continue
            text = p.read_text(encoding="utf-8")
            base_match = re.search(r"canary_baseline:\s*(\d+)", text)
            ev_match = re.search(r"evidence_lesson_ids:\s*\[([\d,\s]*)\]", text)
            body = re.sub(r"^---.*?---\s*", "", text, flags=re.S).strip()
            known_skills.append({
                "mission": p.parent.name,
                "note_length": len(body),
                "evidence_count": len(re.findall(r"\d+", ev_match.group(1))) if ev_match else 0,
                "canary_baseline": int(base_match.group(1)) if base_match else 0,
                "regressed": False,  # DEPRECATED: hardcoded — v2 uses git scan. See commit c7b5721
            })

    # Risk factors
    risk_score = 0
    risk_factors = []

    # Short notes are less likely to cause regression (less surface area)
    if note_length > 500:
        risk_score += 1
        risk_factors.append("note is long (>500 chars) — more injection surface")

    # More evidence = safer (the skill is well-supported)
    if evidence_count < 2:
        risk_score += 2
        risk_factors.append("low evidence count (<2) — skill may not generalize")
    elif evidence_count >= 3:
        risk_factors.append("strong evidence base (>=3 lessons)")

    # Mission with more tasks has more canary exposure
    with _ledger_conn() as c:
        task_count = c.execute(
            "SELECT count(*) FROM tasks WHERE mission_id=? AND status='done'",
            (mission_id,)).fetchone()[0]
    if task_count > 10:
        risk_score += 1
        risk_factors.append(f"high mission activity ({task_count} done tasks) — more canary exposure")

    risk_level = "low" if risk_score <= 1 else "medium" if risk_score <= 3 else "high"

    return {
        "domain": "skill_safety",
        "features": {
            "mission_id": mission_id,
            "note_length": note_length,
            "evidence_count": evidence_count,
        },
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_factors": risk_factors,
        "n_known_skills": len(known_skills),
        "known_skill_details": [{"mission": s["mission"], "evidence": s["evidence_count"],
                                  "baseline": s["canary_baseline"]}
                                 for s in known_skills],
        "model": "heuristic_risk_scoring",
    }


# ── prediction recording (the closed loop) ─────────────────────────────────────

def record_prediction(prediction: dict, context: str) -> int:
    """Record a prediction in the experiences table BEFORE the action.

    This is the 'predict' step. The prediction is stored with:
      - context: what action this prediction is for
      - prediction: the full prediction dict (JSON)
      - timestamp: when the prediction was made

    Returns the experience row ID (used later to record the actual outcome).
    """
    with _ledgerbook_conn() as c:
        cur = c.execute(
            "INSERT INTO experiences (task_id, context, action, outcome, worked) "
            "VALUES (NULL, ?, ?, ?, ?)",
            (context,
             json.dumps({"type": "prediction", "prediction": prediction}),
             "pending", 0))
        return cur.lastrowid


def record_outcome(experience_id: int, actual_outcome: dict) -> dict:
    """Record the actual outcome and compute prediction error.

    This is the 'measure' step. It:
      1. Reads the prediction from the experiences table
      2. Computes the error between predicted and actual
      3. Updates the row with the actual outcome and error
      4. Returns a summary of the prediction accuracy

    The error computation depends on the prediction domain:
      - task_outcome: verdict match (1/0) + token error percentage
      - video_engagement: view error percentage
      - skill_safety: risk level match (1/0)
    """
    with _ledgerbook_conn() as c:
        row = c.execute(
            "SELECT * FROM experiences WHERE id=?", (experience_id,)).fetchone()
        if not row:
            return {"error": f"experience #{experience_id} not found"}

        pred_data = json.loads(row["action"])
        prediction = pred_data.get("prediction", {})
        domain = prediction.get("domain", "unknown")

        # Compute error
        error = {}
        if domain == "task_outcome":
            pred_verdict = prediction.get("predicted_verdict", "")
            actual_verdict = actual_outcome.get("verdict", "")
            pred_tokens = prediction.get("predicted_tokens", 0)
            actual_tokens = actual_outcome.get("tokens", 0)
            error = {
                "verdict_correct": pred_verdict == actual_verdict,
                "token_error_pct": round(abs(pred_tokens - actual_tokens)
                                         / max(actual_tokens, 1) * 100, 1)
                    if actual_tokens > 0 else None,
                "predicted_tokens": pred_tokens,
                "actual_tokens": actual_tokens,
            }
        elif domain == "video_engagement":
            pred_views = prediction.get("predicted_views_7d", 0)
            actual_views = actual_outcome.get("views", 0)
            error = {
                "view_error_pct": round(abs(pred_views - actual_views)
                                        / max(actual_views, 1) * 100, 1)
                    if actual_views > 0 else None,
                "predicted_views": pred_views,
                "actual_views": actual_views,
            }
        elif domain == "skill_safety":
            pred_risk = prediction.get("risk_level", "unknown")
            actual_regressed = actual_outcome.get("regressed", False)
            error = {
                "risk_correct": (pred_risk == "high" and actual_regressed) or
                                (pred_risk == "low" and not actual_regressed),
                "predicted_risk": pred_risk,
                "actual_regressed": actual_regressed,
            }

        # Update the row
        c.execute(
            "UPDATE experiences SET outcome=?, worked=? WHERE id=?",
            (json.dumps({"type": "outcome", "outcome": actual_outcome,
                         "error": error}),
             1 if error.get("verdict_correct") or error.get("risk_correct")
             or (error.get("view_error_pct") is not None and error["view_error_pct"] < 50)
             else 0,
             experience_id))

    return {"experience_id": experience_id, "error": error}


# ── prediction accuracy reporting ──────────────────────────────────────────────

def prediction_accuracy() -> dict:
    """Compute aggregate prediction accuracy from the experiences table.

    Returns accuracy by domain and overall. This is the signal that tells
    whether the prediction models are improving over time.
    """
    with _ledgerbook_conn() as c:
        rows = c.execute(
            "SELECT * FROM experiences WHERE outcome != 'pending' "
            "ORDER BY id").fetchall()

    if not rows:
        return {"total_predictions": 0, "note": "no completed predictions yet"}

    by_domain = {}
    for r in rows:
        pred_data = json.loads(r["action"])
        prediction = pred_data.get("prediction", {})
        domain = prediction.get("domain", "unknown")
        outcome_data = json.loads(r["outcome"])
        error = outcome_data.get("error", {})

        by_domain.setdefault(domain, {"correct": 0, "total": 0, "errors": []})

        if domain == "task_outcome":
            if error.get("verdict_correct") is not None:
                by_domain[domain]["total"] += 1
                if error["verdict_correct"]:
                    by_domain[domain]["correct"] += 1
            if error.get("token_error_pct") is not None:
                by_domain[domain]["errors"].append(error["token_error_pct"])
        elif domain == "video_engagement":
            if error.get("view_error_pct") is not None:
                by_domain[domain]["total"] += 1
                by_domain[domain]["errors"].append(error["view_error_pct"])
        elif domain == "skill_safety":
            if error.get("risk_correct") is not None:
                by_domain[domain]["total"] += 1
                if error["risk_correct"]:
                    by_domain[domain]["correct"] += 1

    summary = {}
    for domain, data in by_domain.items():
        errors = data["errors"]
        summary[domain] = {
            "accuracy": round(data["correct"] / data["total"], 3)
                if data["total"] > 0 else None,
            "n_predictions": data["total"],
            "n_correct": data["correct"],
            "mean_error_pct": round(mean(errors), 1) if errors else None,
            "min_error_pct": min(errors) if errors else None,
            "max_error_pct": max(errors) if errors else None,
        }

    total_correct = sum(d["correct"] for d in by_domain.values())
    total_n = sum(d["total"] for d in by_domain.values())
    summary["overall"] = {
        "accuracy": round(total_correct / total_n, 3) if total_n > 0 else None,
        "n_predictions": total_n,
    }
    return summary


# ── legacy helpers ─────────────────────────────────────────────────────────────

def _legacy_experiences() -> list[dict]:
    """Return experiences from the old ledgerbook.db (for backward compat in reports)."""
    try:
        with _ledgerbook_conn() as c:
            rows = c.execute(
                "SELECT id, context, action, outcome, worked FROM experiences "
                "ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _git_rollback_count() -> int:
    """Count actual skill rollbacks from git history (fixes the hardcoded 0 bug)."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--all", "--oneline",
             "--grep=Rollback skill"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return len([l for l in result.stdout.strip().split("\n") if l.strip()])
    except Exception:
        pass
    # Known rollback at c7b5721 always counts
    return 1


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Prediction and simulation layer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("predict-task", help="Predict task outcome")
    pt.add_argument("mission_id")
    pt.add_argument("spec", help="Task spec text")

    pv = sub.add_parser("predict-video", help="Predict video engagement")
    pv.add_argument("content_type", help="roundup/tutorial/opinion/comparison/list/deep_dive")
    pv.add_argument("hook_formula", help="shocking_fact/contrarian_truth/relatable_problem/secret_reveal/timeline_event")
    pv.add_argument("duration_min", type=float)
    pv.add_argument("upload_day", help="monday/tuesday/etc.")

    ps = sub.add_parser("predict-skill", help="Predict skill promotion safety")
    ps.add_argument("mission_id")
    ps.add_argument("note_length", type=int)
    ps.add_argument("evidence_count", type=int)

    rec = sub.add_parser("record", help="Record actual outcome for a prediction")
    rec.add_argument("experience_id", type=int)
    rec.add_argument("outcome_json", help="JSON string of actual outcome")

    sub.add_parser("accuracy", help="Show prediction accuracy report")
    sub.add_parser("report", help="Full simulation report")

    args = ap.parse_args()

    if args.cmd == "predict-task":
        # Delegate to prediction_machine v1 predictor (the live system)
        try:
            sys.path.insert(0, str(ROOT))
            from prediction_machine.predictors.task_outcome.predictor import TaskOutcomePredictor
            from prediction_machine.core.prediction_store import PredictionStore
            predictor = TaskOutcomePredictor()
            store = PredictionStore()
            result = predictor.predict(args.spec, args.mission_id)
            pred_id = store.create_prediction(
                prediction_type="task_outcome",
                target="cli_manual",
                prediction=result,
                confidence=result.get("confidence", "low"),
                input_features={"mission_id": args.mission_id, "spec": args.spec},
                model_version=result.get("model_version", "task_outcome_v1"),
                outcome_due_at=datetime.now().isoformat(timespec="seconds"),
                code_commit=None,
            )
            print(json.dumps(result, indent=2))
            print(f"\n[PREDICTION RECORDED] prediction_id={pred_id}")
            print(f"  Stored in prediction_machine/data/predictions.db")
        except Exception as e:
            # Fallback to old model if prediction_machine is unavailable
            print(f"[WARNING] prediction_machine unavailable ({e}), using legacy model")
            result = predict_task_outcome(args.spec, args.mission_id)
            print(json.dumps(result, indent=2))
            eid = record_prediction(result, f"task prediction for {args.mission_id}")
            print(f"\n[LEGACY PREDICTION RECORDED] experience_id={eid}")

    elif args.cmd == "predict-video":
        try:
            sys.path.insert(0, str(ROOT))
            from prediction_machine.predictors.video_engagement.predictor import VideoEngagementPredictor
            from prediction_machine.core.prediction_store import PredictionStore
            predictor = VideoEngagementPredictor()
            store = PredictionStore()
            result = predictor.predict(args.content_type, args.hook_formula,
                                        args.duration_min, args.upload_day)
            pred_id = store.create_prediction(
                prediction_type="video_engagement",
                target="cli_manual",
                prediction=result,
                confidence=result.get("confidence", "low"),
                input_features={"content_type": args.content_type,
                                "hook_formula": args.hook_formula,
                                "duration_min": args.duration_min,
                                "upload_day": args.upload_day},
                model_version=result.get("model_version", "video_engagement_v1"),
                outcome_due_at=datetime.now().isoformat(timespec="seconds"),
                code_commit=None,
            )
            print(json.dumps(result, indent=2))
            print(f"\n[PREDICTION RECORDED] prediction_id={pred_id}")
        except Exception as e:
            print(f"[WARNING] prediction_machine unavailable ({e}), using legacy model")
            result = predict_video_engagement(args.content_type, args.hook_formula,
                                                args.duration_min, args.upload_day)
            print(json.dumps(result, indent=2))

    elif args.cmd == "predict-skill":
        # Delegate to v2 predictor (fixes the hardcoded regressed=False bug)
        try:
            sys.path.insert(0, str(ROOT))
            from prediction_machine.predictors.skill_safety.predictor import SkillSafetyPredictor
            from prediction_machine.core.prediction_store import PredictionStore
            predictor = SkillSafetyPredictor()
            store = PredictionStore()
            result = predictor.predict(args.mission_id, args.note_length,
                                         args.evidence_count)
            pred_id = store.create_prediction(
                prediction_type="skill_safety",
                target="cli_manual",
                prediction=result,
                confidence=result.get("confidence", "low"),
                input_features={"mission_id": args.mission_id,
                                "note_length": args.note_length,
                                "evidence_count": args.evidence_count},
                model_version=result.get("model_version", "skill_safety_v2"),
                outcome_due_at=datetime.now().isoformat(timespec="seconds"),
                code_commit=None,
            )
            print(json.dumps(result, indent=2))
            print(f"\n[PREDICTION RECORDED] prediction_id={pred_id}")
            print(f"  Uses skill_safety_v2 (includes real rollback data from git)")
        except Exception as e:
            print(f"[WARNING] prediction_machine unavailable ({e}), using legacy model")
            print(f"[DEPRECATED] Legacy skill_safety model hardcodes regressed=False — use v2 instead")
            result = predict_skill_safety(args.mission_id, args.note_length,
                                          args.evidence_count)
            print(json.dumps(result, indent=2))

    elif args.cmd == "record":
        outcome = json.loads(args.outcome_json)
        result = record_outcome(args.experience_id, outcome)
        print(json.dumps(result, indent=2))

    elif args.cmd == "accuracy":
        # Try prediction_machine first, fall back to legacy
        try:
            sys.path.insert(0, str(ROOT))
            from prediction_machine.core.prediction_store import PredictionStore
            store = PredictionStore()
            all_preds = store.get_all_predictions(valid_only=None)
            completed = [p for p in all_preds if p.get("actual") is not None]
            by_type = {}
            for p in all_preds:
                ptype = p.get("prediction_type", "unknown")
                by_type.setdefault(ptype, {"total": 0, "completed": 0, "errors": []})
                by_type[ptype]["total"] += 1
                if p.get("actual") is not None:
                    by_type[ptype]["completed"] += 1
                    err = p.get("error")
                    if isinstance(err, str):
                        import json as _json
                        try: err = _json.loads(err)
                        except: pass
                    if isinstance(err, dict):
                        for k, v in err.items():
                            if isinstance(v, (int, float)) and "pct" in k:
                                by_type[ptype]["errors"].append(v)
            summary = {"total_predictions": len(all_preds),
                        "completed": len(completed),
                        "by_type": {}}
            for ptype, data in by_type.items():
                summary["by_type"][ptype] = {
                    "total": data["total"],
                    "completed": data["completed"],
                    "mean_error_pct": round(mean(data["errors"]), 1) if data["errors"] else None,
                }
            print(json.dumps(summary, indent=2))
            if completed:
                print(f"\n  Data source: prediction_machine/data/predictions.db")
            else:
                print(f"\n  No completed predictions yet in prediction_machine.")
                print(f"  Legacy experiences table has {len(_legacy_experiences())} entries.")
        except Exception as e:
            print(f"[WARNING] prediction_machine unavailable ({e}), using legacy data")
            result = prediction_accuracy()
            print(json.dumps(result, indent=2))

    elif args.cmd == "report":
        print("=" * 60)
        print("SIMULATION REPORT — Cognitive AI Harness")
        print("=" * 60)

        # Task outcome model
        tasks = _historical_tasks()
        print(f"\n1. TASK OUTCOME MODEL")
        print(f"   Training data: {len(tasks)} terminal tasks")
        if tasks:
            by_mission = {}
            for t in tasks:
                m = t["mission_id"]
                by_mission.setdefault(m, {"pass": 0, "fail": 0, "tokens": []})
                if t["status"] == "done" and t.get("critic_verdict") == "pass":
                    by_mission[m]["pass"] += 1
                elif t["status"] == "failed":
                    by_mission[m]["fail"] += 1
                tok = (t["tokens_in"] or 0) + (t["tokens_out"] or 0)
                if tok > 0:
                    by_mission[m]["tokens"].append(tok)
            for m, d in sorted(by_mission.items()):
                total = d["pass"] + d["fail"]
                pass_rate = d["pass"] / total if total > 0 else 0
                med_tokens = int(median(d["tokens"])) if d["tokens"] else 0
                print(f"   {m[:30]:30s}  pass={d['pass']:3d}  fail={d['fail']:3d}  "
                      f"rate={pass_rate:.1%}  med_tokens={med_tokens:>10,}")

        # Video engagement model
        dataset = load_vaibhav_dataset()
        print(f"\n2. VIDEO ENGAGEMENT MODEL")
        print(f"   Training data: {len(dataset)} videos")
        if dataset:
            by_type = {}
            for v in dataset:
                t = v["type"]
                by_type.setdefault(t, [])
                by_type[t].append(v["views"])
            for t, views in sorted(by_type.items(), key=lambda x: -mean(x[1])):
                print(f"   {t:15s}  n={len(views):3d}  "
                      f"median={int(median(views)):>8,}  "
                      f"range=[{min(views):>8,} - {max(views):>8,}]")

        # Skill safety model
        skills_dir = ROOT / "skills_analyst"
        known = 0
        if skills_dir.exists():
            known = len(list(skills_dir.glob("*/*.md"))) - 2  # minus README files
        n_rollbacks = _git_rollback_count()
        print(f"\n3. SKILL SAFETY MODEL")
        print(f"   Training data: {max(known, 0)} approved skills")
        print(f"   Regressions observed: {n_rollbacks} (from git history)")
        if n_rollbacks > 0:
            print(f"   NOTE: v2 predictor includes rollback data — use 'predict-skill' CLI")

        # Prediction accuracy
        acc = prediction_accuracy()
        print(f"\n4. PREDICTION ACCURACY")
        if acc.get("total_predictions", 0) == 0:
            print(f"   No completed predictions yet — make predictions and record")
            print(f"   outcomes to start building accuracy history")
        else:
            print(json.dumps(acc, indent=2))

        print(f"\n{'=' * 60}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())