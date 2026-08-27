"""Small, verified continuity checkpoint for context/model recovery.

The current brief is a locator and recovery hint, never a source of truth.
``recover()`` compares it with Git and validates every referenced path before
returning anything an agent should trust.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_context import ROOT

SCHEMA_VERSION = 1
MAX_BRIEF_BYTES = 4096
DEFAULT_MAX_AGE_HOURS = 24
CURRENT = ROOT / ".harness" / "continuity" / "current.json"

_SENSITIVE_KEYS = re.compile(
    r"(^|_)(api_?key|secret|password|credential|access_?token|refresh_?token|"
    r"authorization|cookie|private_?key)($|_)", re.I
)
_SENSITIVE_VALUES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
    re.compile(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{20,}\b"),
)


class ContinuityError(ValueError):
    """The brief is unsafe, malformed, oversized, or unusable."""


def _git(root: Path, *args: str, required: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode and required:
        raise ContinuityError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    # Porcelain status begins with meaningful spaces (the XY status columns).
    # Removing all surrounding whitespace corrupts the first changed path.
    return proc.stdout.rstrip("\r\n") if proc.returncode == 0 else ""


def inspect_repository(root: Path = ROOT) -> dict[str, Any]:
    """Measure current Git state. No value from the brief is used here."""
    lines = _git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    return {
        "branch": _git(root, "branch", "--show-current") or "DETACHED",
        "head": _git(root, "rev-parse", "HEAD"),
        "upstream": (_git(root, "rev-parse", "--abbrev-ref", "@{upstream}",
                          required=False) or None),
        "tree_clean": not lines,
        "changed_paths": [line[3:] for line in lines],
    }


def _assert_no_secrets(value: Any, path: str = "brief") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEYS.search(str(key)):
                raise ContinuityError(f"sensitive field is forbidden: {path}.{key}")
            _assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUES):
            raise ContinuityError(f"possible secret is forbidden at {path}")


def _reference_path(root: Path, raw: str) -> Path:
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise ContinuityError(f"reference must be repository-relative: {raw}")
    resolved_root = root.resolve()
    resolved = (root / rel).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ContinuityError(f"reference escapes repository: {raw}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_brief(brief: dict[str, Any], root: Path = ROOT) -> list[dict[str, Any]]:
    """Validate schema, size, freshness fields, references, and secret hygiene."""
    if not isinstance(brief, dict):
        raise ContinuityError("brief must be a JSON object")
    encoded = json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BRIEF_BYTES:
        raise ContinuityError(f"brief is {len(encoded)} bytes; cap is {MAX_BRIEF_BYTES}")
    if brief.get("schema_version") != SCHEMA_VERSION:
        raise ContinuityError("unsupported schema_version")
    if not isinstance(brief.get("brief_revision"), int) or brief["brief_revision"] < 1:
        raise ContinuityError("brief_revision must be a positive integer")
    try:
        datetime.fromisoformat(brief["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContinuityError("created_at must be an ISO-8601 timestamp") from exc
    required_objects = ("repository", "task", "gate")
    if any(not isinstance(brief.get(name), dict) for name in required_objects):
        raise ContinuityError("repository, task, and gate must be objects")
    for name in ("completed", "locked_constraints", "references"):
        if not isinstance(brief.get(name), list):
            raise ContinuityError(f"{name} must be a list")
    _assert_no_secrets(brief)

    checked = []
    for ref in brief["references"]:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ContinuityError("each reference needs a string path")
        path = _reference_path(root, ref["path"])
        exists = path.is_file()
        actual = _sha256(path) if exists else None
        expected = ref.get("sha256")
        if expected is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected)):
            raise ContinuityError(f"invalid reference sha256: {ref['path']}")
        checked.append({"path": ref["path"], "exists": exists,
                        "sha256_matches": expected is None or expected.lower() == actual})
    return checked


def load_current(path: Path = CURRENT, root: Path = ROOT) -> dict[str, Any]:
    if not path.is_file():
        raise ContinuityError(f"current brief does not exist: {path}")
    if path.stat().st_size > MAX_BRIEF_BYTES:
        raise ContinuityError(f"brief exceeds {MAX_BRIEF_BYTES} bytes")
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityError(f"cannot read current brief: {exc}") from exc
    validate_brief(brief, root)
    return brief


def write_current(
    *, task: dict[str, str], next_action: str, completed: list[str],
    locked_constraints: list[str], gate: dict[str, str],
    references: list[dict[str, str]], root: Path = ROOT, path: Path | None = None,
) -> dict[str, Any]:
    """Measure Git, validate, then atomically replace the current brief."""
    path = path or root / ".harness" / "continuity" / "current.json"
    revision = 1
    if path.is_file():
        try:
            revision = load_current(path, root)["brief_revision"] + 1
        except ContinuityError:
            raise ContinuityError("refusing to overwrite an invalid current brief")
    task_value = dict(task)
    task_value["next_action"] = next_action
    normalized_references = []
    for ref in references:
        item = dict(ref)
        ref_path = _reference_path(root, item.get("path", ""))
        if not ref_path.is_file():
            raise ContinuityError(f"referenced file does not exist: {item.get('path')}")
        item["sha256"] = _sha256(ref_path)
        normalized_references.append(item)
    brief = {
        "schema_version": SCHEMA_VERSION,
        "brief_revision": revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": task_value.get("status", "in_progress"),
        "repository": inspect_repository(root),
        "task": task_value,
        "completed": completed,
        "locked_constraints": locked_constraints,
        "gate": gate,
        "references": normalized_references,
    }
    validate_brief(brief, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="current.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(brief, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if Path(temp_name).stat().st_size > MAX_BRIEF_BYTES:
            raise ContinuityError("formatted brief exceeds size cap")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return brief


def recover(
    path: Path = CURRENT, root: Path = ROOT,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """Return live state plus discrepancies; live state always wins."""
    brief = load_current(path, root)
    live = inspect_repository(root)
    recorded = brief["repository"]
    discrepancies = []
    for key in ("branch", "head", "upstream", "tree_clean", "changed_paths"):
        if recorded.get(key) != live.get(key):
            discrepancies.append({"field": key, "recorded": recorded.get(key),
                                  "live": live.get(key), "winner": "live"})
    created = datetime.fromisoformat(brief["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours > max_age_hours:
        discrepancies.append({"field": "freshness", "recorded": brief["created_at"],
                              "live": f"{age_hours:.1f}h old", "winner": "live"})
    references = validate_brief(brief, root)
    for ref in references:
        if not ref["exists"]:
            discrepancies.append({"field": "reference", "recorded": ref["path"],
                                  "live": "missing", "winner": "live"})
        elif not ref["sha256_matches"]:
            discrepancies.append({"field": "reference_integrity",
                                  "recorded": ref["path"], "live": "content changed",
                                  "winner": "live"})
    return {"brief": brief, "live_repository": live,
            "references": references, "discrepancies": discrepancies}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and reconcile the current Compact Brief")
    parser.add_argument("command", choices=("validate", "recover"))
    parser.add_argument("--path", type=Path, default=CURRENT)
    args = parser.parse_args()
    try:
        result = (load_current(args.path) if args.command == "validate"
                  else recover(args.path))
    except ContinuityError as exc:
        print(f"continuity error: {exc}")
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
