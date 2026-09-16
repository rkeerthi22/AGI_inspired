"""Keyword research template function for distribution engine (Phase 1, Step 1a).

Pure function mapping (client_profile, seed_input) -> (spec, pass_criteria).
Outputs grounded ad/SEO keyword research specifications and arms preflight criteria.
"""
from __future__ import annotations

from typing import Any

VALID_INTENTS = ("commercial", "transactional", "informational", "navigational")
VALID_FUNNEL_STAGES = ("awareness", "consideration", "conversion")


def generate_keyword_research_task(
    client_profile: dict[str, Any],
    seed_input: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate (spec, pass_criteria) for a keyword research task.

    Arms deliverable_preflight checks:
    - Minimum 2 distinct independent sources cited.
    - Mandatory bounded-failure section ('### Sources Attempted').
    - Disallows speculative placeholders (mandates 'not publicly disclosed').
    - Strict classification into valid intents and funnel stages.
    - Excludes client's declared forbidden claims.
    """
    client_id = client_profile.get("client_id", "unknown-client")
    display_name = client_profile.get("display_name", client_id)
    domain = client_profile.get("domain", "")
    offer = client_profile.get("offer", "")
    audience = client_profile.get("audience", "")
    landing_url = client_profile.get("landing_url", "")
    competitors = client_profile.get("competitors", [])
    forbidden_claims = client_profile.get("forbidden_claims", [])

    seeds = list(client_profile.get("seed_keywords", []))
    if seed_input and "seed_keywords" in seed_input:
        for s in seed_input["seed_keywords"]:
            if s not in seeds:
                seeds.append(s)

    spec = (
        f"Mission: Grounded keyword research for client '{display_name}' (client_id: '{client_id}').\n"
        f"Domain/Vertical: {domain}\n"
        f"Core Offer: {offer}\n"
        f"Target Audience: {audience}\n"
        f"Landing Page: {landing_url}\n"
        f"Competitors to analyze: {', '.join(competitors) if competitors else 'None specified'}\n"
        f"Seed Keywords: {', '.join(seeds) if seeds else 'General domain discovery'}\n"
        "\n"
        "Requirements:\n"
        "1. Expand seed keywords into related high-intent search queries based on live web research.\n"
        "2. For each query, classify Search Intent strictly into: commercial, transactional, informational, or navigational.\n"
        "3. Map each query to a Funnel Stage strictly into: awareness, consideration, or conversion.\n"
        "4. Provide a concrete strategic rationale connecting the query intent to the client offer.\n"
        "5. Cite verifiable sources for keyword observations, search queries, or competitor mentions.\n"
    )

    forbidden_clause = ""
    if forbidden_claims:
        claims_str = ", ".join(f"'{c}'" for c in forbidden_claims)
        forbidden_clause = f"\nDeliverable must not contain any forbidden claim: {claims_str}."

    pass_criteria = (
        "Deliverable must provide a markdown table with columns:\n"
        "| Keyword | Intent | Funnel Stage | Rationale | Source URL |\n"
        "\n"
        "Intent classification must be one of: commercial, transactional, informational, navigational.\n"
        "Funnel stage must be one of: awareness, consideration, conversion.\n"
        "At least 2 distinct independent sources cited.\n"
        "Deliverable must include a bounded-failure section titled '### Sources Attempted' naming every source attempted with status (rating-obtained/blocked/unavailable).\n"
        "For unavailable data points, explicitly enter 'not publicly disclosed' rather than speculative placeholders or empty cells."
        f"{forbidden_clause}"
    )

    return spec, pass_criteria
