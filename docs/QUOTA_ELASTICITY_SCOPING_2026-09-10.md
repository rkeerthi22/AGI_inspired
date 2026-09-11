# Quota Elasticity & Upstream Redundancy Scoping Note

**Document ID:** `NOTE-QUOTA-ELASTICITY-SCOPING-2026-09-10`  
**Date:** 2026-09-10  
**Author:** Gemini CLI (Independent Principal Architect)  
**Status:** Architectural Scoping Note & Operator Decision Brief  
**Classification:** Strategic Architecture (No Code Changes — OmniRoute Remains Locked)

---

## 1. Executive Summary & Problem Statement

During live cohort evaluation (Tasks 153–158), upstream cloud provider rate-limiting (BytePlus HTTP 429 / concurrency caps) repeatedly throttled the research and critic pipelines. With only a single external provider configured, rate-limit storms either forced lengthy linear backoffs or caused tasks to fail closed under quota exhaustion.

### Crucial Framing: Deficit C is NOT a Windows-Native Deficit
Deficit C (quota elasticity) is **strictly an upstream provider strategy gap**, completely orthogonal to the Windows-native single-host architecture choice:
* Deficit B (off-machine audit immutability) is a direct consequence of the single-host architecture (key and logs share a single machine).
* Deficit A (multi-process browser containment) was an OS token limitation resolved by Path A.
* Deficit C is solely a provider topology limitation: relying on a single upstream API endpoint without a diversified secondary upstream.

---

## 2. Current Architecture Baseline

The current model topology in `config/models.yaml` consists of:
1. **Primary Cloud Provider (Critic & Manager):** BytePlus Ark (`ep-20250210-deepseek-r1` and `doubao-1.5-pro-32k`).
2. **Local Fallback Worker (Research Worker):** Local Ollama (`qwen3.5:2b-instruct-q4_K_M-ctx64k`, upgraded under F137 from 1.5B).
3. **Mechanical Critic Filter:** Independent evaluator running tool-free citecheck verification.

When BytePlus Ark enforces rate limits:
- The critic and manager paths have **zero alternative cloud providers**.
- While local Ollama serves as a local worker fallback, running large reasoning models (R1/Doubao-Pro) locally exceeds the host hardware capacity (VRAM/compute floor).
- OmniRoute was previously proposed as a dynamic multi-provider router, but was placed under strict architectural lockdown.

---

## 3. The 4 Locked OmniRoute Prerequisite Conditions

OmniRoute remains **STRICTLY HELD and LOCKED**. It must NOT be unlocked until all four conditions are met and empirically verified:

| # | Condition | Architectural Requirement | Current Status |
| :-: | :--- | :--- | :--- |
| **1** | **Constrained Topology** | Explicit, acyclic provider routing graph. Zero dynamic multi-hop redirection or opaque gateway proxying. | **UNMET** (Spec undefined) |
| **2** | **Provenance Transparency** | Every inference response must cryptographically record the authoritative provider identity, upstream endpoint, token usage, and latency in `runs/task{tid}_*.usage.json`. | **UNMET** (Only single-provider metadata implemented) |
| **3** | **Double-Retry Verification** | Bounded retry budget with exponential jittered backoff preventing upstream request storms or thundering herds upon 429s. | **PARTIAL** (`provider_chat.py` has backoff, but lacks multi-provider coordination) |
| **4** | **Adversarial Peer Review** | Formal architectural sign-off by all three independent review perspectives (Gemini CLI, Claude Code, and Codex Astra / Hermes). | **HOLD** (Awaiting formal review) |

---

## 4. Minimal Compliant Path: Cold Failover (Not OmniRoute)

The minimal, lowest-risk, and most transparent path to resolve Deficit C does **NOT** require deploying OmniRoute. Instead, the harness should implement a simple, deterministic **Cold Secondary Cloud Provider**:

### Proposed Architecture: Deterministic Primary-Secondary Cloud Failover
```
                      ┌────────────────────────────────────────┐
                      │    Task Runner / Provider Chat Client  │
                      └───────────────────┬────────────────────┘
                                          │
                   ┌──────────────────────┴──────────────────────┐
                   │                                             │
                   ▼ (Primary)                                   ▼ (Failover on 429 / 503)
     ┌───────────────────────────┐                 ┌───────────────────────────┐
     │  BytePlus Ark API         │                 │  Secondary Cloud Provider │
     │  (DeepSeek-R1 / Doubao)   │                 │  (DeepSeek Official /     │
     │                           │                 │   Groq / Azure OpenAI)    │
     └───────────────────────────┘                 └───────────────────────────┘
```

### Advantages of Cold Failover:
1. **Zero Dynamic Routing Complexity:** No smart proxy, no hidden DNS redirection, no proxy credential leasing.
2. **Deterministic Provenance:** If Primary returns 429 after 2 retries, fail over to Secondary and explicitly tag `provider_failover: true` and `actual_provider: "deepseek_direct"` in the trajectory.
3. **Independent Quota Pools:** The secondary provider operates under a completely separate organization, billing account, and infrastructure pool, providing genuine quota elasticity.

---

## 5. Recommendation & Decision Flag

* **Immediate Action:** Keep OmniRoute **LOCKED**. Make no code modifications to routing or provider dispatch in this cycle.
* **Operator Action Required:** Provision an API key with an independent second provider (e.g. DeepSeek direct platform or Azure OpenAI) and store the secret in Windows Credential Manager under `AGI_like/secondary_provider_key`.
* **Next Architectural Cycle:** Design a minimal, transparent fallback mechanism directly within `orchestrator/provider_chat.py` adhering to the four transparency rules before considering any general-purpose routing layer.
