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
    ):
        self.policy = policy
        self.audit_path = audit_path
        self.resolver = resolver
        self.upstream_connector = upstream_connector or socket.create_connection
        super().__init__((policy.host, policy.port), BrokerHandler)

    def audit(self, **record: object) -> None:
        if self.audit_path is None:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


class BrokerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Decisions are recorded structurally without URLs, headers, or bodies.
        return

    def do_CONNECT(self) -> None:  # noqa: N802 - HTTP verb naming is stdlib API
        self.close_connection = True
        try:
            host, raw_port = self.path.rsplit(":", 1)
            port = int(raw_port)
            resolver = getattr(self.server, "resolver", None)
            kwargs = {"resolver": resolver} if resolver is not None else {}
            host, addresses = egress_policy.authorize_destination(
                host, port, self.server.policy, **kwargs)  # type: ignore[attr-defined]
            connector = getattr(self.server, "upstream_connector", socket.create_connection)
            upstream = connector((addresses[0], port), timeout=15)
        except (ValueError, OSError, egress_policy.EgressPolicyError) as exc:
            self.server.audit(decision="deny", reason=str(exc)[:120])  # type: ignore[attr-defined]
            self.send_error(403, "egress denied")
            return

        self.server.audit(decision="allow", host=host, addresses=list(addresses))  # type: ignore[attr-defined]
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
                        self.server.audit(decision="deny", reason="connection_bytes_exceeded")  # type: ignore[attr-defined]
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
