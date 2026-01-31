"""Tests for core models: ConfidenceValue, LocationValue, Incident, TimelineEvent."""

import pytest

from core.models import ConfidenceValue, LocationValue, Incident, TimelineEvent


class TestConfidenceValue:
    def test_clamps_confidence_to_one(self):
        cv = ConfidenceValue(value="fire", confidence=1.5)
        assert cv.confidence == 1.0

    def test_clamps_confidence_to_zero(self):
        cv = ConfidenceValue(value="fire", confidence=-0.1)
        assert cv.confidence == 0.0

    def test_to_dict(self):
        cv = ConfidenceValue(value="fire", confidence=0.65)
        assert cv.to_dict() == {"value": "fire", "confidence": 0.65}


class TestLocationValue:
    def test_to_dict_without_coords(self):
        loc = LocationValue(value="third floor", confidence=0.6)
        assert loc.to_dict() == {"value": "third floor", "confidence": 0.6}

    def test_to_dict_with_coords(self):
        loc = LocationValue(value="Device", confidence=0.9, lat=51.5, lng=-0.12)
        d = loc.to_dict()
        assert d["value"] == "Device"
        assert d["confidence"] == 0.9
        assert d["lat"] == 51.5
        assert d["lng"] == -0.12


class TestIncident:
    def test_empty_incident(self):
        inc = Incident(incident_id="i1")
        assert inc.incident_id == "i1"
        assert inc.device_location is None
        assert inc.locations == []
        assert inc.incident_type is None
        assert inc.people_estimate is None
        assert inc.hazards == []
        assert inc.timeline == []
        assert inc.last_updated is None

    def test_merge_confidence_never_reduces(self):
        inc = Incident(incident_id="i1")
        new = inc._merge_confidence(0.8, 0.5)
        assert new >= 0.8
        assert new <= 1.0


class TestTimelineEvent:
    def test_to_dict(self):
        e = TimelineEvent(time="2025-01-01T12:00:00Z", claim_type="location", value="third floor", confidence=0.6, source_text="on the third floor")
        d = e.to_dict()
        assert d["time"] == "2025-01-01T12:00:00Z"
        assert d["claim_type"] == "location"
        assert d["value"] == "third floor"
        assert d["confidence"] == 0.6
        assert d["source_text"] == "on the third floor"
