"""Read-only credential lookup for provider authentication.

Windows Credential Manager is preferred when available. The harness never
creates, updates, logs, or otherwise exposes credential values; environment
variables remain the backward-compatible fallback for existing deployments.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import os
import random
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
_SYSRAND = random.SystemRandom()

# This module sits on a top-level sys.path entry (`orchestrator/`), so its
# filename shadows Python's stdlib `secrets` module for child processes that
# import plain `secrets`. Expose the common stdlib API here as a compatibility
# shim so downstream libraries do not break when they expect `secrets.token_bytes`.
SystemRandom = random.SystemRandom
compare_digest = hmac.compare_digest


def randbelow(exclusive_upper_bound: int) -> int:
    if exclusive_upper_bound <= 0:
        raise ValueError("Upper bound must be positive")
    return _SYSRAND.randrange(exclusive_upper_bound)


def randbits(k: int) -> int:
    if k < 0:
        raise ValueError("Number of bits must be non-negative")
    return _SYSRAND.getrandbits(k)


def choice(sequence):
    if len(sequence) == 0:
        raise IndexError("Cannot choose from an empty sequence")
    return _SYSRAND.choice(sequence)


def token_bytes(nbytes: int | None = None) -> bytes:
    size = 32 if nbytes is None else int(nbytes)
    if size < 0:
        raise ValueError("Number of bytes must be non-negative")
    return os.urandom(size)


def token_hex(nbytes: int | None = None) -> str:
    return binascii.hexlify(token_bytes(nbytes)).decode("ascii")


def token_urlsafe(nbytes: int | None = None) -> str:
    return base64.urlsafe_b64encode(token_bytes(nbytes)).rstrip(b"=").decode("ascii")


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
