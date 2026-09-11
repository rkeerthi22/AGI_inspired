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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "audit_retention.yaml"
GENESIS_HASH = "GENESIS"
CHECKPOINT_LOCK_TIMEOUT_SECONDS = 10.0


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


@contextmanager
def _checkpoint_lock(checkpoint_path: Path):
    """Serialize cooperating writers through a persistent OS-locked sidecar.

    Never unlink or steal this file: replacing it can create two lock domains.
    Shared-store deployments must prove server-side locking on their actual SMB
    configuration; this is not a distributed lease or a fencing-token service.
    """
    try:
        import portalocker
    except ImportError as exc:
        raise AuditReplicationError("replica_lock_backend_unavailable") from exc
    lock = portalocker.Lock(
        checkpoint_path.with_name(checkpoint_path.name + ".lock"),
        mode="a+b", timeout=CHECKPOINT_LOCK_TIMEOUT_SECONDS,
        check_interval=0.05, flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
    )
    try:
        handle = lock.acquire()
    except (OSError, portalocker.exceptions.LockException) as exc:
        raise AuditReplicationError(f"replica_lock_unavailable:{type(exc).__name__}") from exc
    try:
        yield
    finally:
        try:
            lock.release()
        finally:
            handle.close()


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
    *,
    replica_root: Path | None = None,
) -> dict[str, Any]:
    """Verify signed remote checkpoint history without touching it.

    If replica_root is provided, every referenced historical artifact is also
    verified for existence, SHA-256 digest match, and byte size.
    """
    if not checkpoint_path.is_file():
        return {"ok": True, "count": 0, "latest": None,
                "latest_checkpoint": None, "checkpoints": [], "error": None}
    previous = GENESIS_HASH
    count = 0
    latest: dict[str, Any] | None = None
    checkpoints: list[dict[str, Any]] = []
    try:
        lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "count": count, "checkpoints": [], "error": type(exc).__name__}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_json_invalid"}
        if not isinstance(record, dict):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_record_invalid"}
        checkpoint = record.get("checkpoint")
        signature = record.get("signature")
        trusted = verify_token(signature) if isinstance(signature, str) else None
        if not isinstance(checkpoint, dict) or trusted != checkpoint:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_signature_invalid"}
        if checkpoint.get("schema_version") != 1 or checkpoint.get("previous_checkpoint_hash") != previous:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_link_invalid"}
        relative = _safe_artifact_relative(checkpoint.get("artifact_relative_path"), config)
        if relative is None:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_artifact_invalid"}
        digest = checkpoint.get("trajectory_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_digest_invalid"}
        if not isinstance(checkpoint.get("source_bytes"), int) or checkpoint["source_bytes"] < 0:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_size_invalid"}
        if _parse_timestamp(checkpoint.get("replicated_at")) is None:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_time_invalid"}
        checkpoint_hash = checkpoint.get("checkpoint_hash")
        if not isinstance(checkpoint_hash, str) or checkpoint_hash != _checkpoint_hash(checkpoint):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_hash_invalid"}

        if replica_root is not None:
            artifact = replica_root / relative
            if not artifact.is_file():
                return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "replica_artifact_missing"}
            if _sha256_file(artifact) != digest:
                return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "replica_artifact_tampered"}
            if artifact.stat().st_size != checkpoint["source_bytes"]:
                return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "replica_artifact_size_mismatch"}

        previous = checkpoint_hash
        latest = checkpoint
        checkpoints.append(checkpoint)
        count += 1
    return {"ok": True, "count": count,
            "latest": latest.get("replicated_at") if latest else None,
            "latest_checkpoint": latest,
            "checkpoints": checkpoints,
            "error": None}


def _latest_manifest_path(root: Path) -> Path:
    return root / "latest-checkpoint.json"


def _write_latest_manifest(
    root: Path,
    checkpoint: dict[str, Any],
    signature: str,
    count: int,
    sign_checkpoint: Callable[[dict[str, Any]], str],
) -> None:
    """Write signed latest-checkpoint manifest to detect chain suffix truncation (D4)."""
    manifest_path = _latest_manifest_path(root)
    payload = {
        "schema_version": 1,
        "checkpoint_hash": checkpoint["checkpoint_hash"],
        "count": count,
        "latest_checkpoint": checkpoint,
        "signature": signature,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_signature = sign_checkpoint(payload)
    data = json.dumps({"manifest": payload, "signature": manifest_signature},
                      sort_keys=True, separators=(",", ":"))
    try:
        temp = manifest_path.with_name(manifest_path.name + f".{os.getpid()}.tmp")
        temp.write_text(data, encoding="utf-8")
        os.replace(temp, manifest_path)
    except OSError as exc:
        raise AuditReplicationError(f"replica_manifest_write_failed:{type(exc).__name__}") from exc


def _copy_immutable(source: Path, destination: Path, expected_digest: str) -> None:
    try:
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
    except OSError as exc:
        raise AuditReplicationError(f"replica_write_failed:{type(exc).__name__}") from exc


def _append_checkpoint(path: Path, checkpoint: dict[str, Any], signature: str) -> None:
    record = json.dumps({"checkpoint": checkpoint, "signature": signature},
                        sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AuditReplicationError(f"replica_checkpoint_write_failed:{type(exc).__name__}") from exc


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
    if verify_checkpoint is None:
        import audit_signing
        verify_checkpoint = audit_signing.verify_checkpoint
    if sign_checkpoint is None:
        import audit_signing
        sign_checkpoint = audit_signing.sign_checkpoint
    checkpoint_path = _checkpoint_path(root, config)
    # The tip must be read under the SAME lock held through durable append.
    # Verify history before copying so retries cannot silently repair a missing
    # historical replica and erase evidence of the retention failure.
    with _checkpoint_lock(checkpoint_path):
        chain = verify_checkpoint_chain(
            checkpoint_path, config, verify_checkpoint, replica_root=root)
        if chain.get("ok") is not True:
            raise AuditReplicationError(f"remote_checkpoint_invalid:{chain.get('error')}")
        try:
            _copy_immutable(source, destination, digest)
        except OSError as exc:
            raise AuditReplicationError(f"replica_write_failed:{type(exc).__name__}") from exc
        checkpoint = {
            "schema_version": 1,
            "task_id": int(re.search(r"task(\d+)", source.name).group(1))
            if re.search(r"task(\d+)", source.name) else None,
            "artifact_relative_path": artifact_relative.as_posix(),
            "trajectory_sha256": digest,
            "source_bytes": destination.stat().st_size,
            "replicated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "previous_checkpoint_hash": (chain.get("latest_checkpoint") or {}).get(
                "checkpoint_hash", GENESIS_HASH),
        }
        checkpoint["checkpoint_hash"] = _checkpoint_hash(checkpoint)
        signature = sign_checkpoint(checkpoint)
        _append_checkpoint(checkpoint_path, checkpoint, signature)
        _write_latest_manifest(root, checkpoint, signature, chain.get("count", 0) + 1, sign_checkpoint)
    return {"artifact_relative_path": artifact_relative.as_posix(),
            "trajectory_sha256": digest, "checkpoint_hash": checkpoint["checkpoint_hash"]}


def replicate_if_enforced(
    trajectory_path: Path,
    config_path: Path = CONFIG_PATH,
    environment: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Replicate only in an explicitly provisioned release environment.

    Fail-closed invariant (B2):
    When enforcement is enabled (HARNESS_AUDIT_ENFORCE=1), replication errors
    (unreachable UNC, WORM reject, write failure) FAIL HARD. Silently falling
    back to local-only is strictly forbidden when enforcement is configured.
    """
    config = load_config(config_path)
    if not enforcement_requested(config, environment):
        return None
    return replicate_trajectory(trajectory_path, config_path=config_path, environment=environment)


def retention_floor_check(
    chain: dict[str, Any],
    config: AuditRetentionConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify that retained checkpoint history spans at least minimum_retention_days (D4)."""
    checkpoints = chain.get("checkpoints") or []
    if not checkpoints:
        return {"ok": False, "error": "checkpoint_chain_empty", "chain_days": 0.0,
                "minimum_retention_days": config.minimum_retention_days}
    earliest_ts = _parse_timestamp(checkpoints[0].get("replicated_at"))
    latest_ts = _parse_timestamp(checkpoints[-1].get("replicated_at"))
    if not earliest_ts or not latest_ts:
        return {"ok": False, "error": "checkpoint_timestamps_unparseable", "chain_days": 0.0,
                "minimum_retention_days": config.minimum_retention_days}
    chain_days = (latest_ts - earliest_ts).total_seconds() / 86400.0
    if chain_days < config.minimum_retention_days:
        return {
            "ok": False,
            "error": "chain_retention_below_minimum",
            "chain_days": chain_days,
            "minimum_retention_days": config.minimum_retention_days,
        }
    return {
        "ok": True,
        "error": None,
        "chain_days": chain_days,
        "minimum_retention_days": config.minimum_retention_days,
    }


def audit_state(
    config_path: Path = CONFIG_PATH,
    environment: dict[str, str] | None = None,
    verify_checkpoint: Callable[[str], dict[str, Any] | None] | None = None,
    signing_state: Callable[[], dict[str, Any]] | None = None,
    now: datetime | None = None,
    enforce_retention_floor: bool | None = None,
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
        chain = verify_checkpoint_chain(
            _checkpoint_path(root, config), config, verify_checkpoint, replica_root=root)
        latest = chain.get("latest_checkpoint")
        timestamp = _parse_timestamp(chain.get("latest"))
        current = now or datetime.now(timezone.utc)
        fresh = timestamp is not None and timestamp <= current and \
            current - timestamp <= timedelta(hours=config.checkpoint_max_age_hours)
        all_artifacts_ok = chain.get("ok") is True and chain.get("count", 0) > 0

        # D4(b): Truncation detection via signed latest-checkpoint manifest
        manifest_path = _latest_manifest_path(root)
        truncation_detected = False
        if chain.get("ok") is True and manifest_path.is_file():
            try:
                manifest_record = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_payload = manifest_record.get("manifest")
                manifest_sig = manifest_record.get("signature")
                if not isinstance(manifest_payload, dict):
                    return {"ok": False, "error": "manifest_invalid"}
                trusted_manifest = verify_checkpoint(manifest_sig) if isinstance(manifest_sig, str) else None
                if trusted_manifest != manifest_payload:
                    return {"ok": False, "error": "manifest_signature_invalid"}
                expected_hash = manifest_payload.get("checkpoint_hash")
                expected_count = manifest_payload.get("count")
                actual_hash = (latest or {}).get("checkpoint_hash")
                actual_count = chain.get("count", 0)
                if actual_count < expected_count or actual_hash != expected_hash:
                    truncation_detected = True
            except Exception as exc:
                return {"ok": False, "error": f"manifest_read_error:{type(exc).__name__}"}

        # D4(a): Retention floor check
        floor = retention_floor_check(chain, config, now=current)
        env = os.environ if environment is None else environment
        should_enforce_floor = (
            enforce_retention_floor
            if enforce_retention_floor is not None
            else (str(env.get("HARNESS_AUDIT_ENFORCE_RETENTION_FLOOR") or "").strip() == "1")
        )

        ok = all_artifacts_ok and fresh and not truncation_detected
        if should_enforce_floor and not floor["ok"]:
            ok = False

        error = None
        if not ok:
            if not chain.get("ok"):
                error = chain.get("error") or "checkpoint_missing_or_stale"
            elif truncation_detected:
                error = "checkpoint_suffix_truncated"
            elif should_enforce_floor and not floor["ok"]:
                error = floor.get("error") or "retention_floor_not_met"
            else:
                error = "checkpoint_missing_or_stale"

        return {
            "ok": ok,
            "replica_root": str(root),
            "checkpoints": chain.get("count", 0),
            "latest": chain.get("latest"),
            "fresh": fresh,
            "artifact_ok": all_artifacts_ok,
            "truncation_detected": truncation_detected,
            "retention_floor_ok": floor["ok"],
            "retention_floor_days": floor["chain_days"],
            "minimum_retention_days": config.minimum_retention_days,
            # D4(c): honest immutability claim — code detects tampering,
            # but storage-level write protection requires operator WORM provisioning.
            "immutability_guarantee": "detection_only_storage_worm_required",
            "error": error,
        }
    except AuditReplicationError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
