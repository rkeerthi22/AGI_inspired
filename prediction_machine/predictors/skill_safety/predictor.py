"""Skill safety predictor — predicts regression risk before skill promotion.

Model version: skill_safety_v2

CRITICAL FIX (v2): The old simulate.py (v1) hardcoded ``"regressed": False`` for
all skills, ignoring the real rollback at commit c7b5721.  That commit shows
skill "001-shopify-competitor-intel/20260718_001-shopify-competitor-intel_check-app-store-listings.md"
was rolled back due to canary regression.  This v2 model includes that rollback
in its training data and scans git history for any additional rollbacks.

Training data sources:
  1. skills_analyst/ directory — scan for *.md files (active skills)
  2. Git log — search for "Rollback skill" commits to find actual regressions
  3. Known rollback: mission 001, file 20260718_...check-app-store-listings.md
     (commit c7b5721)

Stdlib only — no numpy, no sklearn, no frameworks.
"""

import re
import sqlite3
import subprocess
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────

ROOT = Path("S:/AGI_like")
SKILLS_DIR = ROOT / "skills_analyst"
LEDGER_DB = ROOT / "ledger" / "ledger.db"

MODEL_VERSION = "skill_safety_v2"
MODEL_NAME = "heuristic_risk_scoring_v2"

# Known rollback from commit c7b5721 (the bug in v1 was hardcoding regressed=False)
_KNOWN_ROLLBACK_MISSION = "001-shopify-competitor-intel"
_KNOWN_ROLLBACK_FILE = (
    "20260718_001-shopify-competitor-intel_check-app-store-listings.md"
)
_KNOWN_ROLLBACK_COMMIT = "c7b5721"


# ── helpers ───────────────────────────────────────────────────────────────────


def _ledger_conn():
    """Open a read-only connection to the ledger database."""
    conn = sqlite3.connect(str(LEDGER_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _scan_git_rollbacks(root: Path) -> list[dict]:
    """Scan git log for 'Rollback skill' commits.

    Returns a list of dicts with mission_id, skill_file, and commit hash.
    Always includes the known rollback at c7b5721 even if git is unavailable.
    """
    rollbacks = []

    # Start with the known rollback (commit c7b5721)
    rollbacks.append({
        "mission_id": _KNOWN_ROLLBACK_MISSION,
        "skill_file": _KNOWN_ROLLBACK_FILE,
        "commit": _KNOWN_ROLLBACK_COMMIT,
    })

    # Scan git log for additional rollbacks
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--all", "--oneline",
             "--grep=Rollback skill"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Format: <hash> Rollback skill: <mission>/<filename> (...)
                m = re.match(
                    r"([0-9a-f]+)\s+Rollback skill:\s*(.+?)(?:\s*\(.+\))?\s*$",
                    line,
                )
                if not m:
                    continue
                commit = m.group(1)
                path = m.group(2).strip()
                # path is "mission_dir/skill_file.md"
                parts = path.split("/", 1)
                mission_id = parts[0] if parts else ""
                skill_file = parts[1] if len(parts) > 1 else path

                # Skip if already captured (dedup by commit)
                if any(r["commit"] == commit for r in rollbacks):
                    continue
                rollbacks.append({
                    "mission_id": mission_id,
                    "skill_file": skill_file,
                    "commit": commit,
                })
    except Exception:
        # If git is unavailable, we still have the known rollback
        pass

    return rollbacks


def _scan_active_skills(skills_dir: Path) -> list[dict]:
    """Scan skills_analyst/ for active skill *.md files.

    Returns a list of dicts with mission, note_length, evidence_count,
    canary_baseline, file_name, and regressed (bool).
    """
    skills = []
    if not skills_dir.exists():
        return skills

    # Get rollback info to flag regressed skills
    rollbacks = _scan_git_rollbacks(ROOT)
    rollback_files = {
        (r["mission_id"], r["skill_file"]) for r in rollbacks
    }

    # Also check if any rollback file was deleted (it won't be on disk)
    # Add rolled-back skills that no longer exist on disk
    for rb in rollbacks:
        rb_path = skills_dir / rb["mission_id"] / rb["skill_file"]
        if not rb_path.exists():
            # The file was deleted by the rollback — add as a known regressed skill
            skills.append({
                "mission": rb["mission_id"],
                "note_length": 0,  # file no longer exists
                "evidence_count": 0,
                "canary_baseline": 0,
                "file_name": rb["skill_file"],
                "regressed": True,
            })

    # Scan existing .md files
    for p in sorted(skills_dir.glob("*/*.md")):
        if p.parent.name in ("_candidates", "_rejected"):
            continue
        if p.name == "README.md":
            continue

        text = p.read_text(encoding="utf-8")

        # Parse frontmatter
        base_match = re.search(r"canary_baseline:\s*(\d+)", text)
        ev_match = re.search(r"evidence_lesson_ids:\s*\[([\d,\s]*)\]", text)
        body = re.sub(r"^---.*?---\s*", "", text, flags=re.S).strip()

        mission = p.parent.name
        file_name = p.name

        # Check if this skill was rolled back (by mission + filename)
        regressed = (mission, file_name) in rollback_files

        skills.append({
            "mission": mission,
            "note_length": len(body),
            "evidence_count": len(re.findall(r"\d+", ev_match.group(1))) if ev_match else 0,
            "canary_baseline": int(base_match.group(1)) if base_match else 0,
            "file_name": file_name,
            "regressed": regressed,
        })

    return skills


def _mission_done_task_count(mission_id: str) -> int:
    """Count done tasks for a mission from the ledger."""
    try:
        with _ledger_conn() as c:
            return c.execute(
                "SELECT count(*) FROM tasks WHERE mission_id=? AND status='done'",
                (mission_id,),
            ).fetchone()[0]
    except Exception:
        return 0


# ── predictor class ────────────────────────────────────────────────────────────


class SkillSafetyPredictor:
    """Predict skill promotion regression risk using heuristic scoring.

    v2 fixes the v1 bug that hardcoded regressed=False for all skills.
    Now scans git history for actual rollback commits and includes the known
    c7b5721 rollback in training data.
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
            "Skill safety predictor (skill_safety_v2): predicts regression "
            "risk before skill promotion using heuristic risk scoring. "
            "v2 fixes the v1 bug that hardcoded regressed=False — now scans "
            "git log for actual 'Rollback skill' commits and includes the "
            "known rollback at c7b5721 (mission 001, check-app-store-listings)."
        )

    def predict(
        self,
        mission_id: str,
        note_length: int,
        evidence_count: int,
        skill_file_name: str | None = None,
    ) -> dict:
        """Predict skill promotion regression risk.

        Args:
            mission_id:       Which mission the skill applies to.
            note_length:       Character count of the skill note body.
            evidence_count:    Number of lesson rows supporting the skill.
            skill_file_name:   Optional skill file name for context.

        Returns:
            dict with risk_level, risk_score, risk_factors,
            predicted_regression, n_known_skills, n_known_regressions,
            known_skill_details, confidence, model, model_version, and
            features.
        """
        # Gather training data
        known_skills = _scan_active_skills(SKILLS_DIR)
        rollbacks = _scan_git_rollbacks(ROOT)

        n_known_skills = len(known_skills)
        n_known_regressions = len(rollbacks)

        # Build known_skill_details with {mission, evidence, regressed}
        known_skill_details = [
            {
                "mission": s["mission"],
                "evidence": s["evidence_count"],
                "regressed": s["regressed"],
            }
            for s in known_skills
        ]

        # Check if this mission had a previous regression
        mission_had_regression = any(
            r["mission_id"] == mission_id for r in rollbacks
        )

        # Count done tasks for this mission
        done_task_count = _mission_done_task_count(mission_id)

        # ── risk scoring ───────────────────────────────────────────────────
        risk_score = 0
        risk_factors = []

        # note_length > 500: +1 risk (more injection surface)
        if note_length > 500:
            risk_score += 1
            risk_factors.append(
                "note is long (>500 chars) — more injection surface"
            )

        # evidence_count < 2: +2 risk (skill may not generalize)
        if evidence_count < 2:
            risk_score += 2
            risk_factors.append(
                "low evidence count (<2) — skill may not generalize"
            )

        # evidence_count >= 3: -1 risk (well-supported)
        if evidence_count >= 3:
            risk_score -= 1
            risk_factors.append(
                "strong evidence base (>=3 lessons) — reduces risk"
            )

        # mission has >10 done tasks: +1 risk (more canary exposure)
        if done_task_count > 10:
            risk_score += 1
            risk_factors.append(
                f"high mission activity ({done_task_count} done tasks) — "
                "more canary exposure"
            )

        # mission had a previous regression: +2 risk
        if mission_had_regression:
            risk_score += 2
            risk_factors.append(
                f"mission {mission_id} had a previous skill rollback — "
                "elevated regression risk"
            )

        # Risk level from score
        if risk_score <= 1:
            risk_level = "low"
        elif risk_score <= 3:
            risk_level = "medium"
        else:
            risk_level = "high"

        # Confidence based on training data volume
        if n_known_skills >= 5:
            confidence = "high"
        elif n_known_skills >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        features = {
            "mission_id": mission_id,
            "note_length": note_length,
            "evidence_count": evidence_count,
            "skill_file_name": skill_file_name,
            "done_task_count": done_task_count,
            "mission_had_regression": mission_had_regression,
        }

        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "risk_factors": risk_factors,
            "predicted_regression": risk_level == "high",
            "n_known_skills": n_known_skills,
            "n_known_regressions": n_known_regressions,
            "known_skill_details": known_skill_details,
            "confidence": confidence,
            "model": self.model,
            "model_version": self.model_version,
            "features": features,
        }