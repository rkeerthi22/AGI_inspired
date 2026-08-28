"""Portable prediction-machine paths with environment overrides."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("AGI_REPO_ROOT", PACKAGE_ROOT.parent)).resolve()


def configured_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


PREDICTION_DB = configured_path("PREDICTION_DB", PACKAGE_ROOT / "data" / "predictions.db")
REPORTS_DIR = configured_path("PREDICTION_REPORTS_DIR", PACKAGE_ROOT / "reports" / "daily")
LEDGER_DB = configured_path("LEDGER_DB", REPO_ROOT / "ledger" / "ledger.db")
LEDGERBOOK_DB = configured_path("LEDGERBOOK_DB", REPO_ROOT / "memory" / "ledgerbook.db")
SKILLS_ROOT = configured_path("SKILLS_ANALYST_ROOT", REPO_ROOT / "skills_analyst")
MIKS_ENGINE = configured_path(
    "MIKS_ENGINE_PATH", REPO_ROOT / "workspace" / "miks_campaign_simulator" / "engine.py"
)
MIKS_CONFIG = configured_path(
    "MIKS_CONFIG", REPO_ROOT / "workspace" / "miks_campaign_simulator" / "miks.yaml"
)
VIDEO_DATASET = configured_path(
    "VIDEO_DATASET_PATH", REPO_ROOT / "AI videos" / "07_analysis" / "vaibhav_video_dataset.json"
)
