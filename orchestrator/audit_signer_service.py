"""Dedicated-identity audit signer; never launched automatically by a worker."""
from __future__ import annotations

import argparse
import base64
import json
import re
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from audit_signer_protocol import (SignerError, VERSION, canonical, load_config,
                                   validate_checkpoint)

CREDENTIAL_TARGET = "AGI_like/dedicated_audit_signer_v2"


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _load_key():
    import win32cred
    record = win32cred.CredRead(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC, 0)
    blob = record["CredentialBlob"]
    if isinstance(blob, bytes):
        blob = blob.decode("utf-16-le")
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(blob, validate=True))


class AuditSigner:
    def __init__(self, config, key, service_sid):
        if service_sid != config.signer_sid or len({config.signer_sid, config.controller_sid, config.worker_sid}) != 3:
            raise SignerError("signer_identity_not_isolated")
        if public_bytes(key) != config.public_key:
            raise SignerError("signer_key_pin_mismatch")
        self.config, self._key = config, key

    def handle(self, request, peer_sid, restricted=False):
        if peer_sid != self.config.controller_sid or restricted:
            raise SignerError("audit_signer_caller_denied")
        if not isinstance(request, dict):
            raise SignerError("invalid_request")
        if request.get("op") == "sign" and set(request) == {"op", "checkpoint"}:
            payload = {"action": "audit_checkpoint", "checkpoint": validate_checkpoint(request["checkpoint"])}
        elif request.get("op") == "health" and set(request) == {"op", "nonce"}:
            if not isinstance(request["nonce"], str) or not re.fullmatch(r"[0-9a-f]{64}", request["nonce"]):
                raise SignerError("invalid_nonce")
            payload = {"action": "audit_signer_health", "nonce": request["nonce"]}
        else:
            raise SignerError("unsupported_signer_operation")
        raw = canonical(payload)
        token = ".".join((base64.b64encode(raw).decode("ascii"),
                          base64.b64encode(self._key.sign(raw)).decode("ascii"), VERSION))
        return {"ok": True, "token": token}


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "initialize-key"))
    parser.add_argument("--expected-sid", help="Explicit service SID required for key initialization")
    args = parser.parse_args()
    from audit_signer_pipe import current_sid, serve_pipe
    sid = current_sid()
    if args.command == "initialize-key":
        if not args.expected_sid or sid != args.expected_sid:
            raise SignerError("explicit_service_identity_required")
        import win32cred
        try:
            key = _load_key()
        except Exception as exc:
            if getattr(exc, "winerror", None) != 1168:
                raise  # Never overwrite unreadable or malformed credentials.
            key = Ed25519PrivateKey.generate()
            private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
            win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": CREDENTIAL_TARGET,
                                "CredentialBlob": base64.b64encode(private).decode("ascii"),
                                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)
        print(json.dumps({"public_key": base64.b64encode(public_bytes(key)).decode("ascii")}))
        return 0
    config = load_config()
    if sid != config.signer_sid:
        raise SignerError("wrong_service_identity")
    signer = AuditSigner(config, _load_key(), sid)
    stop = threading.Event()
    try:
        serve_pipe(config, signer.handle, stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


def main():
    try:
        return _main()
    except Exception as exc:
        # Credential decoding errors must not print blobs or private material.
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
