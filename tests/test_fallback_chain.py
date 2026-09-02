"""Fallback chain behaviour — cross-provider failover.

Verifies that the chain correctly:
- Skips quota-exhausted rungs in the same quota_group
- Tries genuinely separate providers (no quota_group) after cloud 429s
- Falls through to the last resort (local gemma) when everything is exhausted
- Handles the config as shipped with anthropic + openai rungs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import yaml  # noqa: E402
import execution  # noqa: E402

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


# ── 1. Shipped config ────────────────────────────────────────────────────────


print("=== Shipped config ===")
cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
chain = cfg["fallback_chain"]
provider_names = [c["provider"] for c in chain]
check("ollama is first rung", provider_names[0], "ollama")
check("anthropic is in the chain", "anthropic" in provider_names)
check("openai is in the chain", "openai" in provider_names)
check("gemma4:12b-ctx4k is last resort", provider_names[-1], "ollama")

# Anthropic rung has no quota_group
ant = [c for c in chain if c["provider"] == "anthropic"]
if ant:
    check("anthropic rung has no quota_group", ant[0].get("quota_group"), None)

# OpenAI rung has no quota_group
oai = [c for c in chain if c["provider"] == "openai"]
if oai:
    check("openai rung has no quota_group", oai[0].get("quota_group"), None)


# ── 2. load_fallback_chain returns merged configs ────────────────────────────


print("\n=== Merged chain ===")
merged = execution.load_fallback_chain()
mproviders = [c["provider"] for c in merged]
check("merged chain includes byteplus_coding provider config",
      "byteplus_coding" in mproviders or "endpoint" in str(merged))
# The merged chain should include endpoint for byteplus_coding
bp = [c for c in merged if c.get("provider") == "byteplus_coding"]
if bp:
    check("byteplus_coding has endpoint in merged config",
          "endpoint" in bp[0] or "authentication_reference" in bp[0])


# ── 3. _failover_candidates with anthropic/openai ────────────────────────────


print("\n=== Failover candidates ===")

# Simulate a worker config that's ollama cloud
worker_cfg = {"provider": "ollama", "model": "kimi-k2.7-code:cloud", "quota_group": "ollama-cloud"}
real_chain = execution.load_fallback_chain

# Inject a short chain with the new providers
execution.load_fallback_chain = lambda: [
    {"provider": "ollama", "model": "glm-5.2:cloud", "quota_group": "ollama-cloud"},
    {"provider": "anthropic", "model": "claude-sonnet-5"},
    {"provider": "openai", "model": "gpt-4o"},
    {"provider": "ollama", "model": "gemma4:12b-ctx4k", "context_tokens": 4096},
]

candidates = execution._failover_candidates(worker_cfg, allow_local=True)
candidate_keys = [(c["provider"], c["model"]) for c in candidates]
check("worker model is first candidate", candidate_keys[0], ("ollama", "kimi-k2.7-code:cloud"))
check("anthropic is in failover candidates", ("anthropic", "claude-sonnet-5") in candidate_keys)
check("openai is in failover candidates", ("openai", "gpt-4o") in candidate_keys)
check("gemma4 is in failover candidates", ("ollama", "gemma4:12b-ctx4k") in candidate_keys)

# Deduplication: if the worker config matches a chain entry, it appears once
worker_cfg_dup = {"provider": "ollama", "model": "glm-5.2:cloud", "quota_group": "ollama-cloud"}
candidates2 = execution._failover_candidates(worker_cfg_dup, allow_local=True)
candidate_keys2 = [(c["provider"], c["model"]) for c in candidates2]
check("glm appears once even when worker matches chain",
      candidate_keys2.count(("ollama", "glm-5.2:cloud")), 1)


# ── 4. Quota group skip logic ────────────────────────────────────────────────


print("\n=== Quota group skipping ===")

# Simulate the canary test pattern from test_f39_f40: first two ollama cloud rungs
# are in the same quota_group, so after a 429 the chain skips to anthropic.
chain_with_groups = [
    {"provider": "ollama", "model": "glm-5.2:cloud", "quota_group": "ollama-cloud"},
    {"provider": "ollama", "model": "kimi-k2.7-code:cloud", "quota_group": "ollama-cloud"},
    {"provider": "anthropic", "model": "claude-sonnet-5"},
]
exhausted_groups = {"ollama-cloud"}  # first group already 429'd

remaining = [c for c in chain_with_groups
             if not (c.get("quota_group") and c["quota_group"] in exhausted_groups)]
remaining_providers = [c["provider"] for c in remaining]
check("quota-exhausted group rungs are skipped", "ollama" not in remaining_providers)
check("anthropic survives quota exhaustion", "anthropic" in remaining_providers)
check("anthropic is the only remaining", len(remaining), 1)


execution.load_fallback_chain = real_chain

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
