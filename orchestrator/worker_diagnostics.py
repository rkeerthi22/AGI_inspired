"""Persist worker failure diagnostics without changing execution semantics."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


def diagnostic_output(output: Any, usage: Mapping[str, Any] | None,
                      phase: str) -> str:
    """Return worker output, or a bounded non-empty diagnostic for empty output."""
    if isinstance(output, str) and output.strip():
        return output
    usage = usage or {}
    details = []
    for key in ("failure", "process_error", "error", "stderr"):
        value = usage.get(key)
        if value is not None and str(value).strip():
            details.append(f"{key}: {str(value).strip()[:2000]}")
    detail = "\n".join(details) or "no provider diagnostic was returned"
    return (f"[{phase} diagnostic: provider returned no usable output]\n"
            f"{detail}\n")


def get_task_attempt(task_id: int, row: Mapping[str, Any] | None = None,
                     runs_dir: Path | None = None) -> int:
    """Determine the 1-indexed attempt number for a task run (F129).

    Monotonically advances across retries based on database attempt_count and
    existing artifacts on disk to prevent overwriting prior attempt evidence.
    """
    runs = runs_dir or Path("runs")
    base_attempt = 1
    if row and isinstance(row.get("attempt_count"), int):
        base_attempt = max(base_attempt, row["attempt_count"] + 1)

    existing_max = 0
    if runs.is_dir():
        for p in runs.glob(f"task{task_id}_a*_*"):
            m = re.match(rf"^task{task_id}_a(\d+)_", p.name)
            if m:
                existing_max = max(existing_max, int(m.group(1)))
        if existing_max == 0:
            if ((runs / f"task{task_id}_worker.usage.json").exists() or
                    (runs / f"task{task_id}_worker_raw.txt").exists()):
                existing_max = 1

    if existing_max > 0:
        return max(base_attempt, existing_max + 1)
    return base_attempt


def write_worker_raw(runs: Path, task_id: int, output: Any,
                     usage: Mapping[str, Any] | None, phase: str,
                     attempt: int = 1) -> Path:
    """Persist raw output or failure diagnostics and return the artifact path (F129)."""
    path = runs / f"task{task_id}_a{attempt}_worker_raw.txt"
    content = diagnostic_output(output, usage, phase)
    path.write_text(content, encoding="utf-8")
    if attempt == 1:
        # Also maintain canonical/legacy un-suffixed path for attempt 1
        (runs / f"task{task_id}_worker_raw.txt").write_text(content, encoding="utf-8")
    return path

