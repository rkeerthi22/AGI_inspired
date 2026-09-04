"""Signed, hash-chained replication of completed trajectory artifacts.

The local trajectory chain detects edits on one machine. This module copies a
verified trajectory to an operator-configured UNC replica and appends a signed
checkpoint linked to the preceding remote checkpoint. Missing, stale, or
tampered remote evidence fails release preflight closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "audit_retention.yaml"
GENESIS_HASH = "GENESIS"


class AuditReplicationError(RuntimeError):
    """A trajectory cannot be durably replicated and checkpointed."""


@dataclass(frozen=True)
class AuditRetentionConfig:
    root_environment_variable: str
    require_unc: bool
    artifact_subdirectory: str
    checkpoint_filename: str
    enforcement_environment_variable: str
    checkpoint_max_age_hours: int
    minimum_retention_days: int


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_relative(value: Any, *, filename: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditReplicationError("invalid_replica_path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise AuditReplicationError("invalid_replica_path")
    if filename and path.suffix != ".jsonl":
        raise AuditReplicationError("checkpoint_filename_must_be_jsonl")
    return value


def load_config(path: Path = CONFIG_PATH) -> AuditRetentionConfig:
    """Load the small, fail-closed audit replication configuration."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AuditReplicationError(f"config_unavailable:{type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise AuditReplicationError("invalid_config_schema")
    if data.get("mode") != "signed_hash_chain":
        raise AuditReplicationError("signed_hash_chain_required")
    replica = data.get("replica")
    if not isinstance(replica, dict):
        raise AuditReplicationError("replica_section_required")
    root_env = str(replica.get("root_environment_variable") or "").strip()
    enforce_env = str(data.get("enforcement_environment_variable") or "").strip()
    if not root_env or not enforce_env:
        raise AuditReplicationError("replica_environment_required")
    require_unc = replica.get("require_unc")
    if not isinstance(require_unc, bool):
        raise AuditReplicationError("require_unc_must_be_boolean")
    try:
        max_age = int(data.get("checkpoint_max_age_hours"))
        retention_days = int(data.get("minimum_retention_days"))
    except (TypeError, ValueError) as exc:
        raise AuditReplicationError("invalid_retention_numbers") from exc
    if max_age <= 0 or retention_days < 1:
        raise AuditReplicationError("unsafe_retention_numbers")
    return AuditRetentionConfig(
        root_environment_variable=root_env,
        require_unc=require_unc,
        artifact_subdirectory=_valid_relative(replica.get("artifact_subdirectory")),
        checkpoint_filename=_valid_relative(replica.get("checkpoint_filename"), filename=True),
        enforcement_environment_variable=enforce_env,
        checkpoint_max_age_hours=max_age,
        minimum_retention_days=retention_days,
    )


def enforcement_requested(config: AuditRetentionConfig | None = None,
                          environment: dict[str, str] | None = None) -> bool:
    config = config or load_config()
    env = os.environ if environment is None else environment
    return str(env.get(config.enforcement_environment_variable) or "").strip() == "1"


def _replica_root(config: AuditRetentionConfig,
                  environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    raw = str(env.get(config.root_environment_variable) or "").strip()
    if not raw:
        raise AuditReplicationError("replica_root_missing")
    if config.require_unc and not raw.startswith("\\\\"):
        raise AuditReplicationError("replica_root_must_be_unc")
    root = Path(raw)
    if not root.is_dir():
        raise AuditReplicationError("replica_root_unavailable")
    return root


def _checkpoint_path(root: Path, config: AuditRetentionConfig) -> Path:
    return root / config.checkpoint_filename


def _checkpoint_hash(checkpoint: dict[str, Any]) -> str:
    material = {key: value for key, value in checkpoint.items()
                if key != "checkpoint_hash"}
    return hashlib.sha256(_canonical_bytes(material)).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_artifact_relative(value: Any, config: AuditRetentionConfig) -> Path | None:
    if not isinstance(value, str):
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if len(candidate.parts) != 2 or candidate.parts[0] != config.artifact_subdirectory:
        return None
    if not candidate.name.endswith(".trajectory.jsonl"):
        return None
    return candidate


def verify_checkpoint_chain(
    checkpoint_path: Path,
    config: AuditRetentionConfig,
    verify_token: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    """Verify signed remote checkpoint history without touching it."""
    if not checkpoint_path.is_file():
        return {"ok": True, "count": 0, "latest": None,
                "latest_checkpoint": None, "error": None}
    previous = GENESIS_HASH
    count = 0
    latest: dict[str, Any] | None = None
    try:
        lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "count": count, "error": type(exc).__name__}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "count": count, "error": "checkpoint_json_invalid"}
        if not isinstance(record, dict):
            return {"ok": False, "count": count, "error": "checkpoint_record_invalid"}
        checkpoint = record.get("checkpoint")
        signature = record.get("signature")
        trusted = verify_token(signature) if isinstance(signature, str) else None
        if not isinstance(checkpoint, dict) or trusted != checkpoint:
            return {"ok": False, "count": count, "error": "checkpoint_signature_invalid"}
        if checkpoint.get("schema_version") != 1 or checkpoint.get("previous_checkpoint_hash") != previous:
            return {"ok": False, "count": count, "error": "checkpoint_link_invalid"}
        if _safe_artifact_relative(checkpoint.get("artifact_relative_path"), config) is None:
            return {"ok": False, "count": count, "error": "checkpoint_artifact_invalid"}
        digest = checkpoint.get("trajectory_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return {"ok": False, "count": count, "error": "checkpoint_digest_invalid"}
        if not isinstance(checkpoint.get("source_bytes"), int) or checkpoint["source_bytes"] < 0:
            return {"ok": False, "count": count, "error": "checkpoint_size_invalid"}
        if _parse_timestamp(checkpoint.get("replicated_at")) is None:
            return {"ok": False, "count": count, "error": "checkpoint_time_invalid"}
        checkpoint_hash = checkpoint.get("checkpoint_hash")
        if not isinstance(checkpoint_hash, str) or checkpoint_hash != _checkpoint_hash(checkpoint):
            return {"ok": False, "count": count, "error": "checkpoint_hash_invalid"}
        previous = checkpoint_hash
        latest = checkpoint
        count += 1
    return {"ok": True, "count": count,
            "latest": latest.get("replicated_at") if latest else None,
            "latest_checkpoint": latest, "error": None}


def _copy_immutable(source: Path, destination: Path, expected_digest: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256_file(destination) != expected_digest:
            raise AuditReplicationError("replica_artifact_tampered")
        return
    temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if _sha256_file(temporary) != expected_digest:
            raise AuditReplicationError("replica_copy_digest_mismatch")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _append_checkpoint(path: Path, checkpoint: dict[str, Any], signature: str) -> None:
    record = json.dumps({"checkpoint": checkpoint, "signature": signature},
                        sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record)
        handle.flush()
        os.fsync(handle.fileno())


def replicate_trajectory(
    trajectory_path: Path,
    config_path: Path = CONFIG_PATH,
    environment: dict[str, str] | None = None,
    sign_checkpoint: Callable[[dict[str, Any]], str] | None = None,
    verify_checkpoint: Callable[[str], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Copy one locally verified trajectory and append its signed checkpoint."""
    config = load_config(config_path)
    source = Path(trajectory_path)
    if not source.is_file() or not source.name.endswith(".trajectory.jsonl"):
        raise AuditReplicationError("trajectory_artifact_invalid")
    import trajectory
    if not trajectory.verify_chain(source):
        raise AuditReplicationError("local_trajectory_chain_invalid")
    root = _replica_root(config, environment)
    digest = _sha256_file(source)
    task_name = source.name[:-len(".trajectory.jsonl")]
    artifact_name = f"{task_name}-{digest}.trajectory.jsonl"
    artifact_relative = Path(config.artifact_subdirectory) / artifact_name
    destination = root / artifact_relative
    _copy_immutable(source, destination, digest)
    if verify_checkpoint is None:
        import audit_signing
        verify_checkpoint = audit_signing.verify_checkpoint
    chain = verify_checkpoint_chain(_checkpoint_path(root, config), config, verify_checkpoint)
    if chain.get("ok") is not True:
        raise AuditReplicationError(f"remote_checkpoint_invalid:{chain.get('error')}")
    checkpoint = {
        "schema_version": 1,
        "task_id": int(re.search(r"task(\d+)", source.name).group(1))
        if re.search(r"task(\d+)", source.name) else None,
        "artifact_relative_path": artifact_relative.as_posix(),
        "trajectory_sha256": digest,
        "source_bytes": source.stat().st_size,
        "replicated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "previous_checkpoint_hash": (chain.get("latest_checkpoint") or {}).get(
            "checkpoint_hash", GENESIS_HASH),
    }
    checkpoint["checkpoint_hash"] = _checkpoint_hash(checkpoint)
    if sign_checkpoint is None:
        import audit_signing
        sign_checkpoint = audit_signing.sign_checkpoint
    signature = sign_checkpoint(checkpoint)
    _append_checkpoint(_checkpoint_path(root, config), checkpoint, signature)
    return {"artifact_relative_path": artifact_relative.as_posix(),
            "trajectory_sha256": digest, "checkpoint_hash": checkpoint["checkpoint_hash"]}


def replicate_if_enforced(trajectory_path: Path) -> dict[str, Any] | None:
    """Replicate only in an explicitly provisioned release environment."""
    config = load_config()
    if not enforcement_requested(config):
        return None
    return replicate_trajectory(trajectory_path)


def audit_state(
    config_path: Path = CONFIG_PATH,
    environment: dict[str, str] | None = None,
    verify_checkpoint: Callable[[str], dict[str, Any] | None] | None = None,
    signing_state: Callable[[], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only release diagnostic for remote audit durability."""
    try:
        config = load_config(config_path)
        if not enforcement_requested(config, environment):
            return {"ok": False, "error": "audit_enforcement_not_enabled"}
        if signing_state is None:
            import audit_signing
            signing_state = audit_signing.signer_state
        signer = signing_state()
        if signer.get("ok") is not True:
            return {"ok": False, "error": "audit_signer_unavailable", "signer": signer}
        root = _replica_root(config, environment)
        if verify_checkpoint is None:
            import audit_signing
            verify_checkpoint = audit_signing.verify_checkpoint
        chain = verify_checkpoint_chain(_checkpoint_path(root, config), config, verify_checkpoint)
        latest = chain.get("latest_checkpoint")
        timestamp = _parse_timestamp(chain.get("latest"))
        current = now or datetime.now(timezone.utc)
        fresh = timestamp is not None and timestamp <= current and \
            current - timestamp <= timedelta(hours=config.checkpoint_max_age_hours)
        relative = _safe_artifact_relative(
            latest.get("artifact_relative_path") if isinstance(latest, dict) else None, config)
        artifact = root / relative if relative is not None else None
        artifact_ok = bool(artifact and artifact.is_file() and latest and
                           _sha256_file(artifact) == latest.get("trajectory_sha256"))
        ok = chain.get("ok") is True and chain.get("count", 0) > 0 and fresh and artifact_ok
        return {
            "ok": ok,
            "replica_root": str(root),
            "checkpoints": chain.get("count", 0),
            "latest": chain.get("latest"),
            "fresh": fresh,
            "artifact_ok": artifact_ok,
            "minimum_retention_days": config.minimum_retention_days,
            "error": None if ok else (chain.get("error") or "checkpoint_missing_or_stale"),
        }
    except AuditReplicationError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
