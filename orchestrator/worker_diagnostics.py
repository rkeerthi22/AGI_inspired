"""Persist worker failure diagnostics without changing execution semantics."""
from __future__ import annotations

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


def write_worker_raw(runs: Path, task_id: int, output: Any,
                     usage: Mapping[str, Any] | None, phase: str) -> Path:
    """Persist raw output or failure diagnostics and return the artifact path."""
    path = runs / f"task{task_id}_worker_raw.txt"
    path.write_text(diagnostic_output(output, usage, phase), encoding="utf-8")
    return path
