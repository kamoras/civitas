"""Tests for the sqlite-vec vector store (2026-07 chroma migration).

Uses a temp-file vector DB and a fake similarity model (deterministic
tiny vectors padded to the real dimension) — no model download, no
network.
"""

import numpy as np
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
        out = np.zeros((len(texts), vector_store.EMBEDDING_DIMENSIONS))
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

    def test_empty_index_returns_none_not_empty_list(self, vec_env):
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

    def test_index_model_version_recorded(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        stats = vector_store.collection_stats()
        assert stats["indexModelVersion"] == vector_store.INDEX_MODEL_VERSION
        assert stats["totalVectors"] == 1


class TestBillsAndMaintenance:
    def test_embed_bills_and_stats(self, vec_env):
        vector_store.embed_bills([
            {"billId": "hr-1234-119", "billName": "Test Act", "description": "",
             "policyArea": "DEFENSE", "stance": "supports", "congress": 119},
        ])
        stats = vector_store.collection_stats()
        assert {"name": "bills", "count": 1, "metadata": {}} in stats["collections"]

    def test_reset_clears_everything(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        vector_store.reset_vector_db()
        stats = vector_store.collection_stats()
        assert stats["totalVectors"] == 0
        # Schema recreated — store is immediately usable again.
        assert vector_store.embed_explore_documents([_doc(2, "Fresh")]) == 1


class TestEnsureExploreIndex:
    def test_noop_when_index_current(self, vec_env):
        vector_store.embed_explore_documents([_doc(1, "Anything")])
        with patch.object(vector_store.threading, "Thread") as thread:
            vector_store.ensure_explore_index(lambda: None)
        thread.assert_not_called()

    def test_rebuild_spawned_when_empty_and_docs_exist(self, vec_env, db_session):
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
