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
    # Best-effort push: inert until the operator sets a Telegram home channel
    # (they must message the bot once — platform rule). File above is the source of truth.
    try:
        import scorecard
        scorecard.send_telegram(f"⚠ AGI harness escalation: {reason}")
    except Exception:
        pass


# ── model calls ────────────────────────────────────────────────────────────────
def hermes_worker(prompt: str, model_cfg: dict, usage_path: Path) -> tuple[str, dict]:
    # SECURITY (docs/INCIDENTS.md 2026-07-18): an unrestricted worker previously wrote
    # its own rows straight into ledger.db/ledgerbook.db and self-graded its own task.
    # Tried `-t web` to strip file/terminal/code tools -- it does NOT map to a real
    # restriction (tool inventory came back as terminal/python/write_file etc. minus
    # the actual browser_* tools that real web research needs), so it just broke
    # search without fixing containment. Real web research in this agent runs via
    # browser_* tools in the default toolset, confirmed working in the passing run.
    # Defense now has two independent layers instead: (1) the prompt below never
    # mentions any internal path/schema, so there's nothing for the model to act on
    # even with tools present; (2) db_integrity_guard() below verifies no write
    # happened and reverts it if one did. Prevention-by-ignorance + verification,
    # not a trust-the-flag claim.
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


def _db_counts() -> dict:
    import sqlite3
    counts = {}
    for name, path in (("ledger", ledger.LEDGER_DB),
                       ("ledgerbook", ROOT / "memory" / "ledgerbook.db")):
        with sqlite3.connect(path) as c:
            for table in ("tasks", "entities", "facts", "decisions", "experiences", "failures"):
                counts[f"{name}.{table}"] = c.execute(
                    f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def db_integrity_snapshot() -> dict:
    """Call immediately BEFORE a worker subprocess runs."""
    return _db_counts()


def db_integrity_check(before: dict, context: str) -> None:
    """Call immediately AFTER a worker subprocess returns, BEFORE the orchestrator's own
    ledger.finish_task() write. Any row the worker itself added (not the orchestrator --
    the orchestrator hasn't written yet at this point) is unauthorized by construction.
    Quarantines the extra rows (dump + delete) rather than trusting them. See
    docs/INCIDENTS.md 2026-07-18."""
    import json as _json
    import sqlite3
    after = _db_counts()
    diffs = {k: (before[k], after[k]) for k in after if after[k] != before[k]}
    if not diffs:
        return
    log(f"INTEGRITY VIOLATION during {context}: unauthorized DB writes detected {diffs}")
    dump = {"context": context, "diffs": diffs, "quarantined_rows": {}}
    for key in diffs:
        dbname, table = key.split(".", 1)
        path = ledger.LEDGER_DB if dbname == "ledger" else ROOT / "memory" / "ledgerbook.db"
        with sqlite3.connect(path) as c:
            c.row_factory = sqlite3.Row
            n_new = after[key] - before[key]
            if n_new <= 0:
                continue  # a decrease is not a worker-write; leave it, just log
            rows = c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?"
                             if table != "tasks" else
                             f"SELECT * FROM {table} ORDER BY task_id DESC LIMIT ?",
                             (n_new,)).fetchall()
            id_col = "task_id" if table == "tasks" else "id"
            dump["quarantined_rows"][key] = [dict(r) for r in rows]
            ids = [r[id_col] for r in rows]
            c.executemany(f"DELETE FROM {table} WHERE {id_col}=?", [(i,) for i in ids])
    RUNS.mkdir(exist_ok=True)
    qpath = RUNS / f"quarantine_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    qpath.write_text(_json.dumps(dump, indent=2, default=str), encoding="utf-8")
    log(f"quarantined unauthorized rows -> {qpath.name}; reverted DB to pre-call state")
    escalate(f"worker wrote directly to a database during {context} -- quarantined, "
            f"see {qpath.name}. Toolset restriction is NOT reliable in this Hermes "
            f"version; this guard is the real containment.")


def _strip_tool_chatter(text: str) -> str:
    """Remove Hermes tool-invocation UI lines that bleed into stdout.
    e.g. '[tool] ( ͡° ͜ʖ ͡°) brainstorming...' — cosmetic noise, not deliverable content."""
    return re.sub(r'^\[tool\].*$', '', text, flags=re.MULTILINE).strip()


def is_quota_error(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in ("429", "too many requests", "rate limit",
                                "usage limit", "weekly usage"))


def worker_failed(out: str, usage: dict) -> bool:
    # NOTE 2026-07-18: usage.json's completed=false does NOT reliably mean the task
    # failed -- observed it False on a fully-formed, substantial, well-sourced brief
    # (3119 output tokens, 90 real browser calls) that would otherwise have been
    # silently discarded. Its exact semantics are unverified, so it is NOT trusted
    # alone. usage.get("failed") IS trusted (an explicit signal), plus real output-text
    # evidence (either a short/empty reply, or an actual error string) -- never a
    # metadata flag whose meaning we haven't confirmed.
    if usage.get("failed"):
        return True
    if not out or len(out.strip()) < 50:
        return True  # empty/near-empty is a real failure regardless of any flag
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


def is_first_run_for_mission(mission_id: str) -> bool:
    """True if this mission has never completed a task in an earlier week. A mission's
    week-1 run structurally cannot satisfy a 'changes since last week' criterion -- there
    is no prior week. Confirmed 2026-07-18: an unguided worker correctly self-identified
    this ('no prior brief to diff against, treat as baseline') while a guided one, told
    nothing, got marked FAIL for not producing a diff that cannot exist yet."""
    import sqlite3
    wk = week_key()
    with sqlite3.connect(ledger.LEDGER_DB) as c:
        row = c.execute(
            "SELECT 1 FROM tasks WHERE mission_id=? AND status='done' AND spec NOT LIKE ? LIMIT 1",
            (mission_id, f"[{wk}]%")).fetchone()
    return row is None


def mission_objective(mission: dict) -> str:
    """One-line objective ONLY — never hand the worker the full mission file. The file
    describes OUR storage paths/schema (ledgerbook.db, ledger.db); a worker with real
    tools will act on those as instructions if given the chance (see docs/INCIDENTS.md)."""
    m = re.search(r"## Objective\s*\n(.*?)(?=\n## )", mission["body"], re.S)
    return m.group(1).strip() if m else mission["frontmatter"].get("mission_id", "")


# ── memory-update stage (§2.1) — orchestrator-only ledgerbook writes ──────────
ENTITY_TYPES = {"competitor", "product", "channel", "keyword", "trend", "niche", "other"}


def _parse_json_array(text: str) -> list:
    """Lenient array extraction (adapted from onboarding_autonomy.parse_json):
    strip think-blocks and code fences, take the outermost [...] block."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    t = re.sub(r"```(?:json)?|```", "", t)
    m = re.search(r"\[.*\]", t, flags=re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def extract_facts(tid: int, deliverable: str, manager_model: str) -> int:
    """The loop's Memory-update stage: ONE tool-free manager call turns a PASSED
    deliverable into typed facts; the ORCHESTRATOR validates and writes them.
    Workers never touch ledgerbook.db (docs/INCIDENTS.md) — the extractor model
    only returns JSON text; every write below is this process. Returns rows written."""
    import sqlite3
    prompt = (
        "Extract every concrete, sourced fact from this research brief as a JSON array.\n"
        'Each item: {"entity": "<short subject name>", "entity_type": '
        '"competitor|product|channel|keyword|trend|other", "statement": "<one self-contained '
        'factual sentence, keep any number/price/date>", "source_url": "<the URL cited for it>", '
        '"retrieval_date": "YYYY-MM-DD", "confidence": 1-3}.\n'
        "ONLY include facts explicitly present in the brief WITH a cited source URL. "
        "No inference, no summarizing multiple facts into one. Output ONLY the JSON array.\n\n"
        f"BRIEF:\n{deliverable[:12000]}")
    try:
        raw = ollama_chat(manager_model, prompt)
    except Exception as e:
        log(f"task {tid}: fact-extraction call failed ({e}) — memory update skipped")
        return 0
    written = 0
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db") as c:
        for it in _parse_json_array(raw)[:40]:
            if not isinstance(it, dict):
                continue
            entity = str(it.get("entity", "")).strip()[:80]
            stmt = str(it.get("statement", "")).strip()
            url = str(it.get("source_url", "")).strip()
            if not entity or not stmt or not url.startswith("http"):
                continue
            etype = str(it.get("entity_type", "other")).strip().lower()
            etype = etype if etype in ENTITY_TYPES else "other"
            conf = it.get("confidence", 1)
            conf = conf if isinstance(conf, int) and 1 <= conf <= 3 else 1
            date = str(it.get("retrieval_date", "")).strip() or str(datetime.now().date())
            c.execute("INSERT OR IGNORE INTO entities (type, name) VALUES (?,?)",
                      (etype, entity))
            c.execute("INSERT INTO facts (entity, statement, provenance_url, provenance_date,"
                      " confidence, status, source_task_id) VALUES (?,?,?,?,?,'candidate',?)",
                      (entity, stmt, url, date, conf, tid))
            written += 1
    return written


def retract_facts(task_id: int) -> int:
    """Close validity windows on all facts produced by a given task. Called when
    a spot-check FAILS a task the critic had passed — the facts already extracted
    are tainted and must not persist as current truths. Uses supersede-not-delete
    semantics per HARNESS_DESIGN §1.2."""
    import sqlite3
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db") as c:
        cur = c.execute(
            "UPDATE facts SET valid_until=datetime('now'), status='retracted' "
            "WHERE source_task_id=? AND valid_until IS NULL",
            (task_id,))
        return cur.rowcount


def _recent_fact_lines(days: int = 14, cap: int = 120) -> str:
    """Fact-ledger view fed to synthesis tasks: current + prior week."""
    import sqlite3
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db") as c:
        rows = c.execute(
            "SELECT entity, statement, provenance_date, confidence FROM facts "
            "WHERE created_at >= datetime('now', ?) ORDER BY entity, id",
            (f"-{days} days",)).fetchall()
    return "\n".join(f"- [{r[2]} conf{r[3]}] {r[0]}: {r[1]}" for r in rows[:cap]) or "(none yet)"


def seed_is_synthesis(spec: str) -> bool:
    return re.sub(r"^\[[^\]]*\]\[seed \d+\]\s*", "", spec).lower().startswith("synthesis")


def run_critic(row: dict, out: str, roles: dict, baseline: bool) -> tuple[str, str]:
    """Tool-free critic judging deliverable CONTENT only. Returns (verdict, text)."""
    try:
        verdict_text = ollama_chat(
            roles["critic"]["model"],
            "You are a strict critic judging a research analyst's TEXT deliverable.\n"
            "The criteria below are the mission's full spec, written for the system as a "
            "whole -- some lines describe things a SEPARATE orchestrator process handles "
            "automatically after your review (exact file paths/naming, saving facts to a "
            "database, logging your verdict). Do NOT fail the deliverable for missing those "
            "-- they are not the analyst's job and are not present in the text by design. "
            "Judge ONLY the CONTENT: does it cover the required topics, is every fact backed "
            "by a real source URL + date, is it well-sourced and substantive."
            + ("\n\nThis is the mission's BASELINE (first-ever) run -- there is no prior week "
               "to diff against. Do NOT fail it for lacking a week-over-week diff or NEW-vs-"
               "last-week flags; correct behavior for a baseline run is marking everything as "
               "an initial observation, which is what you should look for instead."
               if baseline else "") +
            "\n\nReply PASS or FAIL on line 1, then ONE sentence why.\n\n"
            f"MISSION SPEC (for context only, see instructions above):\n{row['pass_criteria']}\n\n"
            # 24k cap: at 8k the critic factually mis-judged a real deliverable, marking
            # its later sections "absent" when they sat past the truncation (2026-07-18,
            # task 5 — Notion section at ~9.5k was called missing). Models here have 262k
            # context; the cap only guards against pathological outputs.
            f"DELIVERABLE:\n{out[:24000]}")
        return ("pass" if verdict_text.strip().upper().startswith("PASS") else "fail",
                verdict_text)
    except Exception as e:
        return "fail", f"critic call failed: {e}"


def run_synthesis(tid: int, row: dict, mission: dict, roles: dict, out_dir: Path,
                  wk: str, baseline: bool, baseline_note: str) -> str:
    """Synthesis seeds derive from THIS WEEK'S briefs + the fact ledger — tool-free
    (no browser worker; the material is supplied, inventing new facts is forbidden)."""
    briefs = sorted(p for p in out_dir.glob(f"{wk}_*.md") if "synthesis" not in p.name)
    brief_block = "\n\n".join(
        f"### {p.name}\n{p.read_text(encoding='utf-8')[:6000]}" for p in briefs[:6]) or "(none)"
    facts_block = _recent_fact_lines()
    prompt = (
        f"You are a research analyst. Objective: {mission_objective(mission)}\n\n"
        f"YOUR TASK (one task only):\n{row['spec']}{baseline_note}\n\n"
        "Work ONLY from the material below — this week's research briefs and the fact ledger "
        "(current + prior week). Cite the source URLs already present in the material. Do NOT "
        "invent facts or sources that are not in the material. If a requested item is absent "
        "from the material (e.g. a market-pulse addendum that was never researched), state "
        "that plainly as a data gap instead of fabricating it.\n\n"
        f"## THIS WEEK'S BRIEFS\n{brief_block}\n\n## FACT LEDGER\n{facts_block}\n\n"
        "Reply with ONLY the deliverable markdown.")
    ledger.start_task(tid, f"{roles['worker']['provider']}/{roles['worker']['model']} (tool-free synthesis)")
    import urllib.error
    try:
        out = ollama_chat(roles["worker"]["model"], prompt, timeout=600)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               critic_notes="quota/usage limit — parked (§1.6)")
            log(f"task {tid}: quota_wait (parked)"); return "quota_wait"
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis HTTP {e.code}")
        log(f"task {tid}: infra_failed (HTTP {e.code})"); return "infra_failed"
    except Exception as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis call failed: {e}")
        log(f"task {tid}: infra_failed ({e})"); return "infra_failed"

    (RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = _strip_tool_chatter(out)
    if len(out.strip()) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars)")
        log(f"task {tid}: failed (short output)"); return "failed"

    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {roles['worker']['model']} (synthesis, tool-free)_\n",
                    encoding="utf-8")
    verdict, verdict_text = run_critic(row, out, roles, baseline)
    # No fact extraction for synthesis — it derives from facts already in the ledger;
    # re-extracting would duplicate them.
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status="done")
    log(f"task {tid}: done verdict={verdict} (synthesis, {dest.name})")
    return "done"


def expire_stale_parked() -> None:
    """quota_wait rows from a PREVIOUS ISO week are superseded by the new week's scan —
    mark them 'stale' (excluded from fitness, which counts only done/failed)."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB) as c:
        cur = c.execute(
            "UPDATE tasks SET status='stale', critic_notes=COALESCE(critic_notes,'') || "
            "' | expired: superseded by new week' WHERE status='quota_wait' AND spec NOT LIKE ?",
            (f"[{week_key()}]%",))
        if cur.rowcount:
            log(f"expired {cur.rowcount} stale parked task(s) from previous weeks")


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

    objective = mission_objective(mission)
    baseline = is_first_run_for_mission(mission["id"])
    # Retry-with-feedback: a re-queued row that previously failed review carries the
    # critic's objections — feed them to the worker so the loop actually learns
    # (Evaluate → next attempt, §2.1). Without this the feedback evaporates.
    prior_feedback = ""
    if row.get("critic_verdict") == "fail" and (row.get("critic_notes") or "").strip():
        prior_feedback = (
            "\n\nPREVIOUS ATTEMPT FAILED REVIEW. The reviewer's exact objections:\n"
            f"{row['critic_notes'][:600]}\n"
            "Address each objection specifically in this attempt.")
    baseline_note = (
        "\n\nBASELINE RUN: this is the first tracked run for this mission — there is no prior "
        "week to compare against. Do not attempt a week-over-week diff. Instead, mark every "
        "finding as the initial baseline (e.g. 'NEW — first tracked observation') so next "
        "week's run has something real to compare against."
        if baseline else "")
    if seed_is_synthesis(row["spec"]):
        return run_synthesis(tid, row, mission, roles, out_dir, wk, baseline, baseline_note)
    # Promoted technique notes (§2.4): operator-approved, repo-versioned, capped ~2k.
    try:
        import promote
        skill_notes = promote.active_skills_for(mission["id"])
    except Exception:
        skill_notes = ""
    skills_block = (f"\n\nAPPROVED ANALYST TECHNIQUES (from your past reviewed work — "
                    f"apply where relevant):\n{skill_notes}" if skill_notes else "")
    prompt = (
        f"You are a research analyst. Objective of this research area: {objective}\n\n"
        f"YOUR TASK THIS RUN (one task only):\n{row['spec']}{baseline_note}{prior_feedback}"
        f"{skills_block}\n\n"
        f"Use web search for every fact. RULES: every fact needs a source URL + retrieval date "
        f"({datetime.now().date()}) + confidence 1-3. No fact without a live source. Seed names "
        f"are unverified — verify each is real before citing it. Write the deliverable as clean "
        f"markdown.\n\n"
        f"IMPORTANT: this is a research-only task. Use ONLY web/browser tools to look things up. "
        f"Do NOT use any file, terminal, code-execution, or memory tool for ANY reason — do not "
        f"create, write, or edit any file, and do not run any command. A separate system persists "
        f"your output; your job is only to research and reply with the deliverable markdown as "
        f"your final message text, nothing else."
    )
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
    usage_path = RUNS / f"task{tid}_worker.usage.json"
    snapshot = db_integrity_snapshot()
    try:
        out, usage = hermes_worker(prompt, worker_cfg, usage_path)
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker timeout ({WORKER_TIMEOUT_S}s)")
        log(f"task {tid}: infra_failed (timeout)")
        return "infra_failed"

    db_integrity_check(snapshot, context=f"task {tid} worker call")
    # Persist the FULL raw output regardless of what happens next -- a misclassified
    # task must stay diagnosable. Learned 2026-07-18: a real, substantial brief was
    # nearly lost with only a 200-char snippet surviving in critic_notes.
    (RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = _strip_tool_chatter(out)

    if is_quota_error(out):
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit — parked (§1.6)")
        log(f"task {tid}: quota_wait (parked)")
        return "quota_wait"
    if worker_failed(out, usage):
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker API failure (full text in "
                                       f"runs/task{tid}_worker_raw.txt): {out[:200]}")
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

    verdict, verdict_text = run_critic(row, out, roles, baseline)

    tok_in = int(usage.get("input_tokens") or 0)
    tok_out = int(usage.get("output_tokens") or 0)
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status="done")

    # Lesson capture (baseline weeks: harvest only, promotion stays OFF per §7):
    # critic objections become lesson_candidates so week-3 skill promotion has evidence.
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")

    # Memory-update stage: only PASSED research deliverables become facts.
    facts_n = extract_facts(tid, out, roles["manager"]["model"]) if verdict == "pass" else 0
    log(f"task {tid}: done verdict={verdict} facts+{facts_n} "
        f"({dest.name}, in={tok_in} out={tok_out})")
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
    # dedup/resume like queue_mission_tasks() -- found 2026-07-18: this used to call
    # queue_task() unconditionally, so re-running --canaries duplicated any already-
    # parked C-row instead of resuming it.
    import sqlite3
    worker_cfg = roles["worker"]
    green = 0
    wk = week_key()
    for name, prompt, grade in CANARIES:
        spec = f"[{wk}] {name}"
        with sqlite3.connect(ledger.LEDGER_DB) as c:
            dup = c.execute("SELECT task_id, status FROM tasks WHERE mission_id='canaries' "
                           "AND spec=?", (spec,)).fetchone()
        if dup and dup[1] not in ("quota_wait", "queued"):
            log(f"{name}: already {dup[1]} this week — skipping"); continue
        tid = dup[0] if dup else ledger.queue_task("canaries", spec, "deterministic grade")
        ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
        snapshot = db_integrity_snapshot()
        try:
            out, usage = hermes_worker(prompt, worker_cfg, RUNS / f"canary_{name}.usage.json")
        except subprocess.TimeoutExpired:
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               critic_notes="canary timeout")
            log(f"{name}: infra_failed (timeout)"); continue
        db_integrity_check(snapshot, context=f"canary {name}")
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
    # Report the WEEK's actual state, not just this invocation's count -- found
    # 2026-07-18: a resume pass that only re-attempts quota-parked canaries printed
    # "0/5 green" and fired a false regression escalation, ignoring canaries that
    # already passed earlier this week and weren't touched by this pass.
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB) as c:
        rows = c.execute("SELECT status, critic_verdict FROM tasks WHERE mission_id='canaries' "
                         "AND spec LIKE ?", (f"[{wk}]%",)).fetchall()
    week_green = sum(1 for s, v in rows if s == "done" and v == "pass")
    week_pending = sum(1 for s, _ in rows if s in ("quota_wait", "queued"))
    log(f"canaries this week: {week_green}/{len(CANARIES)} green"
        f"{f', {week_pending} still quota-parked' if week_pending else ''}")
    if week_green + week_pending < len(CANARIES):
        escalate(f"canary regression: {week_green}/{len(CANARIES)} green "
                f"({len(CANARIES) - week_green - week_pending} failed) this week")
    elif week_pending:
        log(f"{week_pending} canary(ies) still quota-parked — not a regression, retry later")

    # Promoted-skill protection (§2.4): if this week's green count fell below the baseline
    # recorded when a skill was approved, auto-rollback the newest such skill. Only judged
    # on COMPLETE data — never while canaries sit quota-parked (a park is not a regression).
    if week_pending == 0:
        try:
            import promote
            culprit = promote.newest_skill_below_baseline(week_green)
            if culprit:
                promote.cmd_rollback(culprit,
                                     reason=f"canary auto-rollback: week green {week_green} "
                                            f"fell below the skill's approval baseline")
                escalate(f"AUTO-ROLLBACK: skill {culprit} removed — canaries dropped to "
                        f"{week_green}/{len(CANARIES)} while it was active")
        except Exception as e:
            log(f"skill-protection check failed ({e}) — manual review advised")


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
    ap.add_argument("--deliver", action="store_true",
                    help="with --scorecard: also push the summary line to Telegram (fail-soft)")
    ap.add_argument("--max-tasks", type=int, default=MAX_WORKER_CALLS_PER_RUN)
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    _log_file = RUNS / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    if args.scorecard:
        import scorecard
        md, line = scorecard.build(deliver=args.deliver)
        log("scorecard written"); print(md); print("SUMMARY:", line)
        # Sunday cadence: promotion review rides the scorecard task (no extra schtask).
        # Fail-soft: a quota-blocked review must never break scorecard delivery.
        try:
            import promote
            promote.cmd_review(notify=args.deliver, dry=False)
        except Exception as e:
            log(f"promotion review skipped ({e}) — retries next Sunday")
        return 0

    if not preflight():
        return 3
    roles = load_roles()
    expire_stale_parked()

    if args.canaries:
        run_canaries(roles)
        return 0

    if args.resume:
        import sqlite3
        with sqlite3.connect(ledger.LEDGER_DB) as c:
            # Filter OUT canaries (use --canaries to resume those) BEFORE slicing to
            # max_tasks -- found 2026-07-18: slicing first let canary rows, which sort
            # earlier by task_id, silently consume the whole budget while the mission
            # task the operator actually wanted resumed was never reached (no error,
            # just a quiet no-op).
            parked = [r[0] for r in c.execute(
                "SELECT task_id, mission_id FROM tasks WHERE status='quota_wait' "
                "AND mission_id != 'canaries'")]
        log(f"resume mode: {len(parked)} parked non-canary task(s)")
        ran = 0
        for tid in parked[:args.max_tasks]:
            with sqlite3.connect(ledger.LEDGER_DB) as c:
                mid = c.execute("SELECT mission_id FROM tasks WHERE task_id=?",
                                (tid,)).fetchone()[0]
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
