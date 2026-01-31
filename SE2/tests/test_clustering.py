"""Tests for clustering: time proximity, geo proximity, combined score, find_best_incident."""

import pytest

from clustering.time_proximity import time_proximity_score
from clustering.geo_proximity import geo_proximity_score, haversine_m
from clustering.assigner import (
    state_to_summary_text,
    claims_to_summary_text,
    combined_match_score,
    find_best_incident,
    new_incident_id,
    DEFAULT_WEIGHTS,
)


class TestTimeProximity:
    def test_same_hour(self):
        t = "2025-01-15T14:00:00Z"
        assert time_proximity_score(t, "2025-01-15T14:30:00Z") == 1.0

    def test_six_hours(self):
        t = "2025-01-15T14:00:00Z"
        assert time_proximity_score(t, "2025-01-15T19:00:00Z") == 0.8

    def test_far_apart(self):
        t = "2025-01-15T14:00:00Z"
        # > 7 days → 0.1; exactly 7 days → 0.3
        assert time_proximity_score(t, "2025-01-23T14:00:00Z") == 0.1

    def test_none_returns_neutral(self):
        assert time_proximity_score(None, "2025-01-15T14:00:00Z") == 0.5


class TestGeoProximity:
    def test_same_spot(self):
        state = {"device_location": {"value": "Device", "lat": 51.507, "lng": -0.127}}
        assert geo_proximity_score(51.507, -0.127, state) == 1.0

    def test_no_coords_neutral(self):
        assert geo_proximity_score(None, -0.127, {}) == 0.5
        assert geo_proximity_score(51.507, None, {"device_location": {"lat": 51.507, "lng": -0.127}}) == 0.5

    def test_haversine_small(self):
        # Same point
        assert haversine_m(51.507, -0.127, 51.507, -0.127) < 1


class TestCombinedScore:
    def test_weights_sum(self):
        # DEFAULT_WEIGHTS = (0.35, 0.35, 0.15, 0.15) for emb, llm, time, geo
        s = combined_match_score(0.8, 0.9, 1.0, 0.7, weights=DEFAULT_WEIGHTS)
        assert 0 <= s <= 1
        assert abs(s - (0.35 * 0.8 + 0.35 * 0.9 + 0.15 * 1.0 + 0.15 * 0.7)) < 0.01


class TestStateToSummaryText:
    def test_builds_from_state(self):
        state = {
            "incident_type": {"value": "fire", "confidence": 0.8},
            "locations": [{"value": "third floor", "confidence": 0.7}],
        }
        text = state_to_summary_text(state)
        assert "fire" in text
        assert "third floor" in text


class TestClaimsToSummaryText:
    def test_builds_from_claims(self):
        claims = [
            {"claim_type": "incident_type", "value": "fire"},
            {"claim_type": "location", "value": "second floor"},
        ]
        text = claims_to_summary_text(claims, chunk_preview="there is a fire")
        assert "fire" in text
        assert "second floor" in text
        assert "there is a fire" in text or "transcript" in text


class TestFindBestIncident:
    def test_empty_entries_returns_none(self):
        best_id, score = find_best_incident(
            "fire on third floor",
            "2025-01-15T14:00:00Z",
            [],
            use_embedding=False,
            use_llm=False,
        )
        assert best_id is None
        assert score == 0.0

    def test_single_incident_below_threshold_returns_none(self):
        entries = [
            ("inc-1", {"incident_type": {"value": "medical"}, "locations": []}, "2025-01-15T14:00:00Z"),
        ]
        best_id, score = find_best_incident(
            "fire on third floor",
            "2025-01-15T14:00:00Z",
            entries,
            use_embedding=False,
            use_llm=False,
            threshold=0.99,
        )
        assert best_id is None
        assert 0 <= score <= 1

    def test_single_incident_above_threshold_returns_it(self):
        entries = [
            ("inc-1", {"incident_type": {"value": "fire"}, "locations": [{"value": "third floor"}]}, "2025-01-15T14:00:00Z"),
        ]
        best_id, score = find_best_incident(
            "fire on third floor",
            "2025-01-15T14:00:00Z",
            entries,
            use_embedding=False,
            use_llm=False,
            threshold=0.3,
        )
        assert best_id == "inc-1"
        assert score >= 0.3


class TestNewIncidentId:
    def test_format(self):
        iid = new_incident_id()
        assert iid.startswith("incident-")
        assert len(iid) == len("incident-") + 12
