"""Regression: CLI help/parse errors are inert; ESTOP independently blocks execution."""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger" / "ledger.db"
PREDICTIONS = ROOT / "prediction_machine" / "data" / "predictions.db"

SCRIPTS = {
    "controlled_hermes": ROOT / "orchestrator" / "controlled_hermes.py",
    "onboarding_autonomy": ROOT / "orchestrator" / "onboarding_autonomy.py",
    "ledger_smoke": ROOT / "orchestrator" / "ledger.py",
    "init_ledger": ROOT / "ledger" / "init_ledger.py",
    "spotcheck": ROOT / "orchestrator" / "spotcheck.py",
    "prediction_tests": ROOT / "prediction_machine" / "tests" / "run_tests.py",
}
UNKNOWN = {
    "controlled_hermes": ["--unknown"],
    "onboarding_autonomy": ["--unknown"],
    "ledger_smoke": ["--unknown"],
    "init_ledger": ["--unknown"],
    "spotcheck": ["notify", "--unknown"],
    "prediction_tests": ["--unknown"],
}

failures = []


def check(name, value, detail=""):
    print(("PASS" if value else "FAIL") + ": " + name + (f" ({detail})" if detail else ""))
    if not value:
        failures.append(name)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def task_count():
    if not LEDGER.exists():
        return None
    with sqlite3.connect(f"file:{LEDGER.as_posix()}?mode=ro", uri=True) as conn:
        return conn.execute("SELECT count(*) FROM tasks").fetchone()[0]


def run_script(path, args, env):
    code = ("import runpy,sys; "
            f"sys.path.insert(0,{str(path.parent)!r}); "
            f"sys.argv=[{str(path)!r}]+{args!r}; "
            f"runpy.run_path({str(path)!r},run_name='__main__')")
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    home = Path(td) / "hermes"
    home.mkdir()
    (home / "ESTOP").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    baseline = (digest(LEDGER), digest(PREDICTIONS), task_count())

    for name, script in SCRIPTS.items():
        help_result = run_script(script, ["--help"], env)
        check(f"{name} --help exits 0", help_result.returncode == 0, help_result.stderr[-200:])
        check(f"{name} --help prints usage", "usage:" in help_result.stdout.lower())
        unknown_result = run_script(script, UNKNOWN[name], env)
        check(f"{name} rejects unknown arguments", unknown_result.returncode == 2,
              unknown_result.stderr[-200:])
        check(f"{name} parse paths leave DB state unchanged",
              baseline == (digest(LEDGER), digest(PREDICTIONS), task_count()))

    # Valid commands must still stop at ESTOP before Hermes imports, ledger writes, or calls.
    controlled = run_script(SCRIPTS["controlled_hermes"], ["-z", "must not run"], env)
    check("controlled_hermes valid command is independently ESTOP-blocked", controlled.returncode == 75)
    onboarding = run_script(SCRIPTS["onboarding_autonomy"], ["run"], env)
    check("onboarding valid command is independently ESTOP-blocked", onboarding.returncode == 75)
    check("ESTOP paths leave DB state unchanged",
          baseline == (digest(LEDGER), digest(PREDICTIONS), task_count()))

    sys.path.insert(0, str(ROOT / "orchestrator"))
    import execution_pause
    original_home = os.environ.get("HERMES_HOME")
    try:
        # A valid home without ESTOP is the one intentional unpaused state.
        (home / "config.yaml").write_text("test: true\n", encoding="utf-8")
        (home / "ESTOP").unlink()
        os.environ["HERMES_HOME"] = str(home)
        check("validated home with missing ESTOP means resumed", not execution_pause.pause_engaged())
        os.environ["HERMES_HOME"] = str(Path(td) / "missing-home")
        check("missing HERMES_HOME fails closed", execution_pause.pause_engaged())
        os.environ["HERMES_HOME"] = str(Path(td) / "unrelated")
        (Path(td) / "unrelated").mkdir()
        check("misconfigured existing HERMES_HOME fails closed", execution_pause.pause_engaged())
        os.environ["HERMES_HOME"] = "relative-home"
        check("relative HERMES_HOME fails closed", execution_pause.pause_engaged())
        os.environ["HERMES_HOME"] = str(home)
        with mock.patch.object(Path, "stat", side_effect=PermissionError("denied")):
            check("unreadable sentinel/home fails closed", execution_pause.pause_engaged())
    finally:
        if original_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = original_home

raise SystemExit(1 if failures else 0)
