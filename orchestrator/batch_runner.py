"""M1 batch execution engine — runs a mission's weekly tasks, the canaries, or the scorecard.
Called by Windows scheduled tasks (see missions/_M1_INDEX.md) or by hand.

    python orchestrator/batch_runner.py --mission 001-shopify-competitor-intel [--dry-run]
    python orchestrator/batch_runner.py --canaries
    python orchestrator/batch_runner.py --scorecard
    python orchestrator/batch_runner.py --resume            # only retry parked tasks (all missions)

Built around what the live runs exposed (HARNESS_DESIGN.md §1.6 + ledgerbook):
- workers go through `hermes -z` (web toolset — bare API cannot browse, sources would be fake);
- 429/quota → park quota_wait and continue queue; API/conn failure → infra_failed (cb106ef);
- resume-first: parked tasks retry before new ones queue; weekly dedup key prevents re-queueing;
- utf-8 everywhere (cp1252 crashes); every run appends runs/batch_<ts>.log;
- policy caps enforced: max worker calls per run, escalations to workspace/ESCALATIONS.md.
Stdlib + PyYAML only."""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
MISSIONS = ROOT / "missions"
ESCALATIONS = ROOT / "workspace" / "ESCALATIONS.md"
MAX_WORKER_CALLS_PER_RUN = 12          # policy cost cap proxy (Ollama returns no $)
WORKER_TIMEOUT_S = 900

_log_file = None


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_roles() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["roles"]


def escalate(reason: str) -> None:
    ESCALATIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(ESCALATIONS, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().isoformat(timespec='seconds')} — {reason}\n")
    log(f"ESCALATION -> {ESCALATIONS.name}: {reason}")


# ── model calls ────────────────────────────────────────────────────────────────
def hermes_worker(prompt: str, model_cfg: dict, usage_path: Path) -> tuple[str, dict]:
    cmd = ["hermes", "-z", prompt, "--provider", model_cfg["provider"],
           "-m", model_cfg["model"], "--usage-file", str(usage_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=WORKER_TIMEOUT_S, cwd=str(ROOT))
    usage = {}
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
    return (proc.stdout or "").strip(), usage


def ollama_chat(model: str, prompt: str, timeout: int = 300) -> str:
    """Tool-free call for the critic (no web needed, cheaper than a hermes session)."""
    import urllib.request
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"]


def is_quota_error(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in ("429", "too many requests", "rate limit",
                                "usage limit", "weekly usage"))


def worker_failed(out: str, usage: dict) -> bool:
    if usage.get("failed") or usage.get("completed") is False:
        return True
    low = out.lower()
    return any(s in low for s in ("api call failed", "connection error",
                                  "connection refused", "traceback (most recent"))


# ── preflight ──────────────────────────────────────────────────────────────────
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


# ── mission parsing ────────────────────────────────────────────────────────────
def parse_mission(mission_id: str) -> dict:
    path = MISSIONS / f"{mission_id}.md"
    text = path.read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---", 2)[1])
    seeds = []
    m = re.search(r"## Task seeds.*?\n(.*?)(?=\n## |\Z)", text, re.S)
    if m:
        seeds = [re.sub(r"^\d+\.\s*", "", ln).strip()
                 for ln in m.group(1).strip().splitlines()
                 if re.match(r"^\d+\.", ln.strip())]
    # merge numbered continuation lines (seeds wrap across lines in the files)
    return {"id": mission_id, "frontmatter": fm, "body": text, "seeds": seeds, "path": path}


def week_key() -> str:
    return datetime.now().strftime("%Y-W%V")


def queue_mission_tasks(mission: dict, dry: bool) -> list[int]:
    """Queue this week's tasks (dedup on mission+seed#+week). Returns task_ids to run."""
    import sqlite3
    wk = week_key()
    ids = []
    with sqlite3.connect(ledger.LEDGER_DB) as c:
        for i, seed in enumerate(mission["seeds"], 1):
            spec = f"[{wk}][seed {i}] {seed}"
            dup = c.execute("SELECT task_id, status FROM tasks WHERE mission_id=? AND spec=?",
                            (mission["id"], spec)).fetchone()
            if dup:
                if dup[1] in ("quota_wait", "queued"):
                    ids.append(dup[0])            # resume it
                continue                           # done/failed this week → skip
            if dry:
                log(f"DRY: would queue: {spec[:100]}")
                continue
            tid = ledger.queue_task(mission["id"], spec,
                                    pass_criteria_for(mission))
            ids.append(tid)
    return ids


def pass_criteria_for(mission: dict) -> str:
    m = re.search(r"## Done-definition.*?\n(.*?)(?=\n## )", mission["body"], re.S)
    return m.group(1).strip() if m else "deliverable exists; every fact sourced+dated"


# ── execution ──────────────────────────────────────────────────────────────────
def run_task(tid: int, mission: dict, roles: dict) -> str:
    """Execute one queued/parked task through worker→classifier→critic→ledger."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB) as c:
        c.row_factory = sqlite3.Row
        row = dict(c.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())

    worker_cfg = roles["worker"]
    wk = week_key()
    out_dir = ROOT / "workspace" / mission_workspace(mission["id"])
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = (
        f"You are the research/BI analyst (IDENTITY.md). Mission context:\n{mission['body']}\n\n"
        f"YOUR TASK THIS RUN (one task only):\n{row['spec']}\n\n"
        f"Use your web search tool for every fact. RULES: every fact needs source URL + "
        f"retrieval date ({datetime.now().date()}) + confidence 1-3. No fact without a live "
        f"source. Competitor seed URLs are unverified — verify before citing. Write your "
        f"deliverable as clean markdown. Reply with ONLY the deliverable content."
    )
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
    usage_path = RUNS / f"task{tid}_worker.usage.json"
    try:
        out, usage = hermes_worker(prompt, worker_cfg, usage_path)
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker timeout ({WORKER_TIMEOUT_S}s)")
        log(f"task {tid}: infra_failed (timeout)")
        return "infra_failed"

    if is_quota_error(out):
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit — parked (§1.6)")
        log(f"task {tid}: quota_wait (parked)")
        return "quota_wait"
    if worker_failed(out, usage):
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker API failure: {out[:200]}")
        log(f"task {tid}: infra_failed ({out[:80]})")
        return "infra_failed"
    if len(out) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars) — no deliverable")
        log(f"task {tid}: failed (short output)")
        return "failed"

    # write deliverable
    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {worker_cfg['model']}_\n", encoding="utf-8")

    # critic (tool-free)
    critic_cfg = roles["critic"]
    try:
        verdict_text = ollama_chat(
            critic_cfg["model"],
            "You are a strict critic. Reply PASS or FAIL on line 1, then ONE sentence why.\n\n"
            f"PASS CRITERIA:\n{row['pass_criteria']}\n\nDELIVERABLE:\n{out[:8000]}")
        verdict = "pass" if verdict_text.strip().upper().startswith("PASS") else "fail"
    except Exception as e:
        verdict, verdict_text = "fail", f"critic call failed: {e}"

    tok_in = int(usage.get("input_tokens") or 0)
    tok_out = int(usage.get("output_tokens") or 0)
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status="done")
    log(f"task {tid}: done verdict={verdict} ({dest.name}, in={tok_in} out={tok_out})")
    return "done"


def mission_workspace(mission_id: str) -> str:
    return {"001-shopify-competitor-intel": "shopify",
            "002-content-niche-research": "content",
            "003-adforge-local-market": "adforge"}.get(mission_id, "onboarding")


# ── canaries (deterministic grading, no critic) ───────────────────────────────
CANARIES = [
    ("C1", "In what year was Shopify founded? Use web search. Reply: the year, then the source URL.",
     lambda t: "2006" in t and "http" in t),
    ("C2", "What does HTTP status code 429 mean? Use web search. Reply: the meaning, then the source URL.",
     lambda t: "too many requests" in t.lower() and "http" in t),
    ("C3", "What is the capital city of Australia? Use web search. Reply: the city, then the source URL.",
     lambda t: "canberra" in t.lower() and "http" in t),
    ("C4", "Who wrote the paper introducing the Transformer architecture and what is its title? "
           "Use web search. Reply: title, at least one author, source URL.",
     lambda t: "attention is all you need" in t.lower() and "http" in t
               and any(a in t.lower() for a in ("vaswani", "shazeer", "parmar"))),
    ("C5", "Answer these four, each with a source URL, as a 4-row markdown table "
           "(question | answer | source): Shopify founding year; meaning of HTTP 429; "
           "capital of Australia; title of the Transformer paper. Use web search.",
     lambda t: t.count("http") >= 4 and t.count("|") >= 12),
]


def run_canaries(roles: dict) -> None:
    worker_cfg = roles["worker"]
    green = 0
    for name, prompt, grade in CANARIES:
        tid = ledger.queue_task("canaries", f"[{week_key()}] {name}", "deterministic grade")
        ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
        try:
            out, usage = hermes_worker(prompt, worker_cfg, RUNS / f"canary_{name}.usage.json")
        except subprocess.TimeoutExpired:
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               critic_notes="canary timeout")
            log(f"{name}: infra_failed (timeout)"); continue
        if is_quota_error(out):
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               critic_notes="quota — canary parked")
            log(f"{name}: quota_wait"); continue
        ok = bool(grade(out))
        green += ok
        ledger.finish_task(tid, artifacts=[], status="done",
                           critic_verdict="pass" if ok else "fail",
                           critic_notes=f"deterministic: {'ok' if ok else 'MISS'} | {out[:150]}")
        log(f"{name}: {'PASS' if ok else 'FAIL'}")
    log(f"canaries green: {green}/{len(CANARIES)}")
    if green < len(CANARIES):
        escalate(f"canary regression: {green}/{len(CANARIES)} green this week")


# ── main ───────────────────────────────────────────────────────────────────────
def main() -> int:
    global _log_file
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission")
    ap.add_argument("--canaries", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-tasks", type=int, default=MAX_WORKER_CALLS_PER_RUN)
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    _log_file = RUNS / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    if args.scorecard:
        import scorecard
        md, line = scorecard.build(deliver=False)
        log("scorecard written"); print(md); print("SUMMARY:", line)
        return 0

    if not preflight():
        return 3
    roles = load_roles()

    if args.canaries:
        run_canaries(roles)
        return 0

    if args.resume:
        import sqlite3
        with sqlite3.connect(ledger.LEDGER_DB) as c:
            parked = [r[0] for r in c.execute(
                "SELECT task_id, mission_id FROM tasks WHERE status='quota_wait'")]
        log(f"resume mode: {len(parked)} parked task(s)")
        # parked tasks re-run under their own mission context
        import sqlite3 as s3
        ran = 0
        for tid in parked[:args.max_tasks]:
            with s3.connect(ledger.LEDGER_DB) as c:
                mid = c.execute("SELECT mission_id FROM tasks WHERE task_id=?",
                                (tid,)).fetchone()[0]
            if mid == "canaries":
                continue
            st = run_task(tid, parse_mission(mid), roles)
            ran += 1
            if st == "quota_wait":
                log("still quota-limited — stopping resume pass"); break
        return 0

    if not args.mission:
        log("nothing to do (need --mission, --canaries, --scorecard, or --resume)")
        return 1

    mission = parse_mission(args.mission)
    if mission["frontmatter"].get("status") != "active":
        log(f"mission {args.mission} is not active — skipping")
        return 0

    ids = queue_mission_tasks(mission, args.dry_run)
    log(f"{args.mission}: {len(ids)} task(s) to run this pass (dedup week {week_key()})")
    if args.dry_run:
        return 0

    statuses = []
    for tid in ids[:args.max_tasks]:
        st = run_task(tid, mission, roles)
        statuses.append(st)
        if st == "quota_wait":
            log("quota-limited — parking remaining tasks for next fire")
            break
    done = statuses.count("done")
    log(f"run complete: {done}/{len(statuses)} done, {statuses.count('quota_wait')} parked, "
        f"{statuses.count('infra_failed')} infra, {statuses.count('failed')} failed")
    print("FITNESS:", json.dumps(ledger.weekly_fitness(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
