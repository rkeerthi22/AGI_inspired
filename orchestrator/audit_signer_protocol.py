"""Public-only configuration and verification for the dedicated audit signer."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_MESSAGE = 16384
VERSION = "audit-v2"
CLIENT_ACCESS = 0x12019B  # Read/write, but NOT FILE_CREATE_PIPE_INSTANCE (0x4).


class SignerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode_key(value: str) -> bytes:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise SignerError("invalid_public_key")
    return raw


@dataclass(frozen=True)
class SignerConfig:
    pipe: str
    signer_sid: str
    controller_sid: str
    worker_sid: str
    public_key: bytes
    legacy_public_keys: tuple[bytes, ...] = ()
    legacy_checkpoint_hashes: frozenset[str] = frozenset()


def load_config() -> SignerConfig:
    """Load an operator-protected public trust file; never infer trust from RPC."""
    raw = os.environ.get("HARNESS_AUDIT_SIGNER_CONFIG", "")
    if not raw:
        raise SignerError("audit_signer_config_missing")
    path = Path(raw)
    if not path.is_absolute() or path.stat().st_size > MAX_MESSAGE:
        raise SignerError("invalid_signer_config_path")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SignerError("invalid_signer_config")
    pipe = data.get("pipe", "")
    if not isinstance(pipe, str) or not re.fullmatch(r"\\\\\.\\pipe\\AGI_like_audit_[A-Za-z0-9_-]{1,64}", pipe):
        raise SignerError("local_audit_pipe_required")
    sids = [data.get(k) for k in ("signer_sid", "controller_sid", "worker_sid")]
    if any(not isinstance(s, str) or not re.fullmatch(r"S-1-5-(?:21-\d+-\d+-\d+-\d+|80-(?:\d+-){4}\d+)", s) for s in sids):
        raise SignerError("dedicated_account_sids_required")
    if len(set(sids)) != 3:
        raise SignerError("distinct_signer_controller_worker_required")
    legacy = data.get("legacy_public_keys", [])
    if not isinstance(legacy, list) or len(legacy) > 8:
        raise SignerError("invalid_legacy_trust")
    hashes = data.get("legacy_checkpoint_hashes", [])
    if not isinstance(hashes, list) or len(hashes) > 128 or any(
            not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        raise SignerError("invalid_legacy_checkpoint_pins")
    if bool(legacy) != bool(hashes):
        raise SignerError("legacy_keys_require_exact_checkpoint_pins")
    return SignerConfig(pipe, *sids, decode_key(data["public_key"]),
                        tuple(decode_key(k) for k in legacy), frozenset(hashes))


def validate_checkpoint(value: Any) -> dict:
    from audit_replication import _checkpoint_hash, _parse_timestamp
    required = {"schema_version", "task_id", "artifact_relative_path", "trajectory_sha256",
                "source_bytes", "replicated_at", "previous_checkpoint_hash", "checkpoint_hash"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
        raise SignerError("invalid_checkpoint_shape")
    if type(value["task_id"]) is not int or value["task_id"] < 0:
        raise SignerError("invalid_checkpoint_task")
    if type(value["source_bytes"]) is not int or value["source_bytes"] < 0:
        raise SignerError("invalid_checkpoint_size")
    path = value["artifact_relative_path"]
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_-]+/task\d+-[0-9a-f]{64}\.trajectory\.jsonl", path):
        raise SignerError("invalid_checkpoint_path")
    for field in ("trajectory_sha256", "checkpoint_hash"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise SignerError("invalid_checkpoint_hash")
    previous = value["previous_checkpoint_hash"]
    if not isinstance(previous, str) or not re.fullmatch(r"GENESIS|[0-9a-f]{64}", previous):
        raise SignerError("invalid_checkpoint_link")
    if _parse_timestamp(value["replicated_at"]) is None or value["checkpoint_hash"] != _checkpoint_hash(value):
        raise SignerError("invalid_checkpoint_integrity")
    return value


def verify_token(token: str, config: SignerConfig) -> dict | None:
    """Verify with configured PUBLIC keys only, including explicit legacy pins."""
    try:
        if not isinstance(token, str) or len(token) > MAX_MESSAGE:
            return None
        payload_b64, signature_b64, version = token.split(".")
        payload = base64.b64decode(payload_b64, validate=True)
        signature = base64.b64decode(signature_b64, validate=True)
        value = json.loads(payload)
        if not isinstance(value, dict):
            return None
        if version == VERSION:
            Ed25519PublicKey.from_public_bytes(config.public_key).verify(signature, payload)
        elif version == "v1":
            embedded = decode_key(value.get("_public_key", ""))
            if embedded not in config.legacy_public_keys:
                return None
            Ed25519PublicKey.from_public_bytes(embedded).verify(signature, payload)
            value.pop("_public_key")
            if value.get("action") != "audit_checkpoint":
                return None
            # A retired operator key may remain accessible to old workers. It
            # can authenticate only explicitly inventoried historical records,
            # never mint new checkpoints (including backdated ones).
            from audit_replication import _checkpoint_hash
            checkpoint = value.get("checkpoint")
            if not isinstance(checkpoint, dict) or checkpoint.get("checkpoint_hash") not in config.legacy_checkpoint_hashes:
                return None
            if checkpoint["checkpoint_hash"] != _checkpoint_hash(checkpoint):
                return None
        else:
            return None
        return value
    except Exception:
        return None


def fingerprint(config: SignerConfig) -> str:
    return hashlib.sha256(config.public_key).hexdigest()
