"""Tests for the hybrid industry classifier.

Tests the three classification tiers:
1. Learning store lookup (DB-backed)
2. Embedding cosine similarity (sentence-transformers)
3. Fallback to OTHER (for LLM reclassification)
"""

import pytest

from app.models import LearnedClassification
from app.pipeline.transform.industry_classifier import (
    INDUSTRY_DESCRIPTIONS,
    classify_batch_with_learning,
    classify_industry,
    classify_with_learning,
    store_llm_classifications,
)


@pytest.mark.slow
class TestEmbeddingClassification:
    """Tier 2: embedding cosine similarity against industry descriptions."""

    @pytest.mark.parametrize(
        "org_name, expected",
        [
            ("Goldman Sachs Investment Banking", "FINANCE"),
            ("JPMorgan Chase Bank", "FINANCE"),
            ("Wells Fargo Bank", "FINANCE"),
            ("Pfizer Inc", "PHARMA"),
            ("National Rifle Association", "GUNS"),
            ("Exxon Mobil Corporation", "OIL_GAS"),
            ("Lockheed Martin Aerospace Defense", "DEFENSE"),
            ("Verizon Communications", "TELECOM"),
            ("Blue Cross Blue Shield", "INSURANCE"),
            ("United Auto Workers Union", "LABOR_UNIONS"),
        ],
    )
    def test_well_known_entities(self, org_name, expected):
        result = classify_industry(org_name)
        assert result == expected, f"{org_name}: got {result}, expected {expected}"

    def test_short_names_handled_gracefully(self):
        """Short company names may classify to a neighbor industry or OTHER.
        The learning store and LLM tiers handle these. We just verify no crashes."""
        for name in [
            "Google LLC", "Microsoft Corporation", "Raytheon Technologies",
            "Merck & Company", "SEIU", "Yale University", "AT&T Inc",
        ]:
            result = classify_industry(name)
            assert isinstance(result, str)
            assert result in list(INDUSTRY_DESCRIPTIONS.keys()) + ["OTHER"]

    def test_unknown_entity_returns_other(self):
        assert classify_industry("Xylophone Kumquat Zephyr") == "OTHER"

    def test_empty_input(self):
        assert classify_industry("") == "OTHER"
        assert classify_industry(None) == "OTHER"

    def test_very_short_input(self):
        assert classify_industry("A") == "OTHER"


@pytest.mark.slow
class TestLearningStore:
    """Tier 1: persistent learning store lookup."""

    def test_learning_store_lookup(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="ACME WIDGETS INC",
            entity_type="industry",
            value="MANUFACTURING",
            confidence=0.9,
            source="embedding",
        ))
        db_session.flush()

        result, source = classify_with_learning("ACME WIDGETS INC", db_session)
        assert result == "MANUFACTURING"
        assert source == "learned"

    def test_learning_store_miss_falls_to_embedding(self, db_session):
        result, source = classify_with_learning("Goldman Sachs", db_session)
        assert result == "FINANCE"
        assert source == "embedding"

    def test_learning_store_miss_unknown_returns_other(self, db_session):
        result, source = classify_with_learning("Xylophone Kumquat Zephyr", db_session)
        assert result == "OTHER"
        assert source == "unknown"

    def test_embedding_result_stored_in_learning_store(self, db_session):
        classify_with_learning("Goldman Sachs", db_session)

        stored = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == "GOLDMAN SACHS",
                LearnedClassification.entity_type == "industry",
            )
            .first()
        )
        assert stored is not None
        assert stored.value == "FINANCE"
        assert stored.source == "embedding"

    def test_store_llm_classifications(self, db_session):
        store_llm_classifications({"ACME CORP": "MANUFACTURING", "BETA INC": "TECH"}, db_session)

        acme = (
            db_session.query(LearnedClassification)
            .filter(LearnedClassification.entity_name == "ACME CORP")
            .first()
        )
        assert acme is not None
        assert acme.value == "MANUFACTURING"
        assert acme.source == "llm"
        assert acme.confidence == 0.7

    def test_higher_confidence_overwrites(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="TEST CORP",
            entity_type="industry",
            value="OTHER",
            confidence=0.5,
            source="llm",
        ))
        db_session.flush()

        from app.pipeline.transform.industry_classifier import _store_classification
        _store_classification(db_session, "TEST CORP", "industry", "FINANCE", 0.9, "embedding")

        stored = (
            db_session.query(LearnedClassification)
            .filter(LearnedClassification.entity_name == "TEST CORP")
            .first()
        )
        assert stored.value == "FINANCE"
        assert stored.confidence == 0.9

    def test_lower_confidence_does_not_overwrite(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="TEST CORP",
            entity_type="industry",
            value="FINANCE",
            confidence=0.9,
            source="embedding",
        ))
        db_session.flush()

        from app.pipeline.transform.industry_classifier import _store_classification
        _store_classification(db_session, "TEST CORP", "industry", "TECH", 0.5, "llm")

        stored = (
            db_session.query(LearnedClassification)
            .filter(LearnedClassification.entity_name == "TEST CORP")
            .first()
        )
        assert stored.value == "FINANCE"


@pytest.mark.slow
class TestBatchClassification:
    """Batch classification with learning store integration."""

    def test_batch_returns_results_and_unknowns(self, db_session):
        names = ["Goldman Sachs", "Pfizer Inc", "Xylophone Kumquat Zephyr"]
        results, unknowns = classify_batch_with_learning(names, db_session)

        assert results["Goldman Sachs"] == "FINANCE"
        assert results["Pfizer Inc"] == "PHARMA"
        assert results["Xylophone Kumquat Zephyr"] == "OTHER"
        assert "Xylophone Kumquat Zephyr" in unknowns
        assert "Goldman Sachs" not in unknowns

    def test_batch_uses_learning_store(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="MYSTERY CORP",
            entity_type="industry",
            value="RETAIL",
            confidence=0.7,
            source="llm",
        ))
        db_session.flush()

        results, unknowns = classify_batch_with_learning(
            ["MYSTERY CORP", "Goldman Sachs"], db_session
        )
        assert results["MYSTERY CORP"] == "RETAIL"
        assert "MYSTERY CORP" not in unknowns
