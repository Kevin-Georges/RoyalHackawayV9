"""
MVP: Uncertainty-aware incident summary from emergency call transcript stream.
No asserted facts; only accumulated evidence with explicit confidence.
Extraction implemented with regex and text parsing (no mocks).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# -----------------------------------------------------------------------------
# 1. Incident State Object
# -----------------------------------------------------------------------------

@dataclass
class ConfidenceValue:
    value: str
    confidence: float  # 0.0 - 1.0

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class TimelineEvent:
    time: str
    claim_type: str
    value: str
    confidence: float
    source_text: str


@dataclass
class Incident:
    incident_id: str
    location: Optional[ConfidenceValue] = None
    incident_type: Optional[ConfidenceValue] = None
    people_estimate: Optional[ConfidenceValue] = None  # range as string, e.g. "2-3"
    hazards: list = field(default_factory=list)  # list of ConfidenceValue
    timeline: list = field(default_factory=list)  # append-only
    last_updated: Optional[str] = None

    def _merge_confidence(self, old: float, evidence: float) -> float:
        """Never reduce confidence. new = 1 - (1 - old) * (1 - evidence)."""
        new = 1.0 - (1.0 - old) * (1.0 - evidence)
        return max(old, new)


# -----------------------------------------------------------------------------
# 2. Evidence Extraction (regex + text parsing; replaceable by LLM later)
# -----------------------------------------------------------------------------

# Location: regexes that capture full phrase. Order matters (more specific first).
LOCATION_REGEXES = [
    re.compile(r"\b(the\s+)?(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|ground)\s+floor\b", re.I),
    re.compile(r"\b(basement|roof|attic)\b", re.I),
    re.compile(r"\b(room|apartment|flat|unit)\s+(\d+[a-z]?|\w+)\b", re.I),
    re.compile(r"\b(building|block)\s+([a-z]|\d+)\b", re.I),
    re.compile(r"\b(\d+\s+)?([a-z]+)\s+(street|st|avenue|ave|road|rd|drive|dr|way|place|pl)\b", re.I),
    re.compile(r"\b(house|building|warehouse|office)\b", re.I),
]

# Incident type: phrase -> canonical type. Match whole words.
INCIDENT_TYPE_PHRASES = [
    (re.compile(r"\b(gas\s+leak|gas\s+leaking)\b", re.I), "gas leak"),
    (re.compile(r"\bheart\s+attack|cardiac|chest\s+pain\b", re.I), "medical"),
    (re.compile(r"\bstroke\b", re.I), "medical"),
    (re.compile(r"\boverdose|od\b", re.I), "overdose"),
    (re.compile(r"\bsuicide|self\s*[- ]?harm\b", re.I), "suicide"),
    (re.compile(r"\bbreak[- ]?in|burglary|breaking\s+in\b", re.I), "break-in"),
    (re.compile(r"\bassault|attack(ed)?|stabbed|shot\b", re.I), "assault"),
    (re.compile(r"\bmissing\s+person|someone\s+missing\b", re.I), "missing"),
    (re.compile(r"\b(car\s+)?(accident|crash|collision)\b", re.I), "accident"),
    (re.compile(r"\b(collapse|collapsed)\b", re.I), "collapse"),
    (re.compile(r"\bflood(ing)?\b", re.I), "flood"),
    (re.compile(r"\bfire\b", re.I), "fire"),
    (re.compile(r"\bmedical|ambulance|heart|breathing|unconscious|seizure\b", re.I), "medical"),
]

# People: phrases that imply counts or ranges.
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
PEOPLE_RANGE_PHRASES = [
    (re.compile(r"\b(two|three|2|3)\s+(or|and)\s+(two|three|2|3)\s*(people|persons|adults|kids)?\b", re.I), "2-3"),
    (re.compile(r"\b(two|2)\s*(or|to)\s*(three|3)\s*(people|persons)?\b", re.I), "2-3"),
    (re.compile(r"\b(three|3)\s*(or|to)\s*(four|4)\s*(people|persons)?\b", re.I), "3-4"),
    (re.compile(r"\b(one|1)\s*(or|and)\s*(two|2)\s*(people|persons)?\b", re.I), "1-2"),
    (re.compile(r"\bseveral\s*(people|persons)?\b", re.I), "3-6"),
    (re.compile(r"\b(a\s+)?few\s*(people|persons)?\b", re.I), "2-4"),
    (re.compile(r"\bmany\s*(people|persons)?\b", re.I), "5+"),
    (re.compile(r"\bmultiple\s*(people|persons)?\b", re.I), "3+"),
    (re.compile(r"\b(one|1)\s+person\b", re.I), "1"),
    (re.compile(r"\b(two|2)\s+people\b", re.I), "2"),
    (re.compile(r"\b(three|3)\s+people\b", re.I), "3"),
    (re.compile(r"\b(four|4)\s+people\b", re.I), "4"),
    (re.compile(r"\b(five|5)\s+people\b", re.I), "5"),
    (re.compile(r"\b(\d+)\s+people\b", re.I), None),  # capture group -> that number
]

HAZARD_WORDS = [
    "fire", "smoke", "flood", "collapse", "gas", "chemical", "electrical",
    "trapped", "injured", "unconscious", "bleeding", "explosion", "burning",
]
HAZARD_REGEX = re.compile(r"\b(" + "|".join(HAZARD_WORDS) + r")\b", re.I)

# Hedging: lowers confidence when present in sentence
HEDGING_REGEX = re.compile(r"\b(i\s+think|maybe|perhaps|might\s+be|could\s+be|not\s+sure|unsure|maybe)\b", re.I)


def _confidence_from_hedging(text: str, base: float) -> float:
    """Reduce base confidence if hedging language is present."""
    if HEDGING_REGEX.search(text):
        return round(base * 0.75, 2)
    return base


def _extract_location(text: str, now: str, source: str) -> Optional[dict]:
    """Extract a single location phrase and compute confidence from phrase completeness."""
    for rx in LOCATION_REGEXES:
        m = rx.search(text)
        if m:
            # Use span to get value from original source (preserve casing)
            start, end = m.span()
            value = source[start:end].strip() if len(source) >= end else m.group(0).strip()
            if not value:
                value = m.group(0).strip()
            # confidence: longer/more specific = higher
            val_lower = value.lower()
            if re.search(r"\d+\s+(street|st|avenue|ave|road)", val_lower):
                conf = 0.85
            elif re.search(r"(room|apartment|flat)\s+", val_lower):
                conf = 0.8
            elif re.search(r"(first|second|third|fourth|ground)\s+floor", val_lower):
                conf = 0.78
            else:
                conf = 0.65
            conf = _confidence_from_hedging(text, conf)
            return {
                "claim_type": "location",
                "value": value,
                "confidence": min(1.0, conf),
                "timestamp": now,
                "source_text": source,
            }
    return None


def _extract_incident_type(text: str, now: str, source: str) -> Optional[dict]:
    """First matching incident type phrase wins; confidence from explicitness."""
    for rx, inc_type in INCIDENT_TYPE_PHRASES:
        if rx.search(text):
            conf = 0.82 if len(inc_type.split()) == 1 else 0.78
            conf = _confidence_from_hedging(text, conf)
            return {
                "claim_type": "incident_type",
                "value": inc_type,
                "confidence": min(1.0, conf),
                "timestamp": now,
                "source_text": source,
            }
    return None


def _extract_people_count(text: str, now: str, source: str) -> Optional[dict]:
    """Extract people count or range; confidence from explicitness."""
    for rx, range_val in PEOPLE_RANGE_PHRASES:
        m = rx.search(text)
        if m:
            if range_val is None:
                g = m.group(1)
                value = g if g.isdigit() else str(NUM_WORDS.get(g.lower(), g))
            else:
                value = range_val
            conf = 0.7 if ("-" in value or "+" in value) else 0.75
            conf = _confidence_from_hedging(text, conf)
            return {
                "claim_type": "people_count",
                "value": value,
                "confidence": min(1.0, conf),
                "timestamp": now,
                "source_text": source,
            }
    return None


def _extract_hazards(text: str, now: str, source: str) -> list[dict]:
    """Extract all hazard keywords in text with confidence from context."""
    claims = []
    for m in HAZARD_REGEX.finditer(text):
        value = m.group(1).lower()
        conf = 0.72
        conf = _confidence_from_hedging(text, conf)
        claims.append({
            "claim_type": "hazard",
            "value": value,
            "confidence": min(1.0, conf),
            "timestamp": now,
            "source_text": source,
        })
    return claims


def extract_claims(text: str) -> list[dict]:
    """
    Extract claims from a text chunk. Returns list of dicts with:
    claim_type, value, confidence, timestamp, source_text.
    Implemented with regex and parsing; no mocks.
    """
    if not text or not text.strip():
        return []
    text_clean = text.strip()
    text_lower = text_clean.lower()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    claims = []

    loc = _extract_location(text_lower, now, text_clean)
    if loc:
        claims.append(loc)

    inc = _extract_incident_type(text_lower, now, text_clean)
    if inc:
        claims.append(inc)

    people = _extract_people_count(text_lower, now, text_clean)
    if people:
        claims.append(people)

    hazards = _extract_hazards(text_lower, now, text_clean)
    claims.extend(hazards)

    return claims


# -----------------------------------------------------------------------------
# 3. Confidence Update Logic & 4. Timeline
# -----------------------------------------------------------------------------

def _append_timeline(incident: Incident, claim: dict) -> None:
    incident.timeline.append(TimelineEvent(
        time=claim["timestamp"],
        claim_type=claim["claim_type"],
        value=claim["value"],
        confidence=claim["confidence"],
        source_text=claim["source_text"],
    ))


def _apply_claim(incident: Incident, claim: dict) -> None:
    ctype = claim["claim_type"]
    value = claim["value"]
    conf = claim["confidence"]
    ts = claim["timestamp"]

    _append_timeline(incident, claim)
    incident.last_updated = ts

    if ctype == "location":
        if incident.location is None:
            incident.location = ConfidenceValue(value=value, confidence=conf)
        else:
            new_conf = incident._merge_confidence(incident.location.confidence, conf)
            incident.location = ConfidenceValue(value=value, confidence=new_conf)

    elif ctype == "incident_type":
        if incident.incident_type is None:
            incident.incident_type = ConfidenceValue(value=value, confidence=conf)
        else:
            new_conf = incident._merge_confidence(incident.incident_type.confidence, conf)
            incident.incident_type = ConfidenceValue(value=value, confidence=new_conf)

    elif ctype == "people_count":
        if incident.people_estimate is None:
            incident.people_estimate = ConfidenceValue(value=value, confidence=conf)
        else:
            new_conf = incident._merge_confidence(incident.people_estimate.confidence, conf)
            incident.people_estimate = ConfidenceValue(value=value, confidence=new_conf)

    elif ctype == "hazard":
        existing = next((h for h in incident.hazards if h.value == value), None)
        if existing is None:
            incident.hazards.append(ConfidenceValue(value=value, confidence=conf))
        else:
            new_conf = incident._merge_confidence(existing.confidence, conf)
            existing.confidence = new_conf


# -----------------------------------------------------------------------------
# 5. Stream processing interface
# -----------------------------------------------------------------------------

_INCIDENT: Optional[Incident] = None


def process_text_chunk(text: str, incident_id: str = "incident-001") -> Incident:
    global _INCIDENT
    if _INCIDENT is None:
        _INCIDENT = Incident(incident_id=incident_id)

    claims = extract_claims(text)
    for claim in claims:
        _apply_claim(_INCIDENT, claim)

    _print_summary(_INCIDENT)
    return _INCIDENT


def _print_summary(incident: Incident) -> None:
    print("--- Incident summary (uncertainty-aware) ---")
    print(f"incident_id: {incident.incident_id}")
    print(f"last_updated: {incident.last_updated}")
    if incident.location:
        print(f"location: {incident.location.value} (confidence: {incident.location.confidence:.2f})")
    else:
        print("location: (no evidence)")
    if incident.incident_type:
        print(f"incident_type: {incident.incident_type.value} (confidence: {incident.incident_type.confidence:.2f})")
    else:
        print("incident_type: (no evidence)")
    if incident.people_estimate:
        print(f"people_estimate: {incident.people_estimate.value} (confidence: {incident.people_estimate.confidence:.2f})")
    else:
        print("people_estimate: (no evidence)")
    if incident.hazards:
        h_str = ", ".join(f"{h.value}({h.confidence:.2f})" for h in incident.hazards)
        print(f"hazards: {h_str}")
    else:
        print("hazards: (none)")
    print(f"timeline events: {len(incident.timeline)}")
    print()


# -----------------------------------------------------------------------------
# 6. Demo
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    process_text_chunk("there is a fire on the third floor")
    process_text_chunk("i think two or three people are trapped")
