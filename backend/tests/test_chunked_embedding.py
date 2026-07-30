"""Tests for chunked embedding — the fix for the deep-passage recall gap.

The index used to hold one vector per document over `title + summary +
body[:800]`. Anything past that was unreachable by the semantic channel,
not because of a design trade but because a sentence-transformer silently
truncates at its own `max_seq_length` and the extra text was never
embedded at all.

The encoder here is a deterministic hashing bag-of-words, not the real
model: "does a query matching only deep text retrieve the document" then
has a definite answer instead of a fuzzy one, and the tests need no model
weights.
"""

import numpy as np
import pytest

from app.pipeline import vector_store as vs
from app.pipeline.vector_store import chunk_text


def _count_words(text: str) -> int:
    return len(text.split())


class TestChunkText:
    def test_short_text_is_a_single_window(self):
        assert chunk_text("One short sentence.", 64, _count_words) == [
            "One short sentence."
        ]

    def test_empty_text_yields_nothing(self):
        assert chunk_text("", 64, _count_words) == []
        assert chunk_text("   \n\n  ", 64, _count_words) == []
        assert chunk_text(None, 64, _count_words) == []

    def test_every_window_fits_the_encoder_context(self):
        text = " ".join(f"Sentence number {i} says something." for i in range(200))
        windows = chunk_text(text, 20, _count_words)
        assert len(windows) > 1
        assert all(_count_words(w) <= 20 for w in windows)

    def test_no_text_is_lost(self):
        # The whole point: every word must land in at least one window, or
        # chunking has simply moved the truncation somewhere less visible.
        text = " ".join(f"Unique{i} token here." for i in range(120))
        windows = chunk_text(text, 15, _count_words)
        covered = " ".join(windows)
        for i in range(120):
            assert f"Unique{i}" in covered

    def test_windows_overlap_by_a_sentence(self):
        # A passage straddling a boundary has to be wholly present in at
        # least one window, or chunking creates its own blind spots at every
        # seam.
        text = " ".join(f"Alpha{i} beta gamma delta." for i in range(30))
        windows = chunk_text(text, 12, _count_words)
        assert len(windows) > 2
        overlaps = sum(
            1 for a, b in zip(windows, windows[1:])
            if a.split(".")[-2].strip().split()[0] in b
        )
        assert overlaps > 0

    def test_a_sentence_longer_than_the_window_is_split_not_dropped(self):
        # Federal Register headings run together with their citation blocks
        # produce exactly this: one "sentence" with no interior boundary.
        giant = " ".join(f"word{i}" for i in range(100))
        windows = chunk_text(giant, 10, _count_words)
        assert all(_count_words(w) <= 10 for w in windows)
        covered = " ".join(windows)
        for i in range(100):
            assert f"word{i}" in covered

    def test_paragraph_structure_is_respected(self):
        text = "First para sentence one. First para sentence two.\n\nSecond para here."
        windows = chunk_text(text, 6, _count_words)
        assert all(_count_words(w) <= 6 for w in windows)

    def test_is_deterministic(self):
        text = " ".join(f"Sentence {i} of the document." for i in range(50))
        assert chunk_text(text, 20, _count_words) == chunk_text(text, 20, _count_words)


class _FakeModel:
    """Deterministic hashing encoder — exact, and needs no weights."""

    max_seq_length = 64

    class tokenizer:
        @staticmethod
        def tokenize(text):
            return text.split()

    def encode(self, texts, **_kwargs):
        out = np.zeros((len(texts), vs.EMBEDDING_DIMENSIONS), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in text.lower().split():
                out[i, hash(word) % vs.EMBEDDING_DIMENSIONS] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


@pytest.fixture()
def vector_index(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    monkeypatch.setattr(vs, "_vec_conn", None)
    monkeypatch.setattr(vs, "get_similarity_model", lambda: _FakeModel())
    yield vs
    if vs._vec_conn is not None:
        vs._vec_conn.close()
    monkeypatch.setattr(vs, "_vec_conn", None)


FILLER = "The agency describes routine administrative procedures. " * 120
NEEDLE = "Perfluorooctanoic contamination thresholds govern municipal wellfields."


def _doc(doc_id, title, body):
    return {
        "id": doc_id, "title": title, "summary": "Routine agency notice.",
        "body": body, "doc_type": "Notice", "source": "Federal Register",
        "date": "2026-01-01", "politician_name": "", "politician_id": "",
        "chamber": "Regulatory",
    }


class TestDeepPassageRetrieval:
    def test_text_far_past_the_old_cutoff_is_retrievable(self, vector_index):
        # The regression this whole change exists for. The needle sits ~6,700
        # characters into the body — eight times past the old 800-character
        # truncation, where it was not embedded at all.
        target = _doc(1, "Notice of Administrative Procedure", FILLER + NEEDLE + FILLER)
        decoy = _doc(2, "Another Administrative Notice", FILLER + FILLER)
        assert target["body"].index(NEEDLE) > 6000

        assert vector_index.embed_explore_documents([target, decoy]) == 2
        hits = vector_index.search_explore_documents(
            "Perfluorooctanoic contamination wellfields", n_results=5)
        assert hits[0]["id"] == 1

    def test_results_are_documents_not_chunks(self, vector_index):
        # A long document occupies many chunk slots; without folding them
        # back, one rule would fill the entire first page of results.
        vector_index.embed_explore_documents([
            _doc(1, "Long Rule", FILLER + NEEDLE + FILLER),
            _doc(2, "Second Rule", FILLER + "Grazing permits on rangeland. " + FILLER),
        ])
        hits = vector_index.search_explore_documents("administrative", n_results=10)
        ids = [h["id"] for h in hits]
        assert len(ids) == len(set(ids))

    def test_a_document_is_scored_by_its_best_chunk(self, vector_index):
        # Max pooling, not averaging: a long document with one passage
        # squarely on the query is a good answer, and averaging over its
        # other pages would bury it.
        focused = _doc(1, "Short Notice", NEEDLE)
        buried = _doc(2, "Long Notice", FILLER + NEEDLE + FILLER)
        vector_index.embed_explore_documents([focused, buried])
        hits = vector_index.search_explore_documents(
            "Perfluorooctanoic contamination wellfields", n_results=5)
        assert {h["id"] for h in hits} == {1, 2}

    def test_reembedding_replaces_a_document_rather_than_duplicating_it(
        self, vector_index
    ):
        doc = _doc(1, "Notice", FILLER + NEEDLE)
        vector_index.embed_explore_documents([doc])
        conn = vector_index.get_vec_conn()
        first = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
        vector_index.embed_explore_documents([doc])
        assert conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0] == first

    def test_shrinking_a_document_removes_its_surplus_chunks(self, vector_index):
        vector_index.embed_explore_documents([_doc(1, "Notice", FILLER + FILLER)])
        conn = vector_index.get_vec_conn()
        before = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
        vector_index.embed_explore_documents([_doc(1, "Notice", "Now very short.")])
        after = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
        assert after < before
        assert conn.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM vec_explore").fetchone()[0] == 1

    def test_embedded_ids_are_documents_not_rows(self, vector_index):
        vector_index.embed_explore_documents([
            _doc(1, "One", FILLER), _doc(2, "Two", FILLER),
        ])
        assert vector_index.get_embedded_explore_ids() == {1, 2}

    def test_empty_index_still_reports_not_ready(self, vector_index):
        assert vector_index.search_explore_documents("anything", n_results=5) is None

    def test_metadata_filters_survive_chunking(self, vector_index):
        senate = {**_doc(1, "Senate Remarks", FILLER + NEEDLE), "chamber": "Senate"}
        reg = _doc(2, "Regulatory Notice", FILLER + NEEDLE)
        vector_index.embed_explore_documents([senate, reg])
        hits = vector_index.search_explore_documents(
            "Perfluorooctanoic wellfields", n_results=10, chamber="Senate")
        assert [h["id"] for h in hits] == [1]


class TestIndexIdentity:
    def test_identity_covers_model_and_schema(self):
        identity = vs.index_identity()
        assert vs.INDEX_MODEL_VERSION in identity
        assert vs.INDEX_SCHEMA_VERSION in identity

    def test_stored_identity_is_written_on_embed(self, vector_index):
        vector_index.embed_explore_documents([_doc(1, "Notice", "Short body.")])
        conn = vector_index.get_vec_conn()
        assert vs._get_meta(conn, "explore_index_model") == vs.index_identity()

    def test_chunks_per_document_is_measured_and_stored(self, vector_index):
        vector_index.embed_explore_documents([_doc(1, "Notice", FILLER)])
        conn = vector_index.get_vec_conn()
        measured = float(vs._get_meta(conn, "explore_chunks_per_doc"))
        actual = conn.execute("SELECT COUNT(*) FROM vec_explore").fetchone()[0]
        assert measured == pytest.approx(actual)
