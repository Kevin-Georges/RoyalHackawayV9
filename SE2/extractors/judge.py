"""
LLM as Judge: given current incident state (with confidences) and a new transcript chunk,
output a support score in [0,1] for each claim — how much does this chunk support that claim?
Used for Bayesian confidence updates (not raw LLM confidence).
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("incident_api.judge")

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

JUDGE_PROMPT = """You are a judge for an emergency-call incident summary. You do NOT assert facts. You only score how much the NEW transcript chunk supports or contradicts existing or candidate claims.

Current incident state (each value has a confidence 0–1):
{state_summary}

New transcript chunk:
\"\"\"
{chunk}
\"\"\"

Extracted candidate claims from this chunk (for reference): {claims_summary}

Task: For each claim below that is RELEVANT to the new chunk, output a support score in [0, 1]:
- 1.0 = chunk strongly supports this claim (explicit, clear mention).
- 0.85–0.95 = chunk REPEATS or CONFIRMS an existing claim (e.g. says "fire" again when state has incident_type fire) — use HIGH support so confidence increases.
- 0.7–0.85 = chunk supports (mentioned or implied).
- 0.4–0.6 = chunk is neutral or ambiguous.
- 0.1–0.3 = chunk weakly supports or contradicts.
- 0.0 = chunk contradicts or clearly does not support.

IMPORTANT: If the chunk explicitly mentions the same thing as an existing claim (e.g. "fire" when state has incident_type fire), output 0.9 or higher. Do not output 0.5 for repeated confirmation.

Output a JSON object with keys matching the claim identifiers. Each value is a number in [0, 1].
Include every claim that the chunk is relevant to (including existing state claims that the chunk confirms or repeats).

Format (example):
{{ "location::third floor": 0.85, "incident_type::fire": 0.9, "hazard::smoke": 0.8 }}

Use the exact claim identifiers listed below. Respond with ONLY the JSON object, no other text.
Claim identifiers to consider (output support only for those the chunk is relevant to):
{claim_ids}
"""


def _claim_id(claim_type: str, value: str) -> str:
    return f"{claim_type}::{value.strip().lower()}"


def _state_summary(state: dict) -> str:
    """One-line summary of current state with confidences for the Judge."""
    parts = []
    if state.get("device_location"):
        d = state["device_location"]
        if isinstance(d, dict):
            parts.append("device_location: " + d.get("value", "") + " (" + str(round(d.get("confidence", 0), 2)) + ")")
    for loc in state.get("locations") or []:
        if isinstance(loc, dict):
            parts.append("location: " + loc.get("value", "") + " (" + str(round(loc.get("confidence", 0), 2)) + ")")
    if state.get("incident_type") and isinstance(state["incident_type"], dict):
        inc = state["incident_type"]
        parts.append("incident_type: " + inc.get("value", "") + " (" + str(round(inc.get("confidence", 0), 2)) + ")")
    if state.get("people_estimate") and isinstance(state["people_estimate"], dict):
        pe = state["people_estimate"]
        parts.append("people_estimate: " + pe.get("value", "") + " (" + str(round(pe.get("confidence", 0), 2)) + ")")
    for h in state.get("hazards") or []:
        if isinstance(h, dict):
            parts.append("hazard: " + h.get("value", "") + " (" + str(round(h.get("confidence", 0), 2)) + ")")
    return "\n".join(parts) if parts else "(none)"


def _claims_summary(claims: list[dict]) -> str:
    return ", ".join(c.get("claim_type", "") + "=" + str(c.get("value", "")) for c in claims)


def judge_support_scores(
    state_before: dict,
    chunk_text: str,
    extracted_claims: list[dict],
) -> dict[str, float]:
    """
    LLM Judge: return support score per claim_id (e.g. "location::third floor" -> 0.85).
    Used as likelihood P(chunk | claim true) for Bayesian update.
    """
    if not chunk_text or not chunk_text.strip():
        return {}
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        logger.debug("judge skipped: no OpenAI")
        return _default_scores(extracted_claims)

    state_summary = _state_summary(state_before)
    claims_summary = _claims_summary(extracted_claims)

    # Build list of claim_ids from state + extracted (so Judge can score both existing and new)
    claim_ids_set = set()
    for loc in (state_before.get("locations") or []):
        if isinstance(loc, dict) and loc.get("value"):
            claim_ids_set.add(_claim_id("location", loc["value"]))
    if state_before.get("incident_type") and isinstance(state_before["incident_type"], dict):
        v = state_before["incident_type"].get("value")
        if v:
            claim_ids_set.add(_claim_id("incident_type", v))
    if state_before.get("people_estimate") and isinstance(state_before["people_estimate"], dict):
        v = state_before["people_estimate"].get("value")
        if v:
            claim_ids_set.add(_claim_id("people_count", str(v)))
    for h in (state_before.get("hazards") or []):
        if isinstance(h, dict) and h.get("value"):
            claim_ids_set.add(_claim_id("hazard", h["value"]))
    for c in extracted_claims:
        ctype = c.get("claim_type")
        value = c.get("value")
        if ctype and value is not None:
            if ctype == "people_count":
                claim_ids_set.add(_claim_id("people_count", str(value)))
            else:
                claim_ids_set.add(_claim_id(ctype, str(value)))

    claim_ids_list = sorted(claim_ids_set)
    if not claim_ids_list:
        return {}

    prompt = JUDGE_PROMPT.format(
        state_summary=state_summary,
        chunk=chunk_text.strip()[:2000],
        claims_summary=claims_summary[:500],
        claim_ids=", ".join(f'"{x}"' for x in claim_ids_list),
    )

    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return _default_scores(extracted_claims)
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _default_scores(extracted_claims)
        out = {}
        for k, v in data.items():
            try:
                score = float(v)
                # Normalize key so "incident type::fire" matches engine's "incident_type::fire"
                key_normalized = re.sub(r"[\s_]+", "_", k.strip().lower())
                out[key_normalized] = max(0.0, min(1.0, score))
            except (TypeError, ValueError):
                continue
        logger.info("judge returned %d support scores", len(out))
        return out
    except Exception as e:
        logger.warning("judge failed: %s", e)
        return _default_scores(extracted_claims)


def _default_scores(extracted_claims: list[dict]) -> dict[str, float]:
    """When Judge is unavailable or fails: default support 0.55 (slight positive)."""
    out = {}
    for c in extracted_claims:
        ctype = c.get("claim_type")
        value = c.get("value")
        if ctype and value is not None:
            cid = _claim_id("people_count" if ctype == "people_count" else ctype, str(value))
            out[cid] = 0.55
    return out
