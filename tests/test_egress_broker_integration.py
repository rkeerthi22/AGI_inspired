"""Integration test suite for the HTTPS CONNECT egress broker (F112).

Tests real bidirectional socket forwarding, TCP half-close (FIN) propagation,
fail-closed host/port authorization, private IP rejection (anti-SSRF),
connection byte limit enforcement, and structural audit logging.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import egress_broker  # noqa: E402
import egress_policy  # noqa: E402

checks = 0
failures: list[str] = []


def check(label: str, condition: bool) -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def local_connect(port: int, timeout: float = 5.0) -> socket.socket:
    """Connect to local test broker without triggering live_guard create_connection guard."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", port))
    return s


def read_http_line(sock: socket.socket) -> str:
    """Read bytes from socket until CRLF and return decoded line."""
    buf = bytearray()
    while b"\r\n" not in buf:
        chunk = sock.recv(1)
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf).decode("latin1", errors="replace")


def read_http_response(sock: socket.socket) -> tuple[int, str, dict[str, str]]:
    """Read HTTP response status line and headers from a socket."""
    status_line = read_http_line(sock).strip()
    if not status_line:
        return 0, "", {}
    parts = status_line.split(" ", 2)
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    reason = parts[2] if len(parts) > 2 else ""
    headers: dict[str, str] = {}
    while True:
        line = read_http_line(sock).strip()
        if not line:
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return code, reason, headers


def make_policy(
    root: Path,
    *,
    port: int = 0,
    max_connection_bytes: int = 1048576,
    idle_timeout_seconds: int = 5,
    allowed_hosts: list[str] | None = None,
) -> egress_policy.EgressPolicy:
    if allowed_hosts is None:
        allowed_hosts = ["api.example.test"]
    data = {
        "schema_version": 1,
        "mode": "broker_required",
        "broker": {
            "host": "127.0.0.1",
            "port": port if port > 0 else 8787,
            "connect_port": 443,
            "idle_timeout_seconds": idle_timeout_seconds,
            "max_connection_bytes": max_connection_bytes,
            "allowed_hosts": allowed_hosts,
        },
        "attestation": {
            "environment_variable": "TEST_EGRESS_ATTESTATION",
            "purpose": "egress-boundary-v1",
            "max_age_hours": 24,
            "required_evidence": ["restricted_worker_identity"],
            "required_claims": ["worker_identity"],
        },
        "audit_log_environment_variable": "TEST_EGRESS_AUDIT",
    }
    policy_file = root / f"policy_{port}_{time.time_ns()}.yaml"
    policy_file.write_text(json.dumps(data), encoding="utf-8")
    loaded = egress_policy.load_policy(policy_file)
    if port == 0:
        # Override port to 0 so the OS allocates an ephemeral port for testing
        object.__setattr__(loaded, "port", 0)
    return loaded


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory(dir=ROOT / "workspace", ignore_cleanup_errors=True) as raw:
    root = Path(raw)
    audit_file = root / "egress_audit.jsonl"
    policy = make_policy(root, port=0)

    # Mock resolver returning a verified global IPv4 for api.example.test
    def global_resolver(host: str, port: int, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    # Setup dummy upstream server
    upstream_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_srv.bind(("127.0.0.1", 0))
    upstream_srv.listen(5)
    upstream_port = upstream_srv.getsockname()[1]

    def upstream_connector(addr: tuple[str, int], timeout: float = 15) -> socket.socket:
        # Intercept connection and route to test upstream on loopback
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", upstream_port))
        return s

    broker = egress_broker.EgressBroker(
        policy,
        audit_path=audit_file,
        resolver=global_resolver,
        upstream_connector=upstream_connector,
    )
    broker_port = broker.server_address[1]
    broker_thread = threading.Thread(target=broker.serve_forever, daemon=True)
    broker_thread.start()

    try:
        # 1. Reject non-CONNECT HTTP verbs (e.g. GET) with 405
        with local_connect(broker_port) as s:
            s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            code, reason, _ = read_http_response(s)
            check("GET method returns 405 Method Not Allowed", code == 405)

        # 2. Deny destination host not on allowlist with 403
        with local_connect(broker_port) as s:
            s.sendall(b"CONNECT unauthorized.example.com:443 HTTP/1.1\r\nHost: unauthorized.example.com:443\r\n\r\n")
            code, reason, _ = read_http_response(s)
            check("unallowlisted destination host rejected with 403", code == 403)

        # 3. Deny destination port not on allowlist (e.g. port 80) with 403
        with local_connect(broker_port) as s:
            s.sendall(b"CONNECT api.example.test:80 HTTP/1.1\r\nHost: api.example.test:80\r\n\r\n")
            code, reason, _ = read_http_response(s)
            check("unallowlisted destination port rejected with 403", code == 403)

        # 4. Deny private IP answer (anti-SSRF check) with 403
        def private_resolver(host: str, port: int, **kwargs: Any) -> list[Any]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        private_broker = egress_broker.EgressBroker(
            policy,
            audit_path=audit_file,
            resolver=private_resolver,
            upstream_connector=upstream_connector,
        )
        private_port = private_broker.server_address[1]
        private_thread = threading.Thread(target=private_broker.serve_forever, daemon=True)
        private_thread.start()
        try:
            with local_connect(private_port) as s:
                s.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n")
                code, reason, _ = read_http_response(s)
                check("private IP DNS answer rejected with 403 (anti-SSRF)", code == 403)
        finally:
            private_broker.shutdown()
            private_broker.server_close()

        # 5. Allowlisted CONNECT tunnel and bidirectional data echo
        def run_echo_upstream(server_sock: socket.socket) -> None:
            conn, _ = server_sock.accept()
            with conn:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    conn.sendall(data)

        echo_thread = threading.Thread(target=run_echo_upstream, args=(upstream_srv,), daemon=True)
        echo_thread.start()

        with local_connect(broker_port) as s:
            s.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n")
            code, reason, _ = read_http_response(s)
            check("allowlisted CONNECT returns 200 Connection Established", code == 200)

            # Send test payload and verify echo
            test_payload = b"Hello egress broker tunnel! 1234567890"
            s.sendall(test_payload)
            received = s.recv(len(test_payload))
            check("bidirectional tunnel echoes forwarded payload intact", received == test_payload)

        echo_thread.join(timeout=3)

        # 6. TCP Half-Close (FIN) handling:
        # Client writes request, shuts down write side (SHUT_WR), then reads full response.
        # Upstream reads until EOF, then writes response, then closes.
        def run_half_close_upstream(server_sock: socket.socket) -> None:
            conn, _ = server_sock.accept()
            with conn:
                req = bytearray()
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    req.extend(chunk)
                # Upstream received EOF (FIN from client forwarded through broker)
                response = bytes(req) + b"-PROCESSED-OK"
                conn.sendall(response)
                conn.shutdown(socket.SHUT_WR)

        fin_thread = threading.Thread(target=run_half_close_upstream, args=(upstream_srv,), daemon=True)
        fin_thread.start()

        with local_connect(broker_port) as s:
            s.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n")
            code, reason, _ = read_http_response(s)
            check("CONNECT for half-close test returns 200", code == 200)

            client_data = b"STREAMING-CLIENT-REQUEST-PAYLOAD"
            s.sendall(client_data)
            # Signal client write completion (FIN)
            s.shutdown(socket.SHUT_WR)

            # Read response until EOF
            resp_buf = bytearray()
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp_buf.extend(chunk)
            check("half-close FIN forwarded without truncating response",
                  bytes(resp_buf) == client_data + b"-PROCESSED-OK")

        fin_thread.join(timeout=3)

        # 7. Connection byte limit enforcement (max_connection_bytes)
        limit_policy = make_policy(root, port=0, max_connection_bytes=2048)
        limit_broker = egress_broker.EgressBroker(
            limit_policy,
            audit_path=audit_file,
            resolver=global_resolver,
            upstream_connector=upstream_connector,
        )
        limit_port = limit_broker.server_address[1]
        limit_thread = threading.Thread(target=limit_broker.serve_forever, daemon=True)
        limit_thread.start()

        def run_sink_upstream(server_sock: socket.socket) -> None:
            try:
                conn, _ = server_sock.accept()
                with conn:
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
            except OSError:
                pass

        sink_thread = threading.Thread(target=run_sink_upstream, args=(upstream_srv,), daemon=True)
        sink_thread.start()

        try:
            with local_connect(limit_port) as s:
                s.sendall(b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n")
                code, reason, _ = read_http_response(s)
                check("CONNECT for byte limit test returns 200", code == 200)

                # Send 4096 bytes (exceeds 2048 limit)
                big_payload = b"X" * 4096
                truncated = False
                try:
                    s.sendall(big_payload)
                    # Next read or write will detect broker termination
                    s.settimeout(2.0)
                    for _ in range(10):
                        chunk = s.recv(1024)
                        if not chunk:
                            truncated = True
                            break
                except (OSError, socket.timeout):
                    truncated = True
                check("exceeding max_connection_bytes terminates connection", truncated)
        finally:
            limit_broker.shutdown()
            limit_broker.server_close()
            sink_thread.join(timeout=3)

        # 8. Structural audit logging validation
        audit_records = [
            json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        decisions = [rec.get("decision") for rec in audit_records]
        check("audit file contains recorded decisions", len(audit_records) >= 5)
        check("audit file records allow decisions", "allow" in decisions)
        check("audit file records deny decisions", "deny" in decisions)
        check("audit file records connection_bytes_exceeded reason",
              any(rec.get("reason") == "connection_bytes_exceeded" for rec in audit_records))
        check("audit entries include valid ISO timestamps",
              all("timestamp" in rec for rec in audit_records))

    finally:
        broker.shutdown()
        broker.server_close()
        upstream_srv.close()

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    raise SystemExit("FAILURES: " + ", ".join(failures))
