"""Time proximity score: how close two timestamps are → 0–1 (closer = higher)."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("incident_api.clustering.time_proximity")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # Accept ISO with or without Z
        s = ts.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def time_proximity_score(t1_iso: str | None, t2_iso: str | None) -> float:
    """
    Return a score in [0, 1]: 1 = same hour, decays as time difference increases.
    - Within 1 hour  → 1.0
    - Within 6 hours → 0.8
    - Within 24h     → 0.6
    - Within 7 days  → 0.3
    - Else           → 0.1
    """
    if not t1_iso or not t2_iso:
        return 0.5  # unknown time → neutral
    d1 = _parse_iso(t1_iso)
    d2 = _parse_iso(t2_iso)
    if d1 is None or d2 is None:
        return 0.5
    # Normalize to UTC for difference
    if d1.tzinfo is None:
        d1 = d1.replace(tzinfo=timezone.utc)
    if d2.tzinfo is None:
        d2 = d2.replace(tzinfo=timezone.utc)
    delta = abs((d1 - d2).total_seconds())
    one_hour = 3600
    six_hours = 6 * one_hour
    one_day = 24 * one_hour
    seven_days = 7 * one_day
    if delta <= one_hour:
        return 1.0
    if delta <= six_hours:
        return 0.8
    if delta <= one_day:
        return 0.6
    if delta <= seven_days:
        return 0.3
    return 0.1
