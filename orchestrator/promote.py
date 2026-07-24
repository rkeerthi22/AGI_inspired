"""Gated skill promotion (HARNESS_DESIGN §2.4) — the loop's last stage.

    python orchestrator/promote.py review [--notify] [--dry]   # Sunday: draft candidates
    python orchestrator/promote.py list                        # pending candidates + active skills
    python orchestrator/promote.py approve <candidate-file>    # OPERATOR gate
    python orchestrator/promote.py reject  <candidate-file>
    python orchestrator/promote.py rollback <mission>/<skill-file>

Skills are repo-versioned markdown notes under skills_analyst/<mission_id>/ — injected into
that mission's worker prompt by batch_runner.run_task(). Rollback = git rm + commit (fully
auditable). Promotion is HUMAN-gated in M1: review only ever drafts; approve is the operator.
Stdlib + existing batch_runner helpers only."""
import argparse
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills_analyst"
CANDIDATES = SKILLS / "_candidates"
REJECTED = SKILLS / "_rejected"
MAX_CANDIDATES_PER_MISSION = 1     # per review pass — conservative by design
MIN_EVIDENCE_ROWS = 2              # a lesson pool smaller than this doesn't meet the bar


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _lesson_pool() -> dict[str, list[dict]]:
    """Unpromoted lessons grouped by mission (via tasks join)."""
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT lc.id, lc.lesson, lc.kind, lc.times_seen, t.mission_id "
            "FROM lesson_candidates lc JOIN tasks t ON t.task_id = lc.task_id "
            "WHERE lc.promoted_to IS NULL AND t.mission_id != 'canaries' "
            "ORDER BY t.mission_id, lc.id").fetchall()
    pool: dict[str, list[dict]] = {}
    for r in rows:
        pool.setdefault(r["mission_id"], []).append(dict(r))
    return pool


def _current_canary_green() -> int:
    """This week's canary green count — recorded at approval as the skill's baseline."""
    wk = datetime.now().strftime("%Y-W%V")
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        return c.execute(
            "SELECT count(*) FROM tasks WHERE mission_id='canaries' AND spec LIKE ? "
            "AND status='done' AND critic_verdict='pass'", (f"[{wk}]%",)).fetchone()[0]


def cmd_review(notify: bool, dry: bool) -> int:
    pool = _lesson_pool()
    if not pool:
        _log("review: no unpromoted lessons — nothing to draft")
        return 0
    if dry:
        for mission, lessons in pool.items():
            _log(f"DRY {mission}: {len(lessons)} lesson(s)")
            for l in lessons:
                print(f"    #{l['id']} [{l['kind']} x{l['times_seen']}] {l['lesson'][:90]}")
        return 0

    from batch_runner import load_roles, ollama_chat  # late import: model deps only here
    manager = load_roles()["manager"]["model"]
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    drafted = []
    for mission, lessons in pool.items():
        if len(lessons) < MIN_EVIDENCE_ROWS:
            _log(f"{mission}: {len(lessons)} lesson(s) < bar of {MIN_EVIDENCE_ROWS} — skipped")
            continue
        listing = "\n".join(f"- (id {l['id']}, {l['kind']}, seen x{l['times_seen']}) {l['lesson']}"
                            for l in lessons[:20])
        prompt = (
            "You review an analyst's logged lessons and draft AT MOST ONE reusable technique "
            "note — only if the lessons genuinely support one. A technique note is a short, "
            "imperative instruction the analyst should follow on FUTURE tasks (e.g. 'When a "
            "review site blocks bots, check the product's app-store listing instead').\n\n"
            f"LESSONS for mission {mission}:\n{listing}\n\n"
            "Reply with EITHER exactly NO_CANDIDATE (if nothing generalizes) OR:\n"
            "TITLE: <5-8 word imperative title>\nNOTE: <2-5 sentences, imperative, specific, "
            "no fluff>\nEVIDENCE: <comma-separated lesson ids that support it>")
        try:
            out = ollama_chat(manager, prompt).strip()
        except Exception as e:
            _log(f"{mission}: review call failed ({e}) — will retry next Sunday")
            continue
        if "NO_CANDIDATE" in out[:40].upper():
            _log(f"{mission}: manager found no lesson that met the bar")
            continue
        title_m = re.search(r"TITLE:\s*(.+)", out)
        note_m = re.search(r"NOTE:\s*(.+?)(?=\nEVIDENCE:|\Z)", out, re.S)
        ev_m = re.search(r"EVIDENCE:\s*([\d,\s]+)", out)
        if not (title_m and note_m):
            _log(f"{mission}: unparseable review output — skipped (raw kept in runs/)")
            (ROOT / "runs" / f"promote_review_raw_{mission}.txt").write_text(
                out, encoding="utf-8")
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title_m.group(1).lower()).strip("-")[:50]
        fname = CANDIDATES / f"{datetime.now().strftime('%Y%m%d')}_{mission}_{slug}.md"
        fname.write_text(
            f"---\nmission: {mission}\ntitle: {title_m.group(1).strip()}\n"
            f"status: pending\ncreated: {datetime.now().date()}\n"
            f"evidence_lesson_ids: [{ev_m.group(1).strip() if ev_m else ''}]\n---\n\n"
            f"{note_m.group(1).strip()}\n", encoding="utf-8")
        drafted.append(fname.name)
        _log(f"{mission}: drafted candidate -> {fname.name}")

    if drafted and notify:
        try:
            from scorecard import send_telegram
            send_telegram(f"🧪 {len(drafted)} candidate skill(s) awaiting your approval: "
                          + ", ".join(drafted)
                          + " — python orchestrator/promote.py list")
        except Exception:
            pass
    if not drafted:
        _log("review complete: no candidates drafted this pass")
    return 0


def cmd_list() -> int:
    print("PENDING CANDIDATES (approve/reject by filename):")
    pend = sorted(CANDIDATES.glob("*.md")) if CANDIDATES.exists() else []
    for f in pend:
        print(f"  {f.name}")
        print("    " + f.read_text(encoding="utf-8").split("---")[-1].strip()[:150])
    if not pend:
        print("  (none)")
    print("\nACTIVE SKILLS:")
    active = [p for p in SKILLS.glob("*/*.md")
              if p.parent.name not in ("_candidates", "_rejected")]
    for f in sorted(active):
        print(f"  {f.parent.name}/{f.name}")
    if not active:
        print("  (none)")
    return 0


def cmd_approve(name: str) -> int:
    src = CANDIDATES / name
    if not src.exists():
        _log(f"no such candidate: {name}"); return 1
    text = src.read_text(encoding="utf-8")
    mission_m = re.search(r"mission:\s*(\S+)", text)
    if not mission_m:
        _log("candidate has no mission: field"); return 1
    mission = mission_m.group(1)
    baseline = _current_canary_green()
    text = text.replace("status: pending",
                        f"status: active\napproved: {datetime.now().date()}\n"
                        f"canary_baseline: {baseline}")
    dest = SKILLS / mission / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    src.unlink()
    # mark evidence rows promoted
    ev_m = re.search(r"evidence_lesson_ids:\s*\[([\d,\s]*)\]", text)
    ids = [int(x) for x in re.findall(r"\d+", ev_m.group(1))] if ev_m else []
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        for lid in ids:
            c.execute("UPDATE lesson_candidates SET promoted_to=? WHERE id=?",
                      (f"{mission}/{src.name}", lid))
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        c.execute("INSERT INTO decisions (statement, rationale) VALUES (?,?)",
                  (f"Skill promoted: {mission}/{src.name}",
                   f"Operator-approved. Canary baseline at approval: {baseline}. "
                   f"Evidence lesson ids: {ids}."))
    _git("add", str(dest.relative_to(ROOT)), str(CANDIDATES.relative_to(ROOT)))
    _git("commit", "-m", f"Promote skill: {mission}/{src.name} (canary baseline {baseline})")
    _log(f"APPROVED -> {dest.relative_to(ROOT)} (canary baseline {baseline}, committed)")
    return 0


def cmd_reject(name: str) -> int:
    src = CANDIDATES / name
    if not src.exists():
        _log(f"no such candidate: {name}"); return 1
    REJECTED.mkdir(parents=True, exist_ok=True)
    dest = REJECTED / src.name
    dest.write_text(src.read_text(encoding="utf-8").replace(
        "status: pending", f"status: rejected\nrejected: {datetime.now().date()}"),
        encoding="utf-8")
    src.unlink()
    _log(f"rejected -> {dest.relative_to(ROOT)}")
    return 0


def cmd_rollback(relpath: str, reason: str = "operator/canary rollback") -> int:
    """relpath like <mission>/<file>.md under skills_analyst/."""
    target = SKILLS / relpath
    if not target.exists():
        _log(f"no such active skill: {relpath}"); return 1
    _git("rm", "-q", str(target.relative_to(ROOT)))
    _git("commit", "-m", f"Rollback skill: {relpath} ({reason})")
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.execute("UPDATE lesson_candidates SET promoted_to=NULL WHERE promoted_to=?",
                  (relpath,))
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        c.execute("INSERT INTO decisions (statement, rationale) VALUES (?,?)",
                  (f"Skill rolled back: {relpath}", reason))
    _log(f"ROLLED BACK {relpath} (git commit created; lessons returned to pool)")
    return 0


def active_skills_for(mission_id: str, cap: int = 2000) -> str:
    """Concatenated active notes for a mission, frontmatter stripped, capped.
    Called by batch_runner.run_task() for prompt injection."""
    d = SKILLS / mission_id
    if not d.exists():
        return ""
    parts = []
    for f in sorted(d.glob("*.md")):
        body = re.sub(r"^---.*?---\s*", "", f.read_text(encoding="utf-8"), flags=re.S)
        parts.append(body.strip())
    return "\n\n".join(parts)[:cap]


def newest_skill_below_baseline(week_green: int) -> str | None:
    """Most recently approved skill whose canary_baseline exceeds this week's green count.
    Called by run_canaries() — only when NO canaries are quota-parked (complete data)."""
    best: tuple[str, str] | None = None   # (approved_date, relpath)
    for f in SKILLS.glob("*/*.md"):
        if f.parent.name in ("_candidates", "_rejected"):
            continue
        text = f.read_text(encoding="utf-8")
        b = re.search(r"canary_baseline:\s*(\d+)", text)
        a = re.search(r"approved:\s*(\S+)", text)
        if b and a and week_green < int(b.group(1)):
            rel = f"{f.parent.name}/{f.name}"
            if best is None or a.group(1) > best[0]:
                best = (a.group(1), rel)
    return best[1] if best else None


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("--notify", action="store_true")
    r.add_argument("--dry", action="store_true")
    sub.add_parser("list")
    a = sub.add_parser("approve"); a.add_argument("name")
    j = sub.add_parser("reject"); j.add_argument("name")
    b = sub.add_parser("rollback"); b.add_argument("relpath")
    args = ap.parse_args()
    if args.cmd == "review":
        return cmd_review(args.notify, args.dry)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "approve":
        return cmd_approve(args.name)
    if args.cmd == "reject":
        return cmd_reject(args.name)
    if args.cmd == "rollback":
        return cmd_rollback(args.relpath)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
