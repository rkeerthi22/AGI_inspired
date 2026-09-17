"""Competitive and SERP research template for distribution engine (Phase 1, Step 1b).

Pure function mapping (client_profile, seed_input) -> (spec, pass_criteria).
Grounds competitor organic rankers, ad messaging angles, and content gaps.
"""
from __future__ import annotations

from typing import Any


def generate_competitive_serp_task(
    client_profile: dict[str, Any],
    seed_input: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate (spec, pass_criteria) for competitive / SERP analysis.

    Arms deliverable_preflight checks:
    - Minimum 2 distinct independent sources cited.
    - Mandatory bounded-failure section ('### Sources Attempted').
    - Disallows speculative placeholders (mandates 'not publicly disclosed').
    - Disallows fabricated ranking positions; requires source URLs per competitor.
    - Excludes client's declared forbidden claims.
    """
    client_id = client_profile.get("client_id", "unknown-client")
    display_name = client_profile.get("display_name", client_id)
    domain = client_profile.get("domain", "")
    offer = client_profile.get("offer", "")
    audience = client_profile.get("audience", "")
    competitors = client_profile.get("competitors", [])
    forbidden_claims = client_profile.get("forbidden_claims", [])

    target_kw = ""
    if seed_input and "target_keyword" in seed_input:
        target_kw = str(seed_input["target_keyword"])
    elif seed_input and "seed_keywords" in seed_input and seed_input["seed_keywords"]:
        target_kw = str(seed_input["seed_keywords"][0])
    elif client_profile.get("seed_keywords"):
        target_kw = str(client_profile["seed_keywords"][0])
    else:
        target_kw = domain

    spec = (
        f"Mission: Grounded competitive SERP research for client '{display_name}' (client_id: '{client_id}').\n"
        f"Target Keyword: {target_kw}\n"
        f"Domain/Vertical: {domain}\n"
        f"Core Offer: {offer}\n"
        f"Target Audience: {audience}\n"
        f"Known Competitors: {', '.join(competitors) if competitors else 'Discover via SERP analysis'}\n"
        "\n"
        "Requirements:\n"
        "1. Identify top organic rankers and search competitors targeting this query.\n"
        "2. Analyze their visible ad messaging angles, value propositions, and positioning.\n"
        "3. Identify content gaps in competitors' ranking assets.\n"
        "4. Articulate strategic opportunities for the client to capture search share.\n"
        "5. Ground every competitor observation with a verifiable source URL.\n"
    )

    forbidden_clause = ""
    if forbidden_claims:
        claims_str = ", ".join(f"'{c}'" for c in forbidden_claims)
        forbidden_clause = f"\nDeliverable must not contain any forbidden claim: {claims_str}."

    pass_criteria = (
        "Deliverable must provide a markdown table with columns:\n"
        "| Competitor / Ranker | SERP Angle | Content Gap | Opportunity for Us | Source URL |\n"
        "\n"
        "Every competitor row must cite a verifiable source URL.\n"
        "No fabricated or invented SERP ranking positions.\n"
        "At least 2 distinct independent sources cited.\n"
        "Deliverable must include a bounded-failure section titled '### Sources Attempted' naming every source attempted with status (rating-obtained/blocked/unavailable).\n"
        "For unavailable data points (such as unverified traffic estimates or metrics), explicitly enter 'not publicly disclosed' rather than speculative placeholders or empty cells."
        f"{forbidden_clause}"
    )

    return spec, pass_criteria
