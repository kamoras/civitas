"""Tests for the FTS5 keyword channel: query parsing, BM25F, sync triggers.

The parsing tests matter more than they look. FTS5's MATCH grammar has
operators, and a search box is not a query language — an unescaped
apostrophe raises `fts5: syntax error` and the word "not" silently
becomes a negation. Every one of those is a query that returns nothing
for a reason no user can see.
"""

import pytest

from app.models import ExploreDocument
from app.pipeline.lexical_index import (
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    build_match_expression,
    ensure_lexical_index,
    rebuild_index,
    search_lexical,
)


@pytest.fixture()
def indexed_db(db_session):
    """A session whose explore_documents table has a live FTS5 index."""
    assert ensure_lexical_index(db_session.get_bind()), "FTS5 unavailable"
    return db_session


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


class TestBuildMatchExpression:
    def test_terms_are_quoted_and_or_joined(self):
        assert build_match_expression("clean water") == '"clean" OR "water"'

    def test_fts5_operators_in_user_input_are_neutralised(self):
        # Typed by a person, "NOT" is a word. Passed through raw it is an
        # operator, and the query means the opposite of what was asked.
        assert build_match_expression("water NOT clean") == (
            '"water" OR "NOT" OR "clean"'
        )

    def test_apostrophes_do_not_break_the_grammar(self):
        assert build_match_expression("veterans' benefits") == (
            '"veterans" OR "benefits"'
        )

    def test_punctuation_only_query_yields_nothing(self):
        assert build_match_expression("!!! ???") == ""
        assert build_match_expression("") == ""

    def test_quoted_span_becomes_a_phrase_query(self):
        assert build_match_expression('"clean water act" rules') == (
            '"clean water act" OR "rules"'
        )

    def test_identifiers_survive_tokenisation(self):
        expr = build_match_expression("Executive Order 14110")
        assert '"14110"' in expr

    def test_single_characters_are_dropped(self):
        assert build_match_expression("a b clean") == '"clean"'

    def test_duplicate_terms_appear_once(self):
        assert build_match_expression("water WATER water") == '"water"'


class TestSearchLexical:
    def test_finds_an_exact_identifier_a_bi_encoder_would_blur(self, indexed_db):
        # The reason this channel exists at all. "Executive Order 14110" and
        # "Executive Order 13985" embed to nearly the same point — the number
        # carries the entire query and the encoder never learned it.
        wanted = _add(indexed_db, title="EO 14110: Safe, Secure AI",
                      doc_type="Executive Order", chamber="Executive")
        _add(indexed_db, title="EO 13985: Advancing Racial Equity",
             doc_type="Executive Order", chamber="Executive")
        hits = search_lexical(indexed_db, "Executive Order 14110", limit=10)
        assert hits[0]["id"] == wanted.id

    def test_matches_on_body_text(self, indexed_db):
        doc = _add(indexed_db, title="A Rule", body="perfluoroalkyl substances (PFAS)")
        _add(indexed_db, title="Another Rule", body="grazing permits on federal land")
        hits = search_lexical(indexed_db, "PFAS", limit=10)
        assert [h["id"] for h in hits] == [doc.id]

    def test_title_outranks_body_for_the_same_term(self, indexed_db):
        titled = _add(indexed_db, title="Wildfire Response Rule", body="unrelated text")
        buried = _add(
            indexed_db, title="Unrelated Rule",
            body="paragraph text " * 50 + " wildfire " + "more text " * 50,
        )
        hits = search_lexical(indexed_db, "wildfire", limit=10)
        ids = [h["id"] for h in hits]
        assert ids.index(titled.id) < ids.index(buried.id)

    def test_snippet_marks_the_matched_terms(self, indexed_db):
        _add(indexed_db, title="A Rule", body="the agency proposes new wildfire rules")
        hits = search_lexical(indexed_db, "wildfire", limit=10)
        assert HIGHLIGHT_START in hits[0]["snippet"]
        assert HIGHLIGHT_END in hits[0]["snippet"]
        assert "wildfire" in hits[0]["snippet"]

    def test_filters_are_applied_inside_the_query(self, indexed_db):
        # Not after it: post-filtering is how a chamber-scoped search ends
        # up with three results out of a requested thirty.
        keep = _add(indexed_db, title="Senate wildfire remarks",
                    chamber="Senate", doc_type="Senate Floor Speech")
        _add(indexed_db, title="Regulatory wildfire rule", chamber="Regulatory")
        hits = search_lexical(indexed_db, "wildfire", limit=10, chamber="Senate")
        assert [h["id"] for h in hits] == [keep.id]

    def test_politician_filter(self, indexed_db):
        keep = _add(indexed_db, title="wildfire remarks", politician_id="S001")
        _add(indexed_db, title="wildfire remarks", politician_id="S002")
        hits = search_lexical(indexed_db, "wildfire", limit=10, politician_id="S001")
        assert [h["id"] for h in hits] == [keep.id]

    def test_commentable_filter(self, indexed_db):
        keep = _add(indexed_db, title="open wildfire rule",
                    comment_url="https://regulations.gov/x", comments_close_on="2099-01-01")
        _add(indexed_db, title="closed wildfire rule",
             comment_url="https://regulations.gov/y", comments_close_on="2000-01-01")
        _add(indexed_db, title="uncommentable wildfire rule")
        hits = search_lexical(indexed_db, "wildfire", limit=10,
                              commentable_after="2026-07-30")
        assert [h["id"] for h in hits] == [keep.id]

    def test_no_match_returns_empty(self, indexed_db):
        _add(indexed_db, title="A Rule", body="grazing permits")
        assert search_lexical(indexed_db, "wildfire", limit=10) == []

    def test_unparseable_query_returns_empty_rather_than_raising(self, indexed_db):
        _add(indexed_db, title="A Rule", body="grazing permits")
        assert search_lexical(indexed_db, "!!!", limit=10) == []

    def test_respects_the_limit(self, indexed_db):
        for i in range(10):
            _add(indexed_db, title=f"wildfire rule {i}")
        assert len(search_lexical(indexed_db, "wildfire", limit=3)) == 3


class TestIndexSync:
    def test_insert_is_indexed_by_the_trigger(self, indexed_db):
        doc = _add(indexed_db, title="brand new wildfire rule")
        assert [h["id"] for h in search_lexical(indexed_db, "wildfire", limit=5)] == [doc.id]

    def test_update_reindexes(self, indexed_db):
        # The ingest pipeline rewrites bodies in place during backfill, so
        # this is the path most likely to leave the index stale.
        doc = _add(indexed_db, title="A Rule", body="grazing permits")
        doc.body = "perfluoroalkyl substances"
        indexed_db.commit()
        assert search_lexical(indexed_db, "grazing", limit=5) == []
        assert [h["id"] for h in search_lexical(indexed_db, "perfluoroalkyl", limit=5)] == [doc.id]

    def test_delete_removes_from_the_index(self, indexed_db):
        doc = _add(indexed_db, title="wildfire rule")
        indexed_db.delete(doc)
        indexed_db.commit()
        assert search_lexical(indexed_db, "wildfire", limit=5) == []

    def test_rebuild_reindexes_the_whole_corpus(self, indexed_db):
        _add(indexed_db, title="wildfire rule")
        _add(indexed_db, title="grazing rule")
        assert rebuild_index(indexed_db) == 2
        assert len(search_lexical(indexed_db, "rule", limit=10)) == 2

    def test_ensure_is_idempotent(self, db_session):
        assert ensure_lexical_index(db_session.get_bind())
        assert ensure_lexical_index(db_session.get_bind())


class TestTriggerScope:
    def test_update_trigger_is_scoped_to_the_indexed_columns(self, indexed_db):
        # A bare AFTER UPDATE trigger would re-tokenise every row in the
        # corpus as a side effect of the nightly authority pass, which
        # writes two numeric columns the index does not contain.
        from sqlalchemy import text as sql_text

        sql = indexed_db.execute(sql_text(
            "SELECT sql FROM sqlite_master WHERE name = 'explore_fts_au'"
        )).scalar()
        assert "UPDATE OF title, summary, body" in sql

    def test_authority_writes_leave_the_index_searchable(self, indexed_db):
        from app.pipeline.analyze.document_authority import update_document_authority

        doc = _add(indexed_db, title="wildfire response rule",
                   body="a document about wildfire suppression")
        update_document_authority(indexed_db)
        assert [h["id"] for h in search_lexical(indexed_db, "wildfire", limit=5)] == [doc.id]
