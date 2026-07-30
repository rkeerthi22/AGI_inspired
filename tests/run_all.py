"""Run every regression suite, serially, and fail loudly if any does.

    python tests/run_all.py            # all suites
    python tests/run_all.py f42 f47    # only suites whose name contains one of these

WHY SERIAL, NEVER PARALLEL: the suites deliberately exercise the real repository, because
the mechanisms under test ARE git (`git status`, `git checkout`, ignore resolution) and a
sandboxed copy would prove nothing about them. Several therefore mutate shared real state --
`.gitignore`, `.git/info/exclude`, files under `orchestrator/` -- and restore it by content in
a `finally` block. Two suites running at once would interleave those restores and leave the
repo in a state neither of them wrote.

WHY IT REFUSES TO RUN DURING A FIRE: the fs-guard snapshots the working tree before a worker
call and compares after, attributing any change to the worker (F36). A suite planting probe
files mid-fire looks exactly like tampering, and the guard's remediation removes what it
flags. It has already destroyed real work twice (docs/INCIDENTS.md, 2026-07-29). The lock file
is the cheap, honest check.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
LOCK = ROOT / "runs" / ".batch.lock"


def main() -> int:
    if LOCK.is_file():
        print(f"REFUSING TO RUN: {LOCK.relative_to(ROOT)} exists -- a batch fire is in "
              f"flight.\nThese suites write real files; the fs-guard would read that as "
              f"worker tampering and remove them.\nWait for the fire to finish, then re-run.")
        return 2

    wanted = [a.lower() for a in sys.argv[1:]]
    suites = sorted(p for p in TESTS.glob("test_*.py")
                    if not wanted or any(w in p.stem.lower() for w in wanted))
    if not suites:
        print(f"no suites matched {wanted}")
        return 2

    results = []
    for s in suites:
        proc = subprocess.run([sys.executable, str(s)], cwd=str(ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
        results.append((s.stem, ok, proc))
        print(f"  [{'PASS' if ok else 'FAIL'}] {s.stem}")

    failed = [(n, p) for n, ok, p in results if not ok]
    for name, proc in failed:
        print(f"\n{'=' * 70}\nFAILED: {name}\n{'=' * 70}")
        print((proc.stdout or "").strip()[-3000:])
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr.strip()[-1500:])

    print(f"\n{len(results) - len(failed)}/{len(results)} suites green")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
