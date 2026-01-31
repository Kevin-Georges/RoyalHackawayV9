"""Geo proximity score: how close report device location is to incident location → 0–1."""

import logging
import math

logger = logging.getLogger("incident_api.clustering.geo_proximity")

# Earth radius in metres (approximate)
EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance in metres between two (lat, lng) points."""
    a = math.radians(lat2 - lat1)
    b = math.radians(lng2 - lng1)
    x = math.sin(a / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(b / 2) ** 2
    c = 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))
    return EARTH_RADIUS_M * c


def geo_proximity_score(
    report_lat: float | None,
    report_lng: float | None,
    incident_state: dict,
) -> float:
    """
    Return 0–1: how close the report's device location is to the incident's location(s).
    - If both have coords: 1.0 at same spot, decay with distance (50m→1.0, 200m→0.8, 500m→0.6, 1km→0.4, 2km→0.2, else 0.1).
    - If either missing: 0.5 (neutral, don't penalise).
    """
    if report_lat is None or report_lng is None:
        return 0.5
    # Prefer incident device_location; fallback to first location with lat/lng
    inc_lat, inc_lng = None, None
    dev = incident_state.get("device_location") if isinstance(incident_state.get("device_location"), dict) else None
    if dev and dev.get("lat") is not None and dev.get("lng") is not None:
        try:
            inc_lat = float(dev["lat"])
            inc_lng = float(dev["lng"])
        except (TypeError, ValueError):
            pass
    if inc_lat is None and (incident_state.get("locations") or []):
        for loc in incident_state.get("locations") or []:
            if isinstance(loc, dict) and loc.get("lat") is not None and loc.get("lng") is not None:
                try:
                    inc_lat = float(loc["lat"])
                    inc_lng = float(loc["lng"])
                    break
                except (TypeError, ValueError):
                    continue
    if inc_lat is None or inc_lng is None:
        return 0.5
    try:
        report_lat_f = float(report_lat)
        report_lng_f = float(report_lng)
    except (TypeError, ValueError):
        return 0.5
    dist_m = haversine_m(report_lat_f, report_lng_f, inc_lat, inc_lng)
    if dist_m <= 50:
        return 1.0
    if dist_m <= 200:
        return 0.9
    if dist_m <= 500:
        return 0.7
    if dist_m <= 1000:
        return 0.5
    if dist_m <= 2000:
        return 0.3
    return 0.1


def device_geo_snippet(lat: float | None, lng: float | None, decimals: int = 3) -> str:
    """Rounded lat,lng for inclusion in summary text (same building ≈ same snippet)."""
    if lat is None or lng is None:
        return ""
    try:
        return f"{round(float(lat), decimals)},{round(float(lng), decimals)}"
    except (TypeError, ValueError):
        return ""
