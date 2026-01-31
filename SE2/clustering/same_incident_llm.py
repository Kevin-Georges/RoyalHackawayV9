"""LLM score: how likely is this report the same incident as the given incident summary? 0–1."""

import json
import logging
import os
import re

logger = logging.getLogger("incident_api.clustering.same_incident_llm")

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

SAME_INCIDENT_PROMPT = """You are judging whether a NEW emergency report describes the SAME incident as an EXISTING incident summary.

Existing incident summary:
\"\"\"
{incident_summary}
\"\"\"

New report summary:
\"\"\"
{report_summary}
\"\"\"

Output a single number in [0, 1]:
- 1.0 = almost certainly the same incident (same place, same type, same time window).
- 0.7–0.9 = likely same (e.g. same building/area, same incident type).
- 0.4–0.6 = unclear (could be same or different).
- 0.1–0.3 = likely different (different location, type, or context).
- 0.0 = clearly different incident.

Respond with ONLY the number, no other text."""


def llm_same_incident_score(incident_summary: str, report_summary: str) -> float:
    """
    Return 0–1 score: how likely the report describes the same incident.
    Returns 0.5 (neutral) if no API key or on error.
    """
    if not incident_summary or not report_summary:
        return 0.5
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        logger.debug("llm same-incident skipped: no OpenAI")
        return 0.5
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        prompt = SAME_INCIDENT_PROMPT.format(
            incident_summary=incident_summary.strip()[:2000],
            report_summary=report_summary.strip()[:2000],
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Parse a single number
        m = re.search(r"0?\.\d+|\d+\.?\d*", raw)
        if m:
            score = float(m.group(0))
            return max(0.0, min(1.0, score))
        return 0.5
    except Exception as e:
        logger.warning("llm same-incident failed: %s", e)
        return 0.5
