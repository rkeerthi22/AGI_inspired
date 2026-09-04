"""Purpose-bound signatures for replicated audit checkpoints.

Checkpoint signatures reuse the locally trusted operator key while binding the
payload to the audit-checkpoint action. This is a deployable bridge until the
worker runs under a separate service identity backed by an external KMS; it
does not claim that same-user Credential Manager storage is enterprise RBAC.
"""
from __future__ import annotations

from typing import Any


ACTION = "audit_checkpoint"


class AuditSigningError(RuntimeError):
    """A checkpoint cannot be signed or verified against the trusted key."""


def sign_checkpoint(checkpoint: dict[str, Any]) -> str:
    """Return a purpose-bound signature token for one canonical checkpoint."""
    if not isinstance(checkpoint, dict):
        raise AuditSigningError("checkpoint_must_be_object")
    try:
        import operator_auth
        state = operator_auth.key_status()
        if state.get("present") is not True or state.get("storage") != "credential_manager":
            raise AuditSigningError("credential_manager_signer_required")
        return operator_auth.sign_marker({"action": ACTION, "checkpoint": checkpoint})
    except Exception as exc:
        raise AuditSigningError(f"checkpoint_signing_failed:{type(exc).__name__}") from exc


def verify_checkpoint(token: str) -> dict[str, Any] | None:
    """Return a trusted checkpoint payload only for the audit action."""
    try:
        import operator_auth
        payload = operator_auth.verify_marker(token)
    except Exception:
        return None
    checkpoint = payload.get("checkpoint") if isinstance(payload, dict) else None
    if payload.get("action") != ACTION or not isinstance(checkpoint, dict):
        return None
    return checkpoint


def signer_state() -> dict[str, Any]:
    """Expose key presence without reading or printing key material."""
    try:
        import operator_auth
        status = operator_auth.key_status()
        return {
            "ok": status.get("present") is True and
                  status.get("storage") == "credential_manager",
            "storage": status.get("storage"),
            "fingerprint": status.get("fingerprint"),
        }
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
