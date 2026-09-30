"""Learning-store persistence: what invalidates it, and what kNN learns from.

Two properties the self-training design depends on:

1. The analysis-code fingerprint changes when behavior can change and ONLY
   then — a reworded comment or docstring must not wipe learned data.
2. kNN votes only with labels an upstream tier produced, never with its
   own earlier outputs, so one run's guess can't become the next run's
   evidence.
"""

import pytest

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
    @pytest.mark.parametrize("edited", [
        pytest.param(
            BASE.replace("# explain the rule", "# a completely rewritten explanation")
            .replace("# calibrated 2026-07", "# recalibrated 2026-09 after audit"),
            id="comment_edit",
        ),
        pytest.param(
            BASE.replace('"""Module docstring."""', '"""Rewritten module docs."""')
            .replace('"""Function docstring."""', '"""New wording."""')
            .replace('"""Class docstring."""', '"""Other."""')
            .replace('"""Method docstring."""', '"""Also changed."""'),
            id="docstring_edit",
        ),
        pytest.param(
            BASE.replace("return x > THRESHOLD", "return (x\n            > THRESHOLD)"),
            id="blank_line_and_formatting_edit",
        ),
    ])
    def test_edit_that_cannot_change_behavior_does_not_change_fingerprint(self, edited):
        assert edited != BASE
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

    def test_lobbying_records_is_read_only_by_the_lda_fetch(self):
        """Exempt because it only decides which filing-named bills are shown
        beside donor-vote connections: if a hashed analysis module imported
        it, a retune could change a result the hash no longer notices."""
        import pathlib

        from app.pipeline import senate_pipeline

        app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
        import ast

        def imports_it(tree: ast.AST) -> bool:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    (node.module or "").endswith("lobbying_records")
                    or any(a.name == "lobbying_records" for a in node.names)
                ):
                    return True
                if isinstance(node, ast.Import) and any(a.name.endswith("lobbying_records") for a in node.names):
                    return True
            return False

        importers = {
            str(py.relative_to(app_dir))
            for py in (app_dir / "pipeline").rglob("*.py")
            if imports_it(ast.parse(py.read_text()))
        }
        assert importers == {"pipeline/fetch/lda.py"}

    def test_the_explore_summary_prompt_is_read_by_no_pipeline_code(self):
        """Exempt because only the Explore summary service reads it: a
        pipeline import would make its edits changes the hash misses."""
        import ast
        import pathlib

        from app.pipeline import senate_pipeline

        assert "pipeline/analyze/prompts.py" in senate_pipeline._NOT_ANALYSIS_PATHS
        app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
        for py in [*(app_dir / "pipeline").rglob("*.py"), app_dir / "config_definitions.py"]:
            for node in ast.walk(ast.parse(py.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert node.module != "app.pipeline.analyze.prompts", py
                    if node.module == "app.pipeline.analyze":
                        assert all(a.name != "prompts" for a in node.names), py
                elif isinstance(node, ast.Import):
                    assert all(a.name != "app.pipeline.analyze.prompts" for a in node.names), py

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

    def test_hashed_code_calls_the_publishing_modules_only_through_their_entry_points(self):
        """The publishing modules are exempt because they only word and send
        posts. A hashed module importing one of their helpers or constants
        into an analysis decision would be a change the hash no longer
        notices, so the only names hashed code may take from them are the
        post_*/process_* calls that publish."""
        import ast
        import pathlib

        from app.pipeline import senate_pipeline

        app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
        assert senate_pipeline._PUBLISHING_PATHS <= senate_pipeline._NOT_ANALYSIS_PATHS
        modules = {
            "app." + rel.removesuffix(".py").replace("/", "."): rel
            for rel in senate_pipeline._PUBLISHING_PATHS
        }
        hashed = [
            py for py in [*(app_dir / "pipeline").rglob("*.py"), app_dir / "config_definitions.py"]
            if "/fetch/" not in str(py)
            and py.relative_to(app_dir).as_posix() not in senate_pipeline._NOT_ANALYSIS_PATHS
        ]
        for py in hashed:
            for node in ast.walk(ast.parse(py.read_text())):
                if isinstance(node, ast.Import):
                    assert not any(a.name in modules for a in node.names), (py, ast.unparse(node))
                elif isinstance(node, ast.ImportFrom):
                    if node.module in modules:
                        for a in node.names:
                            assert a.name.startswith(("post_", "process_")), (py, a.name)
                    elif node.module == "app.pipeline.analyze":
                        assert not any(f"app.pipeline.analyze.{a.name}" in modules for a in node.names), (
                            py, ast.unparse(node))


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
