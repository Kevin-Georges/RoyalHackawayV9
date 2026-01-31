"""Tests for regex extractor: location, incident_type, people_count, hazards."""

import pytest

from extractors.regex_extractor import extract_claims


class TestExtractIncidentType:
    def test_fire(self):
        claims = extract_claims("there is a fire on the third floor", context=None)
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) >= 1
        assert any(c["value"] == "fire" for c in inc)

    def test_gun_shot_assault(self):
        claims = extract_claims("there's a gun shot", context=None)
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["value"] == "assault"

    def test_gunshot_assault(self):
        claims = extract_claims("I heard a gunshot", context=None)
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["value"] == "assault"

    def test_shooting_assault(self):
        claims = extract_claims("there was a shooting", context=None)
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        assert len(inc) == 1
        assert inc[0]["value"] == "assault"


class TestExtractLocation:
    def test_third_floor(self):
        claims = extract_claims("fire on the third floor", context=None)
        locs = [c for c in claims if c["claim_type"] == "location"]
        assert len(locs) >= 1
        assert any("third" in c["value"].lower() and "floor" in c["value"].lower() for c in locs)

    def test_multiple_locations_in_one_chunk(self):
        claims = extract_claims("someone on second floor and another on first floor", context=None)
        locs = [c for c in claims if c["claim_type"] == "location"]
        assert len(locs) >= 2


class TestExtractPeopleCount:
    def test_two_or_three(self):
        claims = extract_claims("i think two or three people are trapped", context=None)
        people = [c for c in claims if c["claim_type"] == "people_count"]
        assert len(people) == 1
        assert people[0]["value"] == "2-3"

    def test_three_people(self):
        claims = extract_claims("three people inside", context=None)
        people = [c for c in claims if c["claim_type"] == "people_count"]
        assert len(people) == 1
        assert people[0]["value"] == "3"


class TestExtractHazards:
    def test_fire_and_trapped(self):
        claims = extract_claims("there is a fire and people are trapped", context=None)
        # Fire is incident type; only situational dangers like trapped are hazards
        inc = [c for c in claims if c["claim_type"] == "incident_type"]
        hazards = [c for c in claims if c["claim_type"] == "hazard"]
        values = [h["value"] for h in hazards]
        assert any(c["value"] == "fire" for c in inc)
        assert "trapped" in values


class TestExtractClaimsStructure:
    def test_each_claim_has_required_keys(self):
        claims = extract_claims("fire on the third floor", context=None)
        for c in claims:
            assert "claim_type" in c
            assert "value" in c
            assert "confidence" in c
            assert "timestamp" in c
            assert "source_text" in c

    def test_empty_text_returns_empty(self):
        assert extract_claims("", context=None) == []
        assert extract_claims("   ", context=None) == []
