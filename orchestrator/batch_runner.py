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
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citecheck  # noqa: E402
import ledger  # noqa: E402
import policy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
MISSIONS = ROOT / "missions"
ESCALATIONS = ROOT / "workspace" / "ESCALATIONS.md"
MAX_WORKER_CALLS_PER_RUN = 12          # policy cost cap proxy (Ollama returns no $)
# Raised 900 -> 1800 on 2026-07-28. 900s was calibrated against UNDER-SPECIFIED tasks: before
# F20 the worker never received the mission's done-definition, so it did far less research
# than it was graded on (mission 001 seed 1 finished in ~4.6 min / 35 api_calls that way).
# Handing it the real spec -- >=2 product URLs per competitor, NEW-vs-last-week flags, promo
# check, review sentiment with rating AND recurring theme, plus a diff section, plus "address
# each prior objection" -- multiplies the browser work, and the first post-F20 run of that
# same seed hit the 900s ceiling with zero output (task 24, infra_failed, no usage file).
# CAUSATION IS UNCONFIRMED: no usage file, session dump, or partial output survived the kill,
# so a hermes hang or cloud slowness are still live alternatives. This raise is the cheap
# discriminating test -- if a task now completes in 15-25 min the size hypothesis holds; if it
# still dies at 1800s the cause is elsewhere and the ceiling is not the problem.
# COUPLED: ledger.LEASE_SECONDS must stay > this + ~360s (raised to 2400 in the same commit).
WORKER_TIMEOUT_S = 1800

_log_file = None


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_roles() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["roles"]


def escalate(reason: str, trigger: str | None = None) -> None:
    # trigger, when given, must be one of policy.yaml's own escalation.triggers
    # (F13, docs/HARDENING.md) -- validated so the declared list stays authoritative
    # rather than becoming stale decoration the moment a caller typos it.
    policy.validate_trigger(trigger)
    tagged = f"[{trigger}] {reason}" if trigger else reason
    ESCALATIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(ESCALATIONS, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().isoformat(timespec='seconds')} — {tagged}\n")
    log(f"ESCALATION -> {ESCALATIONS.name}: {tagged}")
    # Best-effort push: inert until the operator sets a Telegram home channel
    # (they must message the bot once — platform rule). File above is the source of truth.
    try:
        import scorecard
        scorecard.send_telegram(f"⚠ AGI harness escalation: {reason}")
    except Exception:
        pass


# ── model calls ────────────────────────────────────────────────────────────────
def hermes_worker(prompt: str, model_cfg: dict, usage_path: Path,
                  timeout: int = WORKER_TIMEOUT_S) -> tuple[str, dict]:
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
                          errors="replace", timeout=timeout, cwd=str(ROOT))
    usage = {}
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
    return (proc.stdout or "").strip(), usage


def ollama_chat(model: str, prompt: str, timeout: int = 300,
                trace_path: Path | None = None,
                usage_out: dict | None = None) -> str:
    """Tool-free call for the critic (no web needed, cheaper than a hermes session).

    glm-5.2:cloud returns its full chain-of-thought in `message.thinking` on EVERY
    call -- verified live 2026-07-27 by calling /api/chat twice, with and without the
    API's `think` flag: both replies carried a populated `thinking` field of the same
    shape. So there was never a "high-tier reasoning mode" to switch on; the model was
    already reasoning at full tier and this function was simply discarding the trace by
    reading `message.content` alone.

    When `trace_path` is given the trace is persisted there. Rationale matches the
    existing runs/task<id>_worker_raw.txt convention (docs/INCIDENTS.md 2026-07-18): a
    FAIL verdict whose reasoning survives on disk is diagnosable; one whose reasoning
    was dropped is an unfalsifiable assertion. Deliberately a FILE ONLY -- the trace is
    never read back into any prompt, because it is model text derived from fetched web
    content and F10 (docs/HARDENING.md) treats that as an injection path.

    F33 (docs/HARDENING.md): Ollama reports consumption as `prompt_eval_count` /
    `eval_count` at the TOP LEVEL of the reply, and this function read only `message`,
    so every token it spent was discarded at the source. Pass a dict as `usage_out` to
    receive `{"input_tokens", "output_tokens"}` -- an optional out-param rather than a
    changed return type, so the four other call sites keep working unmodified (same
    shape as `trace_path` above).
    """
    import urllib.request
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())
    msg = payload.get("message", {})
    if usage_out is not None:
        usage_out["input_tokens"] = int(payload.get("prompt_eval_count") or 0)
        usage_out["output_tokens"] = int(payload.get("eval_count") or 0)
    if trace_path is not None:
        thinking = (msg.get("thinking") or "").strip()
        if thinking:
            # Never let an audit-trail write failure kill a real run.
            try:
                trace_path.write_text(
                    f"# reasoning trace — model={model} — "
                    f"{datetime.now().isoformat(timespec='seconds')}\n\n{thinking}\n",
                    encoding="utf-8")
            except Exception as e:
                log(f"reasoning trace not persisted to {trace_path.name} ({e})")
    return msg.get("content", "")


# ── F9 cross-provider failover (docs/HARDENING.md) ──────────────────────────────
# models.yaml declared fallback_chain but no orchestrator code ever read it -- 429 is
# ACCOUNT-level (HARNESS_DESIGN §1.6), so a second Ollama Cloud model does not survive
# quota exhaustion; only a genuinely different consumption path does. The chain's last
# rung is local gemma4:12b -- operator decision 2026-07-27: complete the work on a slow
# local model rather than park it. Kill-assumption probed live before building this:
# one real `hermes -z ... --provider ollama -m gemma4:12b` run correctly answered a
# factual question (Shopify founded 2006) with a genuinely reachable citation in ~7 min
# for a single fact. Residual, documented risk: the same probe self-reported "today's
# date" wrong by 2 years when not told -- NOT a blocker, because every real worker
# prompt already injects the literal current date as text (run_task(), line ~730) for
# the model to copy rather than compute; still, every failed-over deliverable is
# escalated (trigger="model_failover") for spot-check priority rather than trusted
# silently, precisely because a smaller/local model is a real accuracy downgrade.
LOCAL_FALLBACK_TIMEOUT_S = 3600  # gemma4:12b measured 1.54 tok/s (§1.6) driving hermes's
                                  # full browser tool-calling loop -- WORKER_TIMEOUT_S
                                  # (900s) would kill a real multi-fact brief mid-generation.


def _is_local_model(model_cfg: dict) -> bool:
    """Every cloud Ollama model in config/models.yaml is suffixed `:cloud`; anything
    else runs on the local daemon and needs LOCAL_FALLBACK_TIMEOUT_S, not
    WORKER_TIMEOUT_S -- confirmed convention, checked across the whole file."""
    return ":cloud" not in (model_cfg.get("model") or "")


def load_fallback_chain() -> list[dict]:
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    return cfg.get("fallback_chain") or []


def _failover_candidates(worker_cfg: dict) -> list[dict]:
    """worker_cfg first, then fallback_chain entries not already equal to it, deduped
    by (provider, model) so a chain that happens to list the worker's own model again
    doesn't retry it twice."""
    candidates = [worker_cfg]
    seen = {(worker_cfg["provider"], worker_cfg["model"])}
    for c in load_fallback_chain():
        key = (c["provider"], c["model"])
        if key not in seen:
            candidates.append(c)
            seen.add(key)
    return candidates


def worker_with_failover(prompt: str, worker_cfg: dict, usage_path: Path,
                         log_prefix: str) -> tuple[str, dict, dict, bool]:
    """hermes_worker() with failover on QUOTA ERRORS ONLY. A genuine subprocess timeout
    on any one candidate still raises subprocess.TimeoutExpired exactly as before --
    the caller's existing except-block handles it unchanged. This only widens what
    happens on actual quota-error text, matching F9's own scope ("on sustained 429 ...
    fail over"), not a general retry-on-any-failure mechanism.

    Returns (out, usage, model_cfg_used, exhausted). exhausted=True means every
    candidate in the chain returned a quota error -- caller should park exactly as it
    did before this fix existed."""
    candidates = _failover_candidates(worker_cfg)
    out, usage, cfg_used = "", {}, candidates[0]
    for i, cfg in enumerate(candidates):
        attempt_path = usage_path if i == 0 else usage_path.with_name(
            f"{usage_path.stem}_fallback{i}{usage_path.suffix}")
        timeout = LOCAL_FALLBACK_TIMEOUT_S if _is_local_model(cfg) else WORKER_TIMEOUT_S
        out, usage = hermes_worker(prompt, cfg, attempt_path, timeout=timeout)
        cfg_used = cfg
        if not is_quota_error(out):
            if i > 0:
                log(f"{log_prefix}: failover succeeded on {cfg['provider']}/{cfg['model']} "
                   f"(rung {i+1}/{len(candidates)})")
            return out, usage, cfg_used, False
        more = i + 1 < len(candidates)
        log(f"{log_prefix}: quota error on {cfg['provider']}/{cfg['model']} "
           f"({i+1}/{len(candidates)})" +
           (" -- trying next" if more else " -- chain exhausted"))
    return out, usage, cfg_used, True


def synthesis_with_failover(prompt: str, worker_cfg: dict, log_prefix: str,
                            usage_out: dict | None = None
                            ) -> tuple[str | None, dict, bool]:
    """ollama_chat() with the same F9 failover, for synthesis's tool-free HTTP call
    (urllib, not the hermes CLI subprocess) -- quota shows up as HTTPError code 429
    here, not as text in a subprocess reply, so the detection differs from
    worker_with_failover() even though the chain-walking logic is the same. Any
    non-429 HTTPError re-raises immediately, preserving the existing infra_failed
    handling at the call site."""
    import urllib.error
    candidates = _failover_candidates(worker_cfg)
    cfg_used = candidates[-1]
    for i, cfg in enumerate(candidates):
        timeout = LOCAL_FALLBACK_TIMEOUT_S if _is_local_model(cfg) else 600
        cfg_used = cfg
        try:
            out = ollama_chat(cfg["model"], prompt, timeout=timeout,
                              usage_out=usage_out)   # F33: carry consumption out
            if i > 0:
                log(f"{log_prefix}: failover succeeded on {cfg['provider']}/{cfg['model']} "
                   f"(rung {i+1}/{len(candidates)})")
            return out, cfg, False
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            more = i + 1 < len(candidates)
            log(f"{log_prefix}: quota error (HTTP 429) on {cfg['provider']}/{cfg['model']} "
               f"({i+1}/{len(candidates)})" +
               (" -- trying next" if more else " -- chain exhausted"))
    return None, cfg_used, True


# Tables where the orchestrator stamps run_id on every insert (H2, docs/HARDENING.md).
# A NULL run_id on a NEW row in one of these is the real rogue-write signature -- the
# worker is never told this schema exists, so it cannot produce a valid value even if
# it tried to write directly. Rows WITH a run_id (even a different process's, e.g. a
# concurrent hand-run that H1's lock didn't quite prevent, or a reclaimed-stale-lock
# edge case) are legitimate and must NOT be deleted -- that blind deletion is exactly
# what F1's probe proved happens with plain count comparison, 2026-07-19.
_PROVENANCE_TABLES = {"tasks": "task_id", "facts": "id"}


def _db_snapshot() -> dict:
    """count + max(id) per table, both DBs. max(id) lets the check identify EXACTLY the
    new rows later (WHERE id > snapshot_max_id) rather than guessing via ORDER BY LIMIT,
    which can misidentify rows if anything else changed the table shape meanwhile."""
    import sqlite3
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
    import sqlite3
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
                   "CLAUDE.md", "HARNESS_DESIGN.md", "docs", "skills_analyst"]


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
    return {"porcelain": set(out.stdout.splitlines()), "hashes": _tracked_hashes()}


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
    if not changed and not new_untracked:
        return
    log(f"FILESYSTEM INTEGRITY VIOLATION during {context}: "
        f"{len(changed)} tracked modified {changed[:5]}, "
        f"{len(new_untracked)} new untracked {new_untracked[:5]}")

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
    escalate(f"worker modified protected harness files during {context} -- auto-reverted "
            f"via git (originals preserved in {stash.name}). F14 containment fired: "
            f"{(changed + new_untracked)[:5]}")


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
    """Queue this week's tasks (dedup on mission+seed#+week). Returns task_ids to run,
    ordered so NEVER-ATTEMPTED seeds go before any seed already attempted this week.

    F6 (docs/HARDENING.md): this used to return ids in fixed seed order every call. On a
    retry fire, a seed that already parked (started_at IS SET -- it reached start_task()
    and hermes_worker() actually ran before hitting quota) was still first in line, hit
    the same quota/budget wall again, and the caller's `break`-on-quota_wait meant the
    seeds behind it were never even tried. Live evidence 2026-07-24: mission 001 seed 1
    (task 16) sat quota_wait since 2026-07-20 while seeds 2-4 (tasks 17-19) sat 'queued'
    with started_at=NULL -- structurally unable to ever run as long as seed 1 kept
    getting first crack at a scarce daily budget. Sorting never-attempted (started_at
    NULL) ahead of already-attempted gives every seed one try before any seed gets a
    second -- a fairness/rotation fix, not a scheduling rewrite. On a mission's first
    fire of the week all rows are equally untried, so ties break on task_id and the
    order is unchanged from before (seed 1,2,3,4)."""
    import sqlite3
    wk = week_key()
    rows = []  # (task_id, started_at) in seed-encounter order; sorted before returning
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        for i, seed in enumerate(mission["seeds"], 1):
            spec = f"[{wk}][seed {i}] {seed}"
            dup = c.execute("SELECT task_id, status, started_at FROM tasks WHERE "
                            "mission_id=? AND spec=?", (mission["id"], spec)).fetchone()
            if dup:
                if dup[1] in ("quota_wait", "queued", "interrupted"):  # H3: crash-recovered
                    rows.append((dup[0], dup[2]))     # resume it
                continue                               # done/failed this week → skip
            if dry:
                log(f"DRY: would queue: {spec[:100]}")
                continue
            tid = ledger.queue_task(mission["id"], spec,
                                    pass_criteria_for(mission))
            rows.append((tid, None))                  # brand new row, never started
    rows.sort(key=lambda r: (r[1] is not None, r[0]))
    return [tid for tid, _ in rows]


def pass_criteria_for(mission: dict) -> str:
    m = re.search(r"## Done-definition.*?\n(.*?)(?=\n## )", mission["body"], re.S)
    return m.group(1).strip() if m else "deliverable exists; every fact sourced+dated"


# Lines in a done-definition that describe the ORCHESTRATOR's job, not the analyst's.
# These name our own storage layout, and handing a tool-holding worker that layout is
# precisely what produced the 2026-07-18 rogue-write incident (docs/INCIDENTS.md), so
# they are stripped before any of this text reaches a worker prompt.
_INTERNAL_CRITERIA_RE = re.compile(
    r"workspace[/\\]|memory[/\\]|ledgerbook|ledger\.db|\bthe ledger\b|critic verdict",
    re.I)


def deliverable_requirements(mission: dict) -> str:
    """The mission's done-definition reduced to the CONTENT/FORMAT requirements the
    analyst is actually judged on -- every line naming an internal path or schema removed.

    F20 (docs/HARDENING.md): run_critic() feeds the critic row['pass_criteria'] -- the
    FULL done-definition -- while the worker only ever received mission_objective()'s
    single "## Objective" line. The analyst was therefore graded against requirements it
    was never shown. Proven live 2026-07-27, the first real W31 run: mission 001 tasks
    24, 25 and 26 ALL failed review, and every stated reason was a done-definition item
    absent from the worker's prompt -- the top "Changes since last week" diff section,
    NEW flags on unseen products, >=2 product URLs per price range, and one section per
    tracked competitor. Completion for the day was 0/3 on requirements the worker had no
    way to know existed.

    Whole requirements are dropped, never half of one: a matching line takes its
    continuation lines and sub-bullets (anything more indented) with it, so the worker
    never sees a dangling fragment like "price/promo facts get a valid_until" with the
    sentence that gave it meaning removed."""
    kept: list[str] = []
    drop_indent: int | None = None
    for line in pass_criteria_for(mission).splitlines():
        if not line.strip():
            drop_indent = None          # a blank line ends any requirement block
            kept.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if drop_indent is not None:
            if indent > drop_indent:
                continue                # continuation / sub-bullet of a dropped line
            drop_indent = None          # back at a sibling level -- resume keeping
        if _INTERNAL_CRITERIA_RE.search(line):
            drop_indent = indent
            continue
        kept.append(line)
    # Drop "[ ]" checkboxes -- they read as a form to tick rather than a spec to satisfy.
    return re.sub(r"^(\s*-)\s*\[[ x]\]\s*", r"\1 ", "\n".join(kept).strip(), flags=re.M)


def task_scope_note(spec: str, mission: dict) -> str:
    """Which slice of the mission's done-definition THIS task is answerable for.

    F31 (docs/HARDENING.md), 2026-07-29. F20 was right that the worker must see the
    done-definition, but it handed EVERY task the whole thing -- and a done-definition
    describes the mission's COMBINED weekly brief, not one seed's share of it. The two
    resulting impossibilities were both live:

      * mission 001 seed 4 (task 27) is the TOOL-FREE synthesis, and was graded on "a
        review-sentiment signal: current average rating + one recurring theme" for each
        tracked competitor. The critic failed it for exactly that -- "three of five
        tracked competitors are missing the required review-sentiment signal" -- on a
        task forbidden from doing the lookups that would produce one, working from three
        briefs. No possible output passes.
      * the per-competitor seeds (1-3) are each told they must deliver "one section per
        tracked competitor" and "a top 'Changes since last week' diff section", which a
        single-competitor task cannot produce either.

    Returned to the WORKER and to the CRITIC from one function on purpose: F20's root
    cause was the two being given different specs, and re-deriving this note separately
    at each site would rebuild that exact failure mode."""
    n = len(mission["seeds"])
    if seed_is_synthesis(spec):
        return (
            f"This mission's done-definition describes the COMBINED weekly brief, which "
            f"{n} separate tasks produce between them. This task is the SYNTHESIS: it "
            f"works only from briefs and ledger facts the other tasks already produced, "
            f"and is forbidden from doing its own research. Per-subject requirements are "
            f"therefore met by whatever the supplied material actually contains. Where "
            f"the material does not cover one, the correct outcome is an explicit data "
            f"gap naming the subject and the missing item -- not a fabricated value, and "
            f"not a defect in this deliverable.")
    return (
        f"This mission's done-definition describes the COMBINED weekly brief, which {n} "
        f"separate tasks produce between them. This task is ONE of them and covers only "
        f"the single subject named in its spec. Every requirement that applies to that "
        f"subject must be met here, in full. Requirements that exist only across the "
        f"whole set -- a section for every tracked subject, or the combined cross-subject "
        f"\"changes since last week\" diff -- belong to the synthesis task; their absence "
        f"from a single-subject deliverable is expected and is not a defect.")


def is_first_run_for_mission(mission_id: str) -> bool:
    """True if this mission has never completed a task in an earlier week. A mission's
    week-1 run structurally cannot satisfy a 'changes since last week' criterion -- there
    is no prior week. Confirmed 2026-07-18: an unguided worker correctly self-identified
    this ('no prior brief to diff against, treat as baseline') while a guided one, told
    nothing, got marked FAIL for not producing a diff that cannot exist yet."""
    import sqlite3
    wk = week_key()
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
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
    if policy.manager_call_budget_breached():
        log(f"task {tid}: manager-call budget exhausted for today — memory update skipped")
        return 0
    policy.record_manager_call()
    try:
        raw = ollama_chat(manager_model, prompt)
    except Exception as e:
        log(f"task {tid}: fact-extraction call failed ({e}) — memory update skipped")
        return 0
    written = 0
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
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
                      " confidence, status, source_task_id, run_id) "
                      "VALUES (?,?,?,?,?,'candidate',?,?)",
                      (entity, stmt, url, date, conf, tid, ledger.RUN_ID))
            written += 1
    return written


def retract_facts(task_id: int) -> int:
    """Close validity windows on all facts produced by a given task. Called when
    a spot-check FAILS a task the critic had passed — the facts already extracted
    are tainted and must not persist as current truths. Uses supersede-not-delete
    semantics per HARNESS_DESIGN §1.2."""
    import sqlite3
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        cur = c.execute(
            "UPDATE facts SET valid_until=datetime('now'), status='retracted' "
            "WHERE source_task_id=? AND valid_until IS NULL",
            (task_id,))
        return cur.rowcount


def _recent_fact_lines(days: int = 14, cap: int = 120) -> str:
    """Fact-ledger view fed to synthesis tasks: current + prior week."""
    import sqlite3
    with sqlite3.connect(ROOT / "memory" / "ledgerbook.db", timeout=30) as c:
        rows = c.execute(
            "SELECT entity, statement, provenance_date, confidence FROM facts "
            "WHERE created_at >= datetime('now', ?) ORDER BY entity, id",
            (f"-{days} days",)).fetchall()
    return "\n".join(f"- [{r[2]} conf{r[3]}] {r[0]}: {r[1]}" for r in rows[:cap]) or "(none yet)"


def seed_is_synthesis(spec: str) -> bool:
    """Does this seed describe a synthesis (tool-free, works only from material already
    gathered) rather than fresh research?

    F30 (docs/HARDENING.md), 2026-07-29: this used to require the seed to literally START
    with "synthesis". Mission 002's seed 3 reads "Cross-channel synthesis: ..." -- one word
    off -- so it was routed to the full browser worker every week and did fresh web
    research instead of synthesising the two channel briefs it was written to combine.
    Confirmed in task 30's deliverable: it invented a channel ("AI News Recap", not one of
    the mission's two) and cited corticallabs.com, bbc.com and a Google blog post about
    self-healing roads -- generic AI news, no connection to the operator's channels. Every
    002 synthesis has failed since the mission went active (tasks 14, 22, 30); this is why.

    Match on the seed's LEADING CLAUSE only (to the first colon, capped), so a research
    seed that happens to mention synthesis in its body is not misrouted into the tool-free
    path where it could not do the lookups it needs."""
    body = re.sub(r"^\[[^\]]*\]\[seed \d+\]\s*", "", spec).lower()
    return bool(re.search(r"synthesi[sz]", body.split(":", 1)[0][:80]))


def run_critic(row: dict, out: str, roles: dict, baseline: bool,
               scope_note: str = "") -> tuple[str, str]:
    """Tool-free critic judging deliverable CONTENT, now backed by a mechanical,
    non-LLM truth signal (H4, docs/HARDENING.md — fixes F3, F4). Returns
    (verdict, text) where verdict is 'pass' | 'fail' | 'needs_review' — the third
    value means "could not be judged, do not treat as pass or a confirmed fail,
    escalate for a human" (never a silent auto-fail, H4's stated fix for F4's
    brittle-parse bug that used to invert good verdicts unnoticed).

    NOTE on F5 (critic self-anchoring): H4's other prescribed fix, "a distinct
    critic model whenever a second provider exists," is not applied here --
    manager and critic are still the SAME model (glm-5.2:cloud) because the
    model hierarchy stays Ollama-only until the operator adds a second provider
    (locked decision, CLAUDE.md). This function is already blind to its own
    prior notes (row['pass_criteria'] never carries a past verdict — prior
    feedback is injected into the WORKER's prompt only, in run_task), so the
    remaining F5 exposure is real but narrower than the original finding
    implied. citecheck's mechanical hard-fail below is a genuinely independent
    signal regardless: it never calls any LLM at all."""
    try:
        evidence = citecheck.verify(out)
    except Exception as e:
        log(f"citation check failed ({e}) -- proceeding without mechanical evidence")
        evidence = []
    summary = citecheck.summarize(evidence)
    if citecheck.is_hard_fail(summary):
        dead = [e["url"] for e in evidence if not e["reachable"]][:5]
        return "fail", (f"MECHANICAL FAIL: {summary['dead']}/{summary['checked']} cited "
                        f"URLs unreachable (dead_frac={summary['dead_frac']}): {dead}")

    if policy.manager_call_budget_breached():
        return "needs_review", "manager-role call budget exhausted for today (policy.yaml " \
                               "cost_caps.manager_calls_per_day) -- critic skipped, not judged"
    policy.record_manager_call()
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
            # F31: the spec below is the mission's COMBINED brief; this deliverable is one
            # task's share of it. Without this the critic demands work of a task that is
            # structurally unable to do it (task 27, 2026-07-28) -- see task_scope_note().
            + (f"\n\nSCOPE -- READ BEFORE JUDGING: {scope_note} Grade this deliverable ONLY "
               f"on the share of the spec that is actually its own. Do NOT fail it for "
               f"omitting work that belongs to a sibling task."
               if scope_note else "")
            + ("\n\nThis is the mission's BASELINE (first-ever) run -- there is no prior week "
               "to diff against. Do NOT fail it for lacking a week-over-week diff or NEW-vs-"
               "last-week flags; correct behavior for a baseline run is marking everything as "
               "an initial observation, which is what you should look for instead."
               if baseline else "") +
            "\n\nA mechanical citation check already ran against the URLs in this "
            "deliverable (fetched live, independent of you) -- weigh it, but it does not "
            "replace your own judgment of substance and coverage:\n"
            f"{citecheck.evidence_block(evidence)}\n"
            "\n\nReply with a line reading exactly 'VERDICT: PASS' or 'VERDICT: FAIL', "
            "then ONE sentence why.\n\n"
            f"MISSION SPEC (for context only, see instructions above):\n{row['pass_criteria']}\n\n"
            # 24k cap: at 8k the critic factually mis-judged a real deliverable, marking
            # its later sections "absent" when they sat past the truncation (2026-07-18,
            # task 5 — Notion section at ~9.5k was called missing). Models here have 262k
            # context; the cap only guards against pathological outputs.
            f"DELIVERABLE:\n{out[:24000]}",
            # Persist WHY, not just the verdict: today's three 001 failures (24/25/26)
            # were only diagnosable because the one-sentence reason happened to name a
            # missing section. The full trace makes that reliable instead of lucky.
            trace_path=RUNS / f"task{row['task_id']}_critic_reasoning.txt")
    except Exception as e:
        return "needs_review", f"critic call failed: {e}"

    # Tolerant parse (H4, fixes F4): the old `.startswith("PASS")` check silently
    # inverted any reply with markdown bold, a "VERDICT:" prefix, or a leading
    # think-block into a false FAIL, indistinguishable from a real one in the
    # ledger. An unparseable reply is now 'needs_review', never a silent fail.
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", verdict_text, re.I)
    if not m:
        return "needs_review", verdict_text[:500] + " [UNPARSEABLE VERDICT]"
    return m.group(1).lower(), verdict_text


def run_synthesis(tid: int, row: dict, mission: dict, roles: dict, out_dir: Path,
                  wk: str, baseline: bool, baseline_note: str) -> str:
    """Synthesis seeds derive from THIS WEEK'S briefs + the fact ledger — tool-free
    (no browser worker; the material is supplied, inventing new facts is forbidden)."""
    briefs = sorted(p for p in out_dir.glob(f"{wk}_*.md") if "synthesis" not in p.name)
    brief_block = "\n\n".join(
        f"### {p.name}\n{p.read_text(encoding='utf-8')[:6000]}" for p in briefs[:6]) or "(none)"
    facts_block = _recent_fact_lines()
    # F20, extended to synthesis 2026-07-28. This path was deliberately left out of the
    # original fix because there was no failure evidence for it -- there is now: task 27
    # failed the same night with the exact F20 signature ("omits the required per-fact
    # retrieval dates and confidence scores, lacks a dedicated top 'Changes since last
    # week' diff section"), i.e. graded against a spec it was never shown. The
    # work-only-from-supplied-material rule below is what keeps this safe: the prompt
    # already instructs the model to report an absent item as a data gap rather than
    # invent it, so stating the requirements cannot license fabrication.
    requirements = deliverable_requirements(mission)
    requirements_block = (
        "\n\nREQUIRED SHAPE OF THE DELIVERABLE — a reviewer checks your output against "
        "exactly these points, and a missing one is a FAIL even when the analysis itself "
        "is sound. Where the supplied material cannot support one of them, say so "
        f"explicitly as a data gap rather than inventing it:\n{requirements}"
        if requirements else "")
    # F31: same note the critic is given, from the same function -- see task_scope_note().
    scope_note = task_scope_note(row["spec"], mission)
    scope_block = f"\n\nSCOPE OF THIS TASK: {scope_note}"
    prompt = (
        f"You are a research analyst. Objective: {mission_objective(mission)}\n\n"
        f"YOUR TASK (one task only):\n{row['spec']}{requirements_block}{scope_block}"
        f"{baseline_note}\n\n"
        "Work ONLY from the material below — this week's research briefs and the fact ledger "
        "(current + prior week). Cite the source URLs already present in the material. Do NOT "
        "invent facts or sources that are not in the material. If a requested item is absent "
        "from the material (e.g. a market-pulse addendum that was never researched), state "
        "that plainly as a data gap instead of fabricating it.\n\n"
        f"## THIS WEEK'S BRIEFS\n{brief_block}\n\n## FACT LEDGER\n{facts_block}\n\n"
        "Reply with ONLY the deliverable markdown.")
    if policy.token_budget_breached():
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="policy.yaml tokens_per_day_hard_stop reached — parked",
                           append_note=True)
        escalate(f"task {tid}: daily token budget exhausted, parked (synthesis)",
                trigger="cost_cap_breach")
        log(f"task {tid}: quota_wait (token budget)"); return "quota_wait"
    worker_cfg = roles["worker"]
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']} (tool-free synthesis)")
    import urllib.error
    try:
        # F9: synthesis_with_failover() consumes every 429 internally (trying the next
        # candidate) and only ever re-raises a NON-429 HTTPError, so the branch below
        # no longer needs its own e.code==429 case -- that path is handled before it
        # could reach here.
        syn_usage: dict = {}
        out, model_used_cfg, exhausted = synthesis_with_failover(
            prompt, worker_cfg, log_prefix=f"task {tid} (synthesis)",
            usage_out=syn_usage)
    except urllib.error.HTTPError as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis HTTP {e.code}",
                           append_note=True)
        log(f"task {tid}: infra_failed (HTTP {e.code})"); return "infra_failed"
    except Exception as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"synthesis call failed: {e}",
                           append_note=True)
        log(f"task {tid}: infra_failed ({e})"); return "infra_failed"

    if exhausted:
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit on every model in the "
                                        "fallback chain — parked (§1.6, F9)",
                           append_note=True)
        log(f"task {tid}: chain_exhausted (every fallback model quota-limited)")
        return "chain_exhausted"
    if model_used_cfg != worker_cfg:
        ledger.update_model_used(
            tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']} (tool-free synthesis)")
        escalate(f"task {tid}: synthesis completed via failover to "
                f"{model_used_cfg['provider']}/{model_used_cfg['model']} after quota "
                f"exhaustion on the primary worker", trigger="model_failover")
        worker_cfg = model_used_cfg  # so the deliverable footer below is truthful too

    (RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = _strip_tool_chatter(out)
    if len(out.strip()) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars)")
        log(f"task {tid}: failed (short output)"); return "failed"

    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {worker_cfg['model']} (synthesis, tool-free)_\n",
                    encoding="utf-8")
    verdict, verdict_text = run_critic(row, out, roles, baseline, scope_note=scope_note)
    if verdict == "needs_review":
        escalate(f"task {tid}: critic verdict ambiguous -- {verdict_text[:200]}",
                trigger="pass_criteria_ambiguous")
    # F18 (docs/HARDENING.md): status must reflect the verdict, not just "a call
    # returned." Previously EVERY resolved synthesis landed status='done' regardless
    # of verdict -- weekly_fitness() and is_first_run_for_mission() both read status
    # only, so a critic-REJECTED deliverable was silently indistinguishable from a
    # pass anywhere except the separate critic_verdict column nobody was filtering on.
    status = "done" if verdict == "pass" else "failed"
    # No fact extraction for synthesis — it derives from facts already in the ledger;
    # re-extracting would duplicate them.
    # F33 (docs/HARDENING.md): this call used to omit tokens entirely, so no synthesis
    # in the project's history ever recorded what it spent and policy.tokens_used_today()
    # was structurally blind to the whole task type. Measured 2026-07-29 by re-running
    # task 30: the daily counter sat at exactly 4,640,719 before AND after a real
    # synthesis. Accumulated onto the row's prior total for the same reason as F32 --
    # this path is retried like any other.
    tok_in = int(syn_usage.get("input_tokens") or 0) + int(row.get("tokens_in") or 0)
    tok_out = int(syn_usage.get("output_tokens") or 0) + int(row.get("tokens_out") or 0)
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status=status)
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")
    log(f"task {tid}: {status} verdict={verdict} (synthesis, {dest.name})")
    return status


def expire_stale_parked() -> None:
    """Previous-ISO-week rows that can never run again are marked 'stale', which
    weekly_fitness() counts as `dropped` — the honest record of scheduled work that
    did not happen.

    F35 (docs/HARDENING.md), 2026-07-29: this used to cover `quota_wait` only, and the
    omission of `queued` left NEVER-ATTEMPTED work permanently stranded. No code path
    could reach such a row: queue_mission_tasks() matches only specs carrying the CURRENT
    week, `--resume` selects only quota_wait/interrupted, reconcile_interrupted_tasks()
    touches only `running`, and this function skipped it. Five rows sat in exactly that
    state (tasks 4, 13, 14, 17, 19 -- W29/W30 seeds, four with started_at NULL and zero
    tokens), unrunnable forever.

    The honesty cost was the worse half. weekly_fitness() reports a `queued` row as
    `pending` only while it is inside the 7-day window; once it ages out it is counted
    nowhere, and `dropped` read 0 despite five abandoned seeds -- the same vanishing-work
    failure H5/F7 was written to close, recurring at a boundary that fix did not reach.

    Never-attempted rows get a distinct note and their own count in the log line:
    `started_at IS NULL` means the seed was starved before it ever reached a worker (the
    F6 signature), a different operational signal from work that ran and then parked.
    `interrupted` is deliberately left alone -- `--resume` can still reach it, so it is
    not stranded, and expiring it would break H3's crash-recovery path. Current-week rows
    are untouched; they are still legitimately waiting for this week's fire."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        wk = f"[{week_key()}]%"
        never = c.execute(
            "SELECT count(*) FROM tasks WHERE status IN ('quota_wait','queued') "
            "AND started_at IS NULL AND spec NOT LIKE ?", (wk,)).fetchone()[0]
        cur = c.execute(
            "UPDATE tasks SET status='stale', critic_notes=TRIM(COALESCE(critic_notes,'') || "
            "CASE WHEN started_at IS NULL "
            "     THEN ' | expired: NEVER ATTEMPTED, superseded by the new week' "
            "     ELSE ' | expired: superseded by new week' END) "
            "WHERE status IN ('quota_wait','queued') AND spec NOT LIKE ?", (wk,))
        if cur.rowcount:
            log(f"expired {cur.rowcount} task(s) from previous weeks "
                f"({never} never attempted) — they now count as dropped, not invisible")


def reconcile_interrupted_tasks() -> int:
    """H3 (docs/HARDENING.md, fixes F2): on every process start, before any new queueing,
    find 'running' rows whose lease has expired -- the owning process crashed, was killed,
    or the machine lost power. Previously these were orphaned FOREVER: no code path ever
    read or reset status='running', so the task was never retried, never counted (fitness
    counts only done/failed), and its seed was blocked for the rest of the week by dedup.

    Recovered rows go to 'interrupted' (dedup-resumable, see queue_mission_tasks) with an
    incremented attempt_count. Past MAX_TASK_ATTEMPTS, mark 'failed' instead of retrying
    forever -- an honest give-up beats a silent crash-loop. Always logged, never silent;
    surfaced on the scorecard so the operator sees it happened."""
    import sqlite3
    n_recovered = n_gave_up = 0
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        expired = c.execute(
            "SELECT task_id, attempt_count FROM tasks WHERE status='running' "
            "AND (lease_expires_at IS NULL OR lease_expires_at < datetime('now'))"
        ).fetchall()
        for row in expired:
            tid, attempts = row["task_id"], (row["attempt_count"] or 0) + 1
            if attempts >= ledger.MAX_TASK_ATTEMPTS:
                c.execute(
                    "UPDATE tasks SET status='failed', attempt_count=?, "
                    "critic_notes=COALESCE(critic_notes,'') || "
                    "' | gave up after ' || ? || ' interruptions (crash/power-loss recovery cap)' "
                    "WHERE task_id=?", (attempts, attempts, tid))
                n_gave_up += 1
            else:
                c.execute(
                    "UPDATE tasks SET status='interrupted', attempt_count=?, "
                    "critic_notes=COALESCE(critic_notes,'') || "
                    "' | recovered from an orphaned running state (attempt ' || ? || ')' "
                    "WHERE task_id=?", (attempts, attempts, tid))
                n_recovered += 1
    if n_recovered or n_gave_up:
        log(f"crash recovery: {n_recovered} task(s) recovered for retry, "
           f"{n_gave_up} gave up after {ledger.MAX_TASK_ATTEMPTS} interruptions")
    return n_recovered + n_gave_up


# ── execution ──────────────────────────────────────────────────────────────────
def run_task(tid: int, mission: dict, roles: dict) -> str:
    """Execute one queued/parked task through worker→classifier→critic→ledger."""
    import sqlite3
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
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
    # H7 (docs/HARDENING.md): "log every injection in the run log". This text persists
    # into EVERY future prompt for the mission, which is what makes F10's chain worse
    # than a single poisoned task -- so which skills were live for a given run has to be
    # reconstructable from the log afterwards, not inferred from whatever the files
    # happen to say today. Names + total size, never the note bodies (the run log is not
    # the audit trail for content; git is).
    if skill_notes:
        try:
            names = [p.name for p in sorted(
                (promote.SKILLS / mission["id"]).glob("*.md"))]
        except Exception:
            names = ["<unreadable>"]
        capped = " CAPPED" if len(skill_notes) >= promote.MAX_INJECTED_CHARS else ""
        log(f"task {tid}: injecting {len(names)} approved skill(s), "
            f"{len(skill_notes)}/{promote.MAX_INJECTED_CHARS} chars{capped}: {names}")
    skills_block = (f"\n\nAPPROVED ANALYST TECHNIQUES (from your past reviewed work — "
                    f"apply where relevant):\n{skill_notes}" if skill_notes else "")
    compliance_block = policy.compliance_prompt_block()
    # F20 (docs/HARDENING.md): the critic grades against the mission's done-definition;
    # until now the worker never saw it, so it was judged on a spec it had no access to
    # (mission 001 tasks 24/25/26, 2026-07-27 -- 0/3, every reason a requirement stated
    # only in text the worker was not given). Internal paths/schema are stripped by
    # deliverable_requirements(), so this does NOT reopen the 2026-07-18 containment hole.
    # Ordered BEFORE baseline_note deliberately: on a first-ever run baseline_note's "do
    # not attempt a week-over-week diff" must read as the later, overriding exception to
    # the diff requirement below it.
    requirements = deliverable_requirements(mission)
    requirements_block = (
        "\n\nREQUIRED SHAPE OF THE DELIVERABLE — a reviewer checks your output against "
        "exactly these points, and a missing one is a FAIL even when the research itself "
        f"is sound:\n{requirements}" if requirements else "")
    # F31: same note the critic is given, from the same function -- see task_scope_note().
    scope_note = task_scope_note(row["spec"], mission)
    scope_block = f"\n\nSCOPE OF THIS TASK: {scope_note}"
    prompt = (
        f"You are a research analyst. Objective of this research area: {objective}\n\n"
        f"YOUR TASK THIS RUN (one task only):\n{row['spec']}"
        f"{requirements_block}{scope_block}{baseline_note}{prior_feedback}{skills_block}\n\n"
        f"Use web search for every fact. RULES: every fact needs a source URL + retrieval date "
        f"({datetime.now().date()}) + confidence 1-3. No fact without a live source. Seed names "
        f"are unverified — verify each is real before citing it. Write the deliverable as clean "
        f"markdown.\n\n"
        # F25 (docs/HARDENING.md): the 2026-07-28 spot-check found confidence 3 asserted on
        # values absent from the cited page, and one verbatim-quoted price sentence that does
        # not exist on the page it names. The prompt had never said what the levels MEAN, so
        # "3" was being used to signal conviction rather than verification.
        f"WHAT THE CONFIDENCE LEVELS MEAN — these are claims about EVIDENCE, not about how "
        f"sure you feel:\n"
        f"  3 = you loaded the cited page THIS RUN and read the exact value on it.\n"
        f"  2 = the value comes from a secondary/aggregator source, or the primary page was "
        f"blocked, cached, or rendered incompletely.\n"
        f"  1 = inferred, dated, or otherwise uncertain.\n"
        f"If a page was unreachable, 403/404, or Cloudflare-blocked, the highest honest "
        f"confidence is 1 — say so plainly rather than assigning 3 to a source you could not "
        f"read. Never cite a page you did not successfully open for a value you did not see "
        f"on it.\n"
        f"QUOTATIONS: text in quotation marks must be copied VERBATIM from the cited page. If "
        f"you are paraphrasing or reconstructing pricing/terms, write it as your own summary "
        f"without quote marks. A quoted sentence that does not appear on the page is treated "
        f"as fabrication, even when the underlying number is right.\n\n"
        f"IMPORTANT: this is a research-only task. Use ONLY web/browser tools to look things up. "
        f"Do NOT use any file, terminal, code-execution, or memory tool for ANY reason — do not "
        f"create, write, or edit any file, and do not run any command. A separate system persists "
        f"your output; your job is only to research and reply with the deliverable markdown as "
        f"your final message text, nothing else."
        + (f"\n\n{compliance_block}" if compliance_block else "")
    )
    # F8/F13 (docs/HARDENING.md): Ollama reports no $, so token count is the real
    # daily consumption signal -- check BEFORE spending the call, not after.
    if policy.token_budget_breached():
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="policy.yaml tokens_per_day_hard_stop reached "
                                        "-- parked, retry once the day rolls over",
                           append_note=True)
        escalate(f"task {tid}: daily token budget exhausted, parked", trigger="cost_cap_breach")
        log(f"task {tid}: quota_wait (token budget)")
        return "quota_wait"
    # F24 (docs/HARDENING.md): admission control. The check above is a pure gate -- it
    # stops the next task only AFTER the cap is blown, which is how 2026-07-27 hit 360%
    # of cap on one uninterruptible 8.5M call. A hermes subprocess cannot be stopped
    # mid-research, so refuse work we can already predict will not fit instead of
    # pretending we can halt it later. Evidence comes from this task's own prior spend
    # (preserved only because F21 stopped retries zeroing it).
    est = policy.estimated_tokens_for(tid, mission["id"])
    if policy.budget_insufficient_for(est):
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes=f"parked by admission control: estimated {est:,} "
                                        f"tokens will not fit today's remaining budget",
                           append_note=True)
        escalate(f"task {tid}: estimated {est:,} tokens exceeds remaining daily budget "
                f"({policy.tokens_used_today():,} already spent) -- parked before starting",
                trigger="cost_cap_breach")
        log(f"task {tid}: budget_skip (estimated {est:,} won't fit) — trying the next seed")
        return "budget_skip"
    ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
    usage_path = RUNS / f"task{tid}_worker.usage.json"
    snapshot = db_integrity_snapshot()
    fs_snapshot = fs_integrity_snapshot()
    try:
        out, usage, model_used_cfg, exhausted = worker_with_failover(
            prompt, worker_cfg, usage_path, log_prefix=f"task {tid}")
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes="worker timeout",
                           append_note=True)
        log(f"task {tid}: infra_failed (timeout)")
        return "infra_failed"

    db_integrity_check(snapshot, context=f"task {tid} worker call")
    fs_integrity_check(fs_snapshot, context=f"task {tid} worker call")
    # Persist the FULL raw output regardless of what happens next -- a misclassified
    # task must stay diagnosable. Learned 2026-07-18: a real, substantial brief was
    # nearly lost with only a 200-char snippet surviving in critic_notes.
    (RUNS / f"task{tid}_worker_raw.txt").write_text(out, encoding="utf-8")
    out = _strip_tool_chatter(out)

    if exhausted:
        # F9: every model in the chain hit quota -- park exactly as before this fix.
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="quota/usage limit on every model in the "
                                        "fallback chain — parked (§1.6, F9)",
                           append_note=True)
        log(f"task {tid}: chain_exhausted (every fallback model quota-limited)")
        return "chain_exhausted"
    if model_used_cfg != worker_cfg:
        # F9: keep provenance truthful and flag the degraded-model deliverable for
        # spot-check priority -- a failover completion is not a free pass.
        ledger.update_model_used(tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']}")
        escalate(f"task {tid}: completed via failover to {model_used_cfg['provider']}/"
                f"{model_used_cfg['model']} after quota exhaustion on the primary worker",
                trigger="model_failover")
        worker_cfg = model_used_cfg  # so the deliverable footer below is truthful too
    if worker_failed(out, usage):
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"worker API failure (full text in "
                                       f"runs/task{tid}_worker_raw.txt): {out[:200]}",
                           append_note=True)
        log(f"task {tid}: infra_failed ({out[:80]})")
        return "infra_failed"
    if len(out) < 200:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"output too short ({len(out)} chars) — no deliverable")
        log(f"task {tid}: failed (short output)")
        return "failed"

    # F13 deny-list (docs/HARDENING.md): heuristic scan of the worker's OWN report
    # for language claiming a hard-excluded action. The worker is never told these
    # tools/actions are off-limits by omission alone -- this makes the deny-list an
    # executed, escalated check rather than an unread document.
    deny_hits = policy.deny_list_scan(out)
    if deny_hits:
        ledger.finish_task(tid, artifacts=[], status="failed", critic_verdict="fail",
                           critic_notes=f"deny-list match: {deny_hits} -- see policy.yaml "
                                        f"hard_exclusions; output not persisted as a deliverable")
        escalate(f"task {tid}: worker output matched deny-list pattern(s) {deny_hits}",
                trigger="deny_list_match")
        log(f"task {tid}: failed (deny-list match {deny_hits})")
        return "failed"

    # write deliverable
    slug = re.sub(r"[^a-z0-9]+", "-", row["spec"].lower())[:60].strip("-")
    dest = out_dir / f"{wk}_{slug}.md"
    dest.write_text(out + f"\n\n---\n_task {tid} · {datetime.now().isoformat(timespec='seconds')}"
                          f" · {worker_cfg['model']}_\n", encoding="utf-8")

    verdict, verdict_text = run_critic(row, out, roles, baseline, scope_note=scope_note)
    if verdict == "needs_review":
        escalate(f"task {tid}: critic verdict ambiguous -- {verdict_text[:200]}",
                trigger="pass_criteria_ambiguous")
    # F18 (docs/HARDENING.md): status must reflect the verdict. Previously EVERY
    # resolved task landed status='done' regardless of critic_verdict -- proven live
    # 2026-07-24: task_id 20/21/22 all carry critic_verdict='fail' with status='done',
    # so weekly_fitness() (which reads only status) reported 100% completion on a week
    # where the TRUE pass rate was 0/10. needs_review is also not 'done' -- an
    # unjudged deliverable must not silently count as complete either.
    status = "done" if verdict == "pass" else "failed"

    # F32 (docs/HARDENING.md), 2026-07-29: accumulate, don't replace. F21 made an
    # OMITTED token count preserve the prior attempt's; it does nothing for a retry that
    # SUCCEEDS and passes real numbers, which overwrites them -- so the failed attempt's
    # spend vanishes from tokens_used_today() and the daily guard again protects less
    # than it should. Latent while retries were rare; directive-2 below makes retries
    # routine, so it has to be closed first. `row` was read before this attempt started,
    # so it holds exactly the prior total (0/NULL on a first run -- a no-op there).
    tok_in = int(usage.get("input_tokens") or 0) + int(row.get("tokens_in") or 0)
    tok_out = int(usage.get("output_tokens") or 0) + int(row.get("tokens_out") or 0)
    ledger.finish_task(tid, artifacts=[str(dest.relative_to(ROOT))], cost_usd=0.0,
                       tokens_in=tok_in, tokens_out=tok_out, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status=status)

    # Lesson capture (baseline weeks: harvest only, promotion stays OFF per §7):
    # critic objections become lesson_candidates so week-3 skill promotion has evidence.
    if verdict == "fail":
        ledger.add_lesson(tid, f"[{mission['id']}] {verdict_text[:300]}", kind="failed")
        _check_repeated_failure(mission["id"])

    # Memory-update stage: only PASSED research deliverables become facts.
    facts_n = extract_facts(tid, out, roles["manager"]["model"]) if verdict == "pass" else 0
    log(f"task {tid}: {status} verdict={verdict} facts+{facts_n} "
        f"({dest.name}, in={tok_in} out={tok_out})")
    return status


# Directive-1 (2026-07-29): a task can park for three very different reasons, and the
# batch loop used to treat all of them as "stop the whole fire". They are not the same:
#
#   budget_skip     -- admission control refused THIS task's predicted cost (F24). A
#                      cheaper seed behind it may well fit. Costs zero model calls.
#   quota_wait      -- the daily hard cap is blown. Every remaining task will park too,
#                      but parking them is free and leaves an honest, annotated row
#                      instead of a silent 'queued' with no explanation.
#   chain_exhausted -- every model in the fallback chain returned a quota error. This is
#                      the only one whose retry costs anything real, so it is the only
#                      one that stops the pass, and only after repeating.
#
# Treating budget_skip as a full stop was F6's head-of-line blocking rebuilt one layer
# up: on 2026-07-28 task 26 alone estimated ~8.5M tokens while tasks 28/29 needed 2.4M
# and 1.4M -- the expensive seed parked first and the two affordable ones behind it were
# never attempted.
PARK_STATUSES = ("quota_wait", "budget_skip", "chain_exhausted")
MAX_CONSECUTIVE_CHAIN_EXHAUSTED = 2

MAX_RETRIES_PER_FIRE = 3


def retry_failed_this_fire(ids: list[int], mission: dict, roles: dict) -> list[str]:
    """Directive-2 (2026-07-29): re-attempt this fire's CONTENT failures immediately.

    Until now a critic-rejected task was simply left failed. Next week's fire does not
    pick it up either -- queue_mission_tasks() dedups on a spec containing the ISO week,
    so a new week creates a NEW row and the rejected one is never revisited. The
    Evaluate -> next-attempt edge of the loop (HARNESS_DESIGN §2.1) therefore existed in
    code (run_task() has built prior_feedback from critic_notes all along) but had no
    path that ever exercised it. This is that path.

    Deliberately NOT retried: infra_failed. A worker timeout re-run costs another full
    WORKER_TIMEOUT_S (1800s) to most likely time out again -- that is a budget decision
    for the operator, not an automatic one. Content failures are what the critic's
    objections can actually steer.

    Synthesis retries go LAST so they rebuild from whatever briefs the research retries
    have just corrected, rather than from the versions that failed."""
    import sqlite3
    if not ids:
        return []
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"SELECT task_id, spec FROM tasks WHERE task_id IN "
            f"({','.join('?' * len(ids))}) AND status='failed' AND critic_verdict='fail'",
            ids).fetchall()
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (seed_is_synthesis(r["spec"]), r["task_id"]))
    picked = ordered[:MAX_RETRIES_PER_FIRE]
    log(f"retry pass: {len(rows)} content failure(s) this fire, retrying "
        f"{len(picked)} with the critic's objections attached"
        + (f" ({len(rows) - len(picked)} over the {MAX_RETRIES_PER_FIRE}/fire cap)"
           if len(rows) > len(picked) else ""))
    out = []
    for r in picked:
        st = run_task(r["task_id"], mission, roles)
        log(f"retry task {r['task_id']}: {st}")
        out.append(st)
        if st == "chain_exhausted":
            log("fallback chain exhausted — ending retry pass")
            break
    return out


REPEATED_FAILURE_THRESHOLD = 3


def _check_repeated_failure(mission_id: str) -> None:
    """policy.yaml's repeated_task_failure trigger (escalation.triggers): a mission
    accumulating this many content-FAILED tasks in the current week is a real signal
    the operator should see, independent of any single task's outcome."""
    import sqlite3
    wk = week_key()
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        n = c.execute(
            "SELECT count(*) FROM tasks WHERE mission_id=? AND status='failed' "
            "AND critic_verdict='fail' AND spec LIKE ?", (mission_id, f"[{wk}]%")).fetchone()[0]
    if n == REPEATED_FAILURE_THRESHOLD:  # fire once, at the exact threshold crossing
        escalate(f"mission {mission_id}: {n} content-failed tasks this week ({wk})",
                trigger="repeated_task_failure")


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
        with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
            dup = c.execute("SELECT task_id, status FROM tasks WHERE mission_id='canaries' "
                           "AND spec=?", (spec,)).fetchone()
        if dup and dup[1] not in ("quota_wait", "queued", "interrupted"):  # H3
            log(f"{name}: already {dup[1]} this week — skipping"); continue
        tid = dup[0] if dup else ledger.queue_task("canaries", spec, "deterministic grade")
        # F8/F13: canaries draw from the same daily token budget as mission work.
        if policy.token_budget_breached():
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               critic_notes="policy.yaml tokens_per_day_hard_stop reached",
                           append_note=True)
            escalate(f"canary {name}: daily token budget exhausted, parked",
                    trigger="cost_cap_breach")
            log(f"{name}: quota_wait (token budget)"); continue
        ledger.start_task(tid, f"{worker_cfg['provider']}/{worker_cfg['model']}")
        snapshot = db_integrity_snapshot()
        fs_snapshot = fs_integrity_snapshot()
        try:
            out, usage, model_used_cfg, exhausted = worker_with_failover(
                prompt, worker_cfg, RUNS / f"canary_{name}.usage.json", log_prefix=f"canary {name}")
        except subprocess.TimeoutExpired:
            ledger.finish_task(tid, artifacts=[], status="infra_failed",
                               critic_notes="canary timeout",
                           append_note=True)
            log(f"{name}: infra_failed (timeout)"); continue
        db_integrity_check(snapshot, context=f"canary {name}")
        fs_integrity_check(fs_snapshot, context=f"canary {name}")
        if exhausted:
            ledger.finish_task(tid, artifacts=[], status="quota_wait",
                               critic_notes="quota on every model in the fallback chain "
                                            "— canary parked (F9)",
                           append_note=True)
            log(f"{name}: quota_wait (fallback chain exhausted)"); continue
        if model_used_cfg != worker_cfg:
            ledger.update_model_used(tid, f"{model_used_cfg['provider']}/{model_used_cfg['model']}")
            escalate(f"canary {name}: completed via failover to {model_used_cfg['provider']}/"
                    f"{model_used_cfg['model']} after quota exhaustion on the primary worker",
                    trigger="model_failover")
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
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
        rows = c.execute("SELECT status, critic_verdict FROM tasks WHERE mission_id='canaries' "
                         "AND spec LIKE ?", (f"[{wk}]%",)).fetchall()
    week_green = sum(1 for s, v in rows if s == "done" and v == "pass")
    week_pending = sum(1 for s, _ in rows if s in ("quota_wait", "queued", "interrupted"))
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
LOCK_PATH_NAME = ".batch.lock"  # lives under RUNS; see runlock.py for F1 rationale


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

    import runlock
    try:
        with runlock.acquire(RUNS / LOCK_PATH_NAME):
            return _run(args)
    except runlock.AlreadyRunning as e:
        log(f"another batch_runner is already running — skipping this fire ({e})")
        return 0


def _run(args) -> int:
    """Everything that touches shared state (ledger/ledgerbook/workspace). Runs
    ONLY while main() holds the exclusive lock — never call this directly."""
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
    # F13 (docs/HARDENING.md): one-time-per-run consistency check between the
    # fs-guard's PROTECTED_PATHS (H9) and policy.yaml's declared writable roots --
    # catches the two lists silently drifting apart. Warns + escalates, doesn't
    # block the run (a stale doc shouldn't halt real work; it should get fixed).
    path_problems = policy.validate_paths(PROTECTED_PATHS)
    if path_problems:
        log(f"policy/fs-guard path inconsistency: {path_problems}")
        escalate(f"policy.yaml/fs-guard path lists are inconsistent: {path_problems}")
    roles = load_roles()
    expire_stale_parked()
    reconcile_interrupted_tasks()

    if args.canaries:
        run_canaries(roles)
        return 0

    if args.resume:
        import sqlite3
        with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
            # Filter OUT canaries (use --canaries to resume those) BEFORE slicing to
            # max_tasks -- found 2026-07-18: slicing first let canary rows, which sort
            # earlier by task_id, silently consume the whole budget while the mission
            # task the operator actually wanted resumed was never reached (no error,
            # just a quiet no-op).
            # F6 (docs/HARDENING.md): never-attempted (started_at NULL -- hit the
            # pre-start_task() token-budget check, not an actual worker call) go before
            # already-attempted, same fairness rule as queue_mission_tasks() above.
            parked = [r[0] for r in c.execute(
                "SELECT task_id, mission_id FROM tasks WHERE status IN "
                "('quota_wait', 'interrupted') AND mission_id != 'canaries' "
                "ORDER BY (started_at IS NOT NULL), task_id")]  # H3
        log(f"resume mode: {len(parked)} parked/interrupted non-canary task(s)")
        ran = 0
        exhausted_streak = 0
        for tid in parked[:args.max_tasks]:
            with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
                mid = c.execute("SELECT mission_id FROM tasks WHERE task_id=?",
                                (tid,)).fetchone()[0]
            st = run_task(tid, parse_mission(mid), roles)
            ran += 1
            # Directive-1: same rule as the main loop -- a task that could not fit the
            # budget must not cancel the resume attempt of every task behind it.
            if st == "chain_exhausted":
                exhausted_streak += 1
                if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                    log("every fallback model still quota-limited — stopping resume pass")
                    break
            else:
                exhausted_streak = 0
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

    # Directive-1: give EVERY seed its turn. Only a repeatedly-exhausted provider chain
    # (the one park reason whose retry actually costs anything) stops the pass early --
    # see PARK_STATUSES.
    statuses = []
    exhausted_streak = 0
    for tid in ids[:args.max_tasks]:
        st = run_task(tid, mission, roles)
        statuses.append(st)
        if st == "chain_exhausted":
            exhausted_streak += 1
            if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                log(f"every fallback model quota-limited on {exhausted_streak} consecutive "
                    f"tasks — stopping this pass, remaining seeds stay queued")
                break
        else:
            exhausted_streak = 0

    statuses += retry_failed_this_fire(ids, mission, roles)
    done = statuses.count("done")
    parked = sum(statuses.count(s) for s in PARK_STATUSES)
    log(f"run complete: {done}/{len(statuses)} done, {parked} parked, "
        f"{statuses.count('infra_failed')} infra, {statuses.count('failed')} failed")
    # Directive-5: a fire that produced new deliverables pushes the spot-check queue
    # instead of waiting for the operator to think of running `spotcheck.py list`.
    # Fail-soft on purpose -- an undeliverable notification must never fail the batch.
    if done:
        try:
            import spotcheck
            if spotcheck.notify_pending():
                log("spot-check queue pushed to Telegram")
        except Exception as e:
            log(f"spot-check notification skipped ({e})")
    print("FITNESS:", json.dumps(ledger.weekly_fitness(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
