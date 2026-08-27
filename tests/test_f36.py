"""F36: blast radius scoped, discards recoverable, and detection strengthened.

Runs against the REAL repo on purpose -- the guard's mechanism is git status/checkout,
which only means something against a genuine working tree (docs/INCIDENTS.md 2026-07-24).
escalate() is stubbed so no real Telegram fires, per the lesson recorded that same day.
Every file it touches is restored at the end.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import integrity  # noqa: E402

integrity.escalate = lambda *a, **k: None          # never a real alert from a test
fails = []
VICTIM = ROOT / "config" / "policy.yaml"     # protected, tracked, NOT touched by "worker"
TARGET = ROOT / "orchestrator" / "runlock.py"  # protected, tracked, the "worker" edits this
UNTRACKED = ROOT / "orchestrator" / "_f36_tamper.py"


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


def dirty():
    out = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    return sorted(l[3:].strip() for l in out.splitlines() if l.strip())


def restore_all():
    # SCOPED to the two files this test touches. The first version said
    # `checkout -- config orchestrator` and destroyed the F36 fix itself mid-test --
    # the very blast-radius bug under test, reproduced in its own cleanup. Kept narrow
    # and explicit so it cannot reach anything it did not dirty.
    subprocess.run(["git", "-C", str(ROOT), "checkout", "--",
                    "config/policy.yaml", "orchestrator/runlock.py"],
                   capture_output=True, text=True)
    UNTRACKED.unlink(missing_ok=True)


# The only real precondition is that the two files THIS test dirties start clean, so its
# scoped restore returns them exactly. Any other dirty file is fine — indeed test 1 depends
# on unrelated dirt surviving. The first version hardcoded the expected dirty list and broke
# the moment an extra file was legitimately in flight.
_MINE = {"config/policy.yaml", "orchestrator/runlock.py"}
assert not (_MINE & set(dirty())), \
    f"this test's own targets must start clean; found {sorted(_MINE & set(dirty()))}"
BASELINE = dirty()

try:
    # ---------------------------------------------------------------- blast radius
    print("=== 1. an UNRELATED dirty file must survive (the blast-radius defect) ===")
    victim_original = VICTIM.read_text(encoding="utf-8")
    VICTIM.write_text(victim_original + "\n# operator edit, in flight\n", encoding="utf-8")
    before = integrity.fs_integrity_snapshot()                 # snapshot WITH victim already dirty
    target_original = TARGET.read_text(encoding="utf-8")
    TARGET.write_text(target_original + "\n# WORKER TAMPER\n", encoding="utf-8")
    integrity.fs_integrity_check(before, context="F36 test 1")
    check("worker's edit reverted", "WORKER TAMPER" in TARGET.read_text(encoding="utf-8"), False)
    check("UNRELATED operator edit SURVIVED",
          "operator edit, in flight" in VICTIM.read_text(encoding="utf-8"), True)
    VICTIM.write_text(victim_original, encoding="utf-8")

    # ---------------------------------------------------------------- recoverability
    print("\n=== 2. the discarded content is recoverable, not gone ===")
    before = integrity.fs_integrity_snapshot()
    TARGET.write_text(target_original + "\n# SECOND TAMPER\n", encoding="utf-8")
    integrity.fs_integrity_check(before, context="F36 test 2")
    stashes = sorted((ROOT / "runs").glob("reverted_*"))
    recovered = ""
    if stashes:
        cand = stashes[-1] / "orchestrator" / "runlock.py"
        recovered = cand.read_text(encoding="utf-8") if cand.exists() else ""
    check("reverted content preserved under runs/", "SECOND TAMPER" in recovered, True)
    check("working tree still clean of the tamper",
          "SECOND TAMPER" in TARGET.read_text(encoding="utf-8"), False)

    # ---------------------------------------------------------------- detection
    print("\n=== 3. re-modifying an ALREADY-dirty file is now detected ===")
    TARGET.write_text(target_original + "\n# pre-existing dirt\n", encoding="utf-8")
    before = integrity.fs_integrity_snapshot()                 # already dirty at snapshot time
    TARGET.write_text(target_original + "\n# pre-existing dirt\n# WORKER SNUCK IN\n",
                      encoding="utf-8")
    porcelain_blind = before["porcelain"] == integrity.fs_integrity_snapshot()["porcelain"]
    check("porcelain alone is blind to it (why the old guard missed it)",
          porcelain_blind, True)
    integrity.fs_integrity_check(before, context="F36 test 3")
    check("hash-based detection caught and reverted it",
          "WORKER SNUCK IN" in TARGET.read_text(encoding="utf-8"), False)

    # ---------------------------------------------------------------- untracked
    print("\n=== 4. new untracked files still removed ===")
    before = integrity.fs_integrity_snapshot()
    UNTRACKED.write_text("# planted\n", encoding="utf-8")
    integrity.fs_integrity_check(before, context="F36 test 4")
    check("planted untracked file removed", UNTRACKED.exists(), False)

    print("\n=== 5. no violation => no stash, no churn ===")
    n_before = len(list((ROOT / "runs").glob("reverted_*")))
    before = integrity.fs_integrity_snapshot()
    integrity.fs_integrity_check(before, context="F36 test 5")
    check("clean call creates no stash dir",
          len(list((ROOT / "runs").glob("reverted_*"))), n_before)
finally:
    restore_all()

print(f"\ntree restored to baseline: {dirty() == BASELINE}  ({dirty()})")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
