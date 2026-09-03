"""F107: silence the false-positive protected-path masking warning for
.claude/settings.local.json.

The fs-guard's _masked_under_protected() (F47) flags any path under a
PROTECTED_PATH that an UNVERSIONED exclude source (.git/info/exclude or the
global core.excludesFile) hides from `git ls-files --others`. `.claude/` became a
PROTECTED_PATH in F52 to guard the TRACKED .claude/HANDOFF.md. But Claude Code's
per-machine .claude/settings.local.json is legitimately local: the operator's
GLOBAL git ignore (`**/.claude/settings.local.json`) hides it in every repo. The
guard cannot tell a benign globally-ignored local-settings file from a real
masking attack, so it fired the WARNING on every fs_integrity_snapshot() in the
real working repo -- crying wolf on the one file that is supposed to be local,
and risking drowning a genuine masking of a tracked protected file.

WHY THE GATE NEVER CAUGHT THIS: containment-tier tests run in a DISPOSABLE repo
where .claude/settings.local.json is never copied (the untracked-file allowlist
in _run_containment is orchestrator/prediction_machine/tests/config only), so
_masked_under_protected() is empty there. The warning only fires in the real
working tree. Verified directly 2026-09-03: `MASKED= ['.claude/settings.local.json']`
in the real repo, empty in the disposable one.

FIX: list .claude/settings.local.json in the VERSIONED .gitignore. A path the
versioned .gitignore excludes is excluded in BOTH --exclude-per-directory=.gitignore
and --exclude-standard modes, so it drops out of the masking set difference --
moving it from the "hidden by unversioned rule" bucket into the auditable "hidden
by versioned rule" bucket. The guard's real job is untouched.

This test (containment tier, disposable repo) pins:
  1. the versioned .gitignore carries the exclusion (regression guard against
     someone removing the line), and
  2. the MECHANISM: a path masked only by an unversioned source IS flagged, but
     once the SAME path is also in the versioned .gitignore, it is NOT flagged.
     Proved with a planted probe under a protected path, independent of the
     global-ignore environment.

The production condition (settings.local.json present + globally ignored -> no
longer masked) is verified by hand on the operator machine, not asserted here,
because it depends on the machine's global core.excludesFile and would trivially
pass (false-negative) on a machine without that rule.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import integrity  # noqa: E402

integrity.escalate = lambda *a, **k: None
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got!r} want={want!r}")


GITIGNORE = ROOT / ".gitignore"
EXCLUDE = ROOT / ".git" / "info" / "exclude"
PROBE = ROOT / "orchestrator" / "_f107_probe.py"

print("=== 1. the versioned .gitignore excludes .claude/settings.local.json ===")
# Read bytes so the byte-identical restore later is exact regardless of EOL style.
GI_ORIG = GITIGNORE.read_bytes()
check(".gitignore carries the F107 exclusion",
      b".claude/settings.local.json" in GI_ORIG, True)
# The line must be a real ignore rule (anchored to .claude/), not a substring of
# a comment that happens to mention the path. Confirm it appears as its own line.
check("...as its own line (a real rule, not prose in a comment)",
      b"\n.claude/settings.local.json" in GI_ORIG
      or GI_ORIG.startswith(b".claude/settings.local.json"), True)

print("\n=== 2. mechanism: a versioned .gitignore entry silences an unversioned mask ===")
EXCLUDE_ORIG = EXCLUDE.read_bytes() if EXCLUDE.is_file() else b""
try:
    # Baseline: nothing under orchestrator/ is masked yet.
    check("baseline: probe not masked before planting",
          "orchestrator/_f107_probe.py" in integrity._masked_under_protected(), False)

    # Plant an untracked file under a protected path. It is visible (not masked):
    # it appears in both ignore-modes, so the set difference is empty.
    PROBE.write_text("# probe\n", encoding="utf-8")
    check("planted probe is visible to the untracked scan (not yet masked)",
          "orchestrator/_f107_probe.py" in integrity._untracked_files(), True)

    # Mask it via the UNVERSIONED .git/info/exclude ONLY (the bug class).
    EXCLUDE.write_bytes(EXCLUDE_ORIG + b"\norchestrator/_f107_probe.py\n")
    check("probe masked ONLY by an unversioned source IS flagged (the bug class)",
          "orchestrator/_f107_probe.py" in integrity._masked_under_protected(), True)

    # The F107 move: ALSO list the probe in the VERSIONED .gitignore.
    GITIGNORE.write_bytes(GI_ORIG + b"\norchestrator/_f107_probe.py\n")
    check("once the versioned .gitignore also excludes it, the mask is silenced",
          "orchestrator/_f107_probe.py" in integrity._masked_under_protected(), False)
finally:
    GITIGNORE.write_bytes(GI_ORIG)
    EXCLUDE.write_bytes(EXCLUDE_ORIG)
    PROBE.unlink(missing_ok=True)

# Post-restore sanity (the substantive invariants -- byte-level restoration).
check("probe file removed after test", PROBE.exists(), False)
check(".git/info/exclude restored byte-identical", EXCLUDE.read_bytes(), EXCLUDE_ORIG)
check(".gitignore restored byte-identical", GITIGNORE.read_bytes(), GI_ORIG)

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
