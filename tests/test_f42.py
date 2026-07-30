"""F42: root-level writes are no longer invisible, and legitimate agent output is not
mistaken for tampering.

Real repo (the guard's mechanism is git status/checkout, so a sandbox proves nothing),
escalate() stubbed. Everything it creates is removed in the finally block, scoped to the
exact paths it touched.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402
import policy  # noqa: E402

br.escalate = lambda *a, **k: None
fails = []
ROOT_FILE = ROOT / "_f42_planted_at_root.md"
IGNORED_ROOT = ROOT / "extensive_research.md"
DEEP = ROOT / "workspace" / "shopify" / "_f42_agent_output.md"
GITIGNORE = ROOT / ".gitignore"
# F46: a self-contained untracked DIRECTORY. Deliberately not .claude/ — the real handoff
# lives there and this file's own remediation path unlinks what it flags.
F46_DIR = ROOT / "_f46_untracked_dir"
F46_SEED = F46_DIR / "seed.md"
F46_PLANT = F46_DIR / "payload.py"
F46_WRITABLE = ROOT / "memory" / "scorecards" / "_f46_probe.md"


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


GITIGNORE_ORIGINAL = GITIGNORE.read_text(encoding="utf-8")

try:
    print("=== 1. a new file at the repo ROOT is now detected and removed ===")
    before = br.fs_integrity_snapshot()
    ROOT_FILE.write_text("# planted by a worker\n", encoding="utf-8")
    br.fs_integrity_check(before, context="F42 test 1")
    check("root-level plant removed", ROOT_FILE.exists(), False)

    print("\n=== 2. .gitignore is now protected (the self-hiding attack) ===")
    original = GITIGNORE.read_text(encoding="utf-8")
    before = br.fs_integrity_snapshot()
    GITIGNORE.write_text(original + "\n_f42_planted_at_root.md\n", encoding="utf-8")
    br.fs_integrity_check(before, context="F42 test 2")
    check("tampered .gitignore reverted",
          "_f42_planted_at_root.md" in GITIGNORE.read_text(encoding="utf-8"), False)
    check(".gitignore is in PROTECTED_PATHS", ".gitignore" in br.PROTECTED_PATHS, True)
    check("IDENTITY.md is in PROTECTED_PATHS", "IDENTITY.md" in br.PROTECTED_PATHS, True)

    print("\n=== 3. LEGITIMATE agent output must NOT be flagged ===")
    DEEP.parent.mkdir(parents=True, exist_ok=True)
    before = br.fs_integrity_snapshot()
    DEEP.write_text("# a normal deliverable\n", encoding="utf-8")
    br.fs_integrity_check(before, context="F42 test 3")
    check("workspace/ deliverable survives (gitignored, not protected)", DEEP.exists(), True)

    print("\n=== 4. an ALREADY-ignored root file is not re-flagged every run ===")
    check("extensive_research.md is gitignored now",
          subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q",
                          "extensive_research.md"], capture_output=True).returncode, 0)
    before = br.fs_integrity_snapshot()
    check("it does not appear in the untracked snapshot",
          "extensive_research.md" in br._untracked_of(before), False)
    br.fs_integrity_check(before, context="F42 test 4")
    check("and it still exists (never treated as tampering)", IGNORED_ROOT.exists(), True)

    # F46 replaces F42's depth-0 root scan. The assertion that used to live here — "root
    # snapshot holds no path separators" — asserted the BUG: it passed only while no untracked
    # directory existed, and enforced the very depth-0 filter that made files inside one
    # invisible. Detection-only below (no fs_integrity_check), because its remediation unlinks
    # what it flags and these probes stand in for real files.
    print("\n=== 5. F46: files inside an untracked DIRECTORY are visible ===")
    F46_DIR.mkdir(exist_ok=True)
    F46_SEED.write_text("seed\n", encoding="utf-8")
    base = br.fs_integrity_snapshot()
    check("the seed file itself is listed, not the collapsed dir",
          "_f46_untracked_dir/seed.md" in br._untracked_of(base), True)
    check("the collapsed directory entry is NOT what we track",
          "_f46_untracked_dir/" in br._untracked_of(base), False)

    F46_PLANT.write_text("# payload\n", encoding="utf-8")
    after = br.fs_integrity_snapshot()
    planted = set(br._untracked_of(after)) - set(br._untracked_of(base))
    check("a file planted inside it is DETECTED (was invisible pre-F46)",
          sorted(planted), ["_f46_untracked_dir/payload.py"])

    print("\n=== 5b. F46: in-place rewrite of an already-untracked file ===")
    F46_SEED.write_text("seed\nrewritten by a worker\n", encoding="utf-8")
    after2 = br.fs_integrity_snapshot()
    ub, ua = br._untracked_of(base), br._untracked_of(after2)
    tampered = sorted(p for p in set(ua) & set(ub) if ua[p] != ub[p])
    check("the rewrite is DETECTED by hash (identical '??' line pre-F46)",
          tampered, ["_f46_untracked_dir/seed.md"])

    print("\n=== 5c. F46: policy-writable untracked paths are excluded ===")
    F46_WRITABLE.write_text("# a legitimate scorecard\n", encoding="utf-8")
    check("memory/ write is not flagged (policy.yaml, not a second hardcoded list)",
          "memory/scorecards/_f46_probe.md" in br._untracked_of(br.fs_integrity_snapshot()),
          False)

    print("\n=== 5d. F46: a pre-F46 snapshot is still readable ===")
    check("F42's bare set degrades to {path: None}, no crash",
          br._untracked_of({"root": {"a.md"}}), {"a.md": None})

    print("\n=== 6. policy.yaml / fs-guard drift check still consistent ===")
    check("validate_paths clean", policy.validate_paths(br.PROTECTED_PATHS) or "consistent",
          "consistent")

    print("\n=== 7. a clean call is still a no-op ===")
    n = len(list((ROOT / "runs").glob("reverted_*")))
    before = br.fs_integrity_snapshot()
    br.fs_integrity_check(before, context="F42 test 7")
    check("no stash created when nothing changed",
          len(list((ROOT / "runs").glob("reverted_*"))), n)
finally:
    ROOT_FILE.unlink(missing_ok=True)
    DEEP.unlink(missing_ok=True)
    F46_PLANT.unlink(missing_ok=True)
    F46_SEED.unlink(missing_ok=True)
    F46_WRITABLE.unlink(missing_ok=True)
    if F46_DIR.is_dir():
        F46_DIR.rmdir()
    # Restore by CONTENT, never `git checkout -- .gitignore`. The first version did exactly
    # that and discarded this session's uncommitted ignore rule -- the same over-broad-revert
    # bug F36 is about, now three-for-three at destroying uncommitted work. Writing back the
    # bytes we read is independent of git state and cannot reach anything else.
    GITIGNORE.write_text(GITIGNORE_ORIGINAL, encoding="utf-8")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
