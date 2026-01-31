"""Tests for OpenAI extractor: hazard→incident_type reclassification."""

import pytest

from extractors.openai_extractor import _parse_llm_response


class TestHazardReclassifiedAsIncidentType:
    """When LLM returns incident-type terms in hazards array, they become incident_type claims."""

    def test_fire_in_hazards_becomes_incident_type(self):
        raw = '{"hazards": [{"value": "fire", "confidence": 0.8}]}'
        claims = _parse_llm_response(raw, "there is a fire", "2025-01-01T00:00:00Z")
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        hazards = [c for c in claims if c["claim_type"] == "hazard"]
        assert len(inc) == 1
        assert inc[0]["value"] == "fire"
        assert len(hazards) == 0

    def test_smoke_in_hazards_becomes_incident_type_fire(self):
        raw = '{"hazards": [{"value": "smoke", "confidence": 0.7}]}'
        claims = _parse_llm_response(raw, "lots of smoke", "2025-01-01T00:00:00Z")
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["value"] == "fire"

    def test_trapped_in_hazards_stays_hazard(self):
        raw = '{"hazards": [{"value": "trapped", "confidence": 0.8}]}'
        claims = _parse_llm_response(raw, "people are trapped", "2025-01-01T00:00:00Z")
        hazards = [c for c in claims if c["claim_type"] == "hazard"]
        assert len(hazards) == 1
        assert hazards[0]["value"] == "trapped"

    def test_mixed_hazards_fire_and_trapped(self):
        raw = '{"hazards": [{"value": "fire", "confidence": 0.8}, {"value": "trapped", "confidence": 0.7}]}'
        claims = _parse_llm_response(raw, "fire and people trapped", "2025-01-01T00:00:00Z")
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        hazards = [c for c in claims if c["claim_type"] == "hazard"]
        assert any(c["value"] == "fire" for c in inc)
        assert any(c["value"] == "trapped" for c in hazards)

    def test_incident_type_probability_is_neutral(self):
        """Incident-type probability is decided by Judge + Bayesian; LLM only provides value."""
        from extractors.openai_extractor import NEUTRAL_INCIDENT_TYPE_CONFIDENCE
        raw = '{"incident_type": {"value": "fire", "confidence": 0.9}}'
        claims = _parse_llm_response(raw, "there is a fire", "2025-01-01T00:00:00Z")
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["confidence"] == NEUTRAL_INCIDENT_TYPE_CONFIDENCE

    def test_incident_type_accepted_as_string(self):
        """LLM sometimes returns incident_type as string; must still update."""
        raw = '{"incident_type": "fire"}'
        claims = _parse_llm_response(raw, "there is a fire", "2025-01-01T00:00:00Z")
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["value"] == "fire"
