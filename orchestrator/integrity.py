"""orchestrator/integrity.py — filesystem and DB containment (extracted from batch_runner.py).

This module owns the two integrity guards the harness uses to keep a worker
subprocess from doing what it has not been asked to do: a database row count /
provenance check (F1/H2, INCIDENTS 2026-07-18) and a content-hash-based
filesystem tamper check (F14, F36, F42, F46, F47, F52). Plus the dispatch
side: a small preflight that confirms the local Ollama server is up before
any work runs, and an `escalate()` helper that records an intervention
either to a markdown file (always) or to a real ledger row (F53, when a
task_id is given) or to Telegram (best-effort, when configured).

Extracted from `batch_runner.py` on 2026-08-26 as Move 1 of the Week 9
5-file split (see `REFACTOR_PLAN.md`). Every function in this file is
moved byte-for-byte from the original, with the same docstrings, the
same quirks, and the same F-numbers in the comments. The
`batch_runner.py` module re-exports each name so existing callers
(including 7 test files) continue to work without edits.

Layer position: L0 of the dependency graph. No internal cross-module
imports; only `policy`, `ledger`, `runtime_context`, and stdlib.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from runtime_context import ROOT, RUNS, ESCALATIONS, log

# Policy + ledger are sibling modules in the orchestrator/ directory (the existing
# convention treats `orchestrator/` as a flat namespace, not a real package -- there
# is no `__init__.py`, and tests reach in via `sys.path.insert(0, 'orchestrator')`
# followed by `import policy`). Imported lazily inside the functions that use them
# so this module can be imported at the top of `batch_runner.py` without forcing a
# heavy import chain on every test.
import policy  # noqa: E402
import ledger  # noqa: E402


def escalate(reason: str, trigger: str | None = None, task_id: int | None = None) -> None:
    # trigger, when given, must be one of policy.yaml's own escalation.triggers
    # (F13, docs/HARDENING.md) -- validated so the declared list stays authoritative
    # rather than becoming stale decoration the moment a caller typos it.
    policy.validate_trigger(trigger)
    tagged = f"[{trigger}] {reason}" if trigger else reason
    ESCALATIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(ESCALATIONS, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().isoformat(timespec='seconds')} — {tagged}\n")
    log(f"ESCALATION -> {ESCALATIONS.name}: {tagged}")
    # F53 (docs/HARDENING.md): a TASK-scoped escalation is an intervention -- the system
    # could not finish this task itself and asked a human. Until now that fact reached a
    # markdown file and nothing else, so the ledger column that scores it read 0 forever
    # and 25% of fitness was awarded unconditionally. Run-scoped escalations (ollama
    # unreachable, batch aborted) pass no task_id and are deliberately not counted: they
    # are infrastructure, not a verdict on any one task's autonomy -- the same distinction
    # F37 draws between infra failure and the analyst being wrong. Fail-soft: an
    # escalation must still be delivered even if the ledger write fails.
    if task_id is not None:
        try:
            ledger.record_intervention(task_id, trigger or "escalation")
        except Exception as e:
            log(f"WARNING: intervention not recorded for task {task_id}: {e}")
    # Best-effort push: inert until the operator sets a Telegram home channel
    # (they must message the bot once — platform rule). File above is the source of truth.
    try:
        import scorecard
        scorecard.send_telegram(f"⚠ AGI harness escalation: {reason}")
    except Exception:
        pass


# F1 / H2 (docs/HARDENING.md, docs/INCIDENTS.md 2026-07-18): a worker once wrote
# directly to ledger.db and ledgerbook.db, scored its own task, and walked away
# with a 'pass'. The harness's defense against that is provenance-based: every
# legitimate row the orchestrator writes carries a run_id; worker writes do not,
# because the worker is never told run_id exists. So:
#
#   snapshot BEFORE worker runs    ->  {"ledger.tasks": {count, max_id}, ...}
#   run worker
#   snapshot AFTER                 ->  same shape
#   diff                           ->  any row with id > snapshot.max_id AND run_id IS NULL
#                                     is the worker's unauthorized write; quarantine+revert.
#
# A concurrent legitimate insert (carrying a run_id) is spared. A blind count
# comparison is NOT used because it would punish the legitimate concurrent run
# (proven 2026-07-19 on DB copies). The four tables NOT in _PROVENANCE_TABLES
# (entities, decisions, experiences, failures) keep the legacy count-only
# behaviour: they are orchestrator-only writes in the normal workflow, never written
# by the live worker path, and H1's run-lock means there is no concurrent
# process to protect them from.
_PROVENANCE_TABLES = {"tasks": "task_id", "facts": "id"}


def _db_snapshot() -> dict:
    """count + max(id) per table, both DBs. max(id) lets the check identify EXACTLY the
    new rows later (WHERE id > snapshot_max_id) rather than guessing via ORDER BY LIMIT,
    which can misidentify rows if anything else changed the table shape meanwhile."""
    snap = {}
    for name, path in (("ledger", ledger.LEDGER_DB),
                       ("ledgerbook", ROOT / "memory" / "ledgerbook.db")):
        with sqlite3.connect(path, timeout=30) as c:
            for table in ("tasks", "entities", "facts", "decisions", "experiences", "failures"):
                id_col = _PROVENANCE_TABLES.get(table, "id")
                row = c.execute(f"SELECT count(*), max({id_col}) FROM {table}").fetchone()
                snap[f"{name}.{table}"] = {"count": row[0], "max_id": row[1] or 0}
    return snap


def db_integrity_snapshot() -> dict:
    """Call immediately BEFORE a worker subprocess runs."""
    return _db_snapshot()


def db_integrity_check(before: dict, context: str) -> None:
    """Call immediately AFTER a worker subprocess returns, BEFORE the orchestrator's own
    ledger.finish_task() write. See docs/INCIDENTS.md 2026-07-18 for why this exists and
    docs/HARDENING.md F1/H2 for why it is provenance-based (run_id) rather than a blind
    count comparison: the earlier count-only version deleted a legitimate CONCURRENT
    process's rows and raised a false alarm about it, proven on DB copies 2026-07-19.

    For tasks/facts: only rows with id > snapshot's max_id AND run_id IS NULL are
    quarantined -- a concurrent legitimate insert (valid run_id) is left untouched.
    For the other four tables (never written by the live worker path, and not
    run_id-tracked): unchanged blind behavior, now safe in practice because H1 serializes
    orchestrator processes."""
    import json as _json
    after = _db_snapshot()
    changed = {k: (before[k], after[k]) for k in after if after[k] != before[k]}
    if not changed:
        return
    dump = {"context": context, "changed": changed, "quarantined_rows": {}, "spared_rows": {}}
    any_quarantined = False
    for key, (b, a) in changed.items():
        if a["count"] <= b["count"]:
            continue  # a decrease is not a worker-write; leave it, just recorded above
        dbname, table = key.split(".", 1)
        path = ledger.LEDGER_DB if dbname == "ledger" else ROOT / "memory" / "ledgerbook.db"
        with sqlite3.connect(path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            id_col = _PROVENANCE_TABLES.get(table, "id")
            new_rows = c.execute(
                f"SELECT * FROM {table} WHERE {id_col} > ?", (b["max_id"],)).fetchall()
            if table in _PROVENANCE_TABLES:
                bad = [r for r in new_rows if r["run_id"] is None]
                good = [r for r in new_rows if r["run_id"] is not None]
                if good:
                    dump["spared_rows"][key] = [dict(r) for r in good]
                    log(f"{context}: {len(good)} new {table} row(s) have valid run_id "
                       f"(concurrent legitimate run) -- spared")
            else:
                bad = new_rows  # unchanged legacy behavior for non-provenance tables
            if not bad:
                continue
            any_quarantined = True
            dump["quarantined_rows"][key] = [dict(r) for r in bad]
            ids = [r[id_col] for r in bad]
            c.executemany(f"DELETE FROM {table} WHERE {id_col}=?", [(i,) for i in ids])
    if not any_quarantined:
        return  # every new row had valid provenance -- not an incident, nothing to log loudly
    log(f"INTEGRITY VIOLATION during {context}: unauthorized DB writes detected {changed}")
    RUNS.mkdir(exist_ok=True)
    qpath = RUNS / f"quarantine_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    qpath.write_text(_json.dumps(dump, indent=2, default=str), encoding="utf-8")
    log(f"quarantined unauthorized rows -> {qpath.name}; reverted DB to pre-call state")
    escalate(f"worker wrote directly to a database during {context} -- quarantined, "
            f"see {qpath.name}. Toolset restriction is NOT reliable in this Hermes "
            f"version; this guard is the real containment.")


# H9 / F14 (docs/HARDENING.md): the worker holds write_file/edit_file/terminal/python/patch
# (the -t web flag does NOT reliably strip them -- docs/INCIDENTS.md) and runs as the
# operator's own account. No filesystem confinement exists -- a worker could overwrite
# orchestrator/batch_runner.py itself (e.g. delete this very guard) or the schtasks/.env.
# True sandboxing needs either a container (Docker was removed from this machine,
# 2026-07-08, per standing machine facts) or a separate low-privilege OS user (an invasive
# system change requiring explicit operator consent, not something to do unilaterally).
# What IS buildable right now, using only what already exists: every file worth protecting
# (orchestrator/, config/, missions/, ledger/schema.sql, the top-level docs) is git-tracked.
# `git status --porcelain` on that set becomes the integrity check -- exactly the same
# "compare a snapshot, quarantine the diff" pattern as db_integrity_check(), reusing git's
# own tracking instead of reinventing a hash-based file watcher. `git checkout --` cleanly
# reverts tracked tampering; new untracked files inside the protected set are removed.
# This is detection + auto-revert, NOT prevention -- a worker COULD still read secrets or
# do damage before the next check fires. It closes the specific F14 nightmare scenario
# (the harness silently loses its own integrity mid-run) without requiring an invasive
# system change the operator hasn't approved.
PROTECTED_PATHS = ["orchestrator", "config", "ledger/schema.sql", "missions",
                   "CLAUDE.md", "HARNESS_DESIGN.md", "docs", "skills_analyst",
                   # F42 (docs/HARDENING.md), 2026-07-30: root-level files were never in
                   # this list. `.gitignore` is the worst omission -- rewriting it is how a
                   # worker would make its own output invisible to this very guard, since
                   # detection is `git status`, which honours ignore rules. IDENTITY.md was
                   # simply missed.
                   ".gitignore", "IDENTITY.md",
                   # 2026-07-30: the regression suites moved from session temp scratchpads
                   # into tests/. They are the only thing standing between F34-F47 and a
                   # silent regression, so a worker must not be able to weaken the tests that
                   # check the worker -- the same argument F42 made for .gitignore.
                   "tests",
                   # F52 (docs/HARDENING.md), 2026-07-30: `.claude` had to join this list the
                   # moment HANDOFF.md was COMMITTED. While untracked it was covered by
                   # _untracked_files()'s hashes (F46/F47); tracking it removed it from that set
                   # WITHOUT adding it to _tracked_hashes(), which only walks PROTECTED_PATHS.
                   # Measured, not argued: a simulated tamper produced an empty delta on all
                   # four detection channels. `.claude` is Claude Code's own config tree --
                   # agents, skills, settings, hooks -- i.e. the one place where a written file
                   # steers the SUPERVISING agent, which is exactly why F46 refused to
                   # gitignore it. `.claude/worktrees/` stays out via .git/info/exclude, itself
                   # watched by F47.
                   ".claude",
                   # Compact Brief continuity state. Like HANDOFF.md, this file can steer
                   # the supervising agent after context loss, so it belongs on the same
                   # tracked-hash containment surface. Truth still comes from live Git/DB
                   # state; protection only makes unexpected edits visible.
                   ".harness",
                   # Cross-agent instruction file at repo root. The industry convention
                   # (Aider/Codex/Cursor/Continue auto-discover an `AGENTS.md` at the
                   # project root) means a written edit here can steer any supervising
                   # agent, not just one. Same argument F52 made for `.claude`. Listed
                   # explicitly rather than by directory so a worker cannot drop a
                   # sibling `AGENTS.local.md` to evade the guard.
                   "AGENTS.md"]

# F42: PROTECTED_PATHS covers files that EXIST. A brand-new file at the repo root matched
# nothing in it and was therefore invisible -- found 2026-07-30 when a 699-line
# `extensive_research.md` appeared at the root and no guard noticed (benign: the operator
# put it there deliberately, and its timestamp predated the run).
#
# Deliberately NOT fixed by adding "." to PROTECTED_PATHS, even though that tests clean
# today. `memory/` and `workspace/` are policy.yaml WRITABLE roots living inside the repo
# root, so "." would declare the same subtree both writable and protected. policy.
# validate_paths() compares literal paths and would not catch that contradiction, so the
# drift-check this project relies on would silently pass on an inconsistency -- exactly the
# failure mode F13 exists to prevent. A depth-0 scan states the actual intent instead: the
# root itself accepts no new files, while its writable subdirectories are untouched.
def _untracked_files() -> dict:
    """F46: sha256 of every untracked, non-ignored file that policy.yaml does not declare
    writable. Keyed by repo-relative path, so a NEW path and an in-place rewrite of an
    existing one are both visible.

    Replaces F42's `_root_untracked()`, which listed depth-0 root entries from
    `git status --porcelain` in its DEFAULT untracked mode. That mode collapses an untracked
    DIRECTORY into a single line (`?? .claude/`), so every file inside one was invisible:
    measured 2026-07-30, planting `.claude/evil.py` left the entry set byte-identical
    (`{'.claude/'}` before and after) and produced an EMPTY delta. The depth-0 filter then
    guaranteed the collapse could never be undone, since any expanded path contains a
    separator and was therefore dropped. `--untracked-files=all` lists the files themselves.

    This mattered because of WHICH directory was untracked: `.claude/` is Claude Code's own
    configuration tree — settings, hooks, skills, agent definitions. A payload written there
    targets the SUPERVISING agent rather than the worker, which is the highest-value
    prompt-injection target in the whole system and the one place F14's containment was blind.

    Hashes rather than a bare path set, for the same reason F36 added them for tracked files:
    rewriting an ALREADY-untracked file re-prints an identical `??` line, so a set difference
    cannot see it. `.claude/HANDOFF.md` is exactly that case — the handoff the next session
    reads — and it was equally undetectable (also measured, empty delta).

    Policy-writable roots (`workspace/`, `memory/`, the ledger DB) are excluded because the
    orchestrator itself legitimately creates untracked files there mid-run: a new
    `memory/scorecards/*.md` is authorised work, not tampering. Sourced from policy.yaml via
    `policy.is_path_writable()` rather than a second hardcoded list, per F13 — the policy file
    must be a real, read source, not documentation. Loaded once here rather than per file.

    Scope note: `-uall` is repo-wide, not root-only, because the gap is not specific to the
    root — an untracked directory anywhere collapses the same way. Cost is bounded by
    gitignore (`runs/`, `workspace/`, `inbox/*`, `__pycache__/`, `extensive_research.md` are
    all excluded by git itself) and git does not recurse into the nested worktree under
    `.claude/worktrees/`; the live set is 1 file, measured."""
    pol = policy.load()
    # F47: `ls-files --others --exclude-per-directory=.gitignore` rather than
    # `status --porcelain -uall --exclude-standard`. Two changes, both deliberate:
    #   * ls-files --others names files individually, so no directory collapse to undo.
    #   * --exclude-per-directory honours ONLY the version-controlled .gitignore files.
    #     --exclude-standard additionally honours .git/info/exclude and the global ignore,
    #     neither of which is in the audit trail, so under it an untracked file could be
    #     hidden from this guard by one appended line leaving no trace in git history
    #     (measured: planted a root file, guard saw it, appended one line, guard went blind).
    # A nested repository still reports as one collapsed entry (git will not enumerate
    # another repo's files); those are marked below rather than silently dropped.
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--others",
                          "--exclude-per-directory=.gitignore"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    hashes = {}
    for ln in out.stdout.splitlines():
        rel = ln.strip().strip('"')
        if not rel:
            continue
        p = ROOT / rel
        if policy.is_path_writable(p, pol):
            continue
        if rel.endswith("/"):
            # A nested repository/worktree. git cannot enumerate inside it, so its
            # appearance and disappearance are detectable but its contents are not.
            hashes[rel] = "<nested-repo>"
            continue
        try:
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            hashes[rel] = "<unreadable>"
    return hashes


def _local_exclude_sources() -> list[Path]:
    """F47: the ignore sources git honours that are NOT in the repository.

    `git status`/`ls-files` apply three ignore sources. Only `.gitignore` is version-
    controlled (and protected — F42 put it in PROTECTED_PATHS precisely because rewriting it
    masks the worker's own output). The other two are invisible to the audit trail:

      * `.git/info/exclude` — repo-local, never committed, outside PROTECTED_PATHS.
      * the global file — `core.excludesFile` if set, else git's XDG default
        `~/.config/git/ignore`, which git honours **even when the config key is unset**.
        Measured 2026-07-30: unset here, yet the XDG file exists and carries
        `**/.claude/settings.local.json`, i.e. an active rule hiding a Claude Code settings
        file — hooks and permissions — from this guard.
    """
    out = [ROOT / ".git" / "info" / "exclude"]
    cfg = subprocess.run(["git", "-C", str(ROOT), "config", "--get", "core.excludesFile"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if cfg.stdout.strip():
        out.append(Path(os.path.expanduser(cfg.stdout.strip())))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        out.append(Path(xdg) / "git" / "ignore")
    return out


def _local_exclude_state() -> dict:
    """F47: exact BYTES of each unversioned exclude source, keyed by absolute path.

    Bytes rather than hashes because the remediation is to put them back: these files are
    small (hundreds of bytes), and a hash tells you something changed without letting you
    undo it. A missing file is recorded as None so that CREATING one is a change too — the
    cheapest way to blind the guard is to write a `.git/info/exclude` that did not exist."""
    state = {}
    for p in _local_exclude_sources():
        try:
            state[str(p)] = p.read_bytes()
        except FileNotFoundError:
            state[str(p)] = None
        except Exception:
            state[str(p)] = b"<unreadable>"
    return state


def _masked_under_protected() -> set:
    """F47: paths under PROTECTED_PATHS that an UNVERSIONED exclude source is hiding.

    The direct answer to "a protected path must not be maskable by a local exclusion": ask
    git the same question twice, once honouring every ignore source and once honouring only
    the version-controlled `.gitignore` files, and diff. Anything present in the second and
    absent from the first is being hidden by `.git/info/exclude` or the global file.

    This catches a mask that was planted BEFORE the snapshot, which the byte-comparison in
    `_local_exclude_state()` cannot see — that one only catches a change across a worker
    call. Together they cover both the pre-planted and the freshly-written case."""
    def others(mode: str) -> set:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--others", mode,
                            "--", *PROTECTED_PATHS],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return others("--exclude-per-directory=.gitignore") - others("--exclude-standard")


def _untracked_of(snap: dict) -> dict:
    """The untracked bucket of a snapshot, as {path: hash}. Accepts a pre-F46 snapshot:
    F42's key was `root` and held a bare set, which becomes {path: None} — new paths are
    still detected, only in-place rewrites are not, which is exactly the pre-F46 behaviour
    rather than a crash. Matters because a snapshot is taken before a worker call and read
    after it, so a mid-run code change must not make the two halves incompatible."""
    u = snap.get("untracked", snap.get("root", {}))
    return {k: None for k in u} if isinstance(u, (set, frozenset)) else u


def _tracked_hashes() -> dict:
    """sha256 of every TRACKED file under PROTECTED_PATHS. 31 files / ~510KB measured
    2026-07-29, so this costs milliseconds per worker call."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", *PROTECTED_PATHS],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    hashes = {}
    for rel in out.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        p = ROOT / rel
        try:
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except FileNotFoundError:
            hashes[rel] = "<missing>"      # worker deleted it -- still a violation
        except Exception:
            hashes[rel] = "<unreadable>"
    return hashes


def fs_integrity_snapshot() -> dict:
    """Call immediately BEFORE a worker subprocess runs.

    F36 (docs/HARDENING.md): this used to return only the `git status --porcelain` line
    set, which cannot see a file that was ALREADY dirty and then modified AGAIN by the
    worker -- porcelain prints an identical ' M path' line both times, so the set
    difference is empty and the tamper is invisible. Content hashes close that hole, and
    are what make it safe to scope the revert below to exactly the files that changed."""
    out = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", *PROTECTED_PATHS],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    masked = _masked_under_protected()          # F47
    if masked:
        # Pre-existing, so not attributable to this call -- but it means a protected path is
        # already hidden from the guard by an unversioned rule, which is worth saying out loud
        # every time rather than only when it changes.
        log(f"WARNING: {len(masked)} path(s) under PROTECTED_PATHS are masked by an "
            f"unversioned exclude source: {sorted(masked)[:5]}")
    return {"porcelain": set(out.stdout.splitlines()), "hashes": _tracked_hashes(),
            "untracked": _untracked_files(),   # F42, widened by F46 (key was "root")
            "excludes": _local_exclude_state(), "masked": masked}      # F47


def fs_integrity_check(before: dict, context: str) -> None:
    """Call immediately AFTER a worker subprocess returns. Any tracked file whose CONTENT
    changed, or any new untracked file, under a protected path is unauthorized by
    construction -- the worker is never told these paths exist (same containment principle
    as the DB guard) and the orchestrator does not touch its own source during a task.

    F36 (docs/HARDENING.md), 2026-07-29 -- two defects, fixed together:

    1. BLAST RADIUS. Detection was precise (`after - before`) but remediation was not:
       `git checkout -- *PROTECTED_PATHS` discarded every dirty tracked file under all
       eight protected paths the moment ANY entry was flagged, including files the guard
       never flagged and the worker never touched. Now reverts exactly the paths whose
       content changed. This cannot weaken detection, because it does not touch detection.
    2. UNRECOVERABLE. `git checkout` on a tracked file has no reflog -- discarded
       uncommitted work is simply gone. Proven the hard way twice on 2026-07-29: once when
       a worker call reverted this session's own uncommitted F35 fix, and again when the
       cleanup block of THIS fix's own test did `git checkout -- config orchestrator` and
       destroyed the fix itself. The pattern is genuinely easy to write by accident, which
       is the argument for making it recoverable rather than merely careful. Flagged files
       are now copied into `runs/reverted_<ts>/` BEFORE anything is discarded; `runs/` is
       gitignored and outside PROTECTED_PATHS, so the copy is neither reverted nor
       committed.

    The scoping in (1) was only safe once hashing landed in the snapshot: with porcelain
    alone, a file already dirty before the call and re-modified during it never appeared
    in the diff, so a scoped revert would have missed it while the blanket one caught it
    by accident."""
    after = fs_integrity_snapshot()
    changed = sorted(p for p in set(before["hashes"]) | set(after["hashes"])
                     if before["hashes"].get(p) != after["hashes"].get(p))
    new_untracked = [e[3:].strip().strip('"') for e in (after["porcelain"] - before["porcelain"])
                     if e.startswith("?? ")]
    # F42: a new file dropped at the repo root matches no PROTECTED_PATHS entry, so the
    # porcelain diff above never sees it. F46 widened that from depth-0 root entries to every
    # untracked non-ignored path, keyed by content hash, so a file planted INSIDE an untracked
    # directory and an in-place rewrite of an already-untracked file are both caught.
    u_before, u_after = _untracked_of(before), _untracked_of(after)
    new_root = set(u_after) - set(u_before)
    tampered = {p for p in set(u_after) & set(u_before) if u_after[p] != u_before[p]}
    new_untracked = sorted(set(new_untracked) | new_root | tampered)

    # F47: the ignore machinery itself. Rewriting an unversioned exclude source is a way to
    # make everything above stop reporting, so it is checked separately from what it hides.
    ex_before = before.get("excludes", {})
    ex_after = after.get("excludes", {})
    ex_changed = sorted(p for p in set(ex_before) | set(ex_after)
                        if ex_before.get(p) != ex_after.get(p))
    new_masked = sorted(after.get("masked", set()) - before.get("masked", set()))
    if not changed and not new_untracked and not ex_changed and not new_masked:
        return
    if ex_changed:
        log(f"EXCLUDE-SOURCE TAMPERING during {context}: {ex_changed}")
    if new_masked:
        log(f"PROTECTED PATH NEWLY MASKED during {context}: {new_masked}")
    log(f"FILESYSTEM INTEGRITY VIOLATION during {context}: "
        f"{len(changed)} tracked modified {changed[:5]}, "
        f"{len(new_untracked)} untracked new/tampered {new_untracked[:5]}")

    # Preserve before destroying -- see (2) above.
    stash = RUNS / f"reverted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    saved = 0
    for rel in changed + new_untracked:
        src = ROOT / rel
        if not src.is_file():
            continue                       # deleted by the worker; nothing to preserve
        try:
            dst = stash / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            saved += 1
        except Exception as e:
            log(f"  could not preserve {rel} before revert: {e}")
    if saved:
        log(f"  preserved {saved} file(s) to {stash.relative_to(ROOT)} before reverting")

    if changed:
        subprocess.run(["git", "-C", str(ROOT), "checkout", "--", *changed],
                       capture_output=True, text=True)
    removed = []
    for rel in new_untracked:              # checkout does not touch untracked files
        target = ROOT / rel
        try:
            if target.is_file():
                target.unlink()
                removed.append(rel)
        except Exception as e:
            log(f"  could not remove untracked tampered file {rel}: {e}")
    log(f"reverted {len(changed)} tracked file(s) via git checkout; removed "
        f"{len(removed)} untracked file(s): {removed}")

    # F47: restore a tampered exclude source from the bytes in the snapshot. `git checkout`
    # is useless here -- these files are not tracked, which is the whole problem -- so the
    # pre-call bytes ARE the only source of truth, which is why the snapshot keeps them.
    # Scoped to files inside the repo on purpose: the global ignore is the operator's personal
    # config, and silently rewriting a file outside the project is beyond what a containment
    # guard should do on its own. Out-of-repo tampering is reported and left alone.
    restored, reported = [], []
    for p in ex_changed:
        path = Path(p)
        try:
            # str-prefix on resolved paths: `ROOT in path.parents` is exact-match only and
            # fails on case or separator differences, which on Windows is a coin flip.
            inside = str(path.resolve()).lower().startswith(str(ROOT.resolve()).lower())
        except Exception:
            inside = False
        if not inside:
            reported.append(p)
            continue
        try:
            prior = ex_before.get(p)
            if path.is_file():
                # `changed`/`new_untracked` may both be empty when only an exclude source was
                # touched, in which case the preserve loop above never created the stash.
                stash.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, stash / f"EXCLUDE_{path.name}")
            if prior is None:
                path.unlink(missing_ok=True)   # it did not exist before the call
            elif isinstance(prior, bytes) and prior != b"<unreadable>":
                path.write_bytes(prior)
            restored.append(p)
        except Exception as e:
            log(f"  could not restore exclude source {p}: {e}")
            reported.append(p)
    if restored:
        log(f"  restored {len(restored)} unversioned exclude source(s) from snapshot bytes: "
            f"{restored}")
    if reported:
        log(f"  exclude source(s) changed OUTSIDE the repo -- reported, NOT modified: {reported}")

    escalate(f"worker modified protected harness files during {context} -- auto-reverted "
            f"via git (originals preserved in {stash.name}). F14 containment fired: "
            f"{(changed + new_untracked)[:5]}"
            + (f" | exclude-source tampering: {ex_changed}" if ex_changed else "")
            + (f" | protected paths newly masked: {new_masked}" if new_masked else ""))


def preflight() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5)
        return True
    except Exception:
        log("ollama server down — attempting autostart via `ollama ps`")
        try:
            subprocess.run(["ollama", "ps"], capture_output=True, timeout=60)
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10)
            return True
        except Exception as e:
            log(f"PREFLIGHT FAIL: ollama unreachable ({e})")
            escalate("batch run aborted: ollama server unreachable")
            return False
