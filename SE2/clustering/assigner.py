"""
Combine embedding similarity + LLM same-incident score + time proximity
into one match score. Assign new report to best incident or create new.
"""

import logging
import math
import uuid

from clustering.embedding import get_embedding
from clustering.time_proximity import time_proximity_score
from clustering.same_incident_llm import llm_same_incident_score
from clustering.geo_proximity import geo_proximity_score, device_geo_snippet

logger = logging.getLogger("incident_api.clustering.assigner")

# Weights for combined score: embedding_sim, llm_score, time_score, geo_score (must sum to 1)
DEFAULT_WEIGHTS = (0.35, 0.35, 0.15, 0.15)  # embedding, llm, time, geo
DEFAULT_THRESHOLD = 0.55  # min combined score to assign to existing incident


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def state_to_summary_text(state: dict) -> str:
    """Build a short summary string from incident state dict for embedding/LLM. Includes device_geo when present."""
    parts = []
    if state.get("incident_type") and isinstance(state["incident_type"], dict):
        parts.append("incident_type: " + state["incident_type"].get("value", ""))
    for loc in state.get("locations") or []:
        if isinstance(loc, dict) and loc.get("value"):
            parts.append("location: " + loc["value"])
    if state.get("device_location") and isinstance(state["device_location"], dict):
        dl = state["device_location"]
        parts.append("device: " + dl.get("value", ""))
        geo = device_geo_snippet(dl.get("lat"), dl.get("lng"))
        if geo:
            parts.append("device_geo: " + geo)
    if state.get("people_estimate") and isinstance(state["people_estimate"], dict):
        parts.append("people: " + str(state["people_estimate"].get("value", "")))
    for h in state.get("hazards") or []:
        if isinstance(h, dict) and h.get("value"):
            parts.append("hazard: " + h["value"])
    return " | ".join(parts) if parts else "(no summary)"


def claims_to_summary_text(
    claims: list[dict],
    chunk_preview: str = "",
    device_lat: float | None = None,
    device_lng: float | None = None,
) -> str:
    """Build report summary from claims + chunk preview + optional device_geo so same place clusters together."""
    parts = []
    for c in claims or []:
        ctype = c.get("claim_type")
        value = c.get("value")
        if ctype and value is not None:
            parts.append(f"{ctype}: {value}")
    geo = device_geo_snippet(device_lat, device_lng)
    if geo:
        parts.append("device_geo: " + geo)
    text = " | ".join(parts) if parts else ("device_geo: " + geo if geo else "")
    if chunk_preview:
        text = (text + " | transcript: " + chunk_preview[:200].strip()) if text else ("transcript: " + chunk_preview[:200].strip())
    return text or "(no summary)"


def combined_match_score(
    embedding_sim: float,
    llm_score: float,
    time_score: float,
    geo_score: float = 0.5,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
) -> float:
    """Single score from embedding + LLM + time + geo proximity."""
    w1, w2, w3, w4 = weights
    return round(w1 * embedding_sim + w2 * llm_score + w3 * time_score + w4 * geo_score, 4)


def find_best_incident(
    report_summary: str,
    report_time_iso: str,
    incident_entries: list[tuple[str, dict, str | None]],
    *,
    report_lat: float | None = None,
    report_lng: float | None = None,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    threshold: float = DEFAULT_THRESHOLD,
    use_embedding: bool = True,
    use_llm: bool = True,
    embedding_cache: dict[str, list[float]] | None = None,
) -> tuple[str | None, float]:
    """
    Compare report to existing incidents. Return (best_incident_id, score) or (None, 0) for new.
    incident_entries: list of (incident_id, state_dict, last_updated_iso).
    report_lat, report_lng: caller device location so "first floor" and "windsor building" from same spot cluster.
    embedding_cache: optional dict to read/write incident embeddings (avoids re-embedding).
    """
    if not incident_entries:
        return None, 0.0

    report_embedding = get_embedding(report_summary) if use_embedding else None
    if use_embedding and report_embedding is None:
        use_embedding = False

    best_id: str | None = None
    best_score = 0.0
    cache = embedding_cache if embedding_cache is not None else {}

    for incident_id, state_dict, last_updated in incident_entries:
        inc_summary = state_to_summary_text(state_dict)
        if not inc_summary or inc_summary == "(no summary)":
            continue

        # Embedding similarity (use cache if available)
        if use_embedding and report_embedding is not None:
            inc_embedding = cache.get(incident_id)
            if inc_embedding is None:
                inc_embedding = get_embedding(inc_summary)
                if inc_embedding is not None:
                    cache[incident_id] = inc_embedding
            emb_sim = _cosine_sim(report_embedding, inc_embedding) if inc_embedding else 0.5
        else:
            emb_sim = 0.5  # neutral when no embedding

        # LLM same-incident score
        if use_llm:
            llm_s = llm_same_incident_score(inc_summary, report_summary)
        else:
            llm_s = 0.5

        # Time proximity
        time_s = time_proximity_score(report_time_iso, last_updated or "")

        # Geo proximity: same/similar device location → same incident even if text differs ("first floor" vs "windsor building")
        geo_s = geo_proximity_score(report_lat, report_lng, state_dict)

        score = combined_match_score(emb_sim, llm_s, time_s, geo_s, weights=weights)
        if score > best_score:
            best_score = score
            best_id = incident_id

    if best_score >= threshold and best_id is not None:
        return best_id, best_score
    return None, best_score


def new_incident_id() -> str:
    """Generate a new incident id (e.g. incident-<uuid4>)."""
    return "incident-" + uuid.uuid4().hex[:12]
