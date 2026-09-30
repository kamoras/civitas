"""Tests for the hybrid industry classifier.

Tests the three classification tiers:
1. Learning store lookup (DB-backed)
2. Embedding cosine similarity (sentence-transformers)
3. Fallback to OTHER, resolved later by the kNN classifier
   (nn_classifier.py) — this module has no LLM tier of its own.
"""

import pytest

from app.models import LearnedClassification
from app.pipeline.transform.industry_classifier import (
    INDUSTRY_DESCRIPTIONS,
    classify_batch_with_learning,
    classify_industries_batch_scored,
    classify_industry,
    classify_industry_with_provenance,
    classify_with_learning,
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


class TestNoModelNeeded:
    """Paths that never reach the embedding model, so they run in the fast job."""

    @pytest.mark.parametrize("org_name", [
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
        pytest.param("A", id="very_short"),
    ])
    def test_empty_or_very_short_input_is_other(self, org_name):
        assert classify_industry(org_name) == "OTHER"

    @pytest.mark.parametrize("old, new", [
        pytest.param(("OTHER", 0.5, "llm"), ("FINANCE", 0.9, "embedding"), id="higher_confidence_overwrites"),
        # No confidence guard: the current run's classification always wins.
        pytest.param(("FINANCE", 0.9, "embedding"), ("TECH", 0.5, "llm"), id="lower_confidence_overwrites_too"),
    ])
    def test_latest_classification_always_overwrites(self, db_session, old, new):
        from app.pipeline.transform.industry_classifier import _store_classification

        value, confidence, source = old
        db_session.add(LearnedClassification(
            entity_name="TEST CORP", entity_type="industry", value=value, confidence=confidence, source=source,
        ))
        db_session.flush()

        _store_classification(db_session, "TEST CORP", "industry", *new)

        stored = db_session.query(LearnedClassification).filter(LearnedClassification.entity_name == "TEST CORP").one()
        assert (stored.value, stored.confidence, stored.source) == new


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

    def test_learning_store_miss_falls_to_embedding_and_is_stored(self, db_session):
        result, source = classify_with_learning("Goldman Sachs", db_session)
        assert result == "FINANCE"
        assert source == "embedding"

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

    def test_learning_store_miss_unknown_returns_other(self, db_session):
        result, source = classify_with_learning("Xylophone Kumquat Zephyr", db_session)
        assert result == "OTHER"
        assert source == "unknown"


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


@pytest.mark.slow
class TestBatchScoredAgreesWithSingleEntity:
    """classify_industries_batch_scored used to re-implement the POLITICAL/
    PAC-naming decontextualization decision independently (numpy arrays,
    same SPREAD_THRESHOLD/POLITICAL_MARGIN logic as _decontextualize_political)
    — two places a threshold change could go out of sync. Both paths now
    call the same helper; these prove they still agree, including on the
    PAC-naming case the decontextualization exists for."""

    @pytest.mark.parametrize("org_name", [
        "Goldman Sachs Investment Banking",
        "Teamsters Political Action Committee",  # PAC-naming decontextualization case
        "Xylophone Kumquat Zephyr",  # OTHER
    ])
    def test_batch_and_single_entity_agree(self, org_name):
        single_industry, _ = classify_industry_with_provenance(org_name)
        batch_result = classify_industries_batch_scored([org_name])

        if single_industry == "OTHER":
            assert org_name not in batch_result
        else:
            assert batch_result[org_name][0] == single_industry

    def test_pac_naming_decontextualizes_away_from_political(self):
        # "Political Action Committee" alone scores highest on POLITICAL
        # (confirmed via meta below) — the exact case decontextualization
        # exists for. Asserting the mechanism fired (final result differs
        # from the raw top match) rather than a specific target industry,
        # since the runner-up depends on embedding-space specifics.
        result, meta = classify_industry_with_provenance("Teamsters Political Action Committee")
        assert meta["top_match"] == "POLITICAL"
        assert result != "POLITICAL"
        assert result != "OTHER"
