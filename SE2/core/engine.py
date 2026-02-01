"""Stream processing: apply extracted claims to incident.
Confidence updated via Bayesian posterior with LLM Judge support scores.
"""

import re
from datetime import datetime
from typing import Optional, Callable

from core.models import Incident, ConfidenceValue, LocationValue, TimelineEvent

MAX_CONFIDENCE = 0.95
DEFAULT_PRIOR = 0.40  # prior for a brand-new claim when no state yet


def bayesian_posterior(prior: float, likelihood: float) -> float:
    """
    P(H|E) = P(H)*P(E|H) / ( P(H)*P(E|H) + (1-P(H))*P(E|¬H) ).
    We interpret likelihood as P(E|H). Assume P(E|¬H) = 1 - P(E|H) (symmetric).
    So posterior = (prior * L) / (prior * L + (1-prior) * (1-L)).
    """
    if likelihood <= 0:
        return max(0.0, prior - 0.1)  # weak decrease
    if likelihood >= 1:
        return min(1.0, prior + 0.2)
    denom = prior * likelihood + (1.0 - prior) * (1.0 - likelihood)
    if denom <= 0:
        return prior
    return min(MAX_CONFIDENCE, max(0.05, (prior * likelihood) / denom))


def _claim_id(claim_type: str, value: str) -> str:
    return f"{claim_type}::{value.strip().lower()}"


def _append_timeline(incident: Incident, claim: dict) -> None:
    incident.timeline.append(TimelineEvent(
        time=claim["timestamp"],
        claim_type=claim["claim_type"],
        value=claim["value"],
        confidence=claim["confidence"],
        source_text=claim["source_text"],
        caller_id=claim.get("caller_id"),
        caller_info=claim.get("caller_info"),
    ))


def _get_support(judge_scores: dict[str, float], claim_type: str, value: str) -> float:
    cid = _claim_id(claim_type, str(value))
    if cid in judge_scores:
        return judge_scores[cid]
    # Fallback: Judge may return keys with spaces; normalize and try again
    alt = re.sub(r"[\s_]+", "_", cid)
    return judge_scores.get(alt, 0.55)


def _apply_claim(incident: Incident, claim: dict, judge_scores: Optional[dict[str, float]] = None) -> None:
    ctype = claim["claim_type"]
    value = claim["value"]
    ts = claim["timestamp"]
    support = _get_support(judge_scores or {}, ctype, value) if judge_scores is not None else 0.55

    _append_timeline(incident, claim)
    incident.last_updated = ts

    if ctype == "location":
        existing = next((loc for loc in incident.locations if loc.value.lower().strip() == value.lower().strip()), None)
        lat = claim.get("lat")
        lng = claim.get("lng")
        prior = existing.confidence if existing is not None else DEFAULT_PRIOR
        conf = round(bayesian_posterior(prior, support), 4)
        if existing is None:
            incident.locations.append(LocationValue(value=value, confidence=conf, lat=lat, lng=lng))
        else:
            existing.confidence = conf
            if lat is not None:
                existing.lat = lat
            if lng is not None:
                existing.lng = lng

    elif ctype == "incident_type":
        # Probability is decided by Judge support + Bayesian update, not by the claim's confidence.
        prior = incident.incident_type.confidence if incident.incident_type else DEFAULT_PRIOR
        conf = round(bayesian_posterior(prior, support), 4)
        incident.incident_type = ConfidenceValue(value=value, confidence=conf)

    elif ctype == "people_count":
        prior = incident.people_estimate.confidence if incident.people_estimate else DEFAULT_PRIOR
        conf = round(bayesian_posterior(prior, support), 4)
        incident.people_estimate = ConfidenceValue(value=value, confidence=conf)

    elif ctype == "hazard":
        existing = next((h for h in incident.hazards if h.value.lower() == value.lower()), None)
        prior = existing.confidence if existing else DEFAULT_PRIOR
        conf = round(bayesian_posterior(prior, support), 4)
        if existing is None:
            incident.hazards.append(ConfidenceValue(value=value, confidence=conf))
        else:
            existing.confidence = conf


def apply_claims(incident: Incident, claims: list[dict], judge_scores: Optional[dict[str, float]] = None) -> None:
    """Apply claims to incident. If judge_scores is provided, confidence = Bayesian(prior, support)."""
    for claim in claims:
        _apply_claim(incident, claim, judge_scores=judge_scores)


def get_incident_state_dict(incident: Incident) -> dict:
    """Serialize incident for API/dashboard (device_location + locations include lat/lng when set)."""
    return {
        "incident_id": incident.incident_id,
        "last_updated": incident.last_updated,
        "device_location": incident.device_location.to_dict() if incident.device_location else None,
        "locations": [loc.to_dict() for loc in incident.locations],
        "incident_type": incident.incident_type.to_dict() if incident.incident_type else None,
        "people_estimate": incident.people_estimate.to_dict() if incident.people_estimate else None,
        "hazards": [h.to_dict() for h in incident.hazards],
        "timeline_count": len(incident.timeline),
        "timeline": [e.to_dict() for e in incident.timeline],
    }


def add_demo_locations(incident: Incident, demo_list: list[dict]) -> None:
    """Append demo/simulated locations with lat/lng for map display."""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in demo_list:
        value = entry.get("value", "Demo location")
        lat = entry.get("lat")
        lng = entry.get("lng")
        if lat is None or lng is None:
            continue
        existing = next((loc for loc in incident.locations if loc.value.lower().strip() == value.lower().strip()), None)
        if existing is not None:
            existing.lat = lat
            existing.lng = lng
            continue
        conf = min(MAX_CONFIDENCE, DEFAULT_PRIOR + 0.1)
        incident.locations.append(LocationValue(value=value, confidence=conf, lat=lat, lng=lng))
        incident.timeline.append(TimelineEvent(time=now, claim_type="location", value=value, confidence=conf, source_text="[demo] simulated location"))
    incident.last_updated = now


def set_device_location(incident: Incident, lat: float, lng: float, confidence: float = 0.9) -> None:
    """Set the caller's device location (primary location)."""
    incident.device_location = LocationValue(value="Device", confidence=confidence, lat=lat, lng=lng)
    incident.last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# Optional: single-incident process_text_chunk for CLI (uses extractor passed in)
def process_text_chunk(
    text: str,
    incident: Incident,
    extract_claims_fn: Callable[[str], list[dict]],
) -> Incident:
    """Run extraction and apply claims. Probabilities are updated via merge logic."""
    claims = extract_claims_fn(text)
    apply_claims(incident, claims)
    return incident
