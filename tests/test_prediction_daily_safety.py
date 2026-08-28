"""Regression: daily CLI help is inert and invalidated rows stay retired."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = []


def check(name, condition, detail=""):
    print(("PASS" if condition else "FAIL") + ": " + name + (f" ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


def isolated_env(base):
    env = os.environ.copy()
    env.update({
        "PREDICTION_DB": str(base / "prediction.db"),
        "PREDICTION_REPORTS_DIR": str(base / "reports"),
        "LEDGER_DB": str(base / "ledger.db"),
        "LEDGERBOOK_DB": str(base / "ledgerbook.db"),
        "SKILLS_ANALYST_ROOT": str(base / "skills"),
        "MIKS_ENGINE_PATH": str(base / "miks.py"),
        "MIKS_CONFIG": str(base / "miks.json"),
        "VIDEO_DATASET_PATH": str(base / "videos.json"),
    })
    return env


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    base = Path(raw)
    env = isolated_env(base)
    os.environ.update({key: env[key] for key in (
        "PREDICTION_DB", "PREDICTION_REPORTS_DIR", "LEDGER_DB", "LEDGERBOOK_DB",
        "SKILLS_ANALYST_ROOT", "MIKS_ENGINE_PATH", "MIKS_CONFIG", "VIDEO_DATASET_PATH")})

    from prediction_machine.core.prediction_store import PredictionStore
    from prediction_machine import run_daily

    # --help must exit before any configured state path is created.
    help_out = io.StringIO()
    try:
        with contextlib.redirect_stdout(help_out):
            run_daily.main(["--help"])
        help_code = None
    except SystemExit as exc:
        help_code = exc.code
    check("--help exits successfully", help_code == 0)
    check("--help prints usage", "usage:" in help_out.getvalue().lower())
    check("--help does not create prediction DB", not (base / "prediction.db").exists())
    check("--help does not create report directory", not (base / "reports").exists())

    # An invalidated, due row must not be returned to any collector again.
    store = PredictionStore(base / "retired.db")
    store.register_model_version("skill_safety_test", "skill_safety", "test")
    pid = store.create_prediction(
        "skill_safety", "missing_skill", {"predicted_regression": False}, "high", {},
        "skill_safety_test", "2000-01-01T00:00:00Z",
    )
    store.invalidate_prediction(pid, "already retired")
    pending = store.get_pending_outcomes("skill_safety")
    row = store.get_prediction(pid)
    check("invalidated prediction is excluded from pending outcomes", pending == [])
    check("invalidation remains intact", row["valid_for_training"] == 0 and row["invalid_reason"] == "already retired")
    store.close()

    # Full daily loop runs only against configured temporary state.
    empty = {ptype: {"checked": 0, "recorded": 0, "skipped": 0, "invalidated": 0,
                     "errors": []} for ptype in run_daily._PREDICTION_TYPES}
    run_daily.run_all_collectors = lambda store: empty
    with contextlib.redirect_stdout(io.StringIO()):
        report = run_daily.run_daily()
    check("isolated model-free daily pipeline exits successfully", "Prediction Machine" in report)
    check("isolated daily pipeline creates configured DB", (base / "prediction.db").exists())
    reports = list((base / "reports").glob("*.md")) if (base / "reports").exists() else []
    check("isolated daily pipeline creates configured report", len(reports) == 1)
    if (base / "prediction.db").exists():
        with sqlite3.connect(base / "prediction.db") as conn:
            check("isolated DB integrity", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
            check("isolated model registration completed", conn.execute("SELECT count(*) FROM model_versions").fetchone()[0] == 4)

raise SystemExit(1 if failures else 0)
