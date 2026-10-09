"""kNN returns a label only when enough of the k neighbours carry it."""

from unittest.mock import MagicMock, patch

import numpy as np

from app.models import LearnedClassification
from app.pipeline.analyze import nn_classifier


def _run(db_session, labels, min_agreement):
    for i, label in enumerate(labels):
        db_session.add(LearnedClassification(
            entity_name=f"REF {i}", entity_type="industry", value=label, confidence=0.9, source="fec",
        ))
    db_session.flush()
    model = MagicMock()
    model.encode.side_effect = lambda texts, **kw: np.array([[1.0, 0.0] for _ in texts])
    with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=model):
        return nn_classifier.classify_batch_nn(
            ["QUERY CO"], db_session, entity_type="industry", k=7, min_agreement=min_agreement,
        )["QUERY CO"]


def test_a_split_neighbourhood_is_left_unclassified(db_session):
    assert _run(db_session, ["FINANCE"] * 6 + ["TECH"], min_agreement=7) == "OTHER"


def test_unanimous_neighbours_decide(db_session):
    assert _run(db_session, ["FINANCE"] * 7, min_agreement=7) == "FINANCE"


def test_the_default_is_a_plurality(db_session):
    assert _run(db_session, ["FINANCE"] * 6 + ["TECH"], min_agreement=1) == "FINANCE"
