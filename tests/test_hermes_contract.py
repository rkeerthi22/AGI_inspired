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

# Verify monkey patch ordering in controlled_hermes.py
idx_ad = launcher.find("tools.async_delegation")
idx_agent = launcher.find("import run_agent")
assert idx_ad != -1 and idx_agent != -1 and idx_ad < idx_agent, (
    "controlled_hermes.py must patch tools.async_delegation BEFORE importing run_agent"
)
assert "_ad.restore_undelivered_completions = lambda" in launcher, (
    "controlled_hermes.py must mock restore_undelivered_completions"
)

# Verify execution.py worker containment & environment contracts
exec_src = (ROOT / "orchestrator" / "execution.py").read_text(encoding="utf-8")
assert 'env["HERMES_HOME"] = str(worker_home)' in exec_src, (
    "execution.py must point HERMES_HOME at the dedicated worker home, not .harness"
)
assert "proc.stdin.close()" in exec_src, (
    "execution.py must close worker stdin to avoid child hang on unclosed pipe"
)
assert '"-t", "web"' in exec_src, (
    "execution.py must specify -t web (not web,browser) to avoid desktop UI restrictions"
)

# Verify worker home config resolution
worker_cfg_path = ROOT / "workspace" / "worker_home" / "config.yaml"
if worker_cfg_path.is_file():
    worker_cfg = yaml.safe_load(worker_cfg_path.read_text(encoding="utf-8")) or {}
    worker_custom = get_compatible_custom_providers(worker_cfg)
    assert resolve_custom_provider("custom:byteplus-coding", worker_custom) is not None, (
        "workspace/worker_home/config.yaml must configure custom:byteplus-coding"
    )

print(
    f"Hermes contract v{report.contract_version}: PASS "
    f"revision={report.hermes_revision} capabilities={','.join(report.capabilities)}"
)
