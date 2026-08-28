"""Policy-as-code loader + enforcement (fixes F13, docs/HARDENING.md).

config/policy.yaml declared autonomy limits, a deny-list, and cost caps that NO
code read (`grep usd_per_day|hard_stop|cost_cap` -> none in orchestrator/, proven
2026-07-19). This module is the single place that loads policy.yaml and the
functions batch_runner.py calls to actually enforce it -- swapping a limit means
editing policy.yaml, never code (same discipline as config/models.yaml).

Every check here is block-level (halts/fails the specific task or run and
escalates), not a per-action prompt -- matching policy.yaml's own stated
"WIDE autonomy" posture: enforcement is a small number of hard boundaries, not
constant confirmation.
"""
import json
import re
import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy.yaml"
LEDGER_DB = ROOT / "ledger" / "ledger.db"
# Manager-call daily counter. Lives under runs/ (already gitignored, already the
# run-log home) and relies on the SAME run-lock that already serializes every
# batch_runner.py invocation (H1, docs/HARDENING.md) -- no separate locking
# needed since this file is only ever touched from inside a locked _run().
STATE_PATH = ROOT / "runs" / "policy_state.json"

# Escalation triggers policy.yaml itself declares (escalation.triggers). Any
# escalate(trigger=...) call in batch_runner.py must use one of these names --
# keeps policy.yaml authoritative instead of parallel decoration.
VALID_TRIGGERS = {"deny_list_match", "pass_criteria_ambiguous", "cost_cap_breach",
                  "repeated_task_failure", "model_failover"}


def load() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def validate_trigger(trigger) -> None:
    if trigger is not None and trigger not in VALID_TRIGGERS:
        raise ValueError(f"unknown escalation trigger {trigger!r} -- not declared in "
                         f"policy.yaml escalation.triggers ({sorted(VALID_TRIGGERS)})")


# ── workspace confinement (F13, pairs with H9's fs-guard) ─────────────────────
def writable_roots(pol: dict | None = None) -> list[Path]:
    pol = pol or load()
    return [Path(p) for p in pol["workspace_confinement"]["writes_allowed_under"]]


def is_path_writable(path, pol: dict | None = None) -> bool:
    path = Path(path).resolve()
    for root in writable_roots(pol):
        root = root.resolve()
        if path == root or root in path.parents:
            return True
    return False


def validate_paths(protected_paths: list[str], pol: dict | None = None) -> list[str]:
    """Consistency self-check between batch_runner.py's fs-guard PROTECTED_PATHS
    (H9) and policy.yaml's workspace_confinement.writes_allowed_under. Returns a
    list of problems (empty = consistent). Call once at startup so the two
    declarations of "what the worker may touch" cannot silently drift apart --
    the whole point of F13 is that policy.yaml must be a real, checked source."""
    pol = pol or load()
    problems = []
    for rel in protected_paths:
        p = (ROOT / rel).resolve()
        if is_path_writable(p, pol):
            problems.append(f"{rel} is fs-guard-PROTECTED but also inside a policy.yaml "
                            f"writable root -- inconsistent, fix one of the two lists")
    return problems


# ── deny-list scan (F13 hard_exclusions) ───────────────────────────────────────
# Heuristic scan of a worker's OWN reported output for language claiming one of
# the three hard-excluded actions. Deliberately conservative (regex over the
# worker's self-report, not a guarantee against a determined attacker) -- the
# point is turning the deny-list from an unread document into an executed,
# logged, escalated check, not building a perfect classifier.
_DENY_PATTERNS = [
    (re.compile(r"\b(wire transfer|sent \$\d|purchased \d+ shares|bought \d+ shares|"
               r"executed a trade|placed an order for|transferred \$)\b", re.I),
     "move_money"),
    (re.compile(r"\b(entered the password|typed in the api key|submitted (the |a )?"
               r"credit card|logged in using the credentials|entered my password)\b", re.I),
     "handle_credentials"),
    (re.compile(r"\b(permanently deleted|emptied the trash|deleted all files|rm -rf )\b",
               re.I), "irreversible_delete"),
]


def deny_list_scan(text: str) -> list[str]:
    """Return the hard_exclusions names whose pattern matched, or [] if clean."""
    return [name for pat, name in _DENY_PATTERNS if pat.search(text)]


# ── compliance-floor / hard-exclusion prompt block ─────────────────────────────
_FLOOR_TEXT = {
    "official_apis_only": "use only official public APIs/pages, never scrape behind a login",
    "no_scraping_behind_logins": "never attempt to log in anywhere to access data",
    "no_bot_posting": "never post, comment, or message on any platform on the operator's behalf",
    "no_trending_copyrighted_audio_commercial":
        "never recommend using trending copyrighted audio for commercial work",
}
_EXCLUSION_TEXT = {
    "move_money": "never claim to buy, sell, transfer, or spend any money",
    "handle_credentials": "never claim to enter a password, API key, or other credential",
    "irreversible_delete": "never claim to permanently delete anything",
}


def compliance_prompt_block(pol: dict | None = None) -> str:
    pol = pol or load()
    parts = [_FLOOR_TEXT[f] for f in pol.get("compliance_floor", []) if f in _FLOOR_TEXT]
    parts += [_EXCLUSION_TEXT[e] for e in pol.get("hard_exclusions", []) if e in _EXCLUSION_TEXT]
    if not parts:
        return ""
    return "HARD RULES (never break these): " + "; ".join(parts) + "."


# ── token budget (F8: cost_usd is always 0.0 on Ollama, tokens are real) ───────
def tokens_used_today() -> int:
    # F17 lesson (docs/HARDENING.md): never mix Python-local date math with the
    # DB's UTC clock domain -- compute "today" entirely in SQLite's own clock.
    #
    # F22 (docs/HARDENING.md): this filtered on created_at, i.e. it measured "tokens
    # belonging to tasks CREATED today" rather than "tokens SPENT today" -- and those
    # differ precisely in the workflow this harness is built around: park on quota,
    # resume the next day; retry a stale row; work a backlog. Measured live 2026-07-28:
    # the 02:15 run burned 7,219,268 tokens on tasks 24/25 (created 07-27, finished
    # 07-28) and tokens_used_today() returned 0, so the daily cap was blind to the
    # entire night's spend and would have authorised a full second budget on top.
    # finished_at is when the spend is known and recorded, so it is the honest clock
    # for a consumption guard. A still-running task contributes nothing either way --
    # its usage is not known until it returns (the in-flight overshoot noted in
    # policy.yaml is a separate, still-open gap).
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) FROM tasks "
            "WHERE datetime(finished_at) >= datetime(" + _TODAY_START_SQL + ")").fetchone()
    return row[0] or 0


# F44 (docs/HARDENING.md), 2026-07-30. The boundary above used to be
# `datetime('now','start of day')`, and the comment cited F17's lesson correctly while
# applying it to the wrong reference column.
#
# ledger.window_start_sql() is right to stay in SQLite's UTC domain, because it compares
# against `created_at`, which SQLite itself writes via datetime('now') -- UTC, space
# separated. `finished_at` is a different animal: ledger.finish_task() writes it with
# Python's datetime.now().isoformat(), i.e. LOCAL time with a 'T' separator. That historical
# mismatch motivated F44; new lifecycle writes are canonical UTC and comparisons normalize
# both new and legacy rows through SQLite datetime().
#
# Measured live 2026-07-30 at 01:12 local (23:12 UTC the previous day): the UTC boundary
# resolved to 2026-07-29 00:00:00, so "today" swallowed four of yesterday's tasks and the
# guard reported 11,390,219 tokens spent on a day that had spent nothing. Both directions
# hurt -- an inflated counter makes admission control (F24) refuse work that would fit,
# and at 02:00 local the counter drops to today-only mid-flight, so a run spanning that
# instant sees the budget reset and can exceed the real cap.
#
# Third recurrence of this class (F17 leases, F19 fitness window, now the budget guard),
# and F22 introduced it: switching created_at -> finished_at was the right fix to the right
# bug, but carried the old boundary along -- the same compose-two-correct-changes-into-a-
# wrong-one shape as F22b. The boundary is now explicit RFC3339 UTC.
_TODAY_START_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ','now','start of day')"


def today_start() -> str:
    """Canonical UTC boundary used by tokens_used_today()."""
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        return c.execute("SELECT " + _TODAY_START_SQL).fetchone()[0]


def token_budget_breached(pol: dict | None = None) -> bool:
    pol = pol or load()
    cap = pol["cost_caps"].get("tokens_per_day_hard_stop")
    return bool(cap) and tokens_used_today() >= cap


def estimated_tokens_for(task_id: int, mission_id: str) -> int | None:
    """Best evidence of what this task will cost, or None if there is none.

    Prefers the task's OWN recorded spend from a previous attempt -- the single most
    accurate predictor, and it only exists because F21 stopped retries from zeroing it.
    Falls back to the largest recent completed task in the same mission (largest, not
    mean: the point is to avoid admitting something that will not fit, and mission 001's
    seeds range 0.5M-8.5M, so a mean would wave the expensive ones through)."""
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        own = c.execute("SELECT tokens_in + tokens_out FROM tasks WHERE task_id=?",
                        (task_id,)).fetchone()
        if own and own[0]:
            return int(own[0])
        peer = c.execute(
            "SELECT MAX(tokens_in + tokens_out) FROM tasks WHERE mission_id=? "
            "AND status IN ('done','failed') "
            "AND datetime(finished_at) >= datetime('now','-21 days')",
            (mission_id,)).fetchone()
    return int(peer[0]) if peer and peer[0] else None


def budget_insufficient_for(estimate: int | None, pol: dict | None = None) -> bool:
    """Admission control (F24, docs/HARDENING.md): would starting this task exceed the
    daily cap, given what we already spent today?

    token_budget_breached() is a pure GATE -- it stops the NEXT task once the cap is
    already blown, but cannot stop one in flight. That is how 2026-07-27 reached 360% of
    cap: a single seed spent 8,517,508 against a 3M cap in one uninterruptible call.
    There is no way to interrupt a hermes subprocess mid-research, so the honest fix is
    to refuse ADMISSION to work we can already predict will not fit, rather than to
    pretend we can stop it later. Unknown estimate (no history) admits the task -- the
    guard never blocks on ignorance, it only acts on evidence."""
    if not estimate:
        return False
    pol = pol or load()
    cap = pol["cost_caps"].get("tokens_per_day_hard_stop")
    return bool(cap) and (tokens_used_today() + estimate) > cap


# ── manager-call budget (cost_caps.manager_calls_per_day) ─────────────────────
def _today_key() -> str:
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        return c.execute("SELECT date('now')").fetchone()[0]


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def manager_calls_today() -> int:
    return _load_state().get(_today_key(), 0)


def record_manager_call() -> int:
    """Call once per manager/critic-role LLM invocation (run_critic, extract_facts,
    promote.py review). Returns the new count for today."""
    key = _today_key()
    state = {key: _load_state().get(key, 0) + 1}  # drop older days, only today matters
    _save_state(state)
    return state[key]


def manager_call_budget_breached(pol: dict | None = None) -> bool:
    pol = pol or load()
    cap = pol["cost_caps"].get("manager_calls_per_day")
    return bool(cap) and manager_calls_today() >= cap
