"""Ad copy variants template function for distribution engine (Phase 1, Step 1b).

Pure function mapping (client_profile, seed_input) -> (spec, pass_criteria).
Outputs grounded multi-format ad copy with strict character limits and brand voice constraints.
"""
from __future__ import annotations

from typing import Any


def generate_ad_copy_variants_task(
    client_profile: dict[str, Any],
    seed_input: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate (spec, pass_criteria) for an ad-copy variants task.

    Arms deliverable_preflight and critic evaluation checks:
    - Minimum 2 distinct independent sources cited (grounding offer/competitor angles).
    - Mandatory bounded-failure section ('### Sources Attempted').
    - Strict character limits per format (RSA headlines <= 30 chars, descriptions <= 90 chars).
    - Explicit Call-to-Action (CTA) in every variant.
    - Brand voice adherence ('brand_voice').
    - Unsubstantiated superlatives banned ('best', '#1', 'guaranteed').
    - Disallows speculative placeholders (mandates 'not publicly disclosed').
    - Excludes client's declared forbidden claims.
    """
    client_id = client_profile.get("client_id", "unknown-client")
    display_name = client_profile.get("display_name", client_id)
    domain = client_profile.get("domain", "")
    offer = client_profile.get("offer", "")
    audience = client_profile.get("audience", "")
    landing_url = client_profile.get("landing_url", "")
    brand_voice = client_profile.get("brand_voice", "Professional, clear, trustworthy")
    forbidden_claims = client_profile.get("forbidden_claims", [])

    target_kw = ""
    intent = "transactional"
    if seed_input:
        target_kw = str(seed_input.get("target_keyword", ""))
        intent = str(seed_input.get("intent", "transactional"))
    if not target_kw:
        if client_profile.get("seed_keywords"):
            target_kw = str(client_profile["seed_keywords"][0])
        else:
            target_kw = domain

    spec = (
        f"Mission: Grounded ad copy variant generation for client '{display_name}' (client_id: '{client_id}').\n"
        f"Target Keyword: {target_kw}\n"
        f"Search Intent: {intent}\n"
        f"Client Vertical: {domain}\n"
        f"Core Offer: {offer}\n"
        f"Target Audience: {audience}\n"
        f"Landing URL: {landing_url}\n"
        f"Brand Voice: {brand_voice}\n"
        "\n"
        "Requirements:\n"
        "1. Generate distinct ad copy variants across ad formats:\n"
        "   - Responsive Search Ads (RSA): headlines and descriptions.\n"
        "   - Expanded Search / Text Ads.\n"
        "   - Performance Max (PMax): short headlines, long headlines, and descriptions.\n"
        "2. Strict character count limits:\n"
        "   - Headlines: maximum 30 characters each.\n"
        "   - Descriptions: maximum 90 characters each.\n"
        "   - Long headlines (PMax): maximum 90 characters each.\n"
        "3. Every variant must include an explicit Call-to-Action (CTA) (e.g., 'Call Today', 'Get a Free Quote', 'Schedule Online').\n"
        "4. Tone and copy must match the specified brand voice.\n"
        "5. Ground messaging in verified service features and competitor research (cite verifiable source URLs).\n"
    )

    forbidden_clause = ""
    if forbidden_claims:
        claims_str = ", ".join(f"'{c}'" for c in forbidden_claims)
        forbidden_clause = f"\nDeliverable must not contain any forbidden claim: {claims_str}."

    pass_criteria = (
        "Deliverable must provide structured ad-copy tables specifying Format, Component (Headline, Long Headline, Description), Copy Text, Character Count, and CTA.\n"
        "\n"
        "Strict character limits must be respected: headlines <= 30 characters; descriptions <= 90 characters; long headlines <= 90 characters.\n"
        "Every ad copy line must include explicit character count notation confirming compliance with limits.\n"
        "Every variant must include an explicit Call-to-Action (CTA).\n"
        f"Copy must align with client brand voice: '{brand_voice}'.\n"
        "Unsubstantiated superlatives ('best', '#1', 'guaranteed') are strictly forbidden unless explicitly substantiated in client profile offer.\n"
        "At least 2 distinct independent sources cited.\n"
        "Deliverable must include a bounded-failure section titled '### Sources Attempted' naming every source attempted with status (rating-obtained/blocked/unavailable).\n"
        "For unavailable data points or unverified metrics, explicitly enter 'not publicly disclosed' rather than speculative placeholders or empty cells."
        f"{forbidden_clause}"
    )

    return spec, pass_criteria
