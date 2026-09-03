"""Integration: installed Hermes must satisfy the AGI_like retrieval contract."""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from hermes_contract import validate_installed_hermes  # noqa: E402


report = validate_installed_hermes()
sys.path.insert(0, str(report.hermes_root))

from hermes_cli.auth import AuthError, resolve_provider  # noqa: E402
from hermes_cli.config import get_compatible_custom_providers, load_config  # noqa: E402
from hermes_cli.providers import resolve_custom_provider  # noqa: E402

launcher = (ROOT / "orchestrator" / "controlled_hermes.py").read_text(encoding="utf-8")
assert "validate_installed_hermes(hermes_root)" in launcher, (
    "Hermes launcher does not enforce the contract before execution"
)
models_cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
providers_cfg = models_cfg.get("providers") or {}
assert providers_cfg["anthropic"]["hermes_provider"] == "anthropic", (
    "Anthropic fallback must use Hermes' native anthropic provider id"
)
assert providers_cfg["openai"]["hermes_provider"] == "openai-api", (
    "OpenAI fallback must use Hermes' direct API provider id"
)

custom_providers = get_compatible_custom_providers(load_config())
unresolvable: list[tuple[str, str]] = []
for name, provider_cfg in providers_cfg.items():
    hermes_provider = str(provider_cfg.get("hermes_provider") or "").strip()
    if not hermes_provider:
        continue
    try:
        resolve_provider(hermes_provider)
        continue
    except AuthError:
        pass
    if resolve_custom_provider(hermes_provider, custom_providers) is None:
        unresolvable.append((name, hermes_provider))

assert not unresolvable, (
    "Configured Hermes provider ids are not resolvable in the installed Hermes runtime: "
    f"{unresolvable}"
)
print(
    f"Hermes contract v{report.contract_version}: PASS "
    f"revision={report.hermes_revision} capabilities={','.join(report.capabilities)}"
)
