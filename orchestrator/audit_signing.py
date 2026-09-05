"""Public-key audit verification and RPC client; no private-key/vault access."""
from __future__ import annotations

import os
from typing import Any

from audit_signer_protocol import (SignerError, fingerprint, load_config,
                                   validate_checkpoint, verify_token)

ACTION = "audit_checkpoint"


class AuditSigningError(SignerError):
    """A checkpoint cannot be signed or verified against the trusted key."""


def sign_checkpoint(checkpoint: dict[str, Any]) -> str:
    """Return a purpose-bound signature token for one canonical checkpoint."""
    try:
        from audit_signer_pipe import request
        config = load_config()
        validate_checkpoint(checkpoint)
        response = request(config, {"op": "sign", "checkpoint": checkpoint})
        token = response.get("token")
        if verify_token(token, config) != {"action": ACTION, "checkpoint": checkpoint}:
            raise AuditSigningError("signer_response_not_bound_to_request")
        return token
    except Exception as exc:
        raise AuditSigningError(f"checkpoint_signing_failed:{type(exc).__name__}") from exc


def verify_checkpoint(token: str) -> dict[str, Any] | None:
    """Return a trusted checkpoint payload only for the audit action."""
    try:
        payload = verify_token(token, load_config())
        if not isinstance(payload, dict) or payload.get("action") != ACTION:
            return None
        checkpoint = payload.get("checkpoint")
        return checkpoint if isinstance(checkpoint, dict) else None
    except Exception:
        return None


def signer_state() -> dict[str, Any]:
    """Challenge the service and authenticate its response with the public pin."""
    try:
        from audit_signer_pipe import request
        config = load_config()
        nonce = os.urandom(32).hex()
        reply = request(config, {"op": "health", "nonce": nonce})
        trusted = verify_token(reply.get("token"), config)
        ok = trusted == {"action": "audit_signer_health", "nonce": nonce}
        return {
            "ok": ok, "storage": "dedicated_signer_service", "fingerprint": fingerprint(config),
            "error": None if ok else "signer_health_untrusted",
        }
    except Exception as exc:
        return {"ok": False, "error": f"audit_signer_unavailable:{type(exc).__name__}"}
