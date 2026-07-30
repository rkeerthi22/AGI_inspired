"""F47: unversioned exclude sources can no longer hide anything from the fs-guard.

Real repo (the guard's mechanism is git itself, so a sandbox proves nothing), escalate()
stubbed. `.git/info/exclude` is read once up front and written back byte-for-byte in the
finally block -- never `git checkout`, which cannot restore an untracked file anyway and is
the over-broad move F36 exists to stop.
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402

br.escalate = lambda *a, **k: None
fails = []
PLANT = ROOT / "orchestrator" / "_f47_planted.py"
EXCLUDE = br._local_exclude_sources()[0]
EX_KEY = str(EXCLUDE)
EX_ORIGINAL = EXCLUDE.read_bytes()
GLOBAL_SRC = br._local_exclude_sources()[1]
GLOBAL_BEFORE = GLOBAL_SRC.read_bytes() if GLOBAL_SRC.is_file() else None


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


try:
    print("=== 1. baseline is clean ===")
    # F52 (2026-07-30) moved this baseline: `.claude` became a PROTECTED_PATH when
    # HANDOFF.md was committed, and .git/info/exclude legitimately masks
    # `.claude/worktrees/` -- the operator-DECIDED nested-worktree collapse. So "masked at
    # rest" is no longer empty, and asserting [] would now fail for a correct system.
    # The invariant that still carries weight is narrower and is asserted directly: the
    # ONLY at-rest mask is that decided one. A mask on any OTHER protected path still
    # fails here, and section 2 still requires the attack to add exactly one entry on top.
    AT_REST = sorted(br._masked_under_protected())
    check("the only at-rest mask is the DECIDED worktree collapse",
          [m for m in AT_REST if "worktrees" not in m], [])
    a = br._untracked_files()
    b = br._untracked_files()
    check("untracked scan is stable across calls (no spurious delta)", a == b, True)

    print("\n=== 2. the demonstrated attack: mask a planted file mid-call ===")
    before = br.fs_integrity_snapshot()
    PLANT.write_text("# payload\n", encoding="utf-8")
    EXCLUDE.write_bytes(EX_ORIGINAL + b"\norchestrator/_f47_planted.py\n")
    check("enumeration still sees the file (pre-F47: invisible)",
          "orchestrator/_f47_planted.py" in br._untracked_files(), True)
    check("the exclude-source edit is itself detected",
          br._local_exclude_state()[EX_KEY] != before["excludes"][EX_KEY], True)
    check("the mask is attributed to a protected path",
          sorted(br._masked_under_protected()),
          sorted(AT_REST + ["orchestrator/_f47_planted.py"]))

    print("\n=== 3. remediation restores the exclude and removes the plant ===")
    br.fs_integrity_check(before, context="F47 test 3")
    check("exclude restored byte-identical", EXCLUDE.read_bytes(), EX_ORIGINAL)
    check("planted file removed", PLANT.exists(), False)
    check("masking is gone once the exclude is restored",
          sorted(br._masked_under_protected()), AT_REST)

    print("\n=== 4. an exclude source CREATED during the call is removed ===")
    EXCLUDE.unlink()
    before = br.fs_integrity_snapshot()
    check("snapshot records an absent source as None", before["excludes"][EX_KEY], None)
    EXCLUDE.write_bytes(b"orchestrator/\n")
    br.fs_integrity_check(before, context="F47 test 4")
    check("the created exclude was removed, not left in place", EXCLUDE.exists(), False)
    EXCLUDE.write_bytes(EX_ORIGINAL)

    print("\n=== 5. an out-of-repo source is reported, never rewritten ===")
    check("the global source is classified outside the repo",
          str(GLOBAL_SRC.resolve()).lower().startswith(str(ROOT.resolve()).lower()), False)
    before = br.fs_integrity_snapshot()
    PLANT.write_text("# payload\n", encoding="utf-8")
    br.fs_integrity_check(before, context="F47 test 5")
    now = GLOBAL_SRC.read_bytes() if GLOBAL_SRC.is_file() else None
    check("operator's global ignore untouched by remediation", now, GLOBAL_BEFORE)

    print("\n=== 6. a clean call is still a no-op ===")
    n = len(list((ROOT / "runs").glob("reverted_*")))
    before = br.fs_integrity_snapshot()
    br.fs_integrity_check(before, context="F47 test 6")
    check("no stash created when nothing changed",
          len(list((ROOT / "runs").glob("reverted_*"))), n)
    check("exclude still untouched", EXCLUDE.read_bytes(), EX_ORIGINAL)

    print("\n=== 7. the guard's own view is not maskable ===")
    before = br.fs_integrity_snapshot()
    EXCLUDE.write_bytes(EX_ORIGINAL + b"\n.claude/\n")
    # F52 (2026-07-30): HANDOFF.md is now TRACKED and `.claude` is a PROTECTED_PATH, so it
    # is no longer reachable via the untracked scan -- the old assertion here tested a
    # channel that cannot apply to it any more. The property under test is unchanged
    # ("masking .claude/ must not hide the handoff from the guard") and now holds through a
    # STRONGER channel: `git ls-files` does not consult ignore rules at all, so a tracked
    # file cannot be masked by any exclude source. That is the real payoff of F52 over
    # relying on untracked enumeration, and it is asserted rather than assumed.
    check("masking .claude/ does not hide HANDOFF.md from the guard",
          ".claude/HANDOFF.md" in br._tracked_hashes(), True)
    check("...and the untracked scan legitimately no longer carries it",
          ".claude/HANDOFF.md" in br._untracked_files(), False)
    br.fs_integrity_check(before, context="F47 test 7")
    check("exclude restored after the .claude/ mask attempt",
          EXCLUDE.read_bytes(), EX_ORIGINAL)
    check("HANDOFF.md itself was never touched (not new, not tampered)",
          (ROOT / ".claude" / "HANDOFF.md").is_file(), True)
finally:
    PLANT.unlink(missing_ok=True)
    EXCLUDE.write_bytes(EX_ORIGINAL)

print(f"\nexclude sha256 {hashlib.sha256(EXCLUDE.read_bytes()).hexdigest()[:16]} "
      f"(unchanged: {EXCLUDE.read_bytes() == EX_ORIGINAL})")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
