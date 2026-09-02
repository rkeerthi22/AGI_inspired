"""Read-only credential lookup for provider authentication.

Windows Credential Manager is preferred when available. The harness never
creates, updates, logs, or otherwise exposes credential values; environment
variables remain the backward-compatible fallback for existing deployments.
"""
from __future__ import annotations

import os
from typing import Any

try:  # pywin32 is Windows-only and intentionally optional for import safety.
    import win32cred as _win32cred
except ImportError:  # pragma: no cover - exercised on non-Windows hosts
    _win32cred = None


_CREDENTIAL_PREFIX = "AGI_like/"
_ENVIRONMENT_KEYS = {
    "byteplus_coding": "ARK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def credential_target(provider: str) -> str | None:
    """Return the generic Credential Manager target for a known provider."""
    normalized = provider.strip().lower()
    return f"{_CREDENTIAL_PREFIX}{normalized}" if normalized in _ENVIRONMENT_KEYS else None


def _credential_blob(record: dict[str, Any]) -> str | None:
    blob = record.get("CredentialBlob")
    if isinstance(blob, bytes):
        try:
            blob = blob.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return blob.strip() if isinstance(blob, str) and blob.strip() else None


def get_api_key(provider: str) -> str | None:
    """Read a provider key without surfacing its value in diagnostics.

    Credential Manager failures are intentionally indistinguishable from a
    missing value to callers. This fail-closed behavior permits the existing
    environment-variable fallback without leaking store metadata or secrets.
    """
    normalized = provider.strip().lower()
    target = credential_target(normalized)
    if target is None:
        return None
    if _win32cred is not None:
        try:
            record = _win32cred.CredRead(target, _win32cred.CRED_TYPE_GENERIC, 0)
            value = _credential_blob(record)
            if value:
                return value
        except Exception:
            pass
    value = os.environ.get(_ENVIRONMENT_KEYS[normalized], "").strip()
    return value or None
