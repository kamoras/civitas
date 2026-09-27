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


def _referenced_names(source: str) -> set[str]:
    """Names a module reads or imports (not strings or comments mentioning them)."""
    import ast

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.alias):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


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

    def test_display_only_constant_is_exempt_but_its_neighbours_are_not(self):
        src = 'COLORS = {"A": "#111"}\nTHRESHOLD = 0.5\n'
        exempt = {"COLORS"}
        assert _normalized_source(src.replace("#111", "#222"), exempt) == _normalized_source(src, exempt)
        assert _normalized_source(src.replace("0.5", "0.6"), exempt) != _normalized_source(src, exempt)
        # Without the exemption the same recolor does count.
        assert _normalized_source(src.replace("#111", "#222")) != _normalized_source(src)

    def test_exemptions_name_real_things_and_nothing_reads_them_in_the_pipeline(self):
        """A stale exemption silently stops protecting anything; an
        exempted name that pipeline code read would hide a real change."""
        import pathlib

        from app.pipeline import senate_pipeline

        app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
        for rel in senate_pipeline._NOT_ANALYSIS_PATHS:
            assert (app_dir / rel).exists(), rel
        for rel, names in senate_pipeline._DISPLAY_ONLY_NAMES.items():
            source = (app_dir / rel).read_text()
            for name in names:
                assert f"{name}:" in source or f"{name} =" in source, (rel, name)
                for py in (app_dir / "pipeline").rglob("*.py"):
                    assert name not in _referenced_names(py.read_text()), (py, name)

    def test_exempt_coordination_modules_import_no_analysis_code(self):
        """Exempt because they classify and score nothing: an import of
        analysis code (or config_definitions, its constants) would mean an
        edit here could change a result the hash no longer notices."""
        import ast
        import pathlib

        from app.pipeline import senate_pipeline

        app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
        assert senate_pipeline._COORDINATION_PATHS <= senate_pipeline._NOT_ANALYSIS_PATHS
        for rel in senate_pipeline._COORDINATION_PATHS:
            for node in ast.walk(ast.parse((app_dir / rel).read_text())):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    continue
                for name in names:
                    assert not name.startswith(("app.pipeline.analyze", "app.pipeline.transform", "app.config_definitions")), (rel, name)
                    if name.startswith("app.pipeline."):
                        assert f"pipeline/{name.split('.')[2]}.py" in senate_pipeline._COORDINATION_PATHS, (rel, name)


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
