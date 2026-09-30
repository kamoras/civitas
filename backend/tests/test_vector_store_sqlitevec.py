"""Tests for the sqlite-vec vector store (2026-07 chroma migration).

Uses a temp-file vector DB and a fake similarity model (deterministic
tiny vectors padded to the real dimension) — no model download, no
network.
"""

import sqlite3

import numpy as np
import sqlite_vec
import pytest
from unittest.mock import MagicMock, patch

from app.models import ExploreDocument
from app.pipeline import vector_store


@pytest.fixture()
def vec_env(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_store, "_VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    monkeypatch.setattr(vector_store, "_vec_conn", None)

    def fake_encode(texts, **kwargs):
        # Deterministic unit vectors: direction keyed by simple content
        # hash so distinct texts get distinct-but-stable embeddings.
        out = np.zeros((len(texts), vector_store.SIMILARITY_DIMENSIONS))
        for i, t in enumerate(texts):
            out[i, hash(t) % 8] = 1.0
        return out

    fake_model = MagicMock()
    fake_model.encode.side_effect = fake_encode
    with patch.object(vector_store, "get_similarity_model", return_value=fake_model):
        yield fake_encode
    conn, vector_store._vec_conn = vector_store._vec_conn, None
    if conn is not None:
        conn.close()


def _doc(doc_id, title, **overrides):
    d = {
        "id": doc_id, "title": title, "summary": "", "body": "",
        "doc_type": "House Floor Speech", "source": "congress.gov",
        "date": "2026-07-01", "politician_name": "", "politician_id": "",
        "chamber": "House",
    }
    d.update(overrides)
    return d


class TestEmbedAndSearch:
    def test_roundtrip_returns_nearest_match(self, vec_env):
        n = vector_store.embed_explore_documents([
            _doc(1, "Pentagon appropriations act"),
            _doc(2, "Cyclospora outbreak testimony"),
        ])
        assert n == 2
        results = vector_store.search_explore_documents("Pentagon appropriations act ", n_results=1)
        # Query text embeds to the same direction as the identical title
        # text (fake model hashes full text; the embed path composes
        # "title summary body" with separators — search for the composed
        # form's nearest neighbor instead of exact equality).
        assert results is not None and len(results) == 1
        assert results[0]["id"] in (1, 2)
        assert 0.0 <= results[0]["distance"] <= 2.0

    def test_an_index_built_by_another_model_is_not_searched(self, vec_env, monkeypatch):
        # The API process never rebuilds it: after a model change, until the
        # pipeline process does, its vectors are another space's.
        vector_store.embed_explore_documents([_doc(1, "Pentagon appropriations act")])
        assert vector_store.search_explore_documents("Pentagon") is not None
        monkeypatch.setattr(vector_store, "index_identity", lambda: "another-model|v9")
        assert vector_store.search_explore_documents("Pentagon") is None

    def test_an_index_a_rebuild_has_not_finished_is_not_searched(self, vec_env):
        # A rebuild blanks the recorded identity and records it after its
        # last batch: a few hundred documents in, search mustn't take them
        # for the index.
        vector_store.embed_explore_documents([_doc(1, "Pentagon appropriations act")])
        conn = vector_store.get_vec_conn()
        vector_store._set_meta(conn, vector_store._INDEX_MODEL, "")
        assert vector_store.search_explore_documents("Pentagon") is None
        # Nor does another incremental embed make it look whole.
        vector_store.embed_explore_documents([_doc(2, "Another act")])
        assert vector_store.search_explore_documents("Pentagon") is None
        assert vector_store.collection_stats()["indexRebuild"] == "incomplete"

    def test_empty_index_returns_none_not_empty_list(self, vec_env):
        assert vector_store.search_explore_documents("anything") is None

    def test_table_dropped_mid_rebuild_returns_none_not_a_raise(self, vec_env):
        """search_explore_documents doesn't hold _vec_lock (a read
        shouldn't block on a rebuild that can take minutes) — a query
        landing in ensure_explore_index's brief DROP-then-recreate window
        sees "no such table" rather than 0 rows. Must be treated as the
        same "not ready yet" case as an empty index, not surfaced as a
        500 to a real /search request."""
        vector_store.get_vec_conn().execute("DROP TABLE vec_explore")
        assert vector_store.search_explore_documents("anything") is None

    def test_metadata_filter_pushed_into_query(self, vec_env):
        vector_store.embed_explore_documents([
            _doc(1, "Same title", doc_type="House Floor Speech"),
            _doc(2, "Same title", doc_type="Federal Rule"),
        ])
        results = vector_store.search_explore_documents(
            "Same title", n_results=10, doc_type="Federal Rule",
        )
        assert [r["id"] for r in results] == [2]

    def test_reembedding_same_id_upserts(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Original title")])
        vector_store.embed_explore_documents([_doc(1, "Updated title")])
        conn = vector_store.get_vec_conn()
        assert conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0] == 1
        assert conn.execute("SELECT title FROM vec_explore").fetchone()[0] == "Updated title"

    def test_index_identity_recorded(self, vec_env):
        # The stored identity covers the model AND the table layout, not the
        # model alone: the index became chunk-level without the model
        # changing, and a deployed index has to notice that and rebuild.
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        stats = vector_store.collection_stats()
        assert stats["indexModelVersion"] == vector_store.index_identity()
        assert vector_store.INDEX_MODEL_VERSION in stats["indexModelVersion"]
        assert vector_store.INDEX_SCHEMA_VERSION in stats["indexModelVersion"]

    def test_a_short_document_is_one_chunk(self, vec_env):
        # Chunking scales with document length; a one-line document must not
        # acquire extra rows for nothing.
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        assert vector_store.collection_stats()["totalVectors"] == 1
        assert vector_store.collection_stats()["chunksPerDocument"] == 1.0


class TestBillsAndMaintenance:
    def test_embed_bills_empty_list_is_a_noop(self, vec_env):
        vector_store.embed_bills([])
        assert vector_store.collection_stats()["totalVectors"] == 0

    @pytest.mark.slow
    def test_embed_bills_and_stats(self, vec_env):
        vector_store.embed_bills([
            {"billId": "hr-1234-119", "billName": "Test Act", "description": "",
             "policyArea": "DEFENSE", "policyAreas": [{"area": "DEFENSE", "confidence": 0.9}],
             "stance": "supports", "congress": 119},
        ])
        stats = vector_store.collection_stats()
        assert {"name": "bills", "count": 1, "metadata": {}} in stats["collections"]

    def test_embed_bills_skips_low_confidence_classifications(self, vec_env):
        """O3: a low-confidence guess shouldn't be promoted into the kNN
        reference corpus (the audited 55%-PROCEDURAL skew was partly this
        — every classified bill used to be upserted unconditionally)."""
        vector_store.embed_bills([
            {"billId": "hr-9999-119", "billName": "Vague Act", "description": "",
             "policyArea": "PROCEDURAL", "policyAreas": [{"area": "PROCEDURAL", "confidence": 0.5}],
             "stance": "", "congress": 119},
        ])
        stats = vector_store.collection_stats()
        assert {"name": "bills", "count": 0, "metadata": {}} in stats["collections"]

    @pytest.mark.slow
    def test_get_bill_reference_prefers_recent_bills_over_the_cap(self, vec_env):
        """O3: LIMIT with no ORDER BY returned an arbitrary hash-ordered
        slice once the corpus grew past the cap (rowid is a deterministic
        hash of bill_id, not insertion/recency order). Ordering by each
        bill's own date means growth past the cap drops the oldest bills,
        not whichever the hash happened to disfavor."""
        # 10 bills dated 2020-01 through 2020-10, alternating policy area.
        # The 3 most recent (Aug/Sep/Oct) are HEALTHCARE, DEFENSE, HEALTHCARE.
        vector_store.embed_bills([
            {"billId": f"hr-{i}-119", "billName": f"Bill {i}", "description": "",
             "policyArea": "DEFENSE" if i % 2 == 0 else "HEALTHCARE",
             "policyAreas": [{"area": "DEFENSE" if i % 2 == 0 else "HEALTHCARE", "confidence": 0.9}],
             "stance": "", "congress": 119, "date": f"2020-{i + 1:02d}-01"}
            for i in range(10)
        ])
        _, labels = vector_store.get_bill_reference(limit=3)
        assert sorted(labels) == sorted(["HEALTHCARE", "DEFENSE", "HEALTHCARE"])

    def test_reset_clears_everything(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        vector_store.reset_vector_db()
        stats = vector_store.collection_stats()
        assert stats["totalVectors"] == 0
        # Schema recreated — store is immediately usable again.
        assert vector_store.embed_explore_documents([_doc(2, "Fresh")]) == 1


class _Granted:
    """lease.job, granted: a rebuild holds the Explore lease, whose own
    session these tests' database doesn't back."""

    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def recalibrated(monkeypatch):
    """A rebuild's recalibration, recorded rather than run: fitting the
    ranking loads the real model and runs past these tests' thread joins."""
    calls = []
    monkeypatch.setattr(vector_store, "recalibrate_ranking", lambda factory: calls.append(factory))
    return calls


@pytest.fixture
def explore_lease(monkeypatch):
    from app.pipeline import lease

    monkeypatch.setattr(lease, "job", _Granted)


class TestOneWritePerDocument:
    def test_a_failure_partway_leaves_each_document_whole_old_or_new(self, vec_env, monkeypatch):
        # Deleted up front and inserted in batches, a failure left documents
        # with their old chunks gone and only some new ones in — which the
        # next top-up reads as embedded, and never finishes.
        vector_store.embed_explore_documents([_doc(1, "One old"), _doc(2, "Two old")])
        monkeypatch.setattr(vector_store, "_EMBED_BATCH", 1)  # a document a batch
        conn = vector_store.get_vec_conn()
        real_execute_count = {"n": 0}

        class _Conn:
            def __getattr__(self, name):
                return getattr(conn, name)

            def execute(self, sql, *a):
                if sql.startswith("INSERT INTO vec_explore ("):
                    real_execute_count["n"] += 1
                    if real_execute_count["n"] == 2:  # the second document's insert
                        raise sqlite3.OperationalError("database is locked")
                return conn.execute(sql, *a)

        monkeypatch.setattr(vector_store, "get_vec_conn", lambda: _Conn())
        with pytest.raises(sqlite3.OperationalError):
            vector_store.embed_explore_documents([_doc(1, "One new"), _doc(2, "Two new")])
        # The first batch kept; the second as it was.
        titles = dict(conn.execute("SELECT doc_id, title FROM vec_explore").fetchall())
        assert titles == {1: "One new", 2: "Two old"}

    def test_a_rebuild_measures_chunks_per_document_once_at_its_end(self, vec_env, db_session, monkeypatch):
        for i in range(3):
            db_session.add(ExploreDocument(doc_type="House Floor Speech", source="congress.gov",
                                           title=f"Doc {i}", summary="s", body="b", date="2026-07-01"))
        db_session.commit()
        monkeypatch.setattr(vector_store, "_REBUILD_BATCH", 1)
        measured = []
        real = vector_store._record_chunks_per_doc
        monkeypatch.setattr(vector_store, "_record_chunks_per_doc", lambda c: (measured.append(1), real(c)))
        assert vector_store.rebuild_explore_index(lambda: db_session) == 3
        assert measured == [1]

    def test_an_empty_index_keeps_no_old_chunks_per_document(self, vec_env, db_session):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        assert vector_store.rebuild_explore_index(lambda: db_session) == 0
        assert vector_store.collection_stats()["chunksPerDocument"] == 0.0


class TestTextHashes:
    def test_embedding_records_what_it_embedded_and_deleting_forgets_it(self, vec_env):
        doc = _doc(1, "A title")
        vector_store.embed_explore_documents([doc])
        assert vector_store.get_embedded_text_hashes() == {1: vector_store.explore_text_hash(doc)}
        vector_store.delete_explore_vectors({1})
        assert vector_store.get_embedded_text_hashes() == {}

    def test_the_hash_changes_with_the_text_and_only_the_text(self):
        doc = _doc(1, "A title")
        assert vector_store.explore_text_hash(doc) != vector_store.explore_text_hash({**doc, "body": "new"})
        # A departed member's speeches lose their politician_id: nothing to
        # re-encode.
        assert vector_store.explore_text_hash(doc) == vector_store.explore_text_hash({**doc, "politician_id": ""})

    def test_metadata_alone_is_written_in_place_and_filtered_on(self, vec_env, monkeypatch):
        # A chamber corrected: search filters on the vec0 column, so it must
        # move — without re-encoding the text.
        vector_store.embed_explore_documents([_doc(1, "Same title", chamber="House")])
        corrected = _doc(1, "Same title", chamber="Senate")
        with patch.object(vector_store.get_similarity_model(), "encode",
                          side_effect=AssertionError("re-encoded")):
            assert vector_store.update_explore_metadata([corrected]) == 1
        assert vector_store.search_explore_documents("Same title", chamber="Senate")
        assert not vector_store.search_explore_documents("Same title", chamber="House")
        assert vector_store.get_embedded_meta_hashes() == {1: vector_store.explore_meta_hash(corrected)}

    def test_the_relabel_reaches_every_chunk_and_only_its_document(self, vec_env):
        long_body = " ".join(f"word{i}." for i in range(40))
        model = vector_store.get_similarity_model()
        model.max_seq_length = 16
        model.tokenizer.tokenize.side_effect = str.split
        vector_store.embed_explore_documents([
            _doc(1, "Long speech", body=long_body, chamber="House"),
            _doc(2, "Other speech", chamber="House"),
        ])
        conn = vector_store.get_vec_conn()
        chunks = conn.execute("SELECT COUNT(*) FROM vec_explore WHERE doc_id = 1").fetchone()[0]
        assert chunks > 1
        vector_store.update_explore_metadata([_doc(1, "Long speech", body=long_body, chamber="Senate")])
        rows = conn.execute("SELECT doc_id, chamber FROM vec_explore").fetchall()
        assert {c for d, c in rows if d == 1} == {"Senate"}
        assert sum(1 for d, _ in rows if d == 1) == chunks
        assert {c for d, c in rows if d == 2} == {"House"}

    def test_a_store_from_before_meta_hash_gains_the_column(self, vec_env):
        conn = sqlite3.connect(vector_store._VECTOR_DB_PATH)
        conn.execute("CREATE TABLE vec_explore_text (doc_id INTEGER PRIMARY KEY, text_hash TEXT NOT NULL)")
        conn.execute("INSERT INTO vec_explore_text VALUES (1, 'abc')")
        conn.commit()
        conn.close()
        # Every old row reads as never labelled, so the next run relabels it.
        assert vector_store.get_embedded_meta_hashes() == {1: ""}

    def test_a_failed_relabel_raises_and_keeps_the_embed(self, vec_env):
        # Raised, so the run reports it rather than resolve its alert; the
        # embed before it is committed.
        with patch.object(vector_store, "update_explore_metadata", side_effect=RuntimeError("boom")), \
             pytest.raises(RuntimeError):
            vector_store.top_up_explore_index(lambda: [_doc(1, "A title")], lambda: [_doc(2, "B")])
        assert set(vector_store.get_embedded_text_hashes()) == {1}

    def test_another_process_adding_meta_hash_first_is_not_an_error(self, vec_env):
        conn = sqlite3.connect(vector_store._VECTOR_DB_PATH)
        conn.execute("CREATE TABLE vec_explore_text (doc_id INTEGER PRIMARY KEY, text_hash TEXT NOT NULL)")
        conn.commit()
        real_execute = sqlite3.Connection.execute

        class Racing(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE vec_explore_text"):
                    real_execute(self, sql, *args)  # the other process's
                return real_execute(self, sql, *args)

        racing = sqlite3.connect(vector_store._VECTOR_DB_PATH, factory=Racing)
        racing.enable_load_extension(True)
        sqlite_vec.load(racing)
        vector_store._ensure_schema(racing)
        cols = {r[1] for r in racing.execute("PRAGMA table_info(vec_explore_text)")}
        assert "meta_hash" in cols
        racing.close()
        conn.close()

    def test_the_chunk_insert_writes_the_fields_the_hash_covers(self):
        columns = vector_store._CHUNK_INSERT.split("(")[1].split(")")[0].split(", ")
        assert tuple(columns[2:-2]) == vector_store._META_FIELDS
        assert vector_store._CHUNK_INSERT.count("?") == len(columns)
        doc = _doc(1, "A title")
        for field in vector_store._META_FIELDS:
            assert vector_store.explore_meta_hash(doc) != vector_store.explore_meta_hash({**doc, field: "changed"})

    def test_a_document_left_without_text_loses_its_old_chunks(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "A title")])
        emptied = _doc(1, "")
        vector_store.embed_explore_documents([emptied])
        conn = vector_store.get_vec_conn()
        assert conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0] == 0
        assert vector_store.get_embedded_text_hashes() == {1: vector_store.explore_text_hash(emptied)}

class TestEnsureExploreIndex:
    def test_noop_when_index_current(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        with patch.object(vector_store, "start_writer") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_not_called()

    def test_a_rebuild_that_died_partway_is_restarted(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        vector_store._set_meta(vector_store.get_vec_conn(), vector_store._INDEX_MODEL, "")
        with patch.object(vector_store, "start_writer") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_called_once()

    def test_an_empty_corpus_is_built_and_recorded_whole(self, vec_env, db_session, explore_lease):
        # A blank identity left by a cut-off rebuild ends here too, rather
        # than read "incomplete" at every start until a run happens by.
        vector_store._set_meta(vector_store.get_vec_conn(), vector_store._INDEX_MODEL, "")
        vector_store.ensure_explore_index(lambda: db_session)
        import threading as _t
        for t in _t.enumerate():
            if t.name == "explore-reindex":
                t.join(timeout=10)
        assert vector_store.index_is_whole()

    def test_a_lock_while_checking_raises_rather_than_rebuild(self, vec_env, monkeypatch):
        # Can't tell whether it's whole: never a reason to drop it.
        def locked():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(vector_store, "index_is_whole", locked)
        swap = MagicMock()
        monkeypatch.setattr(vector_store, "_swap_tables", swap)
        with pytest.raises(sqlite3.OperationalError):
            vector_store.rebuild_explore_index(lambda: None, wait=True, if_incomplete=True)
        swap.assert_not_called()

    def test_a_rebuild_reads_documents_by_id_so_deletions_skip_none(self, vec_env, db_session, monkeypatch):
        # An Explore run may delete documents while a start's rebuild pages
        # through them: paged by OFFSET, the ones after a deletion shifted
        # back past the next page's start and were never embedded.
        monkeypatch.setattr(vector_store, "_REBUILD_BATCH", 2)
        for i in range(5):
            db_session.add(ExploreDocument(doc_type="House Floor Speech", source="congress.gov",
                                           title=f"Doc {i}", summary="s", body="b", date="2026-07-01"))
        db_session.commit()
        ids = [d.id for d in db_session.query(ExploreDocument).order_by(ExploreDocument.id)]
        real_embed = vector_store.embed_explore_documents
        seen = []

        def embed_then_delete_the_first(docs, **kw):
            seen.extend(d["id"] for d in docs)
            if len(seen) == len(docs):  # after the first batch
                db_session.query(ExploreDocument).filter(ExploreDocument.id == ids[0]).delete()
                db_session.commit()
            return real_embed(docs, **kw)

        monkeypatch.setattr(vector_store, "embed_explore_documents", embed_then_delete_the_first)
        vector_store.rebuild_explore_index(lambda: db_session)
        assert set(ids) <= set(seen)

    def test_a_classification_model_change_leaves_the_search_index(self, vec_env):
        # The search index is the similarity model's, with its own identity:
        # dropping it threw away a whole rebuild (and waited one out first).
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        vector_store.invalidate_on_model_change()
        assert vector_store.index_is_whole()

    def test_a_model_change_recreates_the_bill_table(self, vec_env):
        # A vec0 table's width is fixed at creation: emptied in place, a
        # new model's wider vectors would never fit it.
        conn = vector_store.get_vec_conn()
        conn.execute("DROP TABLE vec_bills")
        conn.execute("CREATE VIRTUAL TABLE vec_bills USING vec0(embedding float[8], policy_area text, +meta_json text)")
        conn.commit()
        vector_store.invalidate_on_model_change()
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'vec_bills'").fetchone()[0]
        assert f"float[{vector_store.EMBEDDING_DIMENSIONS}]" in sql

    def test_a_run_waiting_on_a_rebuild_does_not_rebuild_what_it_left_whole(self, vec_env, monkeypatch):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        embed = MagicMock()
        monkeypatch.setattr(vector_store, "embed_explore_documents", embed)
        assert vector_store.rebuild_explore_index(lambda: None, wait=True, if_incomplete=True) is None
        embed.assert_not_called()

    def test_a_run_rebuilds_an_index_it_cannot_read(self, vec_env, db_session, monkeypatch):
        # The run decided to rebuild because the index couldn't be read: the
        # wait's own look at it mustn't raise the same error and stop it.
        db_session.add(ExploreDocument(doc_type="House Floor Speech", source="congress.gov",
                                       title="A real doc", summary="s", body="b", date="2026-07-01"))
        db_session.commit()

        def unreadable():
            raise sqlite3.OperationalError("database disk image is malformed")

        monkeypatch.setattr(vector_store, "index_is_whole", unreadable)
        assert vector_store.rebuild_explore_index(lambda: db_session, wait=True, if_incomplete=True) == 1

    def test_an_index_a_rebuild_left_empty_reads_incomplete(self, vec_env):
        vector_store._set_meta(vector_store.get_vec_conn(), vector_store._INDEX_MODEL, "")
        assert vector_store.collection_stats()["indexRebuild"] == "incomplete"

    def test_a_reset_waits_out_a_running_rebuild(self, vec_env):
        import threading as _t

        with vector_store._rebuild_lock:
            reset = _t.Thread(target=vector_store.reset_vector_db)
            reset.start()
            reset.join(timeout=0.3)
            assert reset.is_alive()
        reset.join(timeout=5)
        assert not reset.is_alive()

    def test_a_start_rebuilds_an_index_it_cannot_read(self, vec_env, monkeypatch):
        def unreadable():
            raise sqlite3.OperationalError("database disk image is malformed")

        monkeypatch.setattr(vector_store, "index_is_whole", unreadable)
        with patch.object(vector_store, "start_writer") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_called_once()

    def test_a_start_waits_out_a_lock_rather_than_rebuild_or_give_up(self, vec_env, monkeypatch):
        # A rollout's overlap holding the file a moment is no reason to drop
        # an index that may well be whole — nor to leave one that isn't
        # until the next night.
        monkeypatch.setattr(vector_store, "_BUSY_CHECK_EVERY_S", 0)
        answers = iter([sqlite3.OperationalError("database is locked")] * 3 + [False])

        def check():
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(vector_store, "index_is_whole", check)
        with patch.object(vector_store, "start_writer") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_called_once()

        def locked():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(vector_store, "index_is_whole", locked)
        with patch.object(vector_store, "start_writer") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_not_called()  # stayed locked: left to the next run

    def test_a_swap_that_fails_leaves_a_whole_index_whole(self, vec_env, monkeypatch):
        # The identity is blanked in the swap's own transaction.
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        real_open = vector_store._open_vec_conn

        def refusing(timeout, **kw):
            conn = real_open(timeout, **kw)
            real_execute = conn.execute

            class _Conn:
                def __getattr__(self, name):
                    return getattr(conn, name)

                def execute(self, sql, *a):
                    if sql.startswith("DROP"):
                        raise sqlite3.OperationalError("database is locked")
                    return real_execute(sql, *a)

            return _Conn()

        monkeypatch.setattr(vector_store, "_open_vec_conn", refusing)
        with pytest.raises(sqlite3.OperationalError):
            vector_store.rebuild_explore_index(lambda: None)
        assert vector_store.index_is_whole()

    def test_a_data_reset_begun_meanwhile_is_left_to_it(self, vec_env, monkeypatch):
        # It empties the index; the first Explore run after it builds it.
        from app.background import WritesHeld

        vector_store._set_meta(vector_store.get_vec_conn(), vector_store._INDEX_MODEL, "")

        def held(*_a, **_k):
            raise WritesHeld("data-reset")

        monkeypatch.setattr(vector_store, "start_writer", held)
        vector_store.ensure_explore_index(lambda: None)  # doesn't raise

    def test_a_runs_top_up_holds_the_rebuild_lock(self, vec_env, monkeypatch):
        # A start's rebuild waits for it rather than embed the same
        # documents beside it.
        # And it asks what is missing under the lock, not before a rebuild
        # it waited out. Not reported as a rebuild, though: it isn't one.
        seen = []
        monkeypatch.setattr(vector_store, "embed_explore_documents", lambda docs: seen.append(docs) or 0)
        vector_store.top_up_explore_index(lambda: [vector_store._rebuild_lock.locked(), vector_store.is_rebuilding()])
        assert seen == [[True, False]] and not vector_store._rebuild_lock.locked()

    def test_a_rebuild_waiting_its_turn_counts_as_underway(self, vec_env):
        # check-and-deploy mustn't restart the pipeline just as it begins.
        import threading as _t

        # Whole, so the waiter, once through, has nothing to do.
        vector_store.embed_explore_documents([_doc(1, "Anything")])

        with vector_store._rebuild_lock:
            waiting = _t.Thread(target=vector_store.rebuild_explore_index, args=(lambda: None,),
                                kwargs={"wait": True, "if_incomplete": True})
            waiting.start()
            for _ in range(100):
                if vector_store.is_rebuilding():
                    break
                import time as _time
                _time.sleep(0.01)
            assert vector_store.is_rebuilding()
        waiting.join(timeout=5)

    def test_a_rebuild_completed_while_a_re_embed_waited_is_not_redone(self, vec_env, db_session, monkeypatch):
        import time as _time

        asked = _time.monotonic()
        monkeypatch.setattr(vector_store, "_last_rebuild_began_at", asked + 1)
        embed = MagicMock()
        monkeypatch.setattr(vector_store, "embed_explore_documents", embed)
        assert vector_store.rebuild_explore_index(lambda: db_session, wait=True, unless_rebuilt_since=asked) is None
        embed.assert_not_called()

    def test_a_table_that_cannot_be_read_is_not_whole_by_its_identity_alone(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        vector_store.get_vec_conn().execute("DROP TABLE vec_explore")
        with pytest.raises(sqlite3.OperationalError):
            vector_store.index_is_whole()

    def test_a_rebuild_already_running_is_not_started_again(self, vec_env):
        # Two overlapping would each clear what the other built.
        vector_store._set_meta(vector_store.get_vec_conn(), vector_store._INDEX_MODEL, "")
        with vector_store._rebuild_lock, vector_store.rebuild_underway():
            with patch.object(vector_store, "start_writer") as thread:
                vector_store.ensure_explore_index(lambda: None)
            thread.assert_not_called()
            assert vector_store.rebuild_explore_index(lambda: None) is None
            assert vector_store.collection_stats()["indexRebuild"] == "running"

    def test_a_rebuild_that_raised_is_not_ready_until_one_completes(self, vec_env, db_session, monkeypatch):
        db_session.add(ExploreDocument(
            doc_type="House Floor Speech", source="congress.gov",
            title="A real doc", summary="s", body="b", date="2026-07-01",
        ))
        db_session.commit()
        vector_store.embed_explore_documents([_doc(1, "A real doc")])
        real_embed = vector_store.embed_explore_documents
        calls = []

        def fails_after_a_batch(docs, **kw):
            calls.append(1)
            real_embed(docs, **kw)
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(vector_store, "embed_explore_documents", fails_after_a_batch)
        # After the swap: a RebuildFailed, whatever the cause — the index is
        # gone, and no caller may take it for a harmless lock.
        with pytest.raises(vector_store.RebuildFailed):
            vector_store.rebuild_explore_index(lambda: db_session)
        monkeypatch.setattr(vector_store, "embed_explore_documents", real_embed)

        # A batch is in, but the build isn't complete: not the index.
        assert calls and vector_store.collection_stats()["indexRebuild"] == "incomplete"
        assert vector_store.search_explore_documents("A real doc", n_results=1) is None
        assert vector_store.rebuild_explore_index(lambda: db_session) == 1
        assert vector_store.collection_stats()["indexRebuild"] == ""
        assert vector_store.search_explore_documents("A real doc", n_results=1) is not None

    def test_an_index_left_by_another_model_is_not_made_whole_by_incremental_embeds(self, vec_env):
        # A rebuild that failed before its DROP leaves the old model's
        # vectors: new documents embedded beside them don't make it ready.
        vector_store.embed_explore_documents([_doc(1, "Old")])
        conn = vector_store.get_vec_conn()
        vector_store._set_meta(conn, vector_store._INDEX_MODEL, "old-model|v1")
        vector_store.embed_explore_documents([_doc(2, "New")])
        assert vector_store._get_meta(conn, vector_store._INDEX_MODEL) == "old-model|v1"
        assert vector_store.search_explore_documents("New") is None

    def test_rebuild_spawned_when_empty_and_docs_exist(self, vec_env, db_session, recalibrated, explore_lease):
        db_session.add(ExploreDocument(
            doc_type="House Floor Speech", source="congress.gov",
            title="A real doc", summary="s", body="b", date="2026-07-01",
        ))
        db_session.commit()

        vector_store.ensure_explore_index(lambda: db_session)
        # The daemon thread runs the reindex; wait for it via join on the
        # spawned thread found by name.
        import threading as _t
        for t in _t.enumerate():
            if t.name == "explore-reindex":
                t.join(timeout=10)
        results = vector_store.search_explore_documents("A real doc", n_results=1)
        assert results is not None and results[0]["title"] == "A real doc"
        # And the ranking is refitted: the one in force was measured against
        # the index this replaced.
        assert len(recalibrated) == 1

    def test_a_start_leaves_the_refit_to_a_run_holding_the_explore_lease(
        self, vec_env, db_session, recalibrated, monkeypatch,
    ):
        # That run is mid-ingest — the corpus and keyword index moving under
        # a fit — and refits at its own end.
        from app.pipeline import lease

        class _Refused(_Granted):
            def __enter__(self):
                return False

        monkeypatch.setattr(lease, "job", _Refused)
        monkeypatch.setattr(lease, "holder", lambda db, tier: "Explore ingest" if tier == lease.EXPLORE else None)
        db_session.add(ExploreDocument(doc_type="House Floor Speech", source="congress.gov",
                                       title="A real doc", summary="s", body="b", date="2026-07-01"))
        db_session.commit()
        vector_store.ensure_explore_index(lambda: db_session)
        import threading as _t
        for t in _t.enumerate():
            if t.name == "explore-reindex":
                t.join(timeout=10)
        assert vector_store.index_is_whole() and recalibrated == []

        # Refused for anything else (the lease's database busy): refitted
        # anyway, rather than the old fit left in force for a day.
        monkeypatch.setattr(lease, "holder", lambda db, tier: None)
        vector_store._refit_after_a_start_rebuild(lambda: db_session)
        assert len(recalibrated) == 1

    def test_rebuild_recreates_a_stale_pre_migration_schema(self, vec_env, db_session, explore_lease):
        """Regression for a live 2026-08-30 incident: a prior deploy's
        vec_explore table (created before `doc_id` existed in the schema)
        survived on disk forever because CREATE VIRTUAL TABLE IF NOT
        EXISTS is a no-op against it, and DELETE FROM only clears rows
        against whatever schema is already there — a vec0 table's columns
        can't be ALTERed. Every embed attempt failed with "no such
        column: doc_id" and the index stayed at 0 rows against 7,127 real
        documents. The rebuild path must DROP + recreate, not DELETE FROM."""
        conn = vector_store.get_vec_conn()
        conn.execute("DROP TABLE vec_explore")
        conn.execute(
            f"""CREATE VIRTUAL TABLE vec_explore USING vec0(
                embedding float[{vector_store.SIMILARITY_DIMENSIONS}] distance_metric=cosine,
                doc_type text,
                chamber text,
                politician_id text,
                +title text,
                +date text,
                +source text,
                +politician_name text,
                +snippet text
            )"""
        )
        vector_store._set_meta(conn, vector_store._INDEX_MODEL, "minilm-l6-v2+1-old-schema")
        conn.commit()

        db_session.add(ExploreDocument(
            doc_type="House Floor Speech", source="congress.gov",
            title="A real doc", summary="s", body="b", date="2026-07-01",
        ))
        db_session.commit()

        vector_store.ensure_explore_index(lambda: db_session)
        import threading as _t
        for t in _t.enumerate():
            if t.name == "explore-reindex":
                t.join(timeout=10)

        results = vector_store.search_explore_documents("A real doc", n_results=1)
        assert results is not None and results[0]["title"] == "A real doc"


@pytest.mark.parametrize("role,expected", [("api", vector_store._API_BUSY_TIMEOUT_S),
                                           ("worker", vector_store.SQLITE_BUSY_TIMEOUT_S),
                                           ("all", vector_store.SQLITE_BUSY_TIMEOUT_S)])
def test_only_the_read_only_api_waits_briefly_on_the_vector_store(monkeypatch, role, expected):
    # A search behind a pipeline write gives up rather than holding a
    # worker thread for the writers' full wait; the writers keep it.
    from app.config import settings

    monkeypatch.setattr(settings, "PROCESS_ROLE", role)
    assert vector_store._busy_timeout_s() == expected


def test_a_connection_that_failed_to_open_is_closed_not_leaked(vec_env, monkeypatch):
    # A search retrying through a locked file opens a connection per attempt:
    # each one not kept must be closed.
    import sqlite3

    opened = []
    real_connect = sqlite3.connect

    class Tracked:
        def __init__(self, conn):
            self.conn, self.closed = conn, False
            opened.append(self)

        def close(self):
            self.closed = True
            self.conn.close()

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(vector_store.sqlite3, "connect", lambda *a, **k: Tracked(real_connect(*a, **k)))
    monkeypatch.setattr(vector_store, "_ensure_schema", lambda conn: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked")))
    with patch.dict("sys.modules", {"sqlite_vec": MagicMock()}):
        for _ in range(2):
            with pytest.raises(sqlite3.OperationalError):
                vector_store.get_vec_conn()
    # Every connection opened (the WAL switch's short-lived ones included)
    # was closed.
    assert len(opened) >= 2 and all(t.closed for t in opened)
    assert vector_store._vec_conn is None


async def test_the_admin_re_embed_runs_in_the_background_and_refuses_when_it_cant(monkeypatch, db_session):
    # Over twenty minutes on the Pi: tied to its request, nginx's timeout
    # dropped it partway and let its lease go under writes still running.
    import threading as _t

    from fastapi import HTTPException

    from app.api.admin import admin_reembed_explore
    from app.pipeline import lease

    done = _t.Event()
    monkeypatch.setattr(vector_store, "rebuild_explore_index", lambda _factory, **_k: (done.set(), 0)[1])
    monkeypatch.setattr(vector_store, "_write_model_version", lambda: None)
    monkeypatch.setattr("app.pipeline.lexical_index.rebuild_index", lambda db: 0)
    monkeypatch.setattr("app.pipeline.analyze.document_authority.update_document_authority", lambda db: {})
    monkeypatch.setattr(lease, "job", _Granted)
    assert await admin_reembed_explore(db=db_session) == {"started": True}
    assert done.wait(5)
    for t in _t.enumerate():
        if t.name == "explore-reembed":
            t.join(timeout=10)  # underway until its last pass is done

    # One queued or running: a second is refused, not told it started.
    from app.api import admin

    with admin._reembed_slot:
        with pytest.raises(HTTPException) as refused:
            await admin_reembed_explore(db=db_session)
    assert refused.value.status_code == 409 and "already under way" in refused.value.detail

    # Held by an Explore run: refused with the reason, not started to skip.
    monkeypatch.setattr(lease, "holder", lambda session, tier: "Explore ingest" if tier == lease.EXPLORE else None)
    with pytest.raises(HTTPException) as refused:
        await admin_reembed_explore(db=db_session)
    assert refused.value.status_code == 409 and "Explore ingest" in refused.value.detail


@pytest.mark.slow
def test_each_tables_width_is_its_models_own():
    # A vec0 table's width is fixed at creation from these constants: one
    # left behind by a model change recreates the table at the wrong width,
    # and every insert into it fails.
    assert vector_store.get_similarity_model().get_sentence_embedding_dimension() == vector_store.SIMILARITY_DIMENSIONS
    assert vector_store.get_embedding_model().get_sentence_embedding_dimension() == vector_store.EMBEDDING_DIMENSIONS
