"""
Seed demo incidents by POSTing varied chunks to the SE2 /chunk API.

Run with the API already running (python run_api.py). Optionally set SE2_API_URL in env.
Chunks are assigned occurred_at spread over the last 10 days so Snowflake analytics
show time-series across multiple days (not all on one day).
Usage: python seed_demo_incidents.py
"""

import os
import time
from datetime import datetime, timedelta

import httpx

SE2_API_URL = (os.environ.get("SE2_API_URL") or "http://localhost:8000").rstrip("/")

# Demo chunks: varied incident types, locations, and a few with device coords for the map
DEMO_CHUNKS = [
    # Incident 1: Fire on third floor (with coords so it shows on map)
    {"text": "There's a fire on the third floor of the east wing.", "device_lat": 51.5074, "device_lng": -0.1278},
    {"text": "Smoke is spreading. We need evacuation.", "device_lat": 51.5074, "device_lng": -0.1278},
    {"text": "At least two people are trapped near the stairwell.", "device_lat": 51.5074, "device_lng": -0.1278},
    # Incident 2: Medical in lobby (different location)
    {"text": "Medical emergency in the main lobby. Someone collapsed.", "device_lat": 51.515, "device_lng": -0.142},
    {"text": "We think it might be a heart attack. Need ambulance.", "device_lat": 51.515, "device_lng": -0.142},
    # Incident 3: Different building
    {"text": "Fire in building B, second floor. Multiple people inside.", "device_lat": 51.52, "device_lng": -0.11},
    # Incident 4: Assault / security
    {"text": "Report of an assault near the car park. One person injured.", "device_lat": 51.50, "device_lng": -0.14},
    {"text": "Security is on the way. Suspect may have left the area.", "device_lat": 51.50, "device_lng": -0.14},
    # Incident 5: Gas leak
    {"text": "Smell of gas on the first floor. Possible gas leak.", "device_lat": 51.508, "device_lng": -0.13},
    {"text": "We've evacuated that corridor. Fire brigade en route.", "device_lat": 51.508, "device_lng": -0.13},
    # A few more to fill out timelines and clustering
    {"text": "Update: fire on third floor is under control but building still evacuating.", "device_lat": 51.5074, "device_lng": -0.1278},
    {"text": "Ambulance has arrived at the lobby for the medical.", "device_lat": 51.515, "device_lng": -0.142},
    {"text": "Gas leak isolated. First floor clear.", "device_lat": 51.508, "device_lng": -0.13},
]


def _occurred_at_for_index(i: int, total: int) -> str:
    """Spread chunks over the last 10 days with varied hour (UTC)."""
    now = datetime.utcnow()
    # days_ago: 0, 1, 2, ... 9 then wrap so we use ~10 days
    days_ago = int((i * 1.3) % 10)
    hour_offset = (i * 3) % 24
    t = now - timedelta(days=days_ago) - timedelta(hours=hour_offset)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    print(f"Seeding demo incidents via {SE2_API_URL}/chunk (auto_cluster=True)")
    client = httpx.Client(timeout=30.0)
    try:
        for i, chunk in enumerate(DEMO_CHUNKS):
            payload = {
                "text": chunk["text"],
                "auto_cluster": True,
                "incident_id": "",
                "caller_id": "demo-seed",
                "caller_info": {"label": "Demo seed"},
                "occurred_at": _occurred_at_for_index(i, len(DEMO_CHUNKS)),
            }
            if chunk.get("device_lat") is not None and chunk.get("device_lng") is not None:
                payload["device_lat"] = chunk["device_lat"]
                payload["device_lng"] = chunk["device_lng"]
            r = client.post(
                f"{SE2_API_URL}/chunk",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if r.is_success:
                data = r.json()
                inc = data.get("incident_id", "")
                new = data.get("cluster_new", False)
                score = data.get("cluster_score")
                print(f"  [{i+1}/{len(DEMO_CHUNKS)}] incident_id={inc} cluster_new={new} score={score}")
            else:
                print(f"  [{i+1}/{len(DEMO_CHUNKS)}] FAILED {r.status_code} {r.text[:200]}")
            time.sleep(0.3)
        print("Done. Open the dashboard and Snowflake Analytics to see the data.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
