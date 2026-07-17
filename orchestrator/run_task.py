"""Hand-run one task through the harness: worker (hermes -z) -> critic -> ledger.
This is the M0 acceptance mechanism and the M1 building block (HARNESS_DESIGN.md §2.1).

    python orchestrator/run_task.py --mission 000-onboarding --niche "eco water bottles"

Model routing comes from config/models.yaml — never hardcoded here (model-agnostic §2.2).
Requires a reachable model: an Anthropic key in Hermes .env OR an open Ollama quota window.
On HTTP 429 the task is parked (status=quota_wait) rather than failed (§1.6 / policy.yaml)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml  # PyYAML ships in the Hermes venv; falls back below if absent

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def load_models() -> dict:
    cfg = (ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(cfg)["roles"]


def hermes_oneshot(prompt: str, provider: str, model: str, usage_path: Path) -> tuple[str, dict]:
    """Call `hermes -z` with an explicit model; capture text + usage JSON.
    cp1252 crashes on this box -> force utf-8 (machine rule)."""
    RUNS.mkdir(exist_ok=True)
    cmd = ["hermes", "-z", prompt, "--provider", provider, "-m", model,
           "--usage-file", str(usage_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    usage = {}
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
    return proc.stdout.strip(), usage


def is_quota_error(text: str) -> bool:
    t = text.lower()
    return "429" in t or "too many requests" in t or "rate limit" in t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission", required=True)
    ap.add_argument("--niche", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the task + prompts, skip the model calls")
    args = ap.parse_args()

    mission_path = ROOT / "missions" / f"{args.mission}.md"
    mission = mission_path.read_text(encoding="utf-8")
    roles = load_models()
    worker = roles["worker"]
    critic = roles["critic"]

    spec = f"Mission {args.mission}: gather 3 sourced facts. Niche: {args.niche or 'TBD'}"
    criteria = ("workspace file with 3 facts, each with source URL + retrieval date; "
                "critic verdict logged")
    tid = ledger.queue_task(args.mission, spec, criteria)
    print(f"[ledger] queued task {tid}")

    worker_prompt = (f"{mission}\n\nOperator niche: {args.niche}\n\n"
                     "Produce workspace/onboarding/hello_report.md with exactly 3 facts, "
                     "each on its own line as: FACT — source URL — retrieval date. "
                     "Use official web search only. No fact without a source.")
    critic_prompt_tmpl = ("You are a strict critic. Given the WORKER OUTPUT and the PASS "
                          "CRITERIA, reply PASS or FAIL then one sentence why.\n\n"
                          "PASS CRITERIA:\n{criteria}\n\nWORKER OUTPUT:\n{output}")

    if args.dry_run:
        print("[dry-run] worker model:", worker, "| critic model:", critic)
        print("[dry-run] worker prompt built; skipping model calls.")
        ledger.finish_task(tid, artifacts=[], status="blocked",
                           critic_notes="dry-run: no model called")
        return 0

    ledger.start_task(tid, f"{worker['provider']}/{worker['model']}")
    try:
        out, usage = hermes_oneshot(worker_prompt, worker["provider"], worker["model"],
                                    RUNS / f"task{tid}_worker.usage.json")
    except subprocess.TimeoutExpired:
        ledger.finish_task(tid, artifacts=[], status="failed",
                           critic_notes="worker timeout (900s)")
        print("[fail] worker timed out"); return 1

    if is_quota_error(out):
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes="429 — parked for retry (§1.6)")
        print("[quota_wait] 429 — task parked, retry in an open window"); return 2

    critic_prompt = critic_prompt_tmpl.format(criteria=criteria, output=out)
    verdict_text, _ = hermes_oneshot(critic_prompt, critic["provider"], critic["model"],
                                     RUNS / f"task{tid}_critic.usage.json")
    verdict = "pass" if verdict_text.upper().startswith("PASS") else "fail"
    cost = float(usage.get("estimated_cost", 0.0) or 0.0)
    ledger.finish_task(tid, artifacts=["workspace/onboarding/hello_report.md"],
                       cost_usd=cost, critic_verdict=verdict,
                       critic_notes=verdict_text[:500], status="done")
    print(f"[done] task {tid} verdict={verdict} cost=${cost:.4f}")
    print("[fitness]", json.dumps(ledger.weekly_fitness(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
