"""RED contract and malformed-input suite for staged onboarding."""
import importlib
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
import onboarding_autonomy as onboarding

source = (ORCH / "onboarding_autonomy.py").read_text(encoding="utf-8")
checks = {
    "onboarding uses canonical provider boundary":
        "provider_chat" in source and "urllib.request.urlopen" not in source,
    "module-global token accumulator removed": "TOKENS =" not in source,
    "durable onboarding journal exists":
        "OnboardingRunJournal" in source and "TASK_FINALIZED" in source,
    "model-controlled slugs have a path-safe validator":
        hasattr(onboarding, "validate_slug"),
    "critic infrastructure outcome remains infra_failed":
        hasattr(onboarding, "status_for_critic_verdict"),
}

validate = getattr(onboarding, "validate_onboarding_payload", None)
checks["typed onboarding payload validator exists"] = callable(validate)

valid = {
    "niches": [
        {"name": f"Niche {i}", "slug": f"niche-{i}", "product_angle": "p",
         "content_angle": "c", "rationale": "r"} for i in range(3)
    ],
    "personas": [
        {"name": f"Persona {i}", "age": 20 + i, "psych_trigger": "value",
         "attention_span": "short", "buying_friction": "medium",
         "description": "d"} for i in range(5)
    ],
}

invalid_payloads = [
    {**valid, "niches": valid["niches"][:2]},
    {**valid, "niches": [valid["niches"][0], valid["niches"][0], valid["niches"][2]]},
    {**valid, "niches": [{**valid["niches"][0], "slug": "../../config"},
                           *valid["niches"][1:]]},
    {**valid, "winner_slug": "not-a-candidate"},
    {**valid, "estimates": [{"slug": "niche-0", "conversion_probability": math.nan,
                              "reason": "invalid"}]},
]

rejected = []
if callable(validate):
    for payload in invalid_payloads:
        try:
            validate(payload)
            rejected.append(False)
        except (TypeError, ValueError):
            rejected.append(True)
checks["malformed/unsafe onboarding payloads are all rejected"] = (
    len(rejected) == len(invalid_payloads) and all(rejected))

db_write = source.find("INSERT INTO decisions")
critic = source.find('step("7/7 critic')
checks["domain memory is not committed before critic review"] = (
    db_write < 0 or (critic >= 0 and db_write > critic))
checks["fixed audit filenames cannot overwrite prior runs"] = (
    "run_id" in source and 'RUNS / f"{tag}.json"' not in source)

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'EXPECTED FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("onboarding RED contract unmet: " + ", ".join(failed))

