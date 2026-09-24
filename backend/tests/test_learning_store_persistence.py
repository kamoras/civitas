"""Learning-store persistence: what invalidates it, and what kNN learns from.

Two properties the self-training design depends on:

1. The analysis-code fingerprint changes when behavior can change and ONLY
   then — a reworded comment or docstring must not wipe learned data.
2. kNN votes only with labels an upstream tier produced, never with its
   own earlier outputs, so one run's guess can't become the next run's
   evidence.
"""

from app.models import LearnedClassification
from app.pipeline.analyze.nn_classifier import KNN_SOURCE, _load_references
from app.pipeline.senate_pipeline import (
    _compute_analysis_code_hash,
    _normalized_source,
)

BASE = '''"""Module docstring."""

THRESHOLD = 0.65  # calibrated 2026-07


def classify(x):
    """Function docstring."""
    # explain the rule
    return x > THRESHOLD


class Model:
    """Class docstring."""

    def run(self):
        """Method docstring."""
        return 1
'''


class TestNormalizedSource:
    def test_comment_edit_does_not_change_fingerprint(self):
        edited = BASE.replace("# explain the rule", "# a completely rewritten explanation")
        edited = edited.replace("# calibrated 2026-07", "# recalibrated 2026-09 after audit")
        assert _normalized_source(edited) == _normalized_source(BASE)

    def test_docstring_edit_does_not_change_fingerprint(self):
        edited = (
            BASE.replace('"""Module docstring."""', '"""Rewritten module docs."""')
            .replace('"""Function docstring."""', '"""New wording."""')
            .replace('"""Class docstring."""', '"""Other."""')
            .replace('"""Method docstring."""', '"""Also changed."""')
        )
        assert _normalized_source(edited) == _normalized_source(BASE)

    def test_blank_line_and_formatting_edit_does_not_change_fingerprint(self):
        edited = BASE.replace("return x > THRESHOLD", "return (x\n            > THRESHOLD)")
        assert _normalized_source(edited) == _normalized_source(BASE)

    def test_threshold_change_changes_fingerprint(self):
        assert _normalized_source(BASE.replace("0.65", "0.70")) != _normalized_source(BASE)

    def test_logic_change_changes_fingerprint(self):
        assert _normalized_source(BASE.replace("x > THRESHOLD", "x >= THRESHOLD")) != _normalized_source(BASE)

    def test_non_docstring_string_constant_is_kept(self):
        # Prototype descriptions and prompts are string constants, not
        # docstrings — editing one changes classification and must count.
        src = 'PROTOTYPE = "a pharmaceutical manufacturer"\n'
        assert _normalized_source(src) != _normalized_source(src.replace("pharmaceutical", "medical device"))

    def test_docstring_only_function_still_parses(self):
        src = 'def f():\n    """Only a docstring."""\n'
        assert _normalized_source(src)  # no exception, non-empty

    def test_live_tree_fingerprint_is_stable(self):
        assert _compute_analysis_code_hash() == _compute_analysis_code_hash()


class TestKnnReferencesExcludeOwnOutputs:
    def _add(self, db, name, value, source):
        db.add(LearnedClassification(
            entity_name=name, entity_type="industry", value=value,
            confidence=0.9, source=source,
        ))

    def test_knn_labels_are_not_reference_examples(self, db_session):
        self._add(db_session, "PFIZER INC", "PHARMA", "fec")
        self._add(db_session, "EXXON MOBIL", "OIL_GAS", "embedding")
        self._add(db_session, "ACME HOLDINGS", "FINANCE", KNN_SOURCE)
        db_session.commit()

        names, labels = _load_references(db_session, "industry")

        assert "ACME HOLDINGS" not in names
        assert set(names) == {"PFIZER INC", "EXXON MOBIL"}
        assert len(names) == len(labels)

    def test_prototypes_still_seed_the_reference_set(self, db_session):
        self._add(db_session, "ACME HOLDINGS", "FINANCE", KNN_SOURCE)
        db_session.commit()

        names, labels = _load_references(
            db_session, "industry", prototype_descriptions={"FINANCE": "a bank or investment firm"},
        )

        assert names == ["a bank or investment firm"]
        assert labels == ["FINANCE"]
