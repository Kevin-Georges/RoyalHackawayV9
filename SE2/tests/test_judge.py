"""Tests for Judge: default scores when no OpenAI, claim_id building."""

import os
import pytest

from extractors.judge import judge_support_scores, _claim_id, _default_scores


class TestJudgeClaimId:
    def test_claim_id_format(self):
        assert _claim_id("incident_type", "fire") == "incident_type::fire"
        assert _claim_id("location", "Third Floor") == "location::third floor"


class TestJudgeDefaultScores:
    def test_default_scores_for_extracted_claims(self):
        claims = [
            {"claim_type": "incident_type", "value": "fire"},
            {"claim_type": "hazard", "value": "smoke"},
        ]
        scores = _default_scores(claims)
        assert "incident_type::fire" in scores
        assert "hazard::smoke" in scores
        assert scores["incident_type::fire"] == 0.55
        assert scores["hazard::smoke"] == 0.55

    def test_judge_returns_empty_without_chunk(self):
        scores = judge_support_scores({}, "", [])
        assert scores == {}

    @pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_judge_with_openai_returns_scores(self):
        state = {
            "incident_type": {"value": "fire", "confidence": 0.6},
            "locations": [],
            "hazards": [],
            "people_estimate": None,
        }
        claims = [{"claim_type": "incident_type", "value": "fire", "source_text": "yes it's a fire"}]
        scores = judge_support_scores(state, "yes it's definitely a fire", claims)
        assert isinstance(scores, dict)
        if scores:
            assert "incident_type::fire" in scores
            assert 0 <= scores["incident_type::fire"] <= 1.0
