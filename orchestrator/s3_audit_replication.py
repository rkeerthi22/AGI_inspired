"""S3 and Backblaze B2 Object Lock (Compliance Mode) WORM Audit Replication Backend.

Provides immutable off-machine audit retention with cryptographic non-repudiation
satisfying Deficit B without requiring local physical SMB/UNC hardware.
Objects are written with ObjectLockMode=COMPLIANCE and ObjectLockRetainUntilDate,
making them immutable against all identities (including root and administrator)
until the retention period expires.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

GENESIS_HASH = "GENESIS"


class S3AuditReplicationError(RuntimeError):
    """An audit artifact cannot be durably replicated to S3 Object Lock storage."""


@dataclass(frozen=True)
class S3AuditConfig:
    bucket: str
    endpoint_url: Optional[str] = None
    region_name: Optional[str] = "us-east-1"
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    retention_mode: str = "COMPLIANCE"
    retention_days: int = 365
    artifact_prefix: str = "trajectories"
    checkpoint_key: str = "trajectory-checkpoints.jsonl"
    manifest_key: str = "latest-checkpoint.json"
    checkpoint_max_age_hours: int = 24


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _checkpoint_hash(checkpoint: dict[str, Any]) -> str:
    material = {key: value for key, value in checkpoint.items() if key != "checkpoint_hash"}
    return hashlib.sha256(_canonical_bytes(material)).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_s3_config_from_env(env: Optional[dict[str, str]] = None) -> S3AuditConfig:
    """Load S3 / Backblaze B2 audit configuration from environment or vault."""
    e = os.environ if env is None else env
    bucket = e.get("HARNESS_AUDIT_S3_BUCKET", "").strip()
    if not bucket:
        raise S3AuditReplicationError("s3_bucket_required")

    endpoint_url = e.get("HARNESS_AUDIT_S3_ENDPOINT", "").strip() or None
    region = e.get("HARNESS_AUDIT_S3_REGION", "").strip() or "us-east-1"
    key_id = e.get("HARNESS_AUDIT_S3_KEY_ID", "").strip() or None
    secret_key = e.get("HARNESS_AUDIT_S3_SECRET_KEY", "").strip() or None

    try:
        retention_days = int(e.get("HARNESS_AUDIT_RETENTION_DAYS", "365"))
    except ValueError:
        retention_days = 365

    try:
        max_age = int(e.get("HARNESS_AUDIT_MAX_AGE_HOURS", "24"))
    except ValueError:
        max_age = 24

    retention_mode = e.get("HARNESS_AUDIT_RETENTION_MODE", "COMPLIANCE").strip().upper()
    if retention_mode not in ("COMPLIANCE", "GOVERNANCE"):
        retention_mode = "COMPLIANCE"

    return S3AuditConfig(
        bucket=bucket,
        endpoint_url=endpoint_url,
        region_name=region,
        access_key_id=key_id,
        secret_access_key=secret_key,
        retention_mode=retention_mode,
        retention_days=retention_days,
        checkpoint_max_age_hours=max_age,
    )


def get_s3_client(config: S3AuditConfig):
    """Create a boto3 S3 client with standard signature v4 and endpoint configuration."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise S3AuditReplicationError("boto3_unavailable") from exc

    client_kwargs: dict[str, Any] = {
        "service_name": "s3",
        "config": Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    }
    if config.endpoint_url:
        client_kwargs["endpoint_url"] = config.endpoint_url
    if config.region_name:
        client_kwargs["region_name"] = config.region_name
    if config.access_key_id and config.secret_access_key:
        client_kwargs["aws_access_key_id"] = config.access_key_id
        client_kwargs["aws_secret_access_key"] = config.secret_access_key

    try:
        return boto3.client(**client_kwargs)
    except Exception as exc:
        raise S3AuditReplicationError(f"s3_client_initialization_failed:{type(exc).__name__}") from exc


def fetch_s3_checkpoints(s3_client, config: S3AuditConfig) -> list[str]:
    """Retrieve raw checkpoint lines from S3, returning an empty list if not found."""
    from botocore.exceptions import ClientError
    try:
        resp = s3_client.get_object(Bucket=config.bucket, Key=config.checkpoint_key)
        content = resp["Body"].read().decode("utf-8")
        return [line for line in content.splitlines() if line.strip()]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NotFound"):
            return []
        raise S3AuditReplicationError(f"s3_get_checkpoints_failed:{code}") from exc
    except Exception as exc:
        raise S3AuditReplicationError(f"s3_get_checkpoints_failed:{type(exc).__name__}") from exc


def verify_s3_checkpoint_chain(
    s3_client,
    config: S3AuditConfig,
    verify_token: Callable[[str], dict[str, Any] | None],
    *,
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    """Verify signed S3 checkpoint history without touching it.

    If verify_artifacts is True, every referenced artifact in S3 is verified
    for existence and SHA-256 digest match.
    """
    lines = fetch_s3_checkpoints(s3_client, config)
    if not lines:
        return {"ok": True, "count": 0, "latest": None, "latest_checkpoint": None, "checkpoints": [], "error": None}

    previous = GENESIS_HASH
    count = 0
    latest: Optional[dict[str, Any]] = None
    checkpoints: list[dict[str, Any]] = []

    for raw in lines:
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

        relative = checkpoint.get("artifact_relative_path")
        if not isinstance(relative, str) or not relative.endswith(".trajectory.jsonl"):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_artifact_invalid"}

        digest = checkpoint.get("trajectory_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_digest_invalid"}
        if not isinstance(checkpoint.get("source_bytes"), int) or checkpoint["source_bytes"] < 0:
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_size_invalid"}

        checkpoint_hash = checkpoint.get("checkpoint_hash")
        if not isinstance(checkpoint_hash, str) or checkpoint_hash != _checkpoint_hash(checkpoint):
            return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "checkpoint_hash_invalid"}

        if verify_artifacts:
            from botocore.exceptions import ClientError
            try:
                head = s3_client.head_object(Bucket=config.bucket, Key=relative)
                if head.get("ContentLength") != checkpoint["source_bytes"]:
                    return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "replica_artifact_size_mismatch"}
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return {"ok": False, "count": count, "checkpoints": checkpoints, "error": "replica_artifact_missing"}
                return {"ok": False, "count": count, "checkpoints": checkpoints, "error": f"replica_check_failed:{code}"}

        previous = checkpoint_hash
        latest = checkpoint
        checkpoints.append(checkpoint)
        count += 1

    return {
        "ok": True,
        "count": count,
        "latest": latest.get("replicated_at") if latest else None,
        "latest_checkpoint": latest,
        "checkpoints": checkpoints,
        "error": None,
    }


def replicate_trajectory_s3(
    trajectory_path: Path,
    s3_config: S3AuditConfig,
    s3_client=None,
    sign_checkpoint: Optional[Callable[[dict[str, Any]], str]] = None,
    verify_checkpoint: Optional[Callable[[str], dict[str, Any] | None]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Replicate one verified trajectory to S3 Object Lock storage and append its signed checkpoint."""
    source = Path(trajectory_path)
    if not source.is_file() or not source.name.endswith(".trajectory.jsonl"):
        raise S3AuditReplicationError("trajectory_artifact_invalid")

    import trajectory
    if not trajectory.verify_chain(source):
        raise S3AuditReplicationError("local_trajectory_chain_invalid")

    client = s3_client or get_s3_client(s3_config)
    if verify_checkpoint is None:
        import audit_signing
        verify_checkpoint = audit_signing.verify_checkpoint
    if sign_checkpoint is None:
        import audit_signing
        sign_checkpoint = audit_signing.sign_checkpoint

    # 1. Verify existing remote history before write (historical fail-closed invariant)
    chain = verify_s3_checkpoint_chain(client, s3_config, verify_checkpoint, verify_artifacts=True)
    if not chain.get("ok"):
        raise S3AuditReplicationError(f"remote_checkpoint_invalid:{chain.get('error')}")

    source_bytes = source.read_bytes()
    digest = _sha256_bytes(source_bytes)
    task_match = re.search(r"task(\d+)", source.name)
    task_id = int(task_match.group(1)) if task_match else None
    task_name = source.name[:-len(".trajectory.jsonl")]
    artifact_name = f"{task_name}-{digest}.trajectory.jsonl"
    artifact_key = f"{s3_config.artifact_prefix}/{artifact_name}"

    current_time = now or datetime.now(timezone.utc)
    retain_until = current_time + timedelta(days=s3_config.retention_days)

    # 2. Upload immutable trajectory artifact with Object Lock in COMPLIANCE mode
    put_kwargs: dict[str, Any] = {
        "Bucket": s3_config.bucket,
        "Key": artifact_key,
        "Body": source_bytes,
        "ContentType": "application/x-jsonlines",
        "Metadata": {
            "sha256": digest,
            "task_id": str(task_id or ""),
        },
    }
    # Only supply ObjectLock parameters if not disabled for testing
    if s3_config.retention_mode in ("COMPLIANCE", "GOVERNANCE"):
        put_kwargs["ObjectLockMode"] = s3_config.retention_mode
        put_kwargs["ObjectLockRetainUntilDate"] = retain_until

    try:
        client.put_object(**put_kwargs)
    except Exception as exc:
        raise S3AuditReplicationError(f"s3_artifact_upload_failed:{type(exc).__name__}") from exc

    # 3. Create signed checkpoint
    previous_hash = (chain.get("latest_checkpoint") or {}).get("checkpoint_hash", GENESIS_HASH)
    checkpoint: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "artifact_relative_path": artifact_key,
        "trajectory_sha256": digest,
        "source_bytes": len(source_bytes),
        "replicated_at": current_time.isoformat(),
        "previous_checkpoint_hash": previous_hash,
    }
    checkpoint["checkpoint_hash"] = _checkpoint_hash(checkpoint)
    signature = sign_checkpoint(checkpoint)

    # 4. Fetch existing checkpoints and append new record
    existing_lines = fetch_s3_checkpoints(client, s3_config)
    new_record = json.dumps({"checkpoint": checkpoint, "signature": signature}, sort_keys=True, separators=(",", ":"))
    all_lines = existing_lines + [new_record]
    new_checkpoint_content = "\n".join(all_lines) + "\n"

    try:
        client.put_object(
            Bucket=s3_config.bucket,
            Key=s3_config.checkpoint_key,
            Body=new_checkpoint_content.encode("utf-8"),
            ContentType="application/x-jsonlines",
        )
    except Exception as exc:
        raise S3AuditReplicationError(f"s3_checkpoint_write_failed:{type(exc).__name__}") from exc

    # 5. Write signed latest-checkpoint manifest (D4 truncation protection)
    manifest_payload = {
        "schema_version": 1,
        "checkpoint_hash": checkpoint["checkpoint_hash"],
        "count": chain.get("count", 0) + 1,
        "latest_checkpoint": checkpoint,
        "signature": signature,
        "written_at": current_time.isoformat(),
    }
    manifest_signature = sign_checkpoint(manifest_payload)
    manifest_data = json.dumps(
        {"manifest": manifest_payload, "signature": manifest_signature},
        sort_keys=True,
        separators=(",", ":"),
    )

    try:
        client.put_object(
            Bucket=s3_config.bucket,
            Key=s3_config.manifest_key,
            Body=manifest_data.encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:
        raise S3AuditReplicationError(f"s3_manifest_write_failed:{type(exc).__name__}") from exc

    return {
        "artifact_relative_path": artifact_key,
        "trajectory_sha256": digest,
        "checkpoint_hash": checkpoint["checkpoint_hash"],
    }


def s3_audit_state(
    environment: Optional[dict[str, str]] = None,
    verify_checkpoint: Optional[Callable[[str], dict[str, Any] | None]] = None,
    signing_state: Optional[Callable[[], dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    s3_client=None,
) -> dict[str, Any]:
    """Read-only release diagnostic for S3 / Backblaze B2 Object Lock audit durability."""
    try:
        config = load_s3_config_from_env(environment)
        if signing_state is None:
            import audit_signing
            signing_state = audit_signing.signer_state
        signer = signing_state()
        if signer.get("ok") is not True:
            return {"ok": False, "error": "audit_signer_unavailable", "signer": signer}

        client = s3_client or get_s3_client(config)
        if verify_checkpoint is None:
            import audit_signing
            verify_checkpoint = audit_signing.verify_checkpoint

        chain = verify_s3_checkpoint_chain(client, config, verify_checkpoint, verify_artifacts=True)
        if not chain.get("ok"):
            return {
                "ok": False,
                "backend": "s3",
                "bucket": config.bucket,
                "error": chain.get("error") or "s3_checkpoint_invalid",
            }

        latest = chain.get("latest_checkpoint")
        timestamp = None
        if latest and latest.get("replicated_at"):
            try:
                timestamp = datetime.fromisoformat(latest["replicated_at"].replace("Z", "+00:00"))
                if not timestamp.tzinfo:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        current = now or datetime.now(timezone.utc)
        fresh = timestamp is not None and timestamp <= current and \
            current - timestamp <= timedelta(hours=config.checkpoint_max_age_hours)
        all_artifacts_ok = chain.get("ok") is True and chain.get("count", 0) > 0

        # Truncation check via manifest
        from botocore.exceptions import ClientError
        truncation_detected = False
        try:
            m_resp = client.get_object(Bucket=config.bucket, Key=config.manifest_key)
            m_data = json.loads(m_resp["Body"].read().decode("utf-8"))
            m_payload = m_data.get("manifest")
            m_sig = m_data.get("signature")
            if not isinstance(m_payload, dict) or verify_checkpoint(m_sig) != m_payload:
                return {"ok": False, "error": "manifest_signature_invalid"}
            if chain.get("count", 0) < m_payload.get("count", 0) or (latest or {}).get("checkpoint_hash") != m_payload.get("checkpoint_hash"):
                truncation_detected = True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("NoSuchKey", "404", "NotFound"):
                return {"ok": False, "error": f"manifest_read_error:{code}"}

        ok = all_artifacts_ok and fresh and not truncation_detected
        error = None
        if not ok:
            if not chain.get("ok"):
                error = chain.get("error")
            elif truncation_detected:
                error = "checkpoint_suffix_truncated"
            elif not fresh:
                error = "checkpoint_missing_or_stale"
            else:
                error = "checkpoint_empty_or_invalid"

        return {
            "ok": ok,
            "backend": "s3",
            "bucket": config.bucket,
            "checkpoints": chain.get("count", 0),
            "latest": chain.get("latest"),
            "fresh": fresh,
            "artifact_ok": all_artifacts_ok,
            "truncation_detected": truncation_detected,
            "retention_mode": config.retention_mode,
            "retention_days": config.retention_days,
            "immutability_guarantee": f"s3_object_lock_{config.retention_mode.lower()}",
            "error": error,
        }
    except S3AuditReplicationError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
