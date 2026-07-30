"""Tests for the federal citation graph and its PageRank authority scores.

These cover the two things the ranker depends on being true: that the
citation formats are parsed the way the Federal Register publishes them,
and that a corpus with no cross-references produces a signal that changes
nothing (rather than an arbitrary ordering that changes everything).
"""

import json

import numpy as np
import pytest

from app.models import ExploreDocument
from app.pipeline.analyze.document_authority import (
    build_citation_graph,
    compute_document_authority,
    declared_identifiers,
    extract_citations,
    pagerank,
    update_document_authority,
)


class TestExtractCitations:
    def test_executive_order_long_and_abbreviated_forms(self):
        found = extract_citations(
            "Consistent with Executive Order 12866 and E.O. 13563, agencies shall..."
        )
        assert found == {"eo:12866", "eo:13563"}

    def test_executive_order_with_no_prefix(self):
        assert extract_citations("Executive Order No. 14110 directs") == {"eo:14110"}

    def test_federal_register_citation(self):
        assert "fr:89-12345" in extract_citations("published at 89 FR 12345 on Tuesday")

    def test_federal_register_citation_is_case_sensitive(self):
        # "fr" in ordinary prose is not a Federal Register citation. Matching
        # it case-insensitively invents edges out of sentences like this one.
        assert extract_citations("we waited 89 fr 12345 seconds") == set()

    def test_rin_and_fr_doc_number(self):
        found = extract_citations("RIN 2060-AV50 ... FR Doc. 2024-01234 Filed 1-1-24")
        assert found == {"rin:2060-AV50", "frdoc:2024-01234"}

    def test_proclamation(self):
        assert extract_citations("Proclamation 10714 of June 1") == {"proc:10714"}

    def test_leading_zeros_normalize(self):
        # "Executive Order 09999" and "Executive Order 9999" are the same
        # order; if they hashed differently the graph would silently split
        # one document's inbound citations across two identifiers.
        assert extract_citations("Executive Order 09999") == extract_citations(
            "Executive Order 9999"
        )

    def test_repeated_citation_counts_once(self):
        text = "E.O. 12866. " * 6
        assert extract_citations(text) == {"eo:12866"}

    def test_empty_and_none_safe(self):
        assert extract_citations("") == set()
        assert extract_citations(None) == set()


class TestDeclaredIdentifiers:
    def test_external_id_yields_fr_document_number(self):
        assert "frdoc:2023-24283" in declared_identifiers(
            "fr-2023-24283", "Anything", "Executive Order"
        )
        assert "frdoc:2024-00123" in declared_identifiers(
            "fr-reg-2024-00123", "A Rule", "Final Rule"
        )

    def test_executive_order_number_comes_from_the_title(self):
        # presidential_actions formats EO titles as "EO 14110: ...". This is
        # the path that lets documents ingested before the identifiers column
        # existed still take part in the graph, with no re-ingest.
        assert "eo:14110" in declared_identifiers(
            "fr-2023-24283", "EO 14110: Safe, Secure AI", "Executive Order"
        )

    def test_stored_identifiers_are_used(self):
        ids = declared_identifiers(
            "fr-reg-1", "A Rule", "Final Rule",
            json.dumps(["fr:89-12345", "rin:2060-AV50"]),
        )
        assert {"fr:89-12345", "rin:2060-AV50"} <= ids

    def test_malformed_identifiers_json_does_not_raise(self):
        assert declared_identifiers("fr-1", "T", "Notice", "not json{") == {"frdoc:1"}

    def test_non_federal_register_document_declares_nothing(self):
        # A floor speech has no citable serial number. It should come back
        # empty rather than acquire a fabricated one.
        assert declared_identifiers(
            "senate-floor-smith-2026-01-01-abc", "Sen. Smith — Floor Remarks",
            "Senate Floor Speech",
        ) == set()


class TestCitationGraph:
    def test_edge_from_citing_document_to_cited_document(self):
        docs = [
            {"id": 1, "external_id": "fr-1", "title": "EO 14110: AI",
             "doc_type": "Executive Order", "summary": "", "body": "", "identifiers": None},
            {"id": 2, "external_id": "fr-reg-2", "title": "A Rule",
             "doc_type": "Final Rule", "summary": "",
             "body": "Consistent with Executive Order 14110...", "identifiers": None},
        ]
        order, edges, inbound = build_citation_graph(docs, docs)
        assert order == [1, 2]
        assert edges == [(1, 0)]
        assert inbound.tolist() == [1, 0]

    def test_self_citation_is_dropped(self):
        # Every Federal Register document stamps its own "FR Doc." line into
        # its own body. Counted, the ranking becomes a document-length contest.
        docs = [{
            "id": 1, "external_id": "fr-reg-2024-01234", "title": "A Rule",
            "doc_type": "Final Rule", "summary": "",
            "body": "FR Doc. 2024-01234 Filed 1-1-24", "identifiers": None,
        }]
        _order, edges, inbound = build_citation_graph(docs, docs)
        assert edges == []
        assert inbound.tolist() == [0]

    def test_repeated_citations_are_one_edge(self):
        docs = [
            {"id": 1, "external_id": "fr-1", "title": "EO 12866: Review",
             "doc_type": "Executive Order", "summary": "", "body": "", "identifiers": None},
            {"id": 2, "external_id": "fr-reg-2", "title": "Rule",
             "doc_type": "Final Rule", "summary": "E.O. 12866",
             "body": "E.O. 12866 " * 20, "identifiers": None},
        ]
        _order, edges, _inbound = build_citation_graph(docs, docs)
        assert edges == [(1, 0)]


class TestPageRank:
    def test_no_edges_gives_a_uniform_distribution(self):
        scores = pagerank(5, [])
        assert scores == pytest.approx([0.2] * 5)

    def test_scores_sum_to_one(self):
        scores = pagerank(4, [(0, 1), (2, 1), (3, 1), (1, 0)])
        assert scores.sum() == pytest.approx(1.0)

    def test_more_cited_document_scores_higher(self):
        # 1, 2, 3 all cite 0; nobody cites 3.
        scores = pagerank(4, [(1, 0), (2, 0), (3, 0)])
        assert scores[0] > scores[1]
        assert scores[0] > scores[3]

    def test_dangling_nodes_do_not_drain_the_distribution(self):
        # Most of any federal corpus cites nothing. Without redistributing
        # dangling mass the total leaks away and every score decays toward
        # zero at a rate set by how many speeches happen to be indexed.
        scores = pagerank(50, [(1, 0)])
        assert scores.sum() == pytest.approx(1.0)
        assert np.all(scores > 0)

    def test_empty_corpus(self):
        assert pagerank(0, []).tolist() == []


class TestComputeDocumentAuthority:
    def test_returns_score_and_inbound_count_per_document(self):
        docs = [
            {"id": 10, "external_id": "fr-1", "title": "EO 12866: Review",
             "doc_type": "Executive Order", "summary": "", "body": "", "identifiers": None},
            {"id": 20, "external_id": "fr-reg-2", "title": "Rule A",
             "doc_type": "Final Rule", "summary": "", "body": "per E.O. 12866",
             "identifiers": None},
            {"id": 30, "external_id": "fr-reg-3", "title": "Rule B",
             "doc_type": "Final Rule", "summary": "", "body": "see Executive Order 12866",
             "identifiers": None},
        ]
        result = compute_document_authority(docs)
        assert result[10][1] == 2
        assert result[20][1] == 0
        assert result[10][0] > result[20][0]

    def test_uncited_corpus_leaves_every_document_tied(self):
        # The graceful-degradation property the ranker relies on: with no
        # cross-references, nobody clears cited_by > 0, so the authority
        # ranker is empty and the prior contributes nothing to anyone.
        docs = [
            {"id": i, "external_id": f"senate-floor-{i}", "title": "Floor Remarks",
             "doc_type": "Senate Floor Speech", "summary": "", "body": "words",
             "identifiers": None}
            for i in range(5)
        ]
        result = compute_document_authority(docs)
        assert all(cited == 0 for _, cited in result.values())
        scores = [score for score, _ in result.values()]
        assert max(scores) == pytest.approx(min(scores))

    def test_empty_corpus(self):
        assert compute_document_authority([]) == {}


class TestUpdateDocumentAuthority:
    def test_persists_scores_to_the_database(self, db_session):
        cited = ExploreDocument(
            doc_type="Executive Order", source="Federal Register",
            title="EO 12866: Regulatory Planning", body="", date="2026-01-01",
            chamber="Executive", external_id="fr-1",
        )
        citing = ExploreDocument(
            doc_type="Final Rule", source="Federal Register",
            title="A Rule", body="Reviewed under Executive Order 12866.",
            date="2026-02-01", chamber="Regulatory", external_id="fr-reg-2",
        )
        db_session.add_all([cited, citing])
        db_session.commit()

        stats = update_document_authority(db_session)
        assert stats == {"documents": 2, "cited": 1}

        db_session.expire_all()
        assert cited.cited_by_count == 1
        assert citing.cited_by_count == 0
        assert cited.authority > citing.authority

    def test_empty_table(self, db_session):
        assert update_document_authority(db_session) == {"documents": 0, "cited": 0}


class TestStreamedGraph:
    """The two-iterable form is what `update_document_authority` uses.

    It exists so bodies can be streamed and discarded instead of the whole
    corpus's text sitting in memory next to two sentence-transformer
    models — `yield_per` alone does not do that, because accumulating the
    rows it yields puts every body straight back.
    """

    IDENTITY = [
        {"id": 10, "external_id": "fr-1", "title": "EO 12866: Review",
         "doc_type": "Executive Order", "identifiers": None},
        {"id": 20, "external_id": "fr-reg-2", "title": "A Rule",
         "doc_type": "Final Rule", "identifiers": None},
    ]

    def test_separate_iterables_give_the_same_graph_as_one_list(self):
        combined = [
            {**self.IDENTITY[0], "summary": "", "body": ""},
            {**self.IDENTITY[1], "summary": "", "body": "per Executive Order 12866"},
        ]
        text_rows = [
            {"id": 10, "summary": "", "body": ""},
            {"id": 20, "summary": "", "body": "per Executive Order 12866"},
        ]
        from_list = build_citation_graph(combined, combined)
        from_stream = build_citation_graph(self.IDENTITY, iter(text_rows))
        assert from_list[0] == from_stream[0]
        assert from_list[1] == from_stream[1]
        assert from_list[2].tolist() == from_stream[2].tolist()

    def test_text_rows_may_arrive_in_any_order(self):
        # Two independent queries; nothing guarantees they iterate alike.
        # Matching by id rather than by position is what makes that safe.
        text_rows = [
            {"id": 20, "summary": "", "body": "per Executive Order 12866"},
            {"id": 10, "summary": "", "body": ""},
        ]
        _order, edges, inbound = build_citation_graph(self.IDENTITY, iter(text_rows))
        assert edges == [(1, 0)]
        assert inbound.tolist() == [1, 0]

    def test_a_text_row_for_an_unknown_id_is_skipped(self):
        # A document inserted between the two queries. Indexing it at some
        # arbitrary position would attribute its citations to another
        # document entirely.
        text_rows = [
            {"id": 999, "summary": "", "body": "per Executive Order 12866"},
            {"id": 20, "summary": "", "body": ""},
        ]
        _order, edges, inbound = build_citation_graph(self.IDENTITY, iter(text_rows))
        assert edges == []
        assert inbound.tolist() == [0, 0]

    def test_a_document_with_no_text_row_still_gets_a_score(self):
        _order, _edges, inbound = build_citation_graph(self.IDENTITY, iter([]))
        assert inbound.tolist() == [0, 0]

    def test_update_streams_text_rows_instead_of_materialising_them(self, db_session):
        # Guards the property the docstring claims, structurally: the text
        # rows must reach build_citation_graph as a lazy iterator, so each
        # body is discarded after use. A regression to "read every row into
        # a list, then process" is invisible to an output assertion — the
        # answer is identical, only the peak memory changes — so this
        # asserts on the shape of what gets passed.
        import inspect

        from app.pipeline.analyze import document_authority as module

        for i in range(6):
            db_session.add(ExploreDocument(
                doc_type="Final Rule", source="Federal Register",
                title=f"Rule {i}", body=f"body text {i}",
                date="2026-01-01", chamber="Regulatory", external_id=f"fr-reg-{i}",
            ))
        db_session.commit()

        seen: dict = {}
        real_build = module.build_citation_graph

        def _spy(identity_rows, text_rows):
            seen["is_lazy"] = inspect.isgenerator(text_rows)
            # Consume it to make sure the generator actually yields rows,
            # rather than passing laziness by being empty.
            rows = list(text_rows)
            seen["count"] = len(rows)
            return real_build(identity_rows, iter(rows))

        module.build_citation_graph = _spy
        try:
            result = update_document_authority(db_session)
        finally:
            module.build_citation_graph = real_build

        assert seen["is_lazy"] is True
        assert seen["count"] == 6
        assert result["documents"] == 6
