"""
MiksCampaignCollector — collects real MIKS campaign outcomes.

MIKS campaigns have not yet been run in production, so this collector is
intentionally a stub. It queries the store for any pending
``miks_campaign`` predictions, but because no real campaign data source
exists yet it skips every prediction and reports that MIKS campaigns are
not yet available.

When real campaigns are launched, the ``_collect_campaign_metrics`` method
can be extended to fetch TikTok/Instagram engagement data via subprocess
calls to platform CLIs or HTTP APIs.

Stdlib-only. Python 3.11.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Any

from prediction_machine.paths import MIKS_CONFIG

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MIKS_CONFIG = str(MIKS_CONFIG)


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


class MiksCampaignCollector:
    """Collects real MIKS campaign outcomes (stub — campaigns not yet run)."""

    def __init__(self, miks_config: str | None = None) -> None:
        self.miks_config = miks_config or _MIKS_CONFIG
        self._compute_error = _import_compute_error()

    # -- Helpers ------------------------------------------------------------

    def _campaign_has_real_data(self, campaign_id: str) -> bool:
        """
        Check whether *campaign_id* maps to real TikTok/Instagram posts.

        Currently always returns False because MIKS campaigns have not been
        run in production. Override this when real campaigns are launched.
        """
        # Future: check miks.yaml for campaign_id, then query platform APIs
        # for posted content and metrics.
        return False

    def _collect_campaign_metrics(self, campaign_id: str) -> dict | None:
        """
        Fetch real campaign metrics for *campaign_id*.

        Returns None until real campaign infrastructure exists.
        """
        # Future: subprocess call to tiktok-cli / instagram API
        # result = subprocess.run([...], capture_output=True, text=True, timeout=30)
        # return json.loads(result.stdout)
        return None

    # -- Main entry point ---------------------------------------------------

    def collect_pending(self, store) -> dict[str, Any]:
        """
        Collect real MIKS campaign outcomes.

        Because MIKS campaigns have not been run in production, every
        prediction is skipped. Returns a summary dict:
            {"checked": int, "recorded": int, "skipped": int,
             "invalidated": int, "errors": list[str], "note": str}
        """
        summary: dict[str, Any] = {
            "checked": 0,
            "recorded": 0,
            "skipped": 0,
            "invalidated": 0,
            "errors": [],
            "note": "MIKS campaigns not yet run in production",
        }

        try:
            pending = store.get_pending_outcomes(prediction_type="miks_campaign")
        except Exception as exc:
            summary["errors"].append(f"get_pending_outcomes failed: {exc}")
            return summary

        for pred in pending:
            summary["checked"] += 1
            pred_id = pred.get("id") or pred.get("prediction_id")
            target = pred.get("target")

            if not target:
                summary["errors"].append(
                    f"prediction {pred_id}: missing target (campaign id)"
                )
                continue

            campaign_id = str(target).strip()

            # Check if this campaign has real production data
            has_real = self._campaign_has_real_data(campaign_id)

            if not has_real:
                summary["skipped"] += 1
                continue

            # --- Future: real collection path -------------------------------
            metrics = self._collect_campaign_metrics(campaign_id)
            if metrics is None:
                summary["skipped"] += 1
                continue

            actual: dict[str, Any] = {
                "campaign_id": campaign_id,
                "metrics": metrics,
                "source": "platform_api",
            }
            actual_source = "tiktok/instagram_api"

            error = None
            if self._compute_error is not None:
                try:
                    error = self._compute_error(
                        "miks_campaign", dict(pred), actual
                    )
                except Exception as exc:
                    summary["errors"].append(
                        f"prediction {pred_id}: compute_error failed: {exc}"
                    )

            try:
                store.record_outcome(pred_id, actual, actual_source, error)
                summary["recorded"] += 1
            except Exception as exc:
                summary["errors"].append(
                    f"prediction {pred_id}: record_outcome failed: {exc}"
                )

        return summary
