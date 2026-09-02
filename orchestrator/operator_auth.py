"""Cryptographic operator identity for signed authorization markers.

Provides Ed25519 signing of operator actions (clear, canary) so that
authorization markers are cryptographically bound to the operator's
verified identity rather than being plain file-based tokens.

Key design decisions:
- Ed25519 (``cryptography`` package, already installed).
- Private key stored in Windows Credential Manager as ``AGI_like/operator_key``.
- Public key embedded in signed tokens for offline verification.
- Backward compatible: unsigned JSON markers are still accepted with a
  ``PendingDeprecationWarning``, so existing workflows are not broken.
- No dependency on the ``jose`` or ``PyJWT`` packages — we use a compact
  custom format: ``base64(payload) || '.' || base64(signature)``.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Key management ──────────────────────────────────────────────────────────

_CREDENTIAL_TARGET = "AGI_like/operator_key"

# Try to import cryptography; degrade gracefully if unavailable.
try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False
    InvalidSignature = type("InvalidSignature", (Exception,), {})  # type: ignore[assignment]
    ed25519 = None  # type: ignore[assignment]
    serialization = None  # type: ignore[assignment]

try:
    import win32cred as _win32cred  # type: ignore[import-untyped]
except ImportError:
    _win32cred = None


class OperatorAuthError(RuntimeError):
    """Operator identity or signature operation failed."""


def _hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser()
        if not home.is_absolute():
            raise OperatorAuthError("HERMES_HOME must be an absolute path")
        return home
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def _fallback_key_path() -> Path:
    return _hermes_home() / ".operator_key"


def _legacy_fallback_key_path() -> Path:
    return Path(__file__).resolve().parent / ".operator_key"


def _credential_read(target: str) -> dict[str, Any] | None:
    """Read a generic Credential Manager record; returns None on failure."""
    if _win32cred is None:
        return None
    try:
        return _win32cred.CredRead(target, _win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        return None


def _credential_write(target: str, value: str) -> bool:
    """Write a generic Credential Manager record. Returns True on success."""
    if _win32cred is None:
        return False
    try:
        blob = value.encode("utf-8")
        _win32cred.CredWrite(
            {"Type": _win32cred.CRED_TYPE_GENERIC,
             "TargetName": target,
             "CredentialBlob": blob,
             "Persist": _win32cred.CRED_PERSIST_LOCAL_MACHINE,
             "UserName": "operator"}, 0)
        return True
    except Exception:
        return False


def _generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair.

    Returns
    -------
    (private_bytes, public_bytes)
        Both in raw 32-byte format (not PEM).
    """
    if not _HAS_CRYPTOGRAPHY:
        raise OperatorAuthError(
            "cryptography package is required for operator identity")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (private_bytes, public_bytes)


def _store_keypair(private_bytes: bytes, public_bytes: bytes) -> None:
    """Store keypair in Credential Manager as a JSON blob."""
    blob = json.dumps({
        "private_key": base64.b64encode(private_bytes).decode("ascii"),
        "public_key": base64.b64encode(public_bytes).decode("ascii"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "Ed25519",
    })
    if not _credential_write(_CREDENTIAL_TARGET, blob):
        # Fallback: store under Hermes home, never inside the repository.
        fallback = _fallback_key_path()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(blob, encoding="utf-8")
        os.chmod(fallback, 0o600)


def _decode_keypair_blob(blob: str) -> tuple[bytes, bytes] | None:
    try:
        data = json.loads(blob)
        private = base64.b64decode(data["private_key"])
        public = base64.b64decode(data["public_key"])
        return (private, public)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _load_keypair_with_storage() -> tuple[tuple[bytes, bytes] | None, str | None]:
    """Load keypair from the supported stores and report which store won."""
    record = _credential_read(_CREDENTIAL_TARGET)
    if record:
        blob_raw = record.get("CredentialBlob")
        if isinstance(blob_raw, bytes):
            loaded = _decode_keypair_blob(blob_raw.decode("utf-8", errors="strict"))
            if loaded is not None:
                return loaded, "credential_manager"
    for path, storage in (
            (_fallback_key_path(), "hermes_home_file"),
            (_legacy_fallback_key_path(), "legacy_repo_file")):
        if not path.is_file():
            continue
        try:
            loaded = _decode_keypair_blob(path.read_text(encoding="utf-8"))
        except OSError:
            loaded = None
        if loaded is not None:
            return loaded, storage
    return None, None


def _load_keypair() -> tuple[bytes, bytes] | None:
    """Load keypair from Credential Manager or supported fallback files."""
    loaded, _storage = _load_keypair_with_storage()
    return loaded


def _get_or_create_keypair() -> tuple[bytes, bytes]:
    """Return the existing keypair or generate and store a new one."""
    existing = _load_keypair()
    if existing is not None:
        return existing
    private, public = _generate_keypair()
    _store_keypair(private, public)
    return (private, public)


def _reconstruct_signer(private_bytes: bytes):
    """Reconstruct an Ed25519 private key from raw bytes."""
    if not _HAS_CRYPTOGRAPHY:
        raise OperatorAuthError("cryptography package is required for signing")
    return ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)


def _reconstruct_verifier(public_bytes: bytes):
    """Reconstruct an Ed25519 public key from raw bytes."""
    if not _HAS_CRYPTOGRAPHY:
        raise OperatorAuthError("cryptography package is required for verification")
    return ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)


# ── Signing and verification ────────────────────────────────────────────────

SIGNATURE_VERSION = "v1"


def sign_marker(payload: dict) -> str:
    """Sign an operator marker payload and return a compact signed token.

    Format: ``base64(json_payload) || '.' || base64(signature) || '.' || v1``

    The public key is embedded in the payload as ``_public_key`` so that
    offline verification is possible.
    """
    private_bytes, public_bytes = _get_or_create_keypair()
    public_b64 = base64.b64encode(public_bytes).decode("ascii")

    signer = _reconstruct_signer(private_bytes)
    token_payload = {**payload, "_public_key": public_b64}
    payload_bytes = json.dumps(token_payload, separators=(",", ":")).encode("utf-8")
    signature = signer.sign(payload_bytes)

    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
    sig_b64 = base64.b64encode(signature).decode("ascii")
    return f"{payload_b64}.{sig_b64}.{SIGNATURE_VERSION}"


def verify_marker(token: str) -> dict | None:
    """Verify a signed marker token and return the payload, or None.

    For backward compatibility, plain JSON strings (unsigned markers) are
    returned with a ``PendingDeprecationWarning``.
    """
    # Backward compatibility: plain JSON
    if token.startswith("{"):
        warnings.warn(
            "Unsigned operator marker (plain JSON) — consider upgrading to "
            "signed markers via operator_auth.sign_marker()",
            PendingDeprecationWarning, stacklevel=2,
        )
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload_b64, sig_b64, version = parts
    if version != SIGNATURE_VERSION:
        return None

    try:
        payload_bytes = base64.b64decode(payload_b64)
        signature = base64.b64decode(sig_b64)
    except Exception:
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    public_b64 = payload.get("_public_key")
    if not isinstance(public_b64, str):
        return None

    try:
        public_bytes = base64.b64decode(public_b64)
    except Exception:
        return None

    try:
        verifier = _reconstruct_verifier(public_bytes)
        verifier.verify(signature, payload_bytes)
    except InvalidSignature:
        return None
    except OperatorAuthError:
        return None

    # Strip the embedded public key before returning
    result = {k: v for k, v in payload.items() if k != "_public_key"}
    return result


def marker_is_signed(marker_path: Path) -> bool:
    """Check if a marker file contains a signed token (vs. plain JSON)."""
    try:
        raw = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return not raw.startswith("{") and "." in raw


def public_key_fingerprint() -> str | None:
    """Return a short hex fingerprint of the operator's public key, or None."""
    try:
        _, public_bytes = _get_or_create_keypair()
        import hashlib
        return hashlib.sha256(public_bytes).hexdigest()[:16]
    except (OperatorAuthError, RuntimeError):
        return None


def key_status() -> dict:
    """Return a diagnostic dict about the operator key state."""
    try:
        existing, storage = _load_keypair_with_storage()
        if existing is not None:
            _, public_bytes = existing
            import hashlib
            fp = hashlib.sha256(public_bytes).hexdigest()[:16]
            return {"present": True, "algorithm": "Ed25519",
                    "fingerprint": fp,
                    "storage": storage}
        return {"present": False, "algorithm": None,
                "fingerprint": None, "storage": None}
    except Exception as exc:
        return {"present": False, "error": str(exc)}
