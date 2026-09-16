"""Research template functions for ad/SEO distribution engine (Phase 1).

Pure functions mapping (client_profile, seed_input) -> (spec, pass_criteria).
"""
from .keyword_research import generate_keyword_research_task

__all__ = ["generate_keyword_research_task"]
