"""Tests for legislative-stage classification (app.pipeline.analyze.bill_stage).

Mirrors the style of test_bill_analyzer.py's TestPolicyAreaClassification:
fast tests cover the deterministic short-circuits (is_law, empty/short text)
without loading the embedding model; the embedding-based classification
itself is exercised in a @pytest.mark.slow class against realistic
latestAction strings.
"""

import pytest

from app.pipeline.analyze.bill_stage import classify_bill_stage, clear_stage_embedding_cache


class TestDeterministicShortCircuits:
    """is_law and empty/short text never touch the embedding model."""

    def test_is_law_short_circuits_to_enacted(self):
        # Even with text that reads like an earlier stage, the hard
        # congress.gov "Became Public Law" fact wins outright.
        assert classify_bill_stage("Referred to the Committee on Finance.", is_law=True) == "ENACTED"

    def test_empty_text_falls_back_to_introduced(self):
        assert classify_bill_stage("", is_law=False) == "INTRODUCED"

    def test_whitespace_only_text_falls_back_to_introduced(self):
        assert classify_bill_stage("   ", is_law=False) == "INTRODUCED"

    def test_very_short_text_falls_back_to_introduced(self):
        assert classify_bill_stage("ab", is_law=False) == "INTRODUCED"


@pytest.mark.slow
class TestEmbeddingClassification:
    """Embedding-prototype classification against realistic latestAction text."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        # Prototype embeddings are cached module-globally; clear before and
        # after so this class doesn't leak state into/out of other slow tests.
        clear_stage_embedding_cache()
        yield
        clear_stage_embedding_cache()

    @pytest.mark.parametrize(
        "latest_action, expected",
        [
            ("Introduced in the Senate. Read the first time.", "INTRODUCED"),
            ("Referred to the House Committee on Ways and Means.", "IN_COMMITTEE"),
            ("Reported by the Committee on the Judiciary with an amendment.", "IN_COMMITTEE"),
            ("Passed Senate with an amendment by Yea-Nay Vote.", "PASSED_CHAMBER"),
            ("Passed House amended.", "PASSED_CHAMBER"),
            ("Received in the Senate after passing the House.", "IN_OTHER_CHAMBER"),
            ("Presented to the President.", "TO_PRESIDENT"),
            ("Became Public Law No: 119-42.", "ENACTED"),
            ("Vetoed by the President.", "VETOED"),
        ],
    )
    def test_classifies_realistic_latest_action(self, latest_action, expected):
        assert classify_bill_stage(latest_action, is_law=False) == expected
