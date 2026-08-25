"""Video engagement predictor — predicts views at 24h, 3-day, and 7-day windows.

Model version: video_engagement_v1

Training data: S:/AGI_like/AI videos/07_analysis/vaibhav_video_dataset.json
(or fallback S:/AI videos/vaibhav_video_dataset.json).
The dataset is a JSON list of video dicts with: type, views, likes, duration_min,
hook_formula, and other fields.

IMPORTANT ASSUMPTION: The Vaibhav dataset only contains 7-day cumulative views.
The 24h and 3-day predictions are derived from the 7-day prediction using scaling
factors. These scaling factors are MODEL ASSUMPTIONS, not measured facts:
  - 24h views ≈ 30% of 7-day views (assumed range 25-35%)
  - 3-day views ≈ 62.5% of 7-day views (assumed range 55-70%)

Stdlib only — no numpy, no sklearn, no frameworks.
"""

import json
from pathlib import Path
from statistics import median

# ── paths ─────────────────────────────────────────────────────────────────────

_DATASET_CANDIDATES = [
    Path("S:/AGI_like/AI videos/07_analysis/vaibhav_video_dataset.json"),
    Path("S:/AI videos/07_analysis/vaibhav_video_dataset.json"),
    Path("S:/AI videos/vaibhav_video_dataset.json"),
]

MODEL_VERSION = "video_engagement_v1"
MODEL_NAME = "category_median_with_duration_adjustment"

# ── scaling-factor assumptions (documented, not facts) ────────────────────────
# The Vaibhav dataset only has 7-day views. We derive 24h and 3d from the 7d
# prediction using these scaling factors. They are assumptions, not measurements.

SCALE_24H = 0.30  # 24h ≈ 30% of 7d (assumed range 25-35%)
SCALE_3D = 0.625  # 3d ≈ 62.5% of 7d (assumed range 55-70%)

# ── helpers ───────────────────────────────────────────────────────────────────

_DOW_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


def _find_dataset() -> Path | None:
    """Return the first existing dataset path, or None if not found."""
    for p in _DATASET_CANDIDATES:
        if p.exists():
            return p
    return None


def load_dataset() -> list[dict]:
    """Load the Vaibhav video dataset from JSON.

    Returns [] if the file doesn't exist (e.g., running on a different machine).
    """
    path = _find_dataset()
    if path is None:
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _video_features(
    content_type: str,
    hook_formula: str,
    duration_min: float,
    upload_day: str,
) -> dict:
    """Extract structured features from video metadata."""
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


# ── predictor class ────────────────────────────────────────────────────────────


class VideoEngagementPredictor:
    """Predict video engagement (views + engagement rate) from the Vaibhav dataset.

    Uses category-median by content_type, optionally refined by hook_formula.
    Duration adjustment: <15 min gets 1.15×, >30 min gets 0.85×.
    Confidence scales with the number of similar training examples.
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
            "Video engagement predictor (video_engagement_v1): predicts views "
            "at 24h, 3-day, and 7-day windows plus engagement rate. "
            "Uses category-median from the Vaibhav video dataset, filtered by "
            "content_type and optionally hook_formula. "
            "Duration adjustment: <15min → 1.15×, >30min → 0.85×. "
            "24h and 3d predictions are derived from 7d using scaling-factor "
            "assumptions (30% and 62.5% respectively), NOT measured data."
        )

    def predict(
        self,
        content_type: str,
        hook_formula: str,
        duration_min: float,
        upload_day: str,
    ) -> dict:
        """Predict video engagement.

        Args:
            content_type:  Video type (roundup, tutorial, opinion, etc.).
            hook_formula:  Hook formula (shocking_fact, relatable_problem, etc.).
            duration_min:  Video duration in minutes.
            upload_day:    Day of week name (e.g. "monday").

        Returns:
            dict with predicted_views_24h, predicted_views_3d,
            predicted_views_7d, view_range_7d, predicted_engagement_rate,
            engagement_range, confidence, n_training_examples, model,
            model_version, and features.

            If no dataset is found, returns None predictions with
            confidence="none".
        """
        dataset = load_dataset()

        if not dataset:
            return {
                "predicted_views_24h": None,
                "predicted_views_3d": None,
                "predicted_views_7d": None,
                "view_range_7d": None,
                "predicted_engagement_rate": None,
                "engagement_range": None,
                "confidence": "none",
                "n_training_examples": 0,
                "model": self.model,
                "model_version": self.model_version,
                "features": _video_features(
                    content_type, hook_formula, duration_min, upload_day
                ),
            }

        features = _video_features(content_type, hook_formula, duration_min, upload_day)

        # Filter by content type first (strongest predictor)
        by_type = [v for v in dataset if v.get("type") == features["content_type"]]
        if not by_type:
            by_type = dataset  # fall back to all

        # Further filter by hook formula if we have enough
        by_hook = [
            v for v in by_type
            if v.get("hook_formula") == features["hook_formula"]
        ]
        if len(by_hook) >= 3:
            training = by_hook
        else:
            training = by_type

        # Views prediction (7-day)
        views = [v["views"] for v in training if v.get("views")]
        predicted_views_7d = int(median(views)) if views else 0
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

        # Duration adjustment
        duration_adj = 1.0
        if 0 < features["duration_min"] < 15:
            duration_adj = 1.15  # 15% boost for short videos
        elif features["duration_min"] > 30:
            duration_adj = 0.85  # 15% penalty for long videos

        predicted_views_7d = int(predicted_views_7d * duration_adj)
        view_min = int(view_min * duration_adj)
        view_max = int(view_max * duration_adj)

        # Derive 24h and 3d from 7d using scaling-factor assumptions
        predicted_views_24h = int(predicted_views_7d * SCALE_24H)
        predicted_views_3d = int(predicted_views_7d * SCALE_3D)

        # Confidence from sample size
        n_training = len(training)
        if n_training >= 5:
            confidence = "high"
        elif n_training >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "predicted_views_24h": predicted_views_24h,
            "predicted_views_3d": predicted_views_3d,
            "predicted_views_7d": predicted_views_7d,
            "view_range_7d": [view_min, view_max],
            "predicted_engagement_rate": predicted_engagement,
            "engagement_range": [eng_min, eng_max],
            "confidence": confidence,
            "n_training_examples": n_training,
            "model": self.model,
            "model_version": self.model_version,
            "features": features,
        }