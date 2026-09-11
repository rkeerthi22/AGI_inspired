"""Minimal HTTPS CONNECT broker for a provisioned worker egress boundary.

This process is not the boundary by itself.  The worker must be OS-restricted
to this loopback broker, which is represented by egress_policy's signed
attestation.  The broker validates every CONNECT host, resolves it once, and
connects directly to that verified IP to prevent DNS rebinding.
"""
from __future__ import annotations

import argparse
import json
import select
import socket
import socketserver
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

import egress_policy


def _forward_chunk(target: socket.socket, data: bytes, timeout: float) -> bool:
    """Send all bytes of data to target socket using select for writability.

    Returns True if completely sent, False if connection closed, timed out, or errored.
    """
    view = memoryview(data)
    total_sent = 0
    total_len = len(view)
    while total_sent < total_len:
        _, writable, exceptional = select.select([], [target], [target], timeout)
        if exceptional or not writable:
            return False
        try:
            sent = target.send(view[total_sent:])
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            return False
        if sent <= 0:
            return False
        total_sent += sent
    return True


class ActiveBrokerCorrelation:
    """Context manager setting active task correlation for egress broker logging (F132)."""

    def __init__(
        self,
        runs_dir: Path,
        task_id: int | None,
        attempt: int = 1,
        audit_path: Path | None = None,
    ):
        self.runs_dir = runs_dir
        self.task_id = task_id
        self.attempt = attempt
        self.marker = runs_dir / "task_active_broker_correlation.json"
        self.audit_path = (
            audit_path or
            (runs_dir / f"task{task_id}_a{attempt}_broker.audit.jsonl" if task_id else None)
        )
        self.data = {
            "task_id": task_id,
            "attempt": attempt,
            "audit_path": str(self.audit_path) if self.audit_path else None,
        }

    def __enter__(self) -> ActiveBrokerCorrelation:
        if self.task_id is not None:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self.marker.write_text(json.dumps(self.data), encoding="utf-8")
        return self

    def __exit__(self, *args: object) -> None:
        self.marker.unlink(missing_ok=True)


class EgressBroker(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        policy: egress_policy.EgressPolicy,
        audit_path: Path | None = None,
        *,
        resolver: Callable[..., Any] | None = None,
        upstream_connector: Callable[..., socket.socket] | None = None,
        runs_dir: Path | None = None,
    ):
        self.policy = policy
        self.audit_path = audit_path
        self.resolver = resolver
        self.upstream_connector = upstream_connector or socket.create_connection
        self.runs_dir = runs_dir or (Path("runs") if Path("runs").is_dir() else None)
        self._active_correlation: dict[str, Any] | None = None
        super().__init__((policy.host, policy.port), BrokerHandler)

    def set_active_correlation(
        self, task_id: int | None, attempt: int = 1, audit_path: Path | None = None
    ) -> None:
        """Set or clear active task correlation for programmatic / in-process testing."""
        if task_id is None:
            self._active_correlation = None
        else:
            path = audit_path
            if path is None and self.runs_dir:
                path = self.runs_dir / f"task{task_id}_a{attempt}_broker.audit.jsonl"
            self._active_correlation = {
                "task_id": task_id,
                "attempt": attempt,
                "audit_path": str(path) if path else None,
            }

    def _resolve_attempt_audit(
        self, record: dict[str, Any]
    ) -> tuple[Path | None, dict[str, Any]]:
        corr_meta: dict[str, Any] = {}
        tid = record.get("task_id")
        attempt = record.get("attempt") or 1
        if tid is not None and self.runs_dir:
            # D2 (Codex Astra audit): validate task_id and attempt to prevent
            # path traversal (e.g. task_id="../../../etc/evil") that could write
            # audit logs outside runs_dir or impersonate another task's log.
            try:
                tid = int(tid)
                attempt = int(attempt)
                if tid < 0 or attempt < 1:
                    raise ValueError("negative task_id or non-positive attempt")
            except (TypeError, ValueError):
                return None, {}
            audit_path = self.runs_dir / f"task{tid}_a{attempt}_broker.audit.jsonl"
            if not audit_path.resolve().is_relative_to(self.runs_dir.resolve()):
                return None, {}
            corr_meta = {"task_id": tid, "attempt": attempt}
            return audit_path, corr_meta
        if self._active_correlation and self._active_correlation.get("audit_path"):
            corr_meta = {
                "task_id": self._active_correlation.get("task_id"),
                "attempt": self._active_correlation.get("attempt", 1),
            }
            return Path(self._active_correlation["audit_path"]), corr_meta
        if self.runs_dir:
            marker = self.runs_dir / "task_active_broker_correlation.json"
            if marker.is_file():
                try:
                    data = json.loads(marker.read_text(encoding="utf-8"))
                    if data.get("audit_path"):
                        corr_meta = {
                            "task_id": data.get("task_id"),
                            "attempt": data.get("attempt", 1),
                        }
                        return Path(data["audit_path"]), corr_meta
                except Exception:
                    pass
        return None, corr_meta

    def audit(self, **record: object) -> None:
        rec = dict(record)
        attempt_audit, corr_meta = self._resolve_attempt_audit(rec)
        if corr_meta:
            for k, v in corr_meta.items():
                if k not in rec and v is not None:
                    rec[k] = v

        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **rec}
        serialized = json.dumps(payload, sort_keys=True) + "\n"

        # 1. Main configured audit path (if any)
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)

        # 2. Correlated per-attempt audit logging (F132)
        if attempt_audit is not None and attempt_audit != self.audit_path:
            try:
                attempt_audit.parent.mkdir(parents=True, exist_ok=True)
                with attempt_audit.open("a", encoding="utf-8") as handle:
                    handle.write(serialized)
            except OSError:
                pass


class BrokerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Decisions are recorded structurally without URLs, headers, or bodies.
        return

    def _extract_correlation(self) -> dict[str, Any]:
        corr: dict[str, Any] = {}
        for h in ("X-Task-Id", "X-Harness-Task", "X-Task-ID"):
            val = self.headers.get(h)
            if val:
                try:
                    corr["task_id"] = int(val)
                except ValueError:
                    corr["task_id"] = val
                break
        for h in ("X-Attempt", "X-Harness-Attempt", "X-Attempt-Count"):
            val = self.headers.get(h)
            if val:
                try:
                    corr["attempt"] = int(val)
                except ValueError:
                    corr["attempt"] = val
                break
        return corr

    def do_CONNECT(self) -> None:  # noqa: N802 - HTTP verb naming is stdlib API
        self.close_connection = True
        raw_path = self.path or ""
        extracted_host = raw_path.split(":")[0] if ":" in raw_path else raw_path
        corr = self._extract_correlation()
        try:
            host, raw_port = self.path.rsplit(":", 1)
            port = int(raw_port)
            extracted_host = host
            resolver = getattr(self.server, "resolver", None)
            kwargs = {"resolver": resolver} if resolver is not None else {}
            host, addresses = egress_policy.authorize_destination(
                host, port, self.server.policy, **kwargs)  # type: ignore[attr-defined]
            connector = getattr(self.server, "upstream_connector", socket.create_connection)
            upstream = connector((addresses[0], port), timeout=15)
        except (ValueError, OSError, egress_policy.EgressPolicyError) as exc:
            self.server.audit(decision="deny", host=extracted_host, reason=str(exc)[:120], **corr)  # type: ignore[attr-defined]
            self.send_error(403, "egress denied")
            return

        self.server.audit(decision="allow", host=host, addresses=list(addresses), **corr)  # type: ignore[attr-defined]
        self.send_response(200, "Connection Established")
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            upstream.close()
            return

        self.connection.setblocking(False)
        upstream.setblocking(False)
        timeout = float(self.server.policy.idle_timeout_seconds)  # type: ignore[attr-defined]
        max_bytes = int(self.server.policy.max_connection_bytes)  # type: ignore[attr-defined]
        try:
            client = self.connection
            readers = [client, upstream]
            forwarded_bytes = 0
            while readers:
                readable, _, failed = select.select(readers, [], readers, timeout)
                if failed or not readable:
                    break
                for source in readable:
                    try:
                        data = source.recv(65536)
                    except OSError:
                        readers = []
                        break

                    target = upstream if source is client else client

                    if not data:
                        # Peer sent FIN / closed write direction
                        if source in readers:
                            readers.remove(source)
                        try:
                            target.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue

                    forwarded_bytes += len(data)
                    if forwarded_bytes > max_bytes:
                        self.server.audit(decision="deny", host=extracted_host, reason="connection_bytes_exceeded", **corr)  # type: ignore[attr-defined]
                        readers = []
                        break

                    if not _forward_chunk(target, data, timeout):
                        readers = []
                        break
        finally:
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            upstream.close()

    def do_GET(self) -> None:  # noqa: N802
        raw_path = self.path or ""
        extracted_host = raw_path.split(":")[0] if ":" in raw_path else raw_path
        corr = self._extract_correlation()
        if hasattr(self.server, "audit"):
            self.server.audit(decision="deny", host=extracted_host, reason="unsupported_method", **corr)  # type: ignore[attr-defined]
        self.send_error(405, "HTTPS CONNECT required")

    do_POST = do_GET  # type: ignore[assignment]
    do_PUT = do_GET  # type: ignore[assignment]
    do_DELETE = do_GET  # type: ignore[assignment]


def main() -> int:
    parser = argparse.ArgumentParser(description="AGI_like HTTPS egress broker")
    parser.add_argument("--policy", type=Path, default=egress_policy.POLICY_PATH)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    policy = egress_policy.load_policy(args.policy)
    broker = EgressBroker(policy, args.audit)
    try:
        broker.serve_forever()
    finally:
        broker.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
