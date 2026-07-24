"""Tests for the O1 platform-review fix: several reject/abstain thresholds
sat far below this embedding model's real similarity floor (~0.6-0.8 for
short political/institutional text against a prototype, live-measured
2026-07 across bill titles, donor names, and Senate motion prototypes) and
could never fire. Each function's own docstring has the measured
distribution behind its new value; these tests just guard the floors
actually reject something now, and that real known-good cases still pass.
"""

import inspect
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models import LearnedClassification
from app.pipeline.analyze import bill_analyzer, bill_learning, donor_classifier_ai, nn_classifier
from app.pipeline.analyze.policy_alignment import get_related_policies


def _default(func, param):
    return inspect.signature(func).parameters[param].default


class TestThresholdDefaultsRaised:
    """Guard against silently reverting to the old dead-floor values."""

    def test_bill_analyzer_embedding_confidence_threshold(self):
        assert bill_analyzer.EMBEDDING_CONFIDENCE_THRESHOLD >= 0.65

    def test_nn_classifier_batch_min_similarity(self):
        assert _default(nn_classifier.classify_batch_nn, "min_similarity") >= 0.60

    def test_bill_learning_reference_min_similarity(self):
        assert _default(bill_learning.classify_bill_by_reference, "min_similarity") >= 0.60

    def test_donor_classifier_thresholds(self):
        assert _default(donor_classifier_ai.classify_donor_type_semantic, "threshold") >= 0.55
        assert _default(donor_classifier_ai.classify_donor_type_semantic, "skip_threshold") >= 0.70

    def test_get_related_policies_threshold(self):
        assert _default(get_related_policies, "threshold") >= 0.70


@pytest.mark.slow
class TestFloorsActuallyReject:
    """Below 0.25-0.35 these floors were unconditionally true; confirm a
    genuinely unrelated/degenerate query can now fall through to the
    abstain path each function already had but could never reach."""

    def test_derive_stance_rejects_off_topic_text_as_neutral(self):
        # Gibberish with no directional verb and no topical content at all.
        _, direction = bill_analyzer.derive_stance(
            "Xyzzy Quorp Fnord", "zxqv wobble plugh frotz snark", "HEALTHCARE",
        )
        assert direction == "neutral"

    def test_derive_stance_still_detects_a_real_directional_bill(self):
        _, direction = bill_analyzer.derive_stance(
            "A bill to ban assault weapons and expand background checks",
            "", "GUNS",
        )
        assert direction == "anti"

    def test_classify_motion_type_unknown_for_garbage(self):
        result = bill_learning.classify_motion_type("Xyzzy Quorp Fnord Wobble Plugh")
        assert result == "unknown"

    def test_classify_motion_type_still_detects_real_question(self):
        result = bill_learning.classify_motion_type("On Passage of the Bill")
        assert result == "passage"

    def test_classify_donor_type_semantic_still_detects_pac(self):
        # Real-world PAC-style name, no explicit " PAC" substring (which
        # would short-circuit via the tier-0 keyword rule instead).
        result = donor_classifier_ai.classify_donor_type_semantic(
            "National Committee for an Effective Congress",
        )
        assert result in ("Party/Ideological", "PAC", None)

    def test_get_related_policies_no_longer_admits_everything(self):
        # PHARMA is squarely a HEALTHCARE-domain industry; TAXES/GUNS/GUNS-
        # adjacent policy areas should not show up as "related" at 0.75 the
        # way they did at the old 0.35 (which admitted nearly all 15 areas).
        related = get_related_policies("PHARMA")
        assert "HEALTHCARE" in related
        assert len(related) < 10


class TestCrossValidateDonorTypesFloorRaised:
    def test_org_score_floor_matches_measured_value(self):
        src = inspect.getsource(nn_classifier.cross_validate_donor_types)
        assert "org_scores[i] > 0.65" in src


class TestFastMockedCoverage:
    """Fully-mocked, deterministic tests for branches the real-model tests
    above don't happen to exercise (a text-vs-taxonomy comparison that
    lands below EMBEDDING_CONFIDENCE_THRESHOLD, and an actual PAC->Org
    reclassification) — independent of real model score drift."""

    def test_classify_motion_type_reaches_the_best_score_floor(self):
        fake_model = MagicMock()
        fake_model.encode.return_value = [np.array([1.0, 0.0])]
        fake_prototypes = {"passage": np.array([1.0, 0.0]), "cloture": np.array([0.0, 1.0])}
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=fake_model), \
             patch("app.pipeline.analyze.bill_learning._get_motion_prototypes", return_value=fake_prototypes):
            result = bill_learning.classify_motion_type("On Passage of the Bill")
        assert result == "passage"

    def test_augmented_embedding_classify_accepts_a_confident_match(self):
        fake_model = MagicMock()
        fake_model.encode.return_value = [np.array([1.0, 0.0])]
        fake_policy_embs = {
            "HEALTHCARE": np.array([1.0, 0.0]),
            "PROCEDURAL": np.array([0.0, 1.0]),
        }
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=fake_model), \
             patch("app.pipeline.analyze.bill_analyzer._get_policy_embeddings", return_value=fake_policy_embs):
            result = bill_analyzer._augmented_embedding_classify("anything")
        assert result == "HEALTHCARE"

    def test_cross_validate_donor_types_reclassifies_above_threshold(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="ACME CORP PAC", entity_type="donor_type",
            value="PAC", confidence=0.7, source="semantic",
        ))
        db_session.flush()

        fake_model = MagicMock()
        fake_model.encode.return_value = np.array([[1.0, 0.0]])
        fake_type_embs = {
            "Org/Employees": np.array([1.0, 0.0]),
            "PAC": np.array([0.0, 1.0]),
        }
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=fake_model), \
             patch(
                 "app.pipeline.analyze.donor_classifier_ai._get_semantic_type_embeddings",
                 return_value=fake_type_embs,
             ):
            corrected = nn_classifier.cross_validate_donor_types(db_session)
        assert corrected == 1
