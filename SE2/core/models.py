"""Incident state models: uncertainty-aware, no asserted facts."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConfidenceValue:
    value: str
    confidence: float  # 0.0 - 1.0

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self):
        return {"value": self.value, "confidence": round(self.confidence, 4)}


@dataclass
class LocationValue:
    """Location with optional geo coords for map display."""
    value: str
    confidence: float  # 0.0 - 1.0
    lat: Optional[float] = None
    lng: Optional[float] = None

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self):
        d = {"value": self.value, "confidence": round(self.confidence, 4)}
        if self.lat is not None:
            d["lat"] = round(self.lat, 6)
        if self.lng is not None:
            d["lng"] = round(self.lng, 6)
        return d


@dataclass
class TimelineEvent:
    time: str
    claim_type: str
    value: str
    confidence: float
    source_text: str
    caller_id: Optional[str] = None  # groups all chunks from same caller (start→stop session)
    caller_info: Optional[dict] = None  # optional metadata: started_at, device_location, etc.

    def to_dict(self):
        d = {
            "time": self.time,
            "claim_type": self.claim_type,
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "source_text": self.source_text,
        }
        if self.caller_id is not None:
            d["caller_id"] = self.caller_id
        if self.caller_info is not None:
            d["caller_info"] = self.caller_info
        return d


@dataclass
class Incident:
    incident_id: str
    device_location: Optional[LocationValue] = None  # caller's device (lat/lng) — primary location
    locations: list = field(default_factory=list)  # list of LocationValue (mentioned places, optional coords)
    incident_type: Optional[ConfidenceValue] = None
    people_estimate: Optional[ConfidenceValue] = None
    hazards: list = field(default_factory=list)  # list of ConfidenceValue
    timeline: list = field(default_factory=list)  # append-only
    last_updated: Optional[str] = None

    def _merge_confidence(self, old: float, evidence: float) -> float:
        """Never reduce confidence. new = 1 - (1 - old) * (1 - evidence)."""
        new = 1.0 - (1.0 - old) * (1.0 - evidence)
        return max(old, new)
