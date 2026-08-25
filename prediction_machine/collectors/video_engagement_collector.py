"""
VideoEngagementCollector — collects real YouTube view counts for
``video_engagement`` predictions using yt-dlp.

yt-dlp returns *total* lifetime views, not time-windowed counts. We record
the total as ``views_7d`` only when the video is less than 7 days old;
otherwise we record it as ``total_views`` and note the discrepancy. 24h and
3d windows require the YouTube Data API (not yet available) and are left as
``None``.

If yt-dlp is not installed, fails, or returns no data, the prediction is
skipped — never fabricated.

Stdlib-only. Python 3.11.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import traceback
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_YT_DLP_TIMEOUT = 30  # seconds

# A YouTube video ID is 11 chars from [A-Za-z0-9_-]
_YT_ID_LEN = 11


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


def _parse_ts(raw: Any) -> datetime.datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.datetime.fromtimestamp(float(raw), tz=datetime.timezone.utc)
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            return datetime.datetime.fromtimestamp(float(raw), tz=datetime.timezone.utc)
        except (ValueError, OSError):
            return None
    return None


class VideoEngagementCollector:
    """Collects real video engagement metrics from YouTube via yt-dlp."""

    def __init__(self) -> None:
        self._compute_error = _import_compute_error()

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_url(target: str) -> str:
        """Normalise a prediction target into a URL yt-dlp can consume."""
        target = target.strip()
        if target.startswith("http://") or target.startswith("https://"):
            return target
        # Bare video ID → full watch URL
        if len(target) == _YT_ID_LEN:
            return f"https://www.youtube.com/watch?v={target}"
        # Short youtu.be links without scheme
        if target.startswith("youtu.be/"):
            return f"https://{target}"
        # Assume it's a bare ID or partial URL; prepend YouTube watch prefix
        return f"https://www.youtube.com/watch?v={target}"

    def _fetch_video_info(self, url: str) -> dict | None:
        """Run yt-dlp and return parsed JSON, or None on failure."""
        try:
            result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", url],
                capture_output=True,
                text=True,
                timeout=_YT_DLP_TIMEOUT,
            )
        except FileNotFoundError:
            sys.stderr.write(
                "VideoEngagementCollector: yt-dlp not found on PATH\n"
            )
            return None
        except subprocess.TimeoutExpired:
            sys.stderr.write(
                f"VideoEngagementCollector: yt-dlp timed out for {url}\n"
            )
            return None
        except Exception as exc:
            sys.stderr.write(
                f"VideoEngagementCollector: yt-dlp subprocess error for {url}: {exc}\n"
            )
            return None

        if result.returncode != 0 or not result.stdout.strip():
            sys.stderr.write(
                f"VideoEngagementCollector: yt-dlp failed for {url} "
                f"(rc={result.returncode}): {result.stderr.strip()[:500]}\n"
            )
            return None

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            sys.stderr.write(
                f"VideoEngagementCollector: failed to parse yt-dlp JSON for {url}: {exc}\n"
            )
            return None

    # -- Main entry point ---------------------------------------------------

    def collect_pending(self, store) -> dict[str, Any]:
        """
        Collect real view counts for all pending ``video_engagement`` predictions.

        Returns a summary dict:
            {"checked": int, "recorded": int, "skipped": int,
             "pending": int, "errors": list[str], "note": str|None}
        """
        summary: dict[str, Any] = {
            "checked": 0,
            "recorded": 0,
            "skipped": 0,
            "pending": 0,
            "errors": [],
            "note": None,
        }

        try:
            pending = store.get_pending_outcomes(prediction_type="video_engagement")
        except Exception as exc:
            summary["errors"].append(f"get_pending_outcomes failed: {exc}")
            return summary

        for pred in pending:
            summary["checked"] += 1
            pred_id = pred.get("id") or pred.get("prediction_id")
            target = pred.get("target")

            if not target:
                summary["errors"].append(
                    f"prediction {pred_id}: missing target (video id/url)"
                )
                continue

            url = self._resolve_url(str(target))
            data = self._fetch_video_info(url)

            if data is None:
                # Cannot get real data — skip, do NOT fabricate
                summary["skipped"] += 1
                summary["pending"] += 1
                continue

            views = data.get("view_count", 0)
            if views is None:
                views = 0

            # Determine video age to decide views_7d vs total_views
            upload_date_raw = data.get("upload_date")  # YYYYMMDD string
            video_age_days = None
            if upload_date_raw:
                try:
                    upload_dt = datetime.datetime.strptime(
                        str(upload_date_raw), "%Y%m%d"
                    ).replace(tzinfo=datetime.timezone.utc)
                    video_age_days = (
                        datetime.datetime.now(datetime.timezone.utc) - upload_dt
                    ).days
                except (ValueError, TypeError):
                    pass

            if video_age_days is not None and video_age_days < 7:
                actual: dict[str, Any] = {
                    "views_7d": views,
                    "views_24h": None,
                    "views_3d": None,
                    "source": "yt-dlp",
                    "note": f"video is {video_age_days}d old; total views recorded as views_7d",
                }
            else:
                actual = {
                    "views_7d": None,
                    "views_24h": None,
                    "views_3d": None,
                    "total_views": views,
                    "source": "yt-dlp",
                    "note": (
                        f"video is {video_age_days}d old (>=7d); yt-dlp returns "
                        f"total views, not 7-day windowed — recorded as total_views"
                        if video_age_days is not None
                        else "video age unknown; yt-dlp returns total views — "
                        "recorded as total_views, views_7d is None"
                    ),
                }

            actual_source = "yt-dlp"

            # Compute error via evaluator (optional)
            error = None
            if self._compute_error is not None:
                try:
                    error = self._compute_error(
                        "video_engagement", dict(pred), actual
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