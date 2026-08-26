"""orchestrator/execution.py -- model invocation, failover, and worker-call helpers.

Extracted from batch_runner.py as Move 2 of the W9 5-file split (see REFACTOR_PLAN.md).
This module owns everything that talks to a model: hermes_worker (the subprocess
path), ollama_chat (the tool-free HTTP path), the failover helpers that walk the
fallback chain on quota errors, and the predicates that classify a worker call's
output.

Dependency direction (per the W9 plan, section 1):
    integrity.py -> execution.py -> prompts.py -> evaluation.py -> scheduler.py

This module depends only on:
    - stdlib (json, re, subprocess, urllib)
    - yaml (for load_fallback_chain)
    - ROOT (pathing) -- this module's own definition, mirroring batch_runner.py

What does NOT live here:
    - _strip_tool_chatter: a string cleaner used by both run_task (scheduler) and
      run_critic (evaluation). It is NOT execution-specific, so it stays in
      batch_runner.py for now and will move to a shared utilities module if a
      third caller emerges.
"""


import sys
from pathlib import Path
import json
import re
import subprocess
from datetime import datetime

# Paths shared with batch_runner.py. Both files add `orchestrator/` to sys.path,
# so importing ROOT here works the same as it does in batch_runner.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml  # for load_fallback_chain()

# Module constants needed by the moved functions.
WORKER_TIMEOUT_S = 1800
LOCAL_FALLBACK_TIMEOUT_S = 3600  # gemma4:12b measured 1.54 tok/s (§1.6) driving hermes's
                                  # full browser tool-calling loop -- WORKER_TIMEOUT_S
                                  # (900s) would kill a real multi-fact brief mid-generation.
LOCAL_PROVIDERS = {"ollama"}   # the only provider that can execute on this box
CHARS_PER_TOKEN = 4          # rough English ratio -- only ever used for a fits/doesn't-fit
                             # decision with a large margin, never for accounting (that is
                             # measured from the provider's own counts; see F33)
RESPONSE_RESERVE_TOKENS = 1500   # a deliverable still has to fit in the reply

# `log` and `ROOT` are provided by a tiny import-block at the top of execution.py,
# but they need to live SOMEWHERE accessible from this module. batch_runner.py
# defines them; we mirror the bare minimum here so the moved functions don't have
# to be rewritten to take log() / ROOT as parameters.
ROOT = Path(__file__).resolve().parent.parent

def log(msg: str) -> None:
    """Same shape as batch_runner.log(): print with a timestamp, also append to RUNS/<batch>.log
    if one is open. batch_runner.py owns the actual file handle; this is a no-op stub that
    will be replaced by a real implementation once scheduler.py is moved (which owns
    the run-scoped log file). For now, the moved functions call into the live
    `log` via the re-export shim that batch_runner.py will add."""
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")


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


LOCAL_PROVIDERS = {"ollama"}   # the only provider that can execute on this box


def _is_local_model(model_cfg: dict) -> bool:
    """Does this model run on THIS machine?

    Ollama cloud models are suffixed `:cloud`; an Ollama model without that suffix runs
    on the local daemon and needs LOCAL_FALLBACK_TIMEOUT_S rather than WORKER_TIMEOUT_S.

    F41 (docs/HARDENING.md), 2026-07-29: this used to test the model NAME alone --
    `":cloud" not in model` -- which makes locality a property of a naming convention
    instead of where the model actually runs. Any non-Ollama model is therefore
    misclassified as local, `anthropic/claude-sonnet-5` included: the exact rung
    models.yaml keeps pre-wired and CLAUDE.md calls PREFERRED. Latent while that line
    stayed commented (the only visible cost was a 3600s timeout on a fast API), and it
    became a correctness bug the moment F40 started excluding local models from canaries
    -- a genuinely separate provider would have been excluded too, which is precisely
    backwards, since surviving an Ollama-account 429 is its entire purpose. Found by a
    test that simulated adding that rung rather than by reading the line."""
    if (model_cfg.get("provider") or "").lower() not in LOCAL_PROVIDERS:
        return False
    return ":cloud" not in (model_cfg.get("model") or "")


def load_fallback_chain() -> list[dict]:
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    return cfg.get("fallback_chain") or []


def _quota_group(cfg: dict) -> str | None:
    """Which quota pool this model draws on, or None for "its own pool".

    F39 (docs/HARDENING.md): 429 is ACCOUNT-level, and models.yaml said so only in a
    comment. Declaring it as data lets the failover loops skip rungs that are guaranteed
    to refuse. Absent by default on purpose -- an undeclared model is never skipped by
    inference, so adding a genuinely separate provider needs no code change and a
    mis-declared group can only ever cost a wasted call, never a skipped good rung."""
    return cfg.get("quota_group")


CHARS_PER_TOKEN = 4          # rough English ratio -- only ever used for a fits/doesn't-fit
                             # decision with a large margin, never for accounting (that is
                             # measured from the provider's own counts; see F33)
RESPONSE_RESERVE_TOKENS = 1500   # a deliverable still has to fit in the reply


def _fits_context(cfg: dict, prompt: str) -> bool:
    """Can this rung physically accept `prompt` AND have room left to answer?

    F50 (docs/HARDENING.md), 2026-07-30. `synthesis_with_failover()` walked the whole
    chain including `gemma4:12b-ctx4k`, whose context is 4,096 tokens, while a synthesis
    prompt measured 8,226 (content) to 11,662 (shopify) tokens even at the OLD 6,000-char
    brief cap -- two to three times what that rung can accept. It had therefore never once
    been able to serve this path. F38 made the rung LOADABLE by capping num_ctx; loadable
    is not usable, and nothing checked the difference. The visible cost was a stall at
    1.5 tok/s against LOCAL_FALLBACK_TIMEOUT_S ending in a failure that explains nothing.

    Deliberately keyed on DECLARED CONTEXT, not on locality. `allow_local=False` (F40's
    tool, one line, and the obvious candidate) encodes the wrong cause: it would wrongly
    skip a future local model with a large context, and would wrongly KEEP a small-context
    cloud one. The reason this rung fails is that the prompt does not fit, so that is what
    is tested.

    Opt-in exactly like F39's `quota_group`: a model that does not DECLARE
    `context_tokens` is never skipped by inference. A missing declaration can only cost a
    wasted call; it can never silently skip a rung that would have worked. Keeping the
    number in `config/models.yaml` also holds the line that swapping a model is a config
    edit, never a code edit."""
    declared = cfg.get("context_tokens")
    if not declared:
        return True                       # undeclared: make no claim, never skip
    need = len(prompt) // CHARS_PER_TOKEN + RESPONSE_RESERVE_TOKENS
    return need <= int(declared)


def _context_skip_note(cfg: dict, prompt: str) -> str:
    need = len(prompt) // CHARS_PER_TOKEN + RESPONSE_RESERVE_TOKENS
    return (f"prompt needs ~{need} tok (incl. {RESPONSE_RESERVE_TOKENS} reserved for the "
            f"reply) but {cfg['model']} declares only {cfg.get('context_tokens')}")


def _failover_candidates(worker_cfg: dict, allow_local: bool = True) -> list[dict]:
    """worker_cfg first, then fallback_chain entries not already equal to it, deduped
    by (provider, model) so a chain that happens to list the worker's own model again
    doesn't retry it twice.

    allow_local=False drops local models entirely. Used for work whose GRADE drives an
    automated decision about the system itself -- currently the canaries, whose green
    count can delete an operator-approved skill (F40). Everything else keeps the local
    rung: completing a deliverable on a slow model beats parking it, which was the
    operator's explicit F9 decision, and a mission task's grade is reported rather than
    acted on automatically."""
    candidates = [worker_cfg] if (allow_local or not _is_local_model(worker_cfg)) else []
    seen = {(worker_cfg["provider"], worker_cfg["model"])}
    for c in load_fallback_chain():
        key = (c["provider"], c["model"])
        if key in seen:
            continue
        seen.add(key)
        if not allow_local and _is_local_model(c):
            continue
        candidates.append(c)
    return candidates


def worker_with_failover(prompt: str, worker_cfg: dict, usage_path: Path,
                         log_prefix: str,
                         allow_local: bool = True) -> tuple[str, dict, dict, bool]:
    """hermes_worker() with failover on QUOTA ERRORS ONLY. A genuine subprocess timeout
    on any one candidate still raises subprocess.TimeoutExpired exactly as before --
    the caller's existing except-block handles it unchanged. This only widens what
    happens on actual quota-error text, matching F9's own scope ("on sustained 429 ...
    fail over"), not a general retry-on-any-failure mechanism.

    Returns (out, usage, model_cfg_used, exhausted). exhausted=True means every
    candidate in the chain returned a quota error -- caller should park exactly as it
    did before this fix existed."""
    candidates = _failover_candidates(worker_cfg, allow_local=allow_local)
    if not candidates:
        log(f"{log_prefix}: no eligible model (local excluded, no cloud rung) — parking")
        return "", {}, worker_cfg, True
    out, usage, cfg_used = "", {}, candidates[0]
    dead_groups: set[str] = set()          # F39: quota pools already known exhausted
    for i, cfg in enumerate(candidates):
        grp = _quota_group(cfg)
        if grp and grp in dead_groups:
            log(f"{log_prefix}: skipping {cfg['provider']}/{cfg['model']} "
                f"({i+1}/{len(candidates)}) — quota group '{grp}' already exhausted")
            continue
        if not _fits_context(cfg, prompt):          # F50
            log(f"{log_prefix}: skipping {cfg['provider']}/{cfg['model']} "
                f"({i+1}/{len(candidates)}) — {_context_skip_note(cfg, prompt)}")
            continue
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
        if grp:
            dead_groups.add(grp)
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
    dead_groups: set[str] = set()          # F39: quota pools already known exhausted
    for i, cfg in enumerate(candidates):
        grp = _quota_group(cfg)
        if grp and grp in dead_groups:
            log(f"{log_prefix}: skipping {cfg['provider']}/{cfg['model']} "
                f"({i+1}/{len(candidates)}) — quota group '{grp}' already exhausted")
            continue
        if not _fits_context(cfg, prompt):          # F50
            log(f"{log_prefix}: skipping {cfg['provider']}/{cfg['model']} "
                f"({i+1}/{len(candidates)}) — {_context_skip_note(cfg, prompt)}")
            continue
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
            if grp:
                dead_groups.add(grp)
            more = i + 1 < len(candidates)
            log(f"{log_prefix}: quota error (HTTP 429) on {cfg['provider']}/{cfg['model']} "
               f"({i+1}/{len(candidates)})" +
               (" -- trying next" if more else " -- chain exhausted"))
    return None, cfg_used, True





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
