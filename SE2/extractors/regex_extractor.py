"""Regex-based evidence extraction (no external services)."""

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger("incident_api.regex_extractor")

LOCATION_REGEXES = [
    re.compile(r"\b(the\s+)?(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|ground)\s+floor\b", re.I),
    re.compile(r"\b(basement|roof|attic)\b", re.I),
    re.compile(r"\b(room|apartment|flat|unit)\s+(\d+[a-z]?|\w+)\b", re.I),
    re.compile(r"\b(building|block)\s+([a-z]|\d+)\b", re.I),
    re.compile(r"\b(\d+\s+)?([a-z]+)\s+(street|st|avenue|ave|road|rd|drive|dr|way|place|pl)\b", re.I),
    re.compile(r"\b(house|building|warehouse|office)\b", re.I),
]

INCIDENT_TYPE_PHRASES = [
    (re.compile(r"\b(gas\s+leak|gas\s+leaking)\b", re.I), "gas leak"),
    (re.compile(r"\bheart\s+attack|cardiac|chest\s+pain\b", re.I), "medical"),
    (re.compile(r"\bstroke\b", re.I), "medical"),
    (re.compile(r"\boverdose|od\b", re.I), "overdose"),
    (re.compile(r"\bsuicide|self\s*[- ]?harm\b", re.I), "suicide"),
    (re.compile(r"\bbreak[- ]?in|burglary|breaking\s+in\b", re.I), "break-in"),
    (re.compile(r"\bgun\s*shot|gunshot|shooting|shot\s+at|someone\s+shot\b", re.I), "assault"),
    (re.compile(r"\bassault|attack(ed)?|stabbed|shot\b", re.I), "assault"),
    (re.compile(r"\bmissing\s+person|someone\s+missing\b", re.I), "missing"),
    (re.compile(r"\b(car\s+)?(accident|crash|collision)\b", re.I), "accident"),
    (re.compile(r"\b(collapse|collapsed)\b", re.I), "collapse"),
    (re.compile(r"\bflood(ing)?\b", re.I), "flood"),
    (re.compile(r"\bfire\b", re.I), "fire"),
    (re.compile(r"\bmedical|ambulance|heart|breathing|unconscious|seizure\b", re.I), "medical"),
]

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
    (re.compile(r"\b(\d+)\s+people\b", re.I), None),
]

# Hazard keywords that are really incident types → emit as incident_type, not hazard
HAZARD_AS_INCIDENT_TYPE = {"fire": "fire", "smoke": "fire", "burning": "fire", "flood": "flood", "collapse": "collapse", "gas": "gas leak"}
# Only these are emitted as hazards (situational dangers, not the incident category)
HAZARD_ONLY_WORDS = [
    "trapped", "injured", "unconscious", "bleeding", "explosion", "chemical", "electrical",
]
HAZARD_WORDS = list(HAZARD_AS_INCIDENT_TYPE.keys()) + HAZARD_ONLY_WORDS
HAZARD_REGEX = re.compile(r"\b(" + "|".join(HAZARD_WORDS) + r")\b", re.I)
HEDGING_REGEX = re.compile(r"\b(i\s+think|maybe|perhaps|might\s+be|could\s+be|not\s+sure|unsure)\b", re.I)


def _confidence_from_hedging(text: str, base: float) -> float:
    if HEDGING_REGEX.search(text):
        return round(base * 0.75, 2)
    return base


def _extract_locations(text: str, now: str, source: str) -> list[dict]:
    """Extract ALL location phrases from text (multiple parts per incident, e.g. second floor and first floor)."""
    claims = []
    seen_values: set[str] = set()
    for rx in LOCATION_REGEXES:
        for m in rx.finditer(text):
            start, end = m.span()
            value = source[start:end].strip() if len(source) >= end else m.group(0).strip()
            if not value:
                value = m.group(0).strip()
            val_lower = value.lower()
            if val_lower in seen_values:
                continue
            seen_values.add(val_lower)
            if re.search(r"\d+\s+(street|st|avenue|ave|road)", val_lower):
                conf = 0.85
            elif re.search(r"(room|apartment|flat)\s+", val_lower):
                conf = 0.8
            elif re.search(r"(first|second|third|fourth|ground)\s+floor", val_lower):
                conf = 0.78
            else:
                conf = 0.65
            conf = _confidence_from_hedging(text, conf)
            claims.append({"claim_type": "location", "value": value, "confidence": min(1.0, conf), "timestamp": now, "source_text": source})
    return claims


def _extract_incident_type(text: str, now: str, source: str) -> Optional[dict]:
    for rx, inc_type in INCIDENT_TYPE_PHRASES:
        if rx.search(text):
            conf = 0.82 if len(inc_type.split()) == 1 else 0.78
            conf = _confidence_from_hedging(text, conf)
            return {"claim_type": "incident_type", "value": inc_type, "confidence": min(1.0, conf), "timestamp": now, "source_text": source}
    return None


def _extract_people_count(text: str, now: str, source: str) -> Optional[dict]:
    for rx, range_val in PEOPLE_RANGE_PHRASES:
        m = rx.search(text)
        if m:
            value = (m.group(1) if m.group(1).isdigit() else str(NUM_WORDS.get(m.group(1).lower(), m.group(1)))) if range_val is None else range_val
            conf = 0.7 if ("-" in value or "+" in value) else 0.75
            conf = _confidence_from_hedging(text, conf)
            return {"claim_type": "people_count", "value": value, "confidence": min(1.0, conf), "timestamp": now, "source_text": source}
    return None


def _extract_hazards(text: str, now: str, source: str) -> list[dict]:
    """Extract hazards. Terms that are incident types (fire, smoke, flood, collapse, gas) → incident_type claim; rest → hazard."""
    claims = []
    for m in HAZARD_REGEX.finditer(text):
        value = m.group(1).lower()
        conf = _confidence_from_hedging(text, 0.72)
        if value in HAZARD_AS_INCIDENT_TYPE:
            inc_type = HAZARD_AS_INCIDENT_TYPE[value]
            claims.append({"claim_type": "incident_type", "value": inc_type, "confidence": min(1.0, conf), "timestamp": now, "source_text": source})
        else:
            claims.append({"claim_type": "hazard", "value": value, "confidence": min(1.0, conf), "timestamp": now, "source_text": source})
    return claims


def extract_claims(text: str, context: Optional[dict] = None) -> list[dict]:
    """Extract claims from a text chunk. context is ignored (regex has no context). Returns list of dicts with claim_type, value, confidence, timestamp, source_text."""
    if not text or not text.strip():
        logger.debug("regex extract_claims skipped empty text")
        return []
    text_clean = text.strip()
    text_lower = text_clean.lower()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    claims = []
    claims.extend(_extract_locations(text_lower, now, text_clean))
    if inc := _extract_incident_type(text_lower, now, text_clean):
        claims.append(inc)
    if people := _extract_people_count(text_lower, now, text_clean):
        claims.append(people)
    claims.extend(_extract_hazards(text_lower, now, text_clean))
    logger.info("regex extract done text_len=%d claims_count=%d", len(text_clean), len(claims))
    return claims
