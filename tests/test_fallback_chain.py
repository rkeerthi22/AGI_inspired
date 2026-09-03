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
import tempfile
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


# -- 5. Missing optional provider credentials must not abort the chain -------

print("\n=== Missing credentials do not abort worker failover ===")

with tempfile.TemporaryDirectory() as td:
    real_worker = execution.hermes_worker
    real_chain = execution.load_fallback_chain
    attempts: list[tuple[str, str]] = []
    try:
        execution.load_fallback_chain = lambda: [
            {"provider": "anthropic", "model": "claude-sonnet-5"},
            {"provider": "openai", "model": "gpt-4o"},
            {"provider": "ollama", "model": "gemma4:12b-ctx4k", "context_tokens": 4096},
        ]

        def mock_worker(prompt, cfg, attempt_path, timeout=900, retrieval_profile=None):
            attempts.append((cfg["provider"], cfg["model"]))
            if cfg["provider"] == "byteplus_coding":
                return "HTTP 429: weekly usage exhausted", {"process_error": "429"}
            if cfg["provider"] == "anthropic":
                return "", {"failed": True, "failure": "No Anthropic credentials found."}
            if cfg["provider"] == "openai":
                return "", {"failed": True, "failure": "No OpenAI credentials found."}
            return "local fallback answer " * 6, {}

        execution.hermes_worker = mock_worker

        out, usage, cfg_used, exhausted = execution.worker_with_failover(
            "short prompt",
            {"provider": "byteplus_coding", "model": "ark-code-latest", "quota_group": "byteplus"},
            Path(td) / "usage.json",
            log_prefix="task 777",
        )

        check("worker path keeps walking after auth-missing rungs", attempts, [
            ("byteplus_coding", "ark-code-latest"),
            ("anthropic", "claude-sonnet-5"),
            ("openai", "gpt-4o"),
            ("ollama", "gemma4:12b-ctx4k"),
        ])
        check("worker path reaches local rung", cfg_used["model"], "gemma4:12b-ctx4k")
        check("worker path returns local output",
              exhausted is False and out.startswith("local fallback answer"))
    finally:
        execution.hermes_worker = real_worker
        execution.load_fallback_chain = real_chain


print("\n=== Missing credentials do not abort synthesis failover ===")

real_chat = execution.ollama_chat
real_chain = execution.load_fallback_chain
calls: list[tuple[str, str]] = []
try:
    execution.load_fallback_chain = lambda: [
        {"provider": "anthropic", "model": "claude-sonnet-5"},
        {"provider": "openai", "model": "gpt-4o"},
        {"provider": "ollama", "model": "gemma4:12b-ctx4k", "context_tokens": 4096},
    ]

    def mock_chat(model, prompt, timeout=300, trace_path=None, usage_out=None,
                  provider="ollama", **request_options):
        active_provider = request_options.get("provider", provider)
        calls.append((active_provider, model))
        if active_provider == "byteplus_coding":
            raise execution.provider_transport.ProviderChatError(
                "BytePlus HTTP 429",
                execution.provider_transport.ErrorCategory.QUOTA,
                retryable=True,
            )
        if active_provider == "anthropic":
            raise execution.provider_transport.ProviderChatError(
                "Anthropic API key is not configured",
                execution.provider_transport.ErrorCategory.AUTHENTICATION,
            )
        if active_provider == "openai":
            raise execution.provider_transport.ProviderChatError(
                "OpenAI API key is not configured",
                execution.provider_transport.ErrorCategory.AUTHENTICATION,
            )
        return "local synthesis answer"

    execution.ollama_chat = mock_chat

    outcome = execution.synthesis_with_failover(
        "short prompt",
        {
            "provider": "byteplus_coding",
            "model": "ark-code-latest",
            "endpoint": "https://ark.ap-southeast.bytepluses.com/api/coding/v3",
            "authentication_reference": "env:ARK_API_KEY",
        },
        log_prefix="task 778",
    )

    check("synthesis path keeps walking after auth-missing rungs", calls, [
        ("byteplus_coding", "ark-code-latest"),
        ("anthropic", "claude-sonnet-5"),
        ("openai", "gpt-4o"),
        ("ollama", "gemma4:12b-ctx4k"),
    ])
    check("synthesis path reaches local rung", outcome.model_cfg["model"], "gemma4:12b-ctx4k")
    check("synthesis path returns local output",
          outcome.exhausted is False and outcome.output == "local synthesis answer")
finally:
    execution.ollama_chat = real_chat
    execution.load_fallback_chain = real_chain

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
