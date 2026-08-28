"""
SkillSafetyCollector — determines whether a skill promotion caused a
regression by inspecting the filesystem and git log.

For each pending ``skill_safety`` prediction:
  1. Checks if the skill file still exists under the repository's skills_analyst/.
     If the file is gone the skill was rolled back.
  2. Searches git log for ``Rollback skill: <target>`` commits.
  3. Records the actual outcome:
       - regressed=True  if a rollback is found (file gone or commit present)
       - regressed=False if the file still exists and no rollback found
       - invalidated     if the file disappeared without a rollback record

Actual source: ``git_log + skills_analyst filesystem``.

Stdlib-only. Python 3.11.
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from typing import Any

from prediction_machine.paths import REPO_ROOT, SKILLS_ROOT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SKILLS_ROOT = str(SKILLS_ROOT)
_REPO_ROOT = str(REPO_ROOT)

_ROLLBACK_GREP_PREFIX = "Rollback skill:"


def _import_compute_error():
    """Lazily import compute_error from the evaluator module."""
    eval_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "evaluation", "evaluator.py",
    )
    if os.path.isfile(eval_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("prediction_evaluator", eval_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "compute_error", None)
    return None


class SkillSafetyCollector:
    """Collects real skill-safety outcomes from the filesystem and git log."""

    def __init__(self, skills_root: str | None = None, repo_root: str | None = None) -> None:
        self.skills_root = skills_root or _SKILLS_ROOT
        self.repo_root = repo_root or _REPO_ROOT
        self._compute_error = _import_compute_error()

    # -- Helpers ------------------------------------------------------------

    def _skill_file_exists(self, target: str) -> bool:
        """Return True if the skill file exists under skills_analyst/."""
        candidate = os.path.join(self.skills_root, target)
        if os.path.isfile(candidate):
            return True
        # Some targets may have a leading path separator — normalise
        candidate = os.path.join(self.skills_root, target.lstrip("/\\"))
        return os.path.isfile(candidate)

    def _find_rollback_commit(self, target: str) -> str | None:
        """
        Search git log (all branches) for ``Rollback skill: <target>``.

        Returns the first matching commit hash, or None.
        """
        # Build a grep pattern. Use the target as-is but escape any quotes.
        grep_pattern = f"{_ROLLBACK_GREP_PREFIX} {target}"

        try:
            result = subprocess.run(
                [
                    "git", "-C", self.repo_root,
                    "log", "--all", "--oneline",
                    f"--grep={grep_pattern}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            sys.stderr.write(
                f"SkillSafetyCollector: git log failed for '{target}': {exc}\n"
            )
            return None
        except Exception as exc:
            sys.stderr.write(
                f"SkillSafetyCollector: git log error for '{target}': {exc}\n"
            )
            return None

        if result.returncode != 0:
            sys.stderr.write(
                f"SkillSafetyCollector: git log rc={result.returncode}: "
                f"{result.stderr.strip()[:300]}\n"
            )
            return None

        output = result.stdout.strip()
        if not output:
            return None

        # --oneline format: "<hash> <message>"
        first_line = output.splitlines()[0].strip()
        if not first_line:
            return None
        commit_hash = first_line.split()[0]
        return commit_hash

    # -- Main entry point ---------------------------------------------------

    def collect_pending(self, store) -> dict[str, Any]:
        """
        Collect real skill-safety outcomes for all pending predictions.

        Returns a summary dict:
            {"checked": int, "recorded": int, "skipped": int,
             "invalidated": int, "errors": list[str]}
        """
        summary: dict[str, Any] = {
            "checked": 0,
            "recorded": 0,
            "skipped": 0,
            "invalidated": 0,
            "errors": [],
        }

        try:
            pending = store.get_pending_outcomes(prediction_type="skill_safety")
        except Exception as exc:
            summary["errors"].append(f"get_pending_outcomes failed: {exc}")
            return summary

        for pred in pending:
            summary["checked"] += 1
            pred_id = pred.get("id") or pred.get("prediction_id")
            target = pred.get("target")

            if not target:
                summary["errors"].append(
                    f"prediction {pred_id}: missing target (skill file path)"
                )
                continue

            target_str = str(target).strip()
            file_exists = self._skill_file_exists(target_str)
            rollback_commit = self._find_rollback_commit(target_str)

            try:
                if rollback_commit is not None:
                    # Skill was explicitly rolled back
                    actual: dict[str, Any] = {
                        "regressed": True,
                        "rollback_commit": rollback_commit,
                    }
                    actual_source = "git_log + skills_analyst filesystem"

                    error = None
                    if self._compute_error is not None:
                        try:
                            error = self._compute_error(
                                "skill_safety", dict(pred), actual
                            )
                        except Exception as exc:
                            summary["errors"].append(
                                f"prediction {pred_id}: compute_error failed: {exc}"
                            )

                    store.record_outcome(pred_id, actual, actual_source, error)
                    summary["recorded"] += 1

                elif file_exists:
                    # File still present, no rollback — skill is fine
                    actual = {"regressed": False}
                    actual_source = "git_log + skills_analyst filesystem"

                    error = None
                    if self._compute_error is not None:
                        try:
                            error = self._compute_error(
                                "skill_safety", dict(pred), actual
                            )
                        except Exception as exc:
                            summary["errors"].append(
                                f"prediction {pred_id}: compute_error failed: {exc}"
                            )

                    store.record_outcome(pred_id, actual, actual_source, error)
                    summary["recorded"] += 1

                else:
                    # File gone but no rollback record — can't determine outcome
                    summary["invalidated"] += 1
                    try:
                        store.invalidate_prediction(
                            pred_id,
                            reason=(
                                f"skill file '{target_str}' disappeared without "
                                f"a rollback record"
                            ),
                        )
                    except Exception as exc:
                        summary["errors"].append(
                            f"prediction {pred_id}: invalidate failed: {exc}"
                        )

            except Exception:
                tb = traceback.format_exc()
                summary["errors"].append(
                    f"prediction {pred_id}: unexpected error:\n{tb}"
                )

        return summary
