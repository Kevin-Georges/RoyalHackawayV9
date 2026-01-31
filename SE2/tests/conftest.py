"""Pytest fixtures for incident summary tests."""

import pytest

from core.models import Incident, ConfidenceValue, LocationValue, TimelineEvent


@pytest.fixture
def empty_incident():
    """Fresh incident with no state."""
    return Incident(incident_id="test-001")


@pytest.fixture
def sample_claim():
    """Single claim dict as returned by extractors."""
    return {
        "claim_type": "incident_type",
        "value": "fire",
        "confidence": 0.7,
        "timestamp": "2025-01-01T12:00:00Z",
        "source_text": "there is a fire",
    }


@pytest.fixture
def sample_claims():
    """Multiple claims (location, incident_type, hazard)."""
    ts = "2025-01-01T12:00:00Z"
    return [
        {"claim_type": "location", "value": "third floor", "confidence": 0.6, "timestamp": ts, "source_text": "fire on the third floor"},
        {"claim_type": "incident_type", "value": "fire", "confidence": 0.7, "timestamp": ts, "source_text": "fire on the third floor"},
        {"claim_type": "hazard", "value": "fire", "confidence": 0.65, "timestamp": ts, "source_text": "fire on the third floor"},
    ]


@pytest.fixture
def judge_scores_high():
    """Judge support scores that should increase confidence."""
    return {
        "location::third floor": 0.9,
        "incident_type::fire": 0.9,
        "hazard::fire": 0.85,
    }


@pytest.fixture
def app_client():
    """FastAPI TestClient. Clears in-memory incidents before each use."""
    from fastapi.testclient import TestClient
    import api.main as main_module
    main_module.incidents.clear()
    return TestClient(main_module.app)
