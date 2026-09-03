"""Operator cryptographic identity tests — all key operations mocked.

Verifies:
1. Key generation creates a valid Ed25519 keypair
2. sign_marker produces a verifiable token
3. verify_marker returns the original payload for valid tokens
4. Tampered and foreign-self-signed tokens return None
5. Unsigned markers and missing local trust fail closed
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
    _credential_write,
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


# ── 4. Local trust boundary ─────────────────────────────────────────────────


print("\n=== Local trust boundary ===")

unsigned = json.dumps({"action": "authorize-clear", "ttl_hours": 24})
check("unsigned JSON is rejected", verify_marker(unsigned) is None)

foreign_private, foreign_public = _generate_keypair()
with patch("operator_auth._load_keypair",
           return_value=(foreign_private, foreign_public)):
    foreign_token = sign_marker({"action": "authorize-clear"})
with patch("operator_auth._load_keypair",
           return_value=(private_bytes, public_bytes)):
    check("foreign self-signed marker is rejected",
          verify_marker(foreign_token) is None)

with patch("operator_auth._load_keypair", return_value=None):
    check("signed marker is rejected without local trusted key",
          verify_marker(token) is None)


# ── 5. Public key fingerprint ───────────────────────────────────────────────


print("\n=== Fingerprint ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    fp = public_key_fingerprint()
    check("fingerprint is 16 hex chars", fp is not None and len(fp), 16)
    fp2 = public_key_fingerprint()
    check("fingerprint is deterministic", fp, fp2)


# ── 6. key_status() ─────────────────────────────────────────────────────────


print("\n=== Key status ===")

with patch("operator_auth._load_keypair_with_storage",
           return_value=((private_bytes, public_bytes), "credential_manager")):
    status = key_status()
    check("key present", status.get("present"), True)
    check("algorithm is Ed25519", status.get("algorithm"), "Ed25519")
    check("fingerprint present", len(status.get("fingerprint", "")), 16)
    check("key storage reports known store",
          status.get("storage") in {"credential_manager", "hermes_home_file", "legacy_repo_file"}, True)

with patch("operator_auth._load_keypair_with_storage", return_value=(None, None)):
    status = key_status()
    check("key absent when no keypair", status.get("present"), False)

# pywin32's CredWrite Unicode API requires a string CredentialBlob, and its
# CredRead counterpart returns a string on current Windows builds.
fake_cred = MagicMock()
fake_cred.CRED_TYPE_GENERIC = 1
fake_cred.CRED_PERSIST_LOCAL_MACHINE = 2
with patch("operator_auth._win32cred", fake_cred):
    check("Credential Manager accepts Unicode blob wrapper",
          _credential_write("test-target", "test-value"), True)
    written = fake_cred.CredWrite.call_args.args[0]
    check("Credential Manager blob remains Unicode",
          isinstance(written["CredentialBlob"], str), True)

credential_blob = json.dumps({
    "private_key": base64.b64encode(private_bytes).decode("ascii"),
    "public_key": base64.b64encode(public_bytes).decode("ascii"),
})
with patch("operator_auth._credential_read",
           return_value={"CredentialBlob": credential_blob}):
    from operator_auth import _load_keypair_with_storage
    loaded, storage = _load_keypair_with_storage()
    check("Unicode credential blob loads", loaded, (private_bytes, public_bytes))
    check("Unicode credential blob wins primary storage", storage,
          "credential_manager")

with patch("operator_auth._credential_read",
           return_value={"CredentialBlob": credential_blob.encode("utf-16-le")}):
    loaded, storage = _load_keypair_with_storage()
    check("UTF-16LE credential bytes load", loaded,
          (private_bytes, public_bytes))
    check("UTF-16LE credential bytes win primary storage", storage,
          "credential_manager")


# ── 7. Keypair persistence ──────────────────────────────────────────────────


print("\n=== Keypair persistence ===")

# Test fallback file storage
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    hermes_home = Path(tmp) / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}, clear=False):
        # The real store writes to Credential Manager first. Let's verify
        # the round-trip via _load_keypair (which checks CM first).
        # For this test, we directly test the store/load cycle by mocking CM to fail.
        with patch("operator_auth._credential_write", return_value=False):
            with patch("operator_auth._credential_read", return_value=None):
                fallback = hermes_home / ".operator_key"
                legacy = Path(__file__).resolve().parents[1] / "orchestrator" / ".operator_key"
                if fallback.exists():
                    fallback.unlink()
                if legacy.exists():
                    legacy.unlink()
                _store_keypair(private_bytes, public_bytes)
                loaded = _load_keypair()
                check("fallback file stores keypair", loaded is not None)
                check("fallback file is outside repository module path", fallback.exists(), True)
                check("legacy repo fallback is not recreated", legacy.exists(), False)
                if loaded:
                    check("loaded private key matches", loaded[0], private_bytes)
                    check("loaded public key matches", loaded[1], public_bytes)
                if fallback.exists():
                    fallback.unlink()


# ── 8. marker_is_signed ─────────────────────────────────────────────────────


print("\n=== marker_is_signed ===")

with patch("operator_auth._load_keypair", return_value=(private_bytes, public_bytes)):
    token = sign_marker({"action": "test"})

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    signed_file = Path(tmp) / "signed.marker"
    signed_file.write_text(token, encoding="utf-8")
    with patch("operator_auth._load_keypair",
               return_value=(private_bytes, public_bytes)):
        check("trusted signed marker detected", marker_is_signed(signed_file))

    with patch("operator_auth._load_keypair",
               return_value=(foreign_private, foreign_public)):
        check("foreign signed marker is not trusted", not marker_is_signed(signed_file))

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
