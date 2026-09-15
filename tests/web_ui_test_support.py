"""Temporary, model-free fixtures shared by gateway and web security tests."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import yaml
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import egress_policy
import operator_auth
import policy_manager
import trust_gateway
import web_ui

def init_mock_ledger(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT,
                spec TEXT,
                pass_criteria TEXT,
                status TEXT,
                critic_verdict TEXT,
                critic_notes TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                model_used TEXT,
                started_at TEXT,
                finished_at TEXT,
                lease_expires_at TEXT,
                owner_pid INTEGER,
                owner_process_start_id TEXT,
                run_id TEXT,
                attempt_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    finally:
        conn.close()


def local_http_request(
    port: int,
    method: str = "GET",
    path: str = "/",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Execute HTTP request using raw socket to loopback, complying with sitecustomize.py."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(("127.0.0.1", port))
    with s:
        hdrs = {
            "Host": f"127.0.0.1:{port}",
            "Connection": "close",
        }
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
        if headers:
            hdrs.update(headers)

        lines = [f"{method} {path} HTTP/1.1"]
        for k, v in hdrs.items():
            lines.append(f"{k}: {v}")
        raw = "\r\n".join(lines).encode("latin1") + b"\r\n\r\n"
        if body:
            raw += body
        s.sendall(raw)

        # Read entire response
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)

        header_end = buf.find(b"\r\n\r\n")
        if header_end == -1:
            return 0, {}, bytes(buf)

        hdr_lines = buf[:header_end].decode("latin1", errors="replace").split("\r\n")
        status_parts = hdr_lines[0].split()
        status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0

        resp_headers = {}
        for line in hdr_lines[1:]:
            if ":" in line:
                hk, hv = line.split(":", 1)
                resp_headers[hk.strip().lower()] = hv.strip()

        body_bytes = bytes(buf[header_end + 4:])
        return status_code, resp_headers, body_bytes



@contextmanager
def fixture(*, paused=True):
    with tempfile.TemporaryDirectory(prefix="web_security_") as raw:
        root = Path(raw)
        home = root / "hermes"
        home.mkdir()
        (home / "config.yaml").write_text("{}", encoding="utf-8")
        sentinel = home / "ESTOP"
        if paused:
            sentinel.write_text("fixture pause", encoding="utf-8")
        policy_path = root / "egress_policy.yaml"
        policy_data = yaml.safe_load((ROOT / "config" / "egress_policy.yaml").read_text(encoding="utf-8"))
        policy_data["broker"]["allowed_hosts"] = ["initial.example.com"]
        policy_path.write_text(json.dumps(policy_data), encoding="utf-8")
        db = root / "ledger.db"
        init_mock_ledger(db)
        runs = root / "runs"
        runs.mkdir()
        gw = trust_gateway.Gateway(db, runs, root / "attestation.signed")
        gw.policy_mgr = policy_manager.PolicyManager(
            policy_path, runs / "candidates.jsonl", runs / "approvals.jsonl",
            re_sign_runner=lambda: True,
        )
        keys = operator_auth._generate_keypair()
        # Verification/signing remain real; storage alone is replaced. No host keys.
        with patch.dict(os.environ, {"HERMES_HOME": str(home)}), patch(
            "operator_auth._load_keypair", return_value=keys
        ):
            yield SimpleNamespace(root=root, home=home, sentinel=sentinel, gw=gw, keys=keys)


def sign_attestation(f, **overrides):
    policy = egress_policy.load_policy(f.gw.policy_mgr.policy_path)
    claims = {name: "a" * 64 if name.endswith("_sha256") else "fixture-claim"
              for name in policy.required_claims}
    claims["worker_identity"] = "fixture-worker"
    payload = {
        "purpose": policy.attestation_purpose,
        "policy_sha256": policy.digest,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "broker_endpoint": f"{policy.host}:{policy.port}",
        "claims": claims,
        "evidence": sorted(policy.required_evidence),
        **overrides,
    }
    token = operator_auth.sign_marker(payload)
    f.gw.attest_path.write_text(token, encoding="utf-8")
    return token


@contextmanager
def serving(gw):
    server = web_ui.WebConsoleServer("127.0.0.1", 0, gw)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def request(server, method="GET", path="/", payload=None, *, token=True, body=None, headers=None):
    hdrs = dict(headers or {})
    if token is True:
        token = server.bearer_token
    if token is not None and token is not False:
        hdrs["Authorization"] = "Bearer " + str(token)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    return local_http_request(server.server_address[1], method, path, body, hdrs)
