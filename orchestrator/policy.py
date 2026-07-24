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
                  "repeated_task_failure"}


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
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) FROM tasks "
            "WHERE created_at >= datetime('now','start of day')").fetchone()
    return row[0] or 0


def token_budget_breached(pol: dict | None = None) -> bool:
    pol = pol or load()
    cap = pol["cost_caps"].get("tokens_per_day_hard_stop")
    return bool(cap) and tokens_used_today() >= cap


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
