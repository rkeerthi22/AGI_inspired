"""Research template functions for ad/SEO distribution engine (Phase 1).

Pure functions mapping (client_profile, seed_input) -> (spec, pass_criteria).
"""
from .keyword_research import generate_keyword_research_task
from .competitive_serp import generate_competitive_serp_task
from .ad_copy_variants import generate_ad_copy_variants_task
from .seo_content_brief import generate_seo_content_brief_task
from .landing_page_recco import generate_landing_page_recco_task

__all__ = [
    "generate_keyword_research_task",
    "generate_competitive_serp_task",
    "generate_ad_copy_variants_task",
    "generate_seo_content_brief_task",
    "generate_landing_page_recco_task",
]
