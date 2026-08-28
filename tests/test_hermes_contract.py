"""Integration: installed Hermes must satisfy the AGI_like retrieval contract."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from hermes_contract import validate_installed_hermes  # noqa: E402


report = validate_installed_hermes()
launcher = (ROOT / "orchestrator" / "controlled_hermes.py").read_text(encoding="utf-8")
assert "validate_installed_hermes(hermes_root)" in launcher, (
    "Hermes launcher does not enforce the contract before execution"
)
print(
    f"Hermes contract v{report.contract_version}: PASS "
    f"revision={report.hermes_revision} capabilities={','.join(report.capabilities)}"
)
