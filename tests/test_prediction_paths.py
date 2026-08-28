"""Unit: prediction paths are repository-relative and overrideable."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prediction_machine import paths  # noqa: E402

assert paths.REPO_ROOT == ROOT.resolve(), (paths.REPO_ROOT, ROOT.resolve())
assert paths.PREDICTION_DB == (ROOT / "prediction_machine" / "data" / "predictions.db").resolve()
assert paths.LEDGER_DB == (ROOT / "ledger" / "ledger.db").resolve()
override_root = (ROOT / "workspace" / "path_override_probe").resolve()
override_db = override_root / "custom.db"
env = dict(os.environ)
env["AGI_REPO_ROOT"] = str(override_root)
env["PREDICTION_DB"] = str(override_db)
code = (
    "from prediction_machine.paths import REPO_ROOT,PREDICTION_DB,LEDGER_DB;"
    "print(REPO_ROOT);print(PREDICTION_DB);print(LEDGER_DB)"
)
proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                      capture_output=True, text=True, check=True)
lines = proc.stdout.splitlines()
assert Path(lines[0]) == override_root
assert Path(lines[1]) == override_db
assert Path(lines[2]) == override_root / "ledger" / "ledger.db"

print("prediction paths: repository-relative defaults + environment overrides PASS")
