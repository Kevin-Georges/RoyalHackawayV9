"""Tests for API: POST /chunk, GET /incident, demo-locations, boost_repeated_mention."""

import pytest

from fastapi.testclient import TestClient

import api.main as main_module


@pytest.fixture
def client():
    """Fresh TestClient; clears incidents before each test."""
    main_module.incidents.clear()
    return TestClient(main_module.app)


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "extractor" in data


class TestChunk:
    def test_chunk_creates_incident_and_extracts(self, client):
        r = client.post("/chunk", json={"text": "there is a fire on the third floor", "incident_id": "inc-1"})
        assert r.status_code == 200
        data = r.json()
        assert data["incident_id"] == "inc-1"
        assert "summary" in data
        assert data["summary"]["incident_id"] == "inc-1"
        assert data["claims_added"] >= 1
        assert data["summary"]["incident_type"] is not None
        assert data["summary"]["incident_type"]["value"] == "fire"
        assert "locations" in data["summary"]
        assert len(data["summary"]["timeline"]) >= 1

    def test_chunk_empty_text_rejected(self, client):
        r = client.post("/chunk", json={"text": "", "incident_id": "inc-1"})
        assert r.status_code == 400

    def test_chunk_with_device_location(self, client):
        r = client.post(
            "/chunk",
            json={
                "text": "fire on the third floor",
                "incident_id": "inc-1",
                "device_lat": 51.5,
                "device_lng": -0.12,
            },
        )
        assert r.status_code == 200
        summary = r.json()["summary"]
        assert summary["device_location"] is not None
        assert summary["device_location"]["lat"] == 51.5
        assert summary["device_location"]["lng"] == -0.12

    def test_gun_shot_updates_incident_type(self, client):
        client.post("/chunk", json={"text": "there is a fire", "incident_id": "inc-1"})
        r = client.post("/chunk", json={"text": "there's a gun shot", "incident_id": "inc-1"})
        assert r.status_code == 200
        summary = r.json()["summary"]
        assert summary["incident_type"] is not None
        assert summary["incident_type"]["value"] == "assault"


class TestGetIncident:
    def test_get_incident_404_when_missing(self, client):
        r = client.get("/incident/nonexistent")
        assert r.status_code == 404

    def test_get_incident_after_chunk(self, client):
        client.post("/chunk", json={"text": "fire on third floor", "incident_id": "inc-1"})
        r = client.get("/incident/inc-1")
        assert r.status_code == 200
        data = r.json()
        assert data["incident_id"] == "inc-1"
        assert "device_location" in data
        assert "locations" in data
        assert "timeline" in data


class TestDemoLocations:
    def test_post_demo_locations_adds_locations_with_coords(self, client):
        r = client.post("/incident/inc-1/demo-locations")
        assert r.status_code == 200
        data = r.json()
        assert data["incident_id"] == "inc-1"
        assert len(data["locations"]) >= 2
        with_coords = [loc for loc in data["locations"] if loc.get("lat") is not None and loc.get("lng") is not None]
        assert len(with_coords) >= 2


class TestBoostRepeatedMention:
    def test_boost_when_chunk_contains_fire(self):
        state_before = {
            "incident_type": {"value": "fire", "confidence": 0.6},
            "locations": [],
            "hazards": [],
            "people_estimate": None,
        }
        claims = [{"claim_type": "incident_type", "value": "fire"}]
        judge_scores = {}
        boosted = main_module._boost_repeated_mention("yes it's a fire", state_before, claims, judge_scores)
        assert main_module._claim_id("incident_type", "fire") in boosted
        assert boosted["incident_type::fire"] >= 0.85

    def test_no_boost_when_chunk_does_not_mention_value(self):
        state_before = {"incident_type": {"value": "fire", "confidence": 0.6}, "locations": [], "hazards": [], "people_estimate": None}
        claims = [{"claim_type": "incident_type", "value": "fire"}]
        judge_scores = {"incident_type::fire": 0.5}
        boosted = main_module._boost_repeated_mention("something else entirely", state_before, claims, judge_scores)
        assert boosted.get("incident_type::fire") == 0.5
