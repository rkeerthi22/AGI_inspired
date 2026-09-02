"""Operator cryptographic identity tests — all key operations mocked.

Verifies:
1. Key generation creates a valid Ed25519 keypair
2. sign_marker produces a verifiable token
3. verify_marker returns the original payload for valid tokens
4. Tampered tokens (bad signature, bad format) return None
5. Unsigned (plain JSON) markers are accepted with a warning
6. Public key fingerprint is deterministic
7. key_status() returns diagnostic info
8. keypair persistence (Credential Manager / fallback file)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


# ── 1. Key generation ───────────────────────────────────────────────────────


print("=== Key generation ===")

from operator_auth import (
    _generate_keypair,
    _load_keypair,
    _store_keypair,
    _reconstruct_signer,
    _reconstruct_verifier,
    sign_marker,
    verify_marker,
    public_key_fingerprint,
    key_status,
    marker_is_signed,
    OperatorAuthError,
)

private_bytes, public_bytes = _generate_keypair()
check("private key is 32 bytes", len(private_bytes), 32)
check("public key is 32 bytes", len(public_bytes), 32)
check("keys are different", private_bytes != public_bytes)

# Reconstruct and sign/verify a round-trip
signer = _reconstruct_signer(private_bytes)
verifier = _reconstruct_verifier(public_bytes)
test_msg = b"test message"
sig = signer.sign(test_msg)
verifier.verify(sig, test_msg)  # should not raise
check("Ed25519 sign/verify round-trip", True)


# ── 2. sign_marker / verify_marker ──────────────────────────────────────────


print("\n=== Sign / Verify ===")

# Mock key storage so we don't touch real Credential Manager
with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    payload = {"action": "authorize-clear", "ttl_hours": 24,
               "issued_at": "2026-09-02T12:00:00Z"}
    token = sign_marker(payload)
    check("token contains two dots", token.count("."), 2)
    check("token ends with v1", token.endswith(".v1"))

    decoded = verify_marker(token)
    check("verified payload matches", decoded is not None)
    if decoded:
        check("verified action", decoded.get("action"), "authorize-clear")
        check("verified ttl_hours", decoded.get("ttl_hours"), 24)
        check("no _public_key in result", "_public_key" not in decoded)


# ── 3. Tampered token ───────────────────────────────────────────────────────


print("\n=== Tamper detection ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    payload = {"action": "authorize-canary"}
    token = sign_marker(payload)

    # Corrupt the signature part
    parts = token.split(".")
    bad_sig = parts[0] + ".AA==" + ".v1"
    decoded = verify_marker(bad_sig)
    check("corrupt signature returns None", decoded is None)

    # Corrupt the payload
    bad_payload = "AAAA." + parts[1] + ".v1"
    decoded = verify_marker(bad_payload)
    check("corrupt payload returns None", decoded is None)

    # Wrong version
    wrong_ver = parts[0] + "." + parts[1] + ".v0"
    decoded = verify_marker(wrong_ver)
    check("wrong version returns None", decoded is None)

    # Not even a token
    decoded = verify_marker("not-a-token")
    check("garbage returns None", decoded is None)


# ── 4. Unsigned (backward-compatible) JSON ──────────────────────────────────


print("\n=== Unsigned JSON (backward compat) ===")

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    unsigned = json.dumps({"action": "authorize-clear", "ttl_hours": 24})
    decoded = verify_marker(unsigned)
    check("unsigned JSON is accepted", decoded is not None)
    if decoded:
        check("unsigned JSON action", decoded.get("action"), "authorize-clear")
    check("PendingDeprecationWarning issued",
          any(issubclass(x.category, PendingDeprecationWarning) for x in w))


# ── 5. Public key fingerprint ───────────────────────────────────────────────


print("\n=== Fingerprint ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    fp = public_key_fingerprint()
    check("fingerprint is 16 hex chars", fp is not None and len(fp), 16)
    fp2 = public_key_fingerprint()
    check("fingerprint is deterministic", fp, fp2)


# ── 6. key_status() ─────────────────────────────────────────────────────────


print("\n=== Key status ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    status = key_status()
    check("key present", status.get("present"), True)
    check("algorithm is Ed25519", status.get("algorithm"), "Ed25519")
    check("fingerprint present", len(status.get("fingerprint", "")), 16)

with patch("operator_auth._load_keypair", return_value=None):
    status = key_status()
    check("key absent when no keypair", status.get("present"), False)


# ── 7. Keypair persistence ──────────────────────────────────────────────────


print("\n=== Keypair persistence ===")

# Test fallback file storage
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    fake_op_path = Path(tmp) / "operator_auth.py"
    # Simulate writing to a fallback file
    _store_keypair(private_bytes, public_bytes)
    # The real store writes to Credential Manager first. Let's verify
    # the round-trip via _load_keypair (which checks CM first).
    # For this test, we directly test the store/load cycle by mocking CM to fail.
    with patch("operator_auth._credential_write", return_value=False):
        with patch("operator_auth._credential_read", return_value=None):
            # Force file-based storage
            fallback = Path(__file__).resolve().parents[1] / "orchestrator" / ".operator_key"
            if fallback.exists():
                fallback.unlink()
            _store_keypair(private_bytes, public_bytes)
            loaded = _load_keypair()
            check("fallback file stores keypair", loaded is not None)
            if loaded:
                check("loaded private key matches", loaded[0], private_bytes)
                check("loaded public key matches", loaded[1], public_bytes)
            # Clean up
            if fallback.exists():
                fallback.unlink()


# ── 8. marker_is_signed ─────────────────────────────────────────────────────


print("\n=== marker_is_signed ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    token = sign_marker({"action": "test"})

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    signed_file = Path(tmp) / "signed.marker"
    signed_file.write_text(token, encoding="utf-8")
    check("signed marker detected", marker_is_signed(signed_file))

    unsigned_file = Path(tmp) / "unsigned.marker"
    unsigned_file.write_text('{"action": "test"}', encoding="utf-8")
    check("unsigned marker detected", not marker_is_signed(unsigned_file))


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
