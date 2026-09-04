"""Supply-chain checks for the Python lock and external Hermes runtime.

The harness has two different dependency boundaries:

* public Python packages are installed from a hash-locked requirements file;
* Hermes is an externally managed runtime, not a PyPI wheel.  It is therefore
  attested by executable version, source revision, and explicit file hashes.

Neither check downloads, installs, or repairs anything.  Callers receive a
structured result and fail closed when an input cannot be verified.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_LOCK = ROOT / "scripts" / "requirements.txt"
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "bootstrap.ps1"
HERMES_RUNTIME_MANIFEST = ROOT / "scripts" / "hermes_runtime_attestation.json"

_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\\\s;]+)")
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})\b", re.IGNORECASE)
_VERSION_RE = re.compile(r"Hermes Agent v(?P<version>[0-9][0-9A-Za-z.+-]*)")
_INSTALL_RE = re.compile(r"^Install directory:\s*(?P<path>.+?)\s*$", re.MULTILINE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requirements_lock_state(path: Path = REQUIREMENTS_LOCK) -> dict[str, Any]:
    """Return whether every exact requirement has one or more SHA-256 hashes."""
    try:
        # PowerShell's UTF-8 writer may include a BOM; it is not a lock entry.
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return {"ok": False, "entries": 0, "missing_hashes": [],
                "error": type(exc).__name__}

    entries: dict[str, dict[str, Any]] = {}
    current: str | None = None
    duplicates: list[str] = []
    malformed: list[str] = []
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT_RE.match(line)
        if match:
            package = match.group(1).lower().replace("_", "-")
            if package in entries:
                duplicates.append(package)
            entries[package] = {"version": match.group(2), "hashes": []}
            current = package
            entries[package]["hashes"].extend(_HASH_RE.findall(line))
            continue
        hashes = _HASH_RE.findall(line)
        if hashes and current:
            entries[current]["hashes"].extend(hashes)
            continue
        if line.startswith("--"):
            # Index directives are intentionally not accepted in a sealed lock.
            malformed.append(f"line {line_number}: unexpected option")
        elif current is None:
            malformed.append(f"line {line_number}: not a requirement")

    missing = sorted(name for name, entry in entries.items() if not entry["hashes"])
    public_only = "hermes-agent" not in entries
    ok = bool(entries) and not missing and not duplicates and not malformed and public_only
    return {
        "ok": ok,
        "entries": len(entries),
        "hashes": sum(len(entry["hashes"]) for entry in entries.values()),
        "missing_hashes": missing[:20],
        "duplicates": sorted(set(duplicates))[:20],
        "malformed": malformed[:20],
        "public_only": public_only,
    }


def bootstrap_hash_enforcement_state(path: Path = BOOTSTRAP_SCRIPT) -> dict[str, Any]:
    """Verify bootstrap uses pip's hash checker instead of pin-only installs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}
    required = ("--require-hashes", "--no-deps", "--no-input")
    missing = [flag for flag in required if flag not in text]
    return {"ok": not missing, "missing": missing}


def _command_result(command: list[str], runner: Callable[..., Any]) -> tuple[int, str, str]:
    try:
        result = runner(command, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=30)
        return int(result.returncode), str(result.stdout or ""), str(result.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", type(exc).__name__


def hermes_runtime_state(
    manifest_path: Path = HERMES_RUNTIME_MANIFEST,
    executable_finder: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Attest the separately installed Hermes checkout without trusting PATH alone."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return {"ok": False, "error": "invalid_manifest"}

    expected_version = manifest.get("version")
    expected_revision = manifest.get("source_revision")
    allowed_dirty = manifest.get("allowed_dirty_files", {})
    if (not isinstance(expected_version, str) or not isinstance(expected_revision, str) or
            not isinstance(allowed_dirty, dict)):
        return {"ok": False, "error": "invalid_manifest_fields"}

    command = str(manifest.get("command") or "hermes")
    executable = executable_finder(command)
    if not executable:
        return {"ok": False, "error": "executable_not_found"}
    code, output, _ = _command_result([executable, "--version"], runner)
    if code != 0:
        return {"ok": False, "error": "version_command_failed"}
    version_match = _VERSION_RE.search(output)
    install_match = _INSTALL_RE.search(output)
    if not version_match or not install_match:
        return {"ok": False, "error": "unparseable_runtime_identity"}
    source_root = Path(install_match.group("path"))

    code, revision, _ = _command_result(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], runner)
    if code != 0:
        return {"ok": False, "error": "source_revision_unavailable"}
    code, status, _ = _command_result(
        ["git", "-C", str(source_root), "status", "--porcelain=v1"], runner)
    if code != 0:
        return {"ok": False, "error": "source_status_unavailable"}

    dirty: dict[str, str] = {}
    for row in status.splitlines():
        if len(row) < 4:
            return {"ok": False, "error": "unparseable_source_status"}
        code_prefix, relative = row[:2], row[3:]
        if code_prefix != " M" or not relative or " -> " in relative:
            return {"ok": False, "error": "unexpected_source_change"}
        candidate = (source_root / relative).resolve()
        try:
            candidate.relative_to(source_root.resolve())
        except ValueError:
            return {"ok": False, "error": "source_path_escape"}
        if not candidate.is_file():
            return {"ok": False, "error": "dirty_source_file_missing"}
        dirty[relative.replace("\\", "/")] = _sha256(candidate)

    normalized_allowed = {
        str(name).replace("\\", "/"): str(value).lower().removeprefix("sha256:")
        for name, value in allowed_dirty.items()
    }
    version_ok = version_match.group("version") == expected_version
    revision_ok = revision.strip() == expected_revision
    dirty_ok = dirty == normalized_allowed
    return {
        "ok": version_ok and revision_ok and dirty_ok,
        "version": version_match.group("version"),
        "expected_version": expected_version,
        "revision": revision.strip(),
        "expected_revision": expected_revision,
        "dirty_files": sorted(dirty),
        "dirty_files_attested": dirty_ok,
        "storage": "external_hermes_checkout",
        "error": None if version_ok and revision_ok and dirty_ok else "attestation_mismatch",
    }
