"""Clustering: embeddings + LLM same-incident score + time proximity → combined match score."""

from clustering.embedding import get_embedding
from clustering.time_proximity import time_proximity_score
from clustering.same_incident_llm import llm_same_incident_score
from clustering.assigner import (
    state_to_summary_text,
    claims_to_summary_text,
    combined_match_score,
    find_best_incident,
)

__all__ = [
    "get_embedding",
    "time_proximity_score",
    "llm_same_incident_score",
    "state_to_summary_text",
    "claims_to_summary_text",
    "combined_match_score",
    "find_best_incident",
]
