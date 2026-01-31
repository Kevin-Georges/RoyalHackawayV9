"""Evidence extractors: regex (fallback) and OpenAI (with hallucination handling)."""

from extractors.regex_extractor import extract_claims as regex_extract_claims
from extractors.openai_extractor import extract_claims as openai_extract_claims

__all__ = ["regex_extract_claims", "openai_extract_claims"]
