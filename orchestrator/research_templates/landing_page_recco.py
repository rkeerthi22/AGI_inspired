"""Landing page recommendation template function for distribution engine (Phase 1, Step 1b).

Pure function mapping (client_profile, seed_input) -> (spec, pass_criteria).
Outputs grounded structural recommendations (hero, subhead, proof, CTA) tailored to intent.
"""
from __future__ import annotations

from typing import Any


def generate_landing_page_recco_task(
    client_profile: dict[str, Any],
    seed_input: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate (spec, pass_criteria) for a landing-page recommendation task.

    Arms deliverable_preflight and critic evaluation checks:
    - Minimum 2 distinct independent sources cited.
    - Mandatory bounded-failure section ('### Sources Attempted').
    - Structured recommendation detailing sections (Hero, Subhead, Proof, CTA).
    - Explanation of section purpose and conversion rationale grounded in intent and offer.
    - Disallows speculative placeholders (mandates 'not publicly disclosed').
    - Excludes client's declared forbidden claims.
    """
    client_id = client_profile.get("client_id", "unknown-client")
    display_name = client_profile.get("display_name", client_id)
    domain = client_profile.get("domain", "")
    offer = client_profile.get("offer", "")
    audience = client_profile.get("audience", "")
    landing_url = client_profile.get("landing_url", "")
    forbidden_claims = client_profile.get("forbidden_claims", [])

    target_kw = ""
    intent = "commercial"
    if seed_input:
        target_kw = str(seed_input.get("target_keyword", ""))
        intent = str(seed_input.get("intent", "commercial"))
        if "landing_url" in seed_input and seed_input["landing_url"]:
            landing_url = str(seed_input["landing_url"])
    if not target_kw:
        if client_profile.get("seed_keywords"):
            target_kw = str(client_profile["seed_keywords"][0])
        else:
            target_kw = domain

    spec = (
        f"Mission: Grounded landing page structure recommendation for client '{display_name}' (client_id: '{client_id}').\n"
        f"Target Keyword: {target_kw}\n"
        f"Search Intent: {intent}\n"
        f"Landing Page URL: {landing_url}\n"
        f"Vertical/Domain: {domain}\n"
        f"Core Offer: {offer}\n"
        f"Target Audience: {audience}\n"
        "\n"
        "Requirements:\n"
        "1. Recommend an optimal page structure tailored to search intent and offer (Hero, Subhead, Proof/Trust elements, CTA).\n"
        "   Note: This is a structural architectural recommendation with section rationale, NOT a full copy rewrite.\n"
        "2. Explain the purpose of each section and why it converts for this specific target audience.\n"
        "3. Recommend specific proof points, trust badges, and friction-reducing elements grounded in competitive research.\n"
        "4. Recommend primary and secondary Call-to-Action placements and wording.\n"
        "5. Cite verifiable sources for competitor page patterns, benchmark practices, or conversion evidence.\n"
    )

    forbidden_clause = ""
    if forbidden_claims:
        claims_str = ", ".join(f"'{c}'" for c in forbidden_claims)
        forbidden_clause = f"\nDeliverable must not contain any forbidden claim: {claims_str}."

    pass_criteria = (
        "Deliverable must provide a structured recommendation table or outline with columns or sections for:\n"
        "Section Name (Hero, Subhead, Proof/Trust, CTA), Purpose, Why-It-Converts Rationale, and Evidence/Source.\n"
        "\n"
        "Recommendations must be specifically grounded in target keyword intent and client offer, not generic filler.\n"
        "At least 2 distinct independent sources cited.\n"
        "Deliverable must include a bounded-failure section titled '### Sources Attempted' naming every source attempted with status (rating-obtained/blocked/unavailable).\n"
        "For unavailable data points or unverified conversion metrics, explicitly enter 'not publicly disclosed' rather than speculative placeholders or empty cells."
        f"{forbidden_clause}"
    )

    return spec, pass_criteria
