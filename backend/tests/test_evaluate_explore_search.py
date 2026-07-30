"""Tests for the search-evaluation harness's probe construction and metrics.

The harness is the instrument AGENTS.md principle 3b points at for moving
ranking weights, so it needs to be correct before anyone trusts a number
it prints. It can't be run end to end here (it needs a populated live
index), which is exactly why its pure parts are worth pinning down: a
harness that silently builds degenerate queries would report a hybrid
"win" that means nothing.
"""

import importlib.util
import pathlib

import pytest

# Module scope, not inside the fixture: conftest's db_session runs
# Base.metadata.create_all before any fixture body executes, and that only
# creates tables whose models have already been imported. Importing this
# lazily leaves the run with no explore_documents table — and only when the
# file runs alone, since any other module that imports it first hides the
# problem.
from app.models import ExploreDocument  # noqa: E402

_SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "evaluate_explore_search.py"
)


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("evaluate_explore_search", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _pinned_ranking(fixed_ranking):
    """Ranking parameters are data; pin them so these tests measure the
    mechanism rather than the last calibration."""


class TestProbeConstruction:
    def _docs(self):
        return [{
            "id": 1,
            "title": "EO 14110: Safe, Secure, and Trustworthy Artificial Intelligence",
            "body": (
                "The Secretary shall establish guidelines for perfluoroalkyl "
                "monitoring under Executive Order 14110. Agencies must submit "
                "reports describing mitigation measures and monitoring cadence. "
                "Mitigation measures shall include monitoring of substances."
            ),
        }]

    def test_builds_one_probe_per_supported_style(self, harness):
        probes = harness.build_probes(self._docs(), {}, 1)
        assert {p["style"] for p in probes} == {
            "title", "paraphrase", "identifier", "rare",
        }
        assert all(p["doc_id"] == 1 for p in probes)
        assert all(p["query"].strip() for p in probes)

    def test_paraphrase_excludes_the_title_words(self, harness):
        # Otherwise it is the title probe under another name, and the style
        # that is supposed to be hard for the keyword channel becomes easy.
        probes = {p["style"]: p["query"] for p in harness.build_probes(self._docs(), {}, 1)}
        assert "artificial" not in probes["paraphrase"]
        assert "trustworthy" not in probes["paraphrase"]

    def test_identifier_probe_is_an_actual_citation(self, harness):
        probes = {p["style"]: p["query"] for p in harness.build_probes(self._docs(), {}, 1)}
        assert "14110" in probes["identifier"]

    def test_boilerplate_words_never_become_a_query(self, harness):
        # A query made of Federal Register house style ("shall", "section",
        # "agency") names every document equally, and known-item retrieval
        # against it measures nothing.
        doc = [{"id": 1, "title": "Notice", "body": "The agency shall publish "
                "such notice under section 4 of this part as the Secretary "
                "will determine that these documents have been effective."}]
        for probe in harness.build_probes(doc, {}, 1):
            assert probe["query"].strip()
            assert "shall" not in probe["query"]
            assert "section" not in probe["query"]

    def test_a_document_with_no_usable_text_yields_no_probes(self, harness):
        assert harness.build_probes([{"id": 1, "title": "", "body": ""}], {}, 1) == []

    def test_rare_style_prefers_terms_the_corpus_rarely_uses(self, harness):
        corpus_df = {"monitoring": 900, "perfluoroalkyl": 2, "mitigation": 800,
                     "reports": 850, "substances": 700, "measures": 880,
                     "guidelines": 870, "cadence": 3, "describing": 890,
                     "submit": 860, "must": 895, "establish": 875}
        probes = {
            p["style"]: p["query"]
            for p in harness.build_probes(self._docs(), corpus_df, 1000)
        }
        assert "perfluoroalkyl" in probes["rare"]


class TestMetrics:
    def test_rank_of_is_one_indexed_and_none_when_absent(self, harness):
        assert harness._rank_of([9, 8, 7], 8) == 2
        assert harness._rank_of([9, 8, 7], 1) is None

    def test_summary_counts_misses_against_the_full_probe_set(self, harness):
        # Dividing by the number of *found* items instead would let a
        # configuration that misses nine probes out of ten report a perfect
        # MRR on the one it found.
        stats = harness._summarise([1, None, None, None])
        assert stats["n"] == 4
        assert stats["mrr"] == pytest.approx(0.25)
        assert stats["r@1"] == pytest.approx(0.25)
        assert stats["missed"] == pytest.approx(0.75)

    def test_perfect_and_empty_cases(self, harness):
        assert harness._summarise([1, 1]) == pytest.approx(
            {"n": 2, "mrr": 1.0, "r@1": 1.0, "r@5": 1.0, "r@20": 1.0, "missed": 0.0}
        )
        assert harness._summarise([])["mrr"] == 0.0

    def test_recall_cutoffs_are_nested(self, harness):
        stats = harness._summarise([1, 3, 12, 40])
        assert stats["r@1"] <= stats["r@5"] <= stats["r@20"]
        assert stats["r@20"] == pytest.approx(0.75)


class TestMeasurementLoop:
    """The loop that actually produces the numbers.

    Worth covering specifically: one misread result key and every
    configuration reports a clean 100% miss, which reads as "this channel
    is broken" rather than "the harness is". A harness that reports a
    plausible wrong answer is worse than one that crashes.
    """

    @pytest.fixture()
    def corpus(self, harness, db_session, monkeypatch):
        from app.pipeline.lexical_index import ensure_lexical_index
        from app.services import explore_search

        assert ensure_lexical_index(db_session.get_bind())
        docs = []
        for i, (title, body) in enumerate([
            ("EO 14110: Trustworthy Artificial Intelligence",
             "Agencies shall report on dual-use foundation models and computing clusters."),
            ("Perfluoroalkyl Substances Drinking Water Regulation",
             "Maximum contaminant levels for perfluorooctanoic acid in public water systems."),
            ("Wildfire Fuels Treatment Environmental Impact Statement",
             "Hazardous fuels reduction across the interior West rangelands."),
        ]):
            doc = ExploreDocument(
                doc_type="Final Rule", source="Federal Register", title=title,
                summary=body[:200], body=body, date=f"2026-0{i + 1}-01",
                chamber="Regulatory", external_id=f"fx-{i}",
            )
            db_session.add(doc)
            docs.append(doc)
        db_session.commit()
        for d in docs:
            db_session.refresh(d)

        # No model weights in CI's fast job, and this is not a test of what
        # the encoder returns — the semantic channel stands in as absent.
        # Both references have to be patched: the harness imports the
        # function into its own namespace, so patching only the service's
        # copy leaves the harness calling the real one, which then tries to
        # open /data/vectors.db.
        absent = lambda *a, **k: None  # noqa: E731
        monkeypatch.setattr(explore_search, "search_explore_documents", absent)
        monkeypatch.setattr(harness, "search_explore_documents", absent)
        return db_session, docs

    def test_finds_each_document_from_its_own_title(self, harness, corpus):
        db, docs = corpus
        probes = [
            {"style": "title", "doc_id": d.id, "query": d.title} for d in docs
        ]
        by_style = harness.measure(db, probes)

        assert set(by_style) == {"title", "ALL"}
        assert set(by_style["ALL"]) == {"semantic", "keyword", "fusion", "hybrid"}
        # Known-item retrieval on a document's own title is the easy case;
        # anything but rank 1 here means the plumbing is wrong, not the
        # ranking.
        assert by_style["ALL"]["keyword"] == [1, 1, 1]
        assert by_style["ALL"]["hybrid"] == [1, 1, 1]

    def test_an_absent_channel_reports_misses_not_crashes(self, harness, corpus):
        db, docs = corpus
        probes = [{"style": "title", "doc_id": docs[0].id, "query": docs[0].title}]
        by_style = harness.measure(db, probes)
        assert by_style["ALL"]["semantic"] == [None]
        assert harness._summarise(by_style["ALL"]["semantic"])["missed"] == 1.0

    def test_a_query_matching_nothing_scores_as_a_miss(self, harness, corpus):
        db, docs = corpus
        probes = [{"style": "title", "doc_id": docs[0].id,
                   "query": "zzzzz nonexistent terminology"}]
        by_style = harness.measure(db, probes)
        assert by_style["ALL"]["keyword"] == [None]

    def test_every_style_is_aggregated_into_all(self, harness, corpus):
        db, docs = corpus
        probes = [
            {"style": "title", "doc_id": docs[0].id, "query": docs[0].title},
            {"style": "rare", "doc_id": docs[1].id, "query": "perfluorooctanoic"},
        ]
        by_style = harness.measure(db, probes)
        assert len(by_style["ALL"]["keyword"]) == 2
        assert len(by_style["title"]["keyword"]) == 1
        assert len(by_style["rare"]["keyword"]) == 1

    def test_no_probes_is_not_a_crash(self, harness, corpus):
        db, _docs = corpus
        assert harness.measure(db, []) == {}
