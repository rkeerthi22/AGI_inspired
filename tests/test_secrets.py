"""Read-only Credential Manager vault regressions."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))

network_before = {name for name in sys.modules if name.startswith((
    "requests", "httpx", "urllib.request", "socket"))}
import secrets as credential_vault  # noqa: E402
network_after = {name for name in sys.modules if name.startswith((
    "requests", "httpx", "urllib.request", "socket"))}

import operator_cli  # noqa: E402
import provider_chat  # noqa: E402

checks = 0


def check(label, actual, expected=True):
    global checks
    checks += 1
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


class FakeCredentialManager:
    CRED_TYPE_GENERIC = 1

    def __init__(self, record=None, error=None):
        self.record = record
        self.error = error
        self.calls = []

    def CredRead(self, target, credential_type, flags):
        self.calls.append((target, credential_type, flags))
        if self.error:
            raise self.error
        return self.record or {}


original_backend = credential_vault._win32cred
original_env = os.environ.get("ARK_API_KEY")
original_home = os.environ.get("HERMES_HOME")
original_vault_getter = provider_chat.credential_vault.get_api_key
original_cli_getter = operator_cli.credential_vault.get_api_key
try:
    check("vault import adds no network modules", network_after - network_before, set())
    check("stable BytePlus Credential Manager target",
          credential_vault.credential_target("byteplus_coding"),
          "AGI_like/byteplus_coding")
    check("unknown provider has no credential target",
          credential_vault.credential_target("unknown"), None)

    os.environ["ARK_API_KEY"] = "environment-fallback"
    backend = FakeCredentialManager({"CredentialBlob": b"vault-wins"})
    credential_vault._win32cred = backend
    check("Credential Manager takes precedence",
          credential_vault.get_api_key("byteplus_coding"), "vault-wins")
    check("vault target is generic and exact", backend.calls,
          [("AGI_like/byteplus_coding", backend.CRED_TYPE_GENERIC, 0)])

    credential_vault._win32cred = FakeCredentialManager(error=RuntimeError("unreadable"))
    check("unreadable vault falls back without surfacing errors",
          credential_vault.get_api_key("byteplus_coding"), "environment-fallback")
    os.environ.pop("ARK_API_KEY", None)
    check("missing vault and environment fail closed",
          credential_vault.get_api_key("byteplus_coding"), None)

    provider_chat.credential_vault.get_api_key = lambda provider: "vault-dispatch"
    check("provider transport checks vault before dotenv",
          provider_chat._secure_env_value("ARK_API_KEY"), "vault-dispatch")
    operator_cli.credential_vault.get_api_key = lambda provider: "vault-presence-only"
    credential_check = next(item for item in operator_cli._canary_prerequisites()
                            if item["check"] == "ark_api_key_present_in_env")
    check("preflight accepts vault credential presence", credential_check["ok"], True)
    check("preflight never includes credential value", "vault-presence-only" in credential_check["detail"], False)

    operator_cli.credential_vault.get_api_key = lambda provider: None
    with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
        hermes_home = Path(td)
        (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")
        (hermes_home / ".env").write_text("ARK_API_KEY=private-dotenv-only\n",
                                          encoding="utf-8")
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ.pop("ARK_API_KEY", None)
        credential_check = next(item for item in operator_cli._canary_prerequisites()
                                if item["check"] == "ark_api_key_present_in_env")
    check("preflight accepts Hermes private dotenv presence", credential_check["ok"], True)
    check("preflight detail still withholds dotenv secret value",
          "private-dotenv-only" in credential_check["detail"], False)

    canary_source = (ROOT / "workspace" / "validation" /
                     "byteplus_connectivity_canary.py").read_text(encoding="utf-8")
    check("canary checks the vault rather than process environment",
          "credential_vault.get_api_key(PROVIDER)" in canary_source, True)
finally:
    credential_vault._win32cred = original_backend
    provider_chat.credential_vault.get_api_key = original_vault_getter
    operator_cli.credential_vault.get_api_key = original_cli_getter
    if original_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = original_home
    if original_env is None:
        os.environ.pop("ARK_API_KEY", None)
    else:
        os.environ["ARK_API_KEY"] = original_env

print(f"Credential vault: {checks}/{checks} assertions passed")
