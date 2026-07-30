"""Tests for the hybrid explore ranker — fusion, priors, dedup, diversity.

The semantic channel is stubbed throughout: these tests are about what the
ranker does with a ranking, not about what all-MiniLM-L6-v2 returns for a
given sentence. Stubbing it also keeps the file fast enough to run without
the `slow` marker's model load.
"""

import pytest

from app.models import ExploreDocument
from app.pipeline.lexical_index import ensure_lexical_index
from app.services import explore_search
from app.services.explore_search import hybrid_search


@pytest.fixture()
def indexed_db(db_session):
    assert ensure_lexical_index(db_session.get_bind()), "FTS5 unavailable"
    return db_session


@pytest.fixture()
def stub_semantic(monkeypatch):
    """Replace the vector channel with a scripted ranking.

    `set(None)` reproduces "index not built yet"; `set([...])` supplies an
    ordered list of document ids.
    """
    state: dict = {"hits": []}

    def _fake(query, n_results, doc_type=None, chamber=None, politician_id=None):
        if state["hits"] is None:
            return None
        return [
            {"id": doc_id, "distance": 0.1 * position, "title": "", "date": "",
             "docType": "", "source": "", "politicianName": "", "politicianId": "",
             "chamber": "", "snippet": ""}
            for position, doc_id in enumerate(state["hits"][:n_results])
        ]

    monkeypatch.setattr(explore_search, "search_explore_documents", _fake)
    return lambda hits: state.__setitem__("hits", hits)


def _add(db, **overrides) -> ExploreDocument:
    defaults = {
        "doc_type": "Final Rule", "source": "Federal Register",
        "title": "Untitled", "summary": "", "body": "",
        "date": "2026-01-01", "chamber": "Regulatory",
    }
    doc = ExploreDocument(**{**defaults, **overrides})
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


class TestChannelAvailability:
    def test_neither_channel_available_reports_index_not_ready(
        self, indexed_db, stub_semantic
    ):
        stub_semantic(None)
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["indexReady"] is False
        assert outcome["results"] == []

    def test_keyword_channel_alone_still_serves_results(self, indexed_db, stub_semantic):
        # A vector-index rebuild takes minutes on the Pi and used to take the
        # whole feature down for its duration. It now takes down half of it.
        stub_semantic(None)
        doc = _add(indexed_db, title="wildfire response rule")
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["indexReady"] is True
        assert [r["id"] for r in outcome["results"]] == [doc.id]
        assert outcome["results"][0]["matchedBy"] == ["keyword"]

    def test_semantic_channel_alone_still_serves_results(self, indexed_db, stub_semantic):
        doc = _add(indexed_db, title="grazing permits", body="rangeland management")
        stub_semantic([doc.id])
        outcome = hybrid_search(indexed_db, "livestock on public land", limit=10)
        assert [r["id"] for r in outcome["results"]] == [doc.id]
        assert outcome["results"][0]["matchedBy"] == ["semantic"]

    def test_documents_found_by_both_channels_are_reported_as_such(
        self, indexed_db, stub_semantic
    ):
        doc = _add(indexed_db, title="wildfire response rule")
        stub_semantic([doc.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["results"][0]["matchedBy"] == ["semantic", "keyword"]


class TestFusion:
    def test_agreement_between_channels_beats_a_single_channel_top_hit(
        self, indexed_db, stub_semantic
    ):
        # The whole point of rank fusion: a document both retrievers like
        # outranks one that only the vector index put first.
        both = _add(indexed_db, title="wildfire response rule")
        semantic_only = _add(indexed_db, title="rangeland management")
        stub_semantic([semantic_only.id, both.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert [r["id"] for r in outcome["results"]] == [both.id, semantic_only.id]

    def test_a_vector_hit_with_no_surviving_row_is_dropped(
        self, indexed_db, stub_semantic
    ):
        # A partial reset clears the app DB but not vectors.db; these render
        # as snippet-only cards whose "view details" link 404s.
        doc = _add(indexed_db, title="wildfire rule")
        stub_semantic([doc.id, 999_999])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert [r["id"] for r in outcome["results"]] == [doc.id]


class TestFreshnessPrior:
    def test_a_much_newer_document_outranks_a_slightly_more_relevant_one(
        self, indexed_db, stub_semantic
    ):
        # Sixty candidates. Both retrieval channels rank them in insertion
        # order, so the first document inserted is the most relevant — and
        # it is also the oldest in the pool, while the third is the newest.
        # Recency is supposed to be able to close a two-rank relevance gap
        # against a ~57-rank recency gap, and (per the next test) not a
        # one-rank one.
        def _date(i: int) -> str:
            if i == 0:
                return "2020-01-01"     # oldest in the pool
            if i == 2:
                return "2026-12-31"     # newest in the pool
            j = i - 1 if i < 2 else i - 2
            return f"2023-{1 + j // 28:02d}-{1 + j % 28:02d}"

        docs = [
            _add(indexed_db, title="wildfire notice", agency_name=f"Agency {i}",
                 body=f"document numbered {i + 10} about wildfire operations",
                 date=_date(i))
            for i in range(60)
        ]
        stub_semantic([d.id for d in docs])

        outcome = hybrid_search(indexed_db, "wildfire", limit=60)
        ids = [r["id"] for r in outcome["results"]]
        assert ids.index(docs[2].id) < ids.index(docs[0].id)

    def test_freshness_cannot_flip_an_adjacent_relevance_pair(
        self, indexed_db, stub_semantic
    ):
        # The bound the weights are chosen for: a one-rank recency advantage
        # must not beat a one-rank relevance disadvantage, or the ranking is
        # a date sort wearing a search engine's clothes.
        older = _add(indexed_db, title="wildfire rule", date="2020-01-01",
                     body="the older of two documents about wildfire policy")
        newer = _add(indexed_db, title="wildfire rule", date="2026-06-01",
                     body="the newer of two documents about wildfire policy")
        stub_semantic([older.id, newer.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["results"][0]["id"] == older.id

    def test_freshness_does_not_override_a_clear_relevance_gap(
        self, indexed_db, stub_semantic
    ):
        relevant = _add(indexed_db, title="wildfire response rule", date="2020-01-01")
        for i in range(20):
            _add(indexed_db, title=f"unrelated notice {i}", date="2026-06-01")
        stub_semantic([relevant.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["results"][0]["id"] == relevant.id


class TestAuthorityPrior:
    def test_cited_document_outranks_an_uncited_one_at_equal_relevance(
        self, indexed_db, stub_semantic
    ):
        plain = _add(indexed_db, title="wildfire rule", date="2026-01-01")
        cited = _add(indexed_db, title="wildfire rule", body="different body text "
                     "so these two are not collapsed as duplicates",
                     date="2026-01-01", cited_by_count=7, authority=0.02)
        stub_semantic([plain.id, cited.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["results"][0]["id"] == cited.id

    def test_uncited_documents_are_not_penalised_relative_to_each_other(
        self, indexed_db, stub_semantic
    ):
        # Citability is unevenly distributed by document type — a rule
        # carries an FR citation, a floor speech carries nothing. If uncited
        # documents were ranked at the bottom of an authority ordering
        # rather than left out of it, every query would demote every speech.
        first = _add(indexed_db, title="wildfire remarks one", chamber="Senate",
                     doc_type="Senate Floor Speech")
        second = _add(indexed_db, title="wildfire remarks two", chamber="Senate",
                      doc_type="Senate Floor Speech")
        stub_semantic([first.id, second.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert [r["id"] for r in outcome["results"]] == [first.id, second.id]

    def test_pagerank_orders_the_authority_ranker_not_the_raw_count(
        self, indexed_db, stub_semantic
    ):
        # A document cited many times by routine notices should not outrank
        # one cited fewer times by the orders everything else in the corpus
        # points at. Ranking the authority voter by the raw count would say
        # otherwise, and would make computing a PageRank score pointless.
        #
        # Same shape as the freshness test: sixty candidates, both retrieval
        # channels ranking them in insertion order, authority running the
        # other way. The third-most-relevant document has the highest
        # PageRank but *fewer* citations than the most relevant one.
        docs = [
            _add(indexed_db, title="wildfire notice", agency_name=f"Agency {i}",
                 body=f"document numbered {i + 10} about wildfire operations",
                 date="2026-01-01",
                 cited_by_count=40 if i == 0 else 4,
                 authority=0.001 if i == 0 else (0.9 if i == 2 else 0.01 + i * 0.001))
            for i in range(60)
        ]
        stub_semantic([d.id for d in docs])
        ids = [r["id"] for r in hybrid_search(indexed_db, "wildfire", limit=60)["results"]]
        assert ids.index(docs[2].id) < ids.index(docs[0].id)

    def test_raw_pagerank_is_not_exposed_to_the_client(self, indexed_db, stub_semantic):
        doc = _add(indexed_db, title="wildfire rule", cited_by_count=4, authority=0.01)
        stub_semantic([doc.id])
        result = hybrid_search(indexed_db, "wildfire", limit=10)["results"][0]
        assert "authority" not in result and "_authority" not in result

    def test_citation_count_is_exposed_to_the_client(self, indexed_db, stub_semantic):
        doc = _add(indexed_db, title="wildfire rule", cited_by_count=4, authority=0.01)
        stub_semantic([doc.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["results"][0]["citedByCount"] == 4


class TestDeduplication:
    def test_identical_documents_collapse_to_one_result(self, indexed_db, stub_semantic):
        # This corpus is known to accumulate byte-identical rows: a 2026-07
        # audit found 1,758 of them, 31% of the table.
        body = "The Secretary shall establish a wildfire response program. " * 4
        first = _add(indexed_db, title="Wildfire Program", body=body)
        _add(indexed_db, title="Wildfire Program", body=body)
        _add(indexed_db, title="Wildfire Program", body=body)
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert [r["id"] for r in outcome["results"]] == [first.id]
        assert outcome["results"][0]["duplicateCount"] == 2

    def test_short_documents_are_never_treated_as_duplicates(
        self, indexed_db, stub_semantic
    ):
        # "Body not backfilled yet" is a normal state in this pipeline. A
        # naive content hash collapses every such row into a single result.
        a = _add(indexed_db, title="wildfire", body="")
        b = _add(indexed_db, title="wildfire", body="")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert {r["id"] for r in outcome["results"]} == {a.id, b.id}

    def test_distinct_speeches_by_one_member_are_not_collapsed(
        self, indexed_db, stub_semantic
    ):
        # Every one of a member's floor speeches shares the same generated
        # title, so a title-based dedup would return exactly one of them.
        a = _add(indexed_db, title="Sen. Smith — Floor Remarks", chamber="Senate",
                 doc_type="Senate Floor Speech", politician_id="S001",
                 body="I rise today to speak about wildfire suppression funding "
                      "for the western states and the crews who do that work.")
        b = _add(indexed_db, title="Sen. Smith — Floor Remarks", chamber="Senate",
                 doc_type="Senate Floor Speech", politician_id="S001",
                 body="I rise today to speak about wildfire insurance markets "
                      "and what homeowners in my state are being charged.")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert {r["id"] for r in outcome["results"]} == {a.id, b.id}


class TestDiversity:
    def test_one_agency_cannot_occupy_the_whole_page(self, indexed_db, stub_semantic):
        crowd = [
            _add(indexed_db, title=f"wildfire notice {i}", agency_name="Forest Service",
                 body=f"notice number {i} concerning wildfire operations")
            for i in range(6)
        ]
        other = _add(indexed_db, title="wildfire rule", agency_name="Interior",
                     body="a rule about wildfire from a different agency")
        stub_semantic([d.id for d in crowd] + [other.id])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        ids = [r["id"] for r in outcome["results"]]
        assert other.id in ids[:4]

    def test_demoted_results_are_kept_not_dropped(self, indexed_db, stub_semantic):
        crowd = [
            _add(indexed_db, title=f"wildfire notice {i}", agency_name="Forest Service",
                 body=f"notice number {i} concerning wildfire operations")
            for i in range(6)
        ]
        stub_semantic([d.id for d in crowd])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert len(outcome["results"]) == 6


class TestFiltersAndSort:
    def test_commentable_filters_to_open_comment_periods(self, indexed_db, stub_semantic):
        open_doc = _add(indexed_db, title="wildfire proposed rule",
                        comment_url="https://regulations.gov/x",
                        comments_close_on="2099-01-01")
        _add(indexed_db, title="wildfire final rule")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10, commentable=True)
        assert [r["id"] for r in outcome["results"]] == [open_doc.id]

    def test_sort_by_date_orders_the_pool_not_the_page(self, indexed_db, stub_semantic):
        # The bug this replaces: the old implementation sorted the twenty
        # results it had already picked by relevance, so "newest" meant
        # "newest of the twenty most similar".
        newest = _add(indexed_db, title="wildfire notice newest", date="2026-07-01",
                      body="the newest document about wildfire in the corpus")
        for i in range(30):
            _add(indexed_db, title=f"wildfire notice {i}", date="2020-01-01",
                 body=f"an older wildfire document number {i}")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=5, sort="date")
        assert outcome["results"][0]["id"] == newest.id

    def test_chamber_filter_reaches_the_keyword_channel(self, indexed_db, stub_semantic):
        keep = _add(indexed_db, title="wildfire remarks", chamber="Senate",
                    doc_type="Senate Floor Speech")
        _add(indexed_db, title="wildfire rule", chamber="Regulatory")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10, chamber="Senate")
        assert [r["id"] for r in outcome["results"]] == [keep.id]

    def test_limit_is_respected(self, indexed_db, stub_semantic):
        for i in range(10):
            _add(indexed_db, title=f"wildfire notice {i}", agency_name=f"Agency {i}",
                 body=f"document {i} about wildfire")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=3)
        assert outcome["count"] == 3
        assert len(outcome["results"]) == 3

    def test_no_matches_returns_an_empty_ready_result(self, indexed_db, stub_semantic):
        _add(indexed_db, title="grazing permits")
        stub_semantic([])
        outcome = hybrid_search(indexed_db, "wildfire", limit=10)
        assert outcome["indexReady"] is True
        assert outcome["results"] == []


class TestResponseShape:
    def test_internal_fields_do_not_leak_to_the_client(self, indexed_db, stub_semantic):
        doc = _add(indexed_db, title="wildfire rule", body="a body long enough to "
                   "produce a real content fingerprint for deduplication")
        stub_semantic([doc.id])
        result = hybrid_search(indexed_db, "wildfire", limit=10)["results"][0]
        assert not any(key.startswith("_") for key in result)

    def test_hydrated_fields_are_present(self, indexed_db, stub_semantic):
        doc = _add(indexed_db, title="wildfire rule", url="https://example.gov/x",
                   summary="A summary.", agency_name="Forest Service")
        stub_semantic([doc.id])
        result = hybrid_search(indexed_db, "wildfire", limit=10)["results"][0]
        assert result["url"] == "https://example.gov/x"
        assert result["agencyName"] == "Forest Service"
        assert result["summary"] == "A summary."


class TestLargeCandidatePools:
    def test_hydration_handles_more_candidates_than_one_statement_can_bind(
        self, indexed_db, stub_semantic
    ):
        # Both channels can each return a full pool, so the union can exceed
        # SQLite's historical 999-bind-variable ceiling. Hydration chunks.
        docs = [
            _add(indexed_db, title=f"wildfire notice {i}", agency_name=f"Agency {i}",
                 body=f"document numbered {i} concerning wildfire operations")
            for i in range(700)
        ]
        stub_semantic([d.id for d in reversed(docs)])
        outcome = hybrid_search(indexed_db, "wildfire", limit=20)
        assert outcome["count"] == 20
        assert all(r["title"] for r in outcome["results"])


class TestPriorScaling:
    """The priors are weighted against the relevance evidence present.

    Both retrieval channels together contribute a combined weight of 2.0,
    so a freshness voter of 0.4 is one fifth of the relevance mass. When
    one channel returns nothing — its index rebuilding, or simply no
    keyword match for this query — that mass halves while a fixed prior
    does not, so recency and authority double in relative influence
    exactly when the engine can least afford it.

    Found by measurement, not by reading: on a 93-document corpus with the
    semantic channel unavailable, fixed priors dropped the fusion to MRR
    0.755 / R@1 0.621 against the keyword channel's own 0.976 / 0.958 — a
    third of top hits displaced by recency, in the degraded mode this
    feature otherwise advertises as a benefit. Scaling recovered it to
    0.852 / 0.736.

    The property is *invariance*, not suppression: how far recency can
    reach must not depend on how many retrieval channels happen to be up.
    """

    def _corpus(self, db, stub_semantic, newest_at, *, semantic_up):
        """60 documents ranked by insertion order in both channels.

        Index 0 is the most relevant and the oldest; `newest_at` is the
        newest document in the pool, sitting that far down the relevance
        ranking. Everything else falls between.
        """
        def _date(i: int) -> str:
            if i == 0:
                return "2020-01-01"          # oldest in the pool
            if i == newest_at:
                return "2026-12-31"          # newest in the pool
            j = i - 1 if i < newest_at else i - 2
            return f"2023-{1 + j // 28:02d}-{1 + j % 28:02d}"

        docs = [
            _add(db, title="wildfire notice", agency_name=f"Agency {i}",
                 body=f"document numbered {i + 10} about wildfire operations",
                 date=_date(i))
            for i in range(60)
        ]
        stub_semantic([d.id for d in docs] if semantic_up else None)
        return docs

    def _newest_beats_most_relevant(self, db, stub_semantic, newest_at, semantic_up):
        docs = self._corpus(db, stub_semantic, newest_at, semantic_up=semantic_up)
        ids = [r["id"] for r in hybrid_search(db, "wildfire", limit=60)["results"]]
        return ids.index(docs[newest_at].id) < ids.index(docs[0].id)

    @pytest.mark.parametrize("semantic_up", [True, False])
    def test_recency_reaches_a_close_relevance_gap_either_way(
        self, indexed_db, stub_semantic, semantic_up
    ):
        # Three ranks back and far newer: recency wins, and must win
        # whether or not the semantic index happens to be available.
        assert self._newest_beats_most_relevant(
            indexed_db, stub_semantic, 3, semantic_up) is True

    @pytest.mark.parametrize("semantic_up", [True, False])
    def test_recency_cannot_reach_a_wide_relevance_gap_either_way(
        self, indexed_db, stub_semantic, semantic_up
    ):
        # Ten ranks back: relevance holds. This is the case that
        # discriminates — with the prior left unscaled, the degraded
        # (semantic_up=False) run hoists the newest document to the top
        # while the healthy run does not, which is the bug.
        assert self._newest_beats_most_relevant(
            indexed_db, stub_semantic, 10, semantic_up) is False

    def test_include_priors_false_is_retrieval_only(self, indexed_db, stub_semantic):
        # The measurement affordance the harness uses to tell "did fusing
        # the retrievers help" apart from "did the priors help".
        docs = self._corpus(indexed_db, stub_semantic, 3, semantic_up=True)
        ids = [
            r["id"] for r in hybrid_search(
                indexed_db, "wildfire", limit=60, include_priors=False)["results"]
        ]
        assert ids.index(docs[0].id) < ids.index(docs[3].id)
