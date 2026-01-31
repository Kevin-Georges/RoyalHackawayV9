"""Tests for core engine: Bayesian posterior, apply_claims, get_incident_state_dict, device_location, demo_locations."""

import pytest

from core.models import Incident, ConfidenceValue, LocationValue
from core.engine import (
    bayesian_posterior,
    apply_claims,
    get_incident_state_dict,
    set_device_location,
    add_demo_locations,
    MAX_CONFIDENCE,
    DEFAULT_PRIOR,
)


class TestBayesianPosterior:
    def test_increases_with_high_likelihood(self):
        prior = 0.5
        post = bayesian_posterior(prior, 0.9)
        assert post > prior
        assert post <= MAX_CONFIDENCE

    def test_decreases_with_low_likelihood(self):
        prior = 0.6
        post = bayesian_posterior(prior, 0.2)
        assert post < prior
        assert post >= 0.05

    def test_caps_at_max_confidence(self):
        post = bayesian_posterior(0.9, 0.95)
        assert post <= MAX_CONFIDENCE

    def test_new_claim_default_prior_roughly_mid(self):
        post = bayesian_posterior(DEFAULT_PRIOR, 0.7)
        assert 0.4 <= post <= 0.8


class TestApplyClaims:
    def test_apply_single_incident_type(self, empty_incident, sample_claim):
        apply_claims(empty_incident, [sample_claim], judge_scores=None)
        assert empty_incident.incident_type is not None
        assert empty_incident.incident_type.value == "fire"
        assert 0 < empty_incident.incident_type.confidence <= MAX_CONFIDENCE
        assert len(empty_incident.timeline) == 1

    def test_apply_with_judge_scores_increases_confidence(self, empty_incident, sample_claims, judge_scores_high):
        apply_claims(empty_incident, sample_claims, judge_scores=judge_scores_high)
        assert empty_incident.incident_type is not None
        assert empty_incident.incident_type.value == "fire"
        assert empty_incident.incident_type.confidence > DEFAULT_PRIOR
        assert len(empty_incident.locations) == 1
        assert empty_incident.locations[0].value == "third floor"
        assert len(empty_incident.hazards) == 1
        assert len(empty_incident.timeline) == 3

    def test_repeated_claim_merges_confidence(self, empty_incident, sample_claim, judge_scores_high):
        apply_claims(empty_incident, [sample_claim], judge_scores={"incident_type::fire": 0.85})
        first_conf = empty_incident.incident_type.confidence
        apply_claims(empty_incident, [sample_claim], judge_scores={"incident_type::fire": 0.85})
        assert empty_incident.incident_type.confidence > first_conf

    def test_location_with_lat_lng(self, empty_incident):
        claim = {
            "claim_type": "location",
            "value": "second floor",
            "confidence": 0.6,
            "timestamp": "2025-01-01T12:00:00Z",
            "source_text": "second floor",
            "lat": 51.5,
            "lng": -0.12,
        }
        apply_claims(empty_incident, [claim])
        assert len(empty_incident.locations) == 1
        assert empty_incident.locations[0].lat == 51.5
        assert empty_incident.locations[0].lng == -0.12


class TestGetIncidentStateDict:
    def test_serializes_empty_incident(self, empty_incident):
        d = get_incident_state_dict(empty_incident)
        assert d["incident_id"] == "test-001"
        assert d["device_location"] is None
        assert d["locations"] == []
        assert d["incident_type"] is None
        assert d["people_estimate"] is None
        assert d["hazards"] == []
        assert d["timeline_count"] == 0
        assert d["timeline"] == []

    def test_serializes_device_location(self, empty_incident):
        set_device_location(empty_incident, 51.5, -0.12)
        d = get_incident_state_dict(empty_incident)
        assert d["device_location"] is not None
        assert d["device_location"]["lat"] == 51.5
        assert d["device_location"]["lng"] == -0.12


class TestSetDeviceLocation:
    def test_sets_device_location(self, empty_incident):
        set_device_location(empty_incident, 51.5074, -0.1278)
        assert empty_incident.device_location is not None
        assert empty_incident.device_location.value == "Device"
        assert empty_incident.device_location.lat == 51.5074
        assert empty_incident.device_location.lng == -0.1278
        assert empty_incident.last_updated is not None


class TestAddDemoLocations:
    def test_adds_locations_with_coords(self, empty_incident):
        demo = [
            {"value": "Second floor", "lat": 51.507, "lng": -0.128},
            {"value": "First floor", "lat": 51.508, "lng": -0.129},
        ]
        add_demo_locations(empty_incident, demo)
        assert len(empty_incident.locations) == 2
        assert empty_incident.locations[0].value == "Second floor"
        assert empty_incident.locations[0].lat == 51.507
        assert len(empty_incident.timeline) == 2
