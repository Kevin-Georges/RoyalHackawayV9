"""
OpenAI-based evidence extraction with hallucination handling.
- Only accept claims grounded in source text.
- Cap confidence when value is not clearly present in transcript.
- LLM provides incident_type value (classification); probability is decided by Judge + Bayesian in the pipeline, not by the LLM.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger("incident_api.openai_extractor")

# Optional: only use OpenAI if key is set
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Incident-type probability is decided by Judge + Bayesian in the pipeline; LLM only provides the value.
NEUTRAL_INCIDENT_TYPE_CONFIDENCE = 0.5

# Terms that describe the incident category → use incident_type, NOT hazard
INCIDENT_TYPE_ALIASES = {
    "fire": "fire", "smoke": "fire", "burning": "fire", "flame": "fire", "flames": "fire",
    "flood": "flood", "flooding": "flood",
    "collapse": "collapse", "collapsed": "collapse",
    "gas": "gas leak", "gas leak": "gas leak", "leak": "gas leak",
    "assault": "assault", "shooting": "assault", "gunshot": "assault", "shot": "assault",
    "medical": "medical", "accident": "accident", "break-in": "break-in", "missing": "missing",
    "overdose": "overdose", "suicide": "suicide",
}

EXTRACT_SCHEMA_NO_CONTEXT = """
Return a JSON object with these optional keys. Only include keys where the NEW transcript chunk EXPLICITLY states something. Do not infer.

- locations: [ { "value": "<place/floor/room from transcript>", "confidence": 0.0-1.0 }, ... ]  (can be multiple, e.g. "second floor" and "first floor")
- incident_type: { "value": "fire|medical|accident|collapse|flood|gas leak|assault|break-in|missing|overdose|suicide" }  (probability is set by the system from context; you only choose the type)
- people_count: { "value": "<number or range e.g. 2-3 or 1>", "confidence": 0.0-1.0 }
- hazards: [ { "value": "<keyword>", "confidence": 0.0-1.0 }, ... ]

Rules: Use confidence for certainty. If the speaker hedges ("I think", "maybe"), use lower confidence. Omit keys not stated. For locations, include every distinct place mentioned. For incident_type: gun shot, shooting, shot, stabbed, attack, fire, smoke, flood, collapse, gas leak → use incident_type with the canonical value (e.g. "fire", "assault"). Do NOT put incident types in hazards. Use hazards ONLY for situational dangers (e.g. trapped, injured, downed power line, chemical spill, explosion risk)—not for the main incident category.
"""

CONTEXT_INSTRUCTIONS = """
You are updating an ongoing incident summary. You are given:
1) CURRENT INCIDENT STATE — what we already know (from previous chunks).
2) NEW TRANSCRIPT CHUNK — the latest speech to process.

Output only what this NEW chunk adds or updates. You can:
- Add NEW locations (e.g. "first floor" when we already have "second floor").
- Add or reinforce incident_type, people_count, hazards.
- Use incident_type for the main incident category (fire, smoke→fire, flood, collapse, gas leak, assault, medical, etc.). Use hazards ONLY for situational dangers (trapped, injured, downed power line, chemical)—never put incident categories in hazards.
- For incident_type you only choose the value (classification); the system sets the probability from context (Judge + Bayesian). For other fields use confidence as usual.
- Use confidence to reflect how well the new chunk supports or updates each claim (except incident_type). When the new chunk CONFIRMS or REPEATS something already in state, you may output it with confidence reflecting that reinforcement; when it adds something NEW, output the new claim.
Do not repeat the full state — only output claims that this chunk adds or that should update probabilities (e.g. same location mentioned again → higher confidence).
"""


def _grounding_score(source_text: str, value: str, claim_type: str) -> float:
    """
    Score how well the extracted value is grounded in source text (anti-hallucination).
    Returns 1.0 if value/substrings appear in source, lower if not.
    """
    if not value or not source_text:
        return 0.0
    source_lower = source_text.lower().strip()
    value_lower = value.lower().strip()
    # Exact substring
    if value_lower in source_lower:
        return 1.0
    # Location/incident_type: allow normalized overlap (e.g. "third floor" vs "3rd floor")
    words = set(re.findall(r"\w+", value_lower))
    source_words = set(re.findall(r"\w+", source_lower))
    overlap = len(words & source_words) / max(len(words), 1)
    if overlap >= 0.8:
        return 0.95
    if overlap >= 0.5:
        return 0.7
    if overlap >= 0.3:
        return 0.5
    # People count: "2-3" might correspond to "two or three" — check for number words
    if claim_type == "people_count":
        num_map = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "2-3": "two three", "1-2": "one two"}
        for k, v in num_map.items():
            if k in value_lower and v in source_lower:
                return 0.85
        if any(c.isdigit() for c in value) and any(c.isdigit() for c in source_lower):
            return 0.6
    # Hazard: single word often appears verbatim
    if claim_type == "hazard" and len(words) <= 2:
        return 0.6 if overlap else 0.35
    return max(0.2, overlap)


def _cap_confidence_by_grounding(confidence: float, grounding: float, min_cap: float = 0.25) -> float:
    """Cap confidence so that ungrounded claims cannot have high probability."""
    return round(min(confidence, max(grounding, min_cap)), 4)


def _strip_json_block(raw: str) -> str:
    """Remove markdown code fence if present so we can parse JSON."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_llm_response(raw: str, source_text: str, now: str) -> list[dict]:
    """Parse LLM JSON into list of claims; apply grounding cap to each."""
    claims = []
    raw = _strip_json_block(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("openai json decode failed len=%d err=%s raw_preview=%r", len(raw), e, raw[:200])
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(data, dict):
        logger.warning("openai response not a dict type=%s", type(data).__name__)
        return []

    # locations: array of { value, confidence } (multiple parts per incident)
    if "locations" in data and isinstance(data["locations"], list):
        for loc in data["locations"]:
            if isinstance(loc, dict) and "value" in loc:
                v = str(loc["value"]).strip()
                if not v:
                    continue
                conf = float(loc.get("confidence", 0.6))
                g = _grounding_score(source_text, v, "location")
                conf = _cap_confidence_by_grounding(conf, g)
                claims.append({"claim_type": "location", "value": v, "confidence": conf, "timestamp": now, "source_text": source_text})
    elif "location" in data and data["location"]:
        loc = data["location"]
        if isinstance(loc, dict) and "value" in loc:
            v = str(loc["value"]).strip()
            conf = float(loc.get("confidence", 0.6))
            g = _grounding_score(source_text, v, "location")
            conf = _cap_confidence_by_grounding(conf, g)
            claims.append({"claim_type": "location", "value": v, "confidence": conf, "timestamp": now, "source_text": source_text})

    if "incident_type" in data and data["incident_type"]:
        inc = data["incident_type"]
        v = None
        if isinstance(inc, dict) and "value" in inc:
            v = str(inc["value"]).strip().lower()
        elif isinstance(inc, str) and inc.strip():
            v = inc.strip().lower()
        if v:
            # Probability is decided by Judge + Bayesian in the pipeline; LLM only provides the classification.
            claims.append({"claim_type": "incident_type", "value": v, "confidence": NEUTRAL_INCIDENT_TYPE_CONFIDENCE, "timestamp": now, "source_text": source_text})

    if "people_count" in data and data["people_count"]:
        pc = data["people_count"]
        if isinstance(pc, dict) and "value" in pc:
            v = str(pc["value"]).strip()
            conf = float(pc.get("confidence", 0.5))
            g = _grounding_score(source_text, v, "people_count")
            conf = _cap_confidence_by_grounding(conf, g)
            claims.append({"claim_type": "people_count", "value": v, "confidence": conf, "timestamp": now, "source_text": source_text})

    if "hazards" in data and isinstance(data["hazards"], list):
        for h in data["hazards"]:
            if isinstance(h, dict) and "value" in h:
                v = str(h["value"]).strip().lower()
                conf = float(h.get("confidence", 0.6))
                g = _grounding_score(source_text, v, "hazard")
                conf = _cap_confidence_by_grounding(conf, g)
                # Reclassify: if this "hazard" is really an incident type, emit incident_type not hazard
                canonical = INCIDENT_TYPE_ALIASES.get(v) or INCIDENT_TYPE_ALIASES.get(v.split()[0] if v.split() else "")
                if canonical:
                    # Probability is decided by Judge + Bayesian; LLM only provided the value (reclassified from hazard).
                    claims.append({"claim_type": "incident_type", "value": canonical, "confidence": NEUTRAL_INCIDENT_TYPE_CONFIDENCE, "timestamp": now, "source_text": source_text})
                else:
                    claims.append({"claim_type": "hazard", "value": v, "confidence": conf, "timestamp": now, "source_text": source_text})

    return claims


def _context_summary_for_prompt(context: Optional[dict]) -> str:
    """Build a short summary of current incident state for the LLM prompt."""
    if not context:
        return "(No prior state — first chunk for this incident.)"
    parts = []
    if context.get("locations"):
        locs = context["locations"] if isinstance(context["locations"], list) else []
        parts.append("locations: " + ", ".join(
            (x.get("value", "") + " (" + str(round(x.get("confidence", 0), 2)) + ")" if isinstance(x, dict) else str(x))
            for x in locs
        ))
    if context.get("incident_type") and isinstance(context["incident_type"], dict):
        inc = context["incident_type"]
        parts.append("incident_type: " + inc.get("value", "") + " (" + str(round(inc.get("confidence", 0), 2)) + ")")
    if context.get("people_estimate") and isinstance(context["people_estimate"], dict):
        pe = context["people_estimate"]
        parts.append("people_estimate: " + pe.get("value", "") + " (" + str(round(pe.get("confidence", 0), 2)) + ")")
    if context.get("hazards"):
        hazards = context["hazards"] if isinstance(context["hazards"], list) else []
        parts.append("hazards: " + ", ".join(
            (x.get("value", "") + " (" + str(round(x.get("confidence", 0), 2)) + ")" if isinstance(x, dict) else str(x))
            for x in hazards
        ))
    return "\n".join(parts) if parts else "(No prior state.)"


def extract_claims(text: str, context: Optional[dict] = None) -> list[dict]:
    """
    Extract claims using OpenAI. Context-aware: pass current incident state so the LLM
    can add new locations, reinforce existing claims, and update probabilities.
    Applies grounding checks to cap confidence and reduce hallucinations.
    """
    if not text or not text.strip():
        logger.debug("extract_claims skipped empty text")
        return []
    if OpenAI is None:
        logger.warning("extract_claims openai not installed")
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.debug("extract_claims no OPENAI_API_KEY")
        return []

    source_text = text.strip()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    has_context = bool(context and (context.get("locations") or context.get("incident_type") or context.get("hazards") or context.get("people_estimate")))
    logger.info("openai extract start text_len=%d has_context=%s", len(source_text), has_context)

    client = OpenAI(api_key=api_key)
    if has_context:
        current_state = _context_summary_for_prompt(context)
        prompt = (
            CONTEXT_INSTRUCTIONS
            + "\n\n--- CURRENT INCIDENT STATE ---\n"
            + current_state
            + "\n\n--- NEW TRANSCRIPT CHUNK ---\n\"\"\"\n"
            + source_text
            + "\n\"\"\"\n\n--- OUTPUT (JSON only) ---\n"
            + "Return a JSON object with optional keys: locations (array of { value, confidence }), incident_type, people_count, hazards (array). "
            + "Only include what this NEW chunk adds or updates. No markdown fences."
        )
    else:
        prompt = (
            "You are an evidence extractor for emergency call transcripts. "
            "Extract ONLY what is explicitly stated. Do not infer.\n\n"
            "Transcript chunk:\n\"\"\"\n" + source_text + "\n\"\"\"\n\n"
            + EXTRACT_SCHEMA_NO_CONTEXT
            + "\nRespond with only the JSON object. No markdown code fences."
        )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        if not raw:
            logger.warning("openai empty response")
            return []
        raw = raw.strip()
        claims = _parse_llm_response(raw, source_text, now)
        logger.info("openai extract done claims_count=%d", len(claims))
        return claims
    except Exception as e:
        logger.exception("openai extract failed: %s", e)
        return []
