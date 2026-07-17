"""Tests for _ReferenceCorpusCache — the in-memory ChromaDB reference-corpus
cache used by classify_bill_by_reference / reference_corpus_label_share.
Previously two module-level globals manipulated via `global` statements in
two functions; no direct test coverage existed for the caching behavior
itself (only classify_bill_by_reference/reference_corpus_label_share as
mocked black boxes elsewhere). These pin the cache's own behavior.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.pipeline.analyze.bill_learning import (
    _ReferenceCorpusCache,
    _load_reference_corpus,
    clear_reference_cache,
)


class TestReferenceCorpusCache:
    def test_starts_unloaded(self):
        cache = _ReferenceCorpusCache()
        assert cache.is_loaded is False
        assert cache.embeddings is None
        assert cache.labels == []

    def test_clear_resets_to_unloaded(self):
        cache = _ReferenceCorpusCache()
        cache.embeddings = np.array([[1.0, 0.0]])
        cache.labels = ["HEALTHCARE"]
        cache.clear()
        assert cache.is_loaded is False
        assert cache.embeddings is None
        assert cache.labels == []

    def test_load_returns_cached_values_without_refetching(self):
        cache = _ReferenceCorpusCache()
        cache.embeddings = np.array([[1.0, 0.0]])
        cache.labels = ["HEALTHCARE"]
        with patch("app.pipeline.vector_store.get_chroma_client") as mock_client:
            embs, labels = cache.load()
            mock_client.assert_not_called()
        assert labels == ["HEALTHCARE"]

    def test_load_fetches_and_normalizes_from_chromadb(self):
        cache = _ReferenceCorpusCache()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["S.1", "S.2"],
            "embeddings": [[3.0, 4.0], [1.0, 0.0]],
            "metadatas": [{"policyArea": "HEALTHCARE"}, {"policyArea": None}],
        }
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        with patch("app.pipeline.vector_store.get_chroma_client", return_value=mock_client):
            embs, labels = cache.load()

        assert labels == ["HEALTHCARE", "PROCEDURAL"]  # missing policyArea defaults
        # [3, 4] normalized to unit length -> [0.6, 0.8]
        assert embs[0] == pytest.approx([0.6, 0.8])
        assert cache.is_loaded is True

    def test_load_returns_empty_on_missing_collection(self):
        cache = _ReferenceCorpusCache()
        with patch("app.pipeline.vector_store.get_chroma_client", side_effect=Exception("no collection")):
            embs, labels = cache.load()
        assert embs is None
        assert labels == []
        assert cache.is_loaded is False  # a failed load doesn't get cached as loaded


class TestModuleLevelWrappers:
    """clear_reference_cache()/_load_reference_corpus() preserve the exact
    pre-refactor public API and delegate to the module-level cache instance."""

    def test_clear_reference_cache_clears_module_singleton(self):
        from app.pipeline.analyze import bill_learning
        bill_learning._reference_corpus.embeddings = np.array([[1.0]])
        bill_learning._reference_corpus.labels = ["X"]
        clear_reference_cache()
        assert bill_learning._reference_corpus.is_loaded is False

    def test_load_reference_corpus_delegates_to_singleton(self):
        from app.pipeline.analyze import bill_learning
        clear_reference_cache()
        bill_learning._reference_corpus.embeddings = np.array([[1.0, 0.0]])
        bill_learning._reference_corpus.labels = ["DEFENSE"]
        embs, labels = _load_reference_corpus()
        assert labels == ["DEFENSE"]
        clear_reference_cache()
