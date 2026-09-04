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

import egress_policy


class EgressBroker(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, policy: egress_policy.EgressPolicy, audit_path: Path | None):
        self.policy = policy
        self.audit_path = audit_path
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
        try:
            host, raw_port = self.path.rsplit(":", 1)
            port = int(raw_port)
            host, addresses = egress_policy.authorize_destination(
                host, port, self.server.policy)  # type: ignore[attr-defined]
            upstream = socket.create_connection((addresses[0], port), timeout=15)
        except (ValueError, OSError, egress_policy.EgressPolicyError) as exc:
            self.server.audit(decision="deny", reason=str(exc)[:120])  # type: ignore[attr-defined]
            self.send_error(403, "egress denied")
            return
        self.server.audit(decision="allow", host=host, addresses=list(addresses))  # type: ignore[attr-defined]
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        upstream.setblocking(False)
        try:
            sockets = [self.connection, upstream]
            forwarded_bytes = 0
            while sockets:
                readable, _, failed = select.select(
                    sockets, [], sockets, self.server.policy.idle_timeout_seconds)  # type: ignore[attr-defined]
                if failed or not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        sockets = []
                        break
                    forwarded_bytes += len(data)
                    if forwarded_bytes > self.server.policy.max_connection_bytes:  # type: ignore[attr-defined]
                        self.server.audit(decision="deny", reason="connection_bytes_exceeded")  # type: ignore[attr-defined]
                        sockets = []
                        break
                    target = upstream if source is self.connection else self.connection
                    target.sendall(data)
        finally:
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
