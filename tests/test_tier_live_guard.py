"""Unit: default tiers fail closed on model, mission, and network paths."""

import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get("AGI_TEST_TIER") in {"unit", "containment", "integration"}, (
    "run this guard regression through tests/run_all.py"
)


def blocked(call, label: str) -> None:
    try:
        call()
    except RuntimeError as exc:
        assert "LIVE" in str(exc) and "BLOCKED" in str(exc), (label, exc)
    else:
        raise AssertionError(f"{label} was not blocked")


blocked(lambda: subprocess.run(["hermes", "--version"]), "Hermes executable")
blocked(lambda: subprocess.run([sys.executable, str(ROOT / "orchestrator" / "batch_runner.py"),
                                "--dry-run"]), "mission entry point")
blocked(lambda: socket.create_connection(("example.com", 443)), "network socket")
print("default-tier live guard: model + mission + network paths blocked PASS")
