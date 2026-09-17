"""SEO content brief template function for distribution engine (Phase 1, Step 1b).

Pure function mapping (client_profile, seed_input) -> (spec, pass_criteria).
Outputs grounded SEO content briefs with structured outlines and entity coverage.
"""
from __future__ import annotations

from typing import Any


def generate_seo_content_brief_task(
    client_profile: dict[str, Any],
    seed_input: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate (spec, pass_criteria) for an SEO content brief task.

    Arms deliverable_preflight and critic evaluation checks:
    - Minimum 2 distinct independent sources cited.
    - Mandatory bounded-failure section ('### Sources Attempted').
    - Structured outline with H1, H2, and H3 sections.
    - Mandatory entity coverage list with citations per entity.
    - Disallows speculative placeholders (mandates 'not publicly disclosed').
    - Disallows fabricated search volume numbers.
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
    intent = "informational"
    if seed_input:
        target_kw = str(seed_input.get("target_keyword", ""))
        intent = str(seed_input.get("intent", "informational"))
    if not target_kw:
        if client_profile.get("seed_keywords"):
            target_kw = str(client_profile["seed_keywords"][0])
        else:
            target_kw = domain

    spec = (
        f"Mission: Grounded SEO content brief for client '{display_name}' (client_id: '{client_id}').\n"
        f"Target Keyword: {target_kw}\n"
        f"Search Intent: {intent}\n"
        f"Domain/Vertical: {domain}\n"
        f"Core Offer: {offer}\n"
        f"Target Audience: {audience}\n"
        f"Related Site Landing Page: {landing_url}\n"
        "\n"
        "Requirements:\n"
        "1. Create a structured content outline utilizing H1, H2, and H3 markdown headings.\n"
        "2. Provide an entity coverage list of critical topical entities, semantic concepts, and subtopics to address.\n"
        "3. Provide internal link suggestions connecting to client landing pages or domain resources.\n"
        "4. Specify a recommended word-count band justified by ranking competitor content depth.\n"
        "5. Cite verifiable sources for topical entities, search intent rationale, and competitor benchmarks.\n"
    )

    forbidden_clause = ""
    if forbidden_claims:
        claims_str = ", ".join(f"'{c}'" for c in forbidden_claims)
        forbidden_clause = f"\nDeliverable must not contain any forbidden claim: {claims_str}."

    pass_criteria = (
        "Deliverable must provide a structured markdown outline with H1, H2, and H3 headings.\n"
        "Deliverable must include an entity coverage list detailing key entities and questions to address, each grounded in a cited source URL.\n"
        "Deliverable must provide internal link suggestions and a recommended word-count band.\n"
        "At least 2 distinct independent sources cited.\n"
        "Deliverable must include a bounded-failure section titled '### Sources Attempted' naming every source attempted with status (rating-obtained/blocked/unavailable).\n"
        "For unavailable metrics (such as unverified search volume), explicitly enter 'not publicly disclosed' rather than speculative placeholders or empty cells.\n"
        "No fabricated search-volume numbers (invented metrics such as '1.23M searches/mo' or unverified search numbers are strictly forbidden)."
        f"{forbidden_clause}"
    )

    return spec, pass_criteria
