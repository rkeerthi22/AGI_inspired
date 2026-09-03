"""Run explicit test tiers serially; live execution is opt-in only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
LOCK = ROOT / "runs" / ".batch.lock"
MANIFEST = TESTS / "tiers.json"
DEFAULT_TIERS = ("unit", "containment", "integration")
ALL_TIERS = (*DEFAULT_TIERS, "live")


def _manifest() -> dict[str, str]:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assignments: dict[str, str] = {}
    for tier, names in raw.items():
        if tier not in ALL_TIERS:
            raise ValueError(f"unknown test tier {tier!r}")
        for name in names:
            if name in assignments:
                raise ValueError(f"{name} appears in two tiers")
            assignments[name] = tier
    discovered = {path.stem for path in TESTS.glob("test_*.py")}
    missing = discovered - assignments.keys()
    stale = assignments.keys() - discovered
    if missing or stale:
        raise ValueError(f"tier manifest mismatch: unassigned={sorted(missing)}, stale={sorted(stale)}")
    return assignments


def _guarded_env(tier: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TESTS / "live_guard") + os.pathsep + env.get("PYTHONPATH", "")
    env["AGI_TEST_TIER"] = tier
    env["AGI_LIVE_EXECUTION_ALLOWED"] = "0"
    # F108 (2026-09-03): route test health events away from the production
    # runs/health_events.jsonl. Without this, unit-tier tests that exercise the
    # estop/mailbus/hive_quiesce/prediction subsystems write their events to the
    # DEFAULT production path, and `agi status` (newest-event-per-subsystem) then
    # replays test artifacts as live "recorded subsystem warnings" -- crying wolf.
    # Measured: one run of test_estop_tamper added 4 estop/tamper_recovery events
    # to the production log. pid-scoped under the system temp so one gate run
    # shares one file that lives outside the repo; production never sets this env
    # var and continues to use runs/health_events.jsonl. operator_cli status reads
    # RUNS/health_events.jsonl directly, so production reads are unaffected. The
    # live tier is intentionally un-redirected (it runs real provider calls).
    if tier in {"unit", "containment", "integration"}:
        env["AGI_HEALTH_EVENTS_PATH"] = str(
            Path(tempfile.gettempdir()) / f"agi_test_health_events_{os.getpid()}.jsonl")
    return env


def _run_suite(path: Path, root: Path, tier: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path)], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=_guarded_env(tier),
    )


def _run_containment(path: Path) -> subprocess.CompletedProcess:
    """Run repository-mutating checks only inside a disposable Git repository."""
    with tempfile.TemporaryDirectory(prefix="agi_containment_") as raw:
        worktree = Path(raw) / "repo"
        worktree.mkdir()
        init = subprocess.run(["git", "init", "--quiet"], cwd=str(worktree),
                              capture_output=True, text=True)
        if init.returncode:
            return init
        # Copy committed files from the caller's current working bytes. This
        # tests uncommitted production edits without sharing Git metadata.
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode("utf-8", errors="replace").split("\0")
        for relative_text in filter(None, tracked):
            relative = Path(relative_text)
            source = ROOT / relative
            if source.is_file():
                destination = worktree / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        # Copy new code/config/tests, never runtime artifacts or audit review files.
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode("utf-8", errors="replace").split("\0")
        for relative_text in filter(None, untracked):
            relative = Path(relative_text)
            if relative.parts[0] not in {"orchestrator", "prediction_machine", "tests", "config"}:
                continue
            source = ROOT / relative
            if source.is_file():
                destination = worktree / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        # Supply ignored runtime fixtures that the historical containment tests
        # intentionally inspect, without exposing the primary checkout to writes.
        for relative in (Path("ledger/ledger.db"), Path("memory/ledgerbook.db"),
                         Path("extensive_research.md")):
            source = ROOT / relative
            if source.is_file():
                target_fixture = worktree / relative
                target_fixture.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target_fixture)
        # policy.yaml contains deployment paths by design. Rebase only the
        # disposable fixture so its writable roots describe this clone.
        policy = worktree / "config" / "policy.yaml"
        policy.write_text(
            policy.read_text(encoding="utf-8").replace(str(ROOT), str(worktree)),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=str(worktree),
                       capture_output=True, text=True)
        fixture_commit = subprocess.run(
            ["git", "-c", "user.name=AGI test harness",
             "-c", "user.email=tests@invalid", "commit", "--quiet",
             "-m", "test fixture: mirror working tree"],
            cwd=str(worktree), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if fixture_commit.returncode:
            return fixture_commit
        return _run_suite(worktree / "tests" / path.name, worktree, "containment")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filters", nargs="*", help="suite-name substrings")
    parser.add_argument("--tier", action="append", choices=ALL_TIERS,
                        help="repeatable; default is unit+containment+integration")
    parser.add_argument("--live", action="store_true",
                        help="required acknowledgement for the live tier")
    args = parser.parse_args()
    if LOCK.is_file():
        print(f"REFUSING TO RUN: {LOCK.relative_to(ROOT)} exists -- a batch fire is in flight.")
        return 2
    try:
        assignments = _manifest()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID TEST TIER MANIFEST: {exc}")
        return 2
    tiers = tuple(args.tier or DEFAULT_TIERS)
    if "live" in tiers and not args.live:
        print("REFUSING LIVE TIER: pass both --tier live and --live explicitly.")
        return 2
    if args.live and "live" not in tiers:
        print("--live has no effect unless --tier live is selected")
        return 2
    wanted = [item.lower() for item in args.filters]
    suites = sorted(path for path in TESTS.glob("test_*.py")
                    if assignments[path.stem] in tiers
                    and (not wanted or any(token in path.stem.lower() for token in wanted)))
    if not suites:
        print(f"no suites matched tiers={tiers} filters={wanted}")
        return 2
    results = []
    for suite in suites:
        tier = assignments[suite.stem]
        if tier == "live":
            env = dict(os.environ)
            env["AGI_TEST_TIER"] = "live"
            env["AGI_LIVE_EXECUTION_ALLOWED"] = "1"
            proc = subprocess.run([sys.executable, str(suite)], cwd=str(ROOT),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", env=env)
        elif tier == "containment":
            proc = _run_containment(suite)
        else:
            proc = _run_suite(suite, ROOT, tier)
        ok = proc.returncode == 0
        results.append((suite.stem, tier, ok, proc))
        print(f"  [{'PASS' if ok else 'FAIL'}] [{tier}] {suite.stem}")
    failed = [(name, tier, proc) for name, tier, ok, proc in results if not ok]
    for name, tier, proc in failed:
        print(f"\n{'=' * 70}\nFAILED [{tier}]: {name}\n{'=' * 70}")
        print((proc.stdout or "").strip()[-5000:])
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr.strip()[-3000:])
    print(f"\n{len(results) - len(failed)}/{len(results)} suites green "
          f"(tiers: {', '.join(tiers)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
