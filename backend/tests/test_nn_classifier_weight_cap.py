"""Tests for the inverse-frequency kNN vote weight cap (platform-review O4).

sqrt(total_refs/count) is unbounded — a count-1 class (the always-seeded
SKIP prototype anchor; see _load_references' skip_values, which excludes
real SKIP rows entirely) gets a weight that grows with the corpus. Live
production counts (2026-07): SKIP's weight was ~73x a well-represented
class's weight, letting one high-ranking-but-wrong SKIP match outvote a
solid 6-neighbor genuine consensus. See classify_batch_nn's inline
comment for the exact measured numbers behind the 5.0 cap chosen here.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from app.models import LearnedClassification
from app.pipeline.analyze import nn_classifier


def _unit_at_cosine(reference: np.ndarray, cosine: float) -> np.ndarray:
    """A 2D unit vector whose cosine similarity to `reference` is `cosine`."""
    ref_angle = np.arctan2(reference[1], reference[0])
    offset = np.arccos(np.clip(cosine, -1.0, 1.0))
    angle = ref_angle - offset
    return np.array([np.cos(angle), np.sin(angle)])


class TestInverseFrequencyWeightCap:
    def _seed(self, db_session, n_org: int):
        for i in range(n_org):
            db_session.add(LearnedClassification(
                entity_name=f"ORG {i}", entity_type="donor_type",
                value="Org/Employees", confidence=0.9, source="fec",
            ))
        db_session.flush()

    def _run(self, db_session, skip_cosine: float, org_cosine: float):
        org_vec = np.array([1.0, 0.0])
        query_vec = _unit_at_cosine(org_vec, org_cosine)
        skip_vec = _unit_at_cosine(query_vec, skip_cosine)

        fake_model = MagicMock()

        def encode(texts, **kwargs):
            out = []
            for t in texts:
                if t.startswith("ORG "):
                    out.append(org_vec)
                elif "payment processor" in t:
                    out.append(skip_vec)
                else:
                    out.append(query_vec)
            return np.array(out)

        fake_model.encode.side_effect = encode

        with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=fake_model):
            return nn_classifier.classify_batch_nn(
                ["QUERY DONOR"], db_session, entity_type="donor_type",
                prototype_descriptions={"SKIP": nn_classifier.DONOR_TYPE_PROTOTYPES["SKIP"]},
                k=7, min_similarity=0.5,
            )

    def test_capped_weight_lets_genuine_consensus_win(self, db_session):
        """500 genuine Org/Employees references (weight ~1.0) vs the single
        always-present SKIP prototype anchor (weight would be ~22.4
        uncapped, capped to 5.0). A high-ranking (0.80 cosine) but wrong
        SKIP match must not beat a solid 6-neighbor genuine consensus at a
        realistic real similarity (0.75) once capped."""
        self._seed(db_session, n_org=500)
        result = self._run(db_session, skip_cosine=0.80, org_cosine=0.75)
        assert result["QUERY DONOR"] == "Org/Employees"

    def test_uncapped_weight_would_have_let_skip_win(self, db_session):
        """Same setup, but with the cap patched out — reproduces the
        pre-fix bug directly, proving the cap is what changed the outcome
        above (not some other difference in the two runs)."""
        self._seed(db_session, n_org=500)
        with patch("app.pipeline.analyze.nn_classifier._MAX_INV_FREQ_WEIGHT", 10_000.0):
            result = self._run(db_session, skip_cosine=0.80, org_cosine=0.75)
        assert result["QUERY DONOR"] == "SKIP"
