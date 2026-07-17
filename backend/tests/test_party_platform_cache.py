"""Tests for _PlatformEmbeddingCache — the in-memory R/D party-platform
centroid cache used by classify_party_alignment / classify_party_alignment_multi
/ analyze_partisan_depth. Previously four module-level globals manipulated via
`global` statements in two functions; no direct test coverage existed for the
caching behavior itself. These pin the cache's own behavior.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.pipeline.analyze.party_platform import _PlatformEmbeddingCache


def _fake_model():
    model = MagicMock()

    def encode(texts, show_progress_bar=False, batch_size=None):
        return np.array([[1.0, 0.0, 0.0] for _ in texts])

    model.encode.side_effect = encode
    return model


class TestPlatformEmbeddingCache:
    def test_starts_unloaded(self):
        cache = _PlatformEmbeddingCache()
        assert cache.is_loaded is False
        assert cache.r_embeddings == {}
        assert cache.d_embeddings == {}
        assert cache.r_aggregate is None
        assert cache.d_aggregate is None

    def test_clear_resets_to_unloaded(self):
        cache = _PlatformEmbeddingCache()
        cache.r_embeddings["TAXES"] = np.array([1.0, 0.0])
        cache.d_embeddings["TAXES"] = np.array([0.0, 1.0])
        cache.r_aggregate = np.array([1.0, 0.0])
        cache.d_aggregate = np.array([0.0, 1.0])
        cache.clear()
        assert cache.is_loaded is False
        assert cache.r_embeddings == {}
        assert cache.d_embeddings == {}
        assert cache.r_aggregate is None
        assert cache.d_aggregate is None

    def test_is_loaded_requires_both_parties(self):
        cache = _PlatformEmbeddingCache()
        cache.r_embeddings["TAXES"] = np.array([1.0, 0.0])
        assert cache.is_loaded is False

    def test_initialize_seeds_only_populates_both_parties(self):
        cache = _PlatformEmbeddingCache()
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=_fake_model()):
            cache.initialize(db=None)

        assert cache.is_loaded is True
        assert set(cache.r_embeddings) == set(cache.d_embeddings)
        assert cache.r_aggregate is not None
        assert cache.d_aggregate is not None
        assert np.linalg.norm(cache.r_aggregate) == pytest.approx(1.0)
        assert np.linalg.norm(cache.d_aggregate) == pytest.approx(1.0)

    def test_initialize_is_a_noop_once_loaded(self):
        cache = _PlatformEmbeddingCache()
        cache.r_embeddings["TAXES"] = np.array([1.0, 0.0])
        cache.d_embeddings["TAXES"] = np.array([0.0, 1.0])
        with patch("app.pipeline.vector_store.get_embedding_model") as mock_model:
            cache.initialize(db=None)
            mock_model.assert_not_called()

    def test_initialize_falls_back_to_seeds_when_data_centroids_fail(self):
        cache = _PlatformEmbeddingCache()
        db = MagicMock()
        with (
            patch("app.pipeline.vector_store.get_embedding_model", return_value=_fake_model()),
            patch(
                "app.pipeline.analyze.party_platform._build_data_centroids",
                side_effect=Exception("db unavailable"),
            ),
        ):
            cache.initialize(db=db)
        assert cache.is_loaded is True

    def test_ensure_initializes_cold_start_when_unloaded(self):
        cache = _PlatformEmbeddingCache()
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=_fake_model()):
            cache.ensure()
        assert cache.is_loaded is True

    def test_ensure_is_a_noop_once_loaded(self):
        cache = _PlatformEmbeddingCache()
        cache.r_embeddings["TAXES"] = np.array([1.0, 0.0])
        cache.d_embeddings["TAXES"] = np.array([0.0, 1.0])
        with patch("app.pipeline.vector_store.get_embedding_model") as mock_model:
            cache.ensure()
            mock_model.assert_not_called()


class TestModuleLevelWrappers:
    """clear_platform_cache()/initialize_platform_embeddings()/
    _ensure_platform_embeddings() preserve the exact pre-refactor public API
    and delegate to the module-level cache instance."""

    def test_clear_platform_cache_clears_module_singleton(self):
        from app.pipeline.analyze import party_platform

        party_platform._platform_cache.r_embeddings["TAXES"] = np.array([1.0])
        party_platform._platform_cache.d_embeddings["TAXES"] = np.array([1.0])
        party_platform._platform_cache.r_aggregate = np.array([1.0])
        party_platform._platform_cache.d_aggregate = np.array([1.0])

        party_platform.clear_platform_cache()

        assert party_platform._platform_cache.is_loaded is False

    def test_initialize_platform_embeddings_delegates_to_singleton(self):
        from app.pipeline.analyze import party_platform

        party_platform.clear_platform_cache()
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=_fake_model()):
            party_platform.initialize_platform_embeddings(db=None)

        assert party_platform._platform_cache.is_loaded is True
        party_platform.clear_platform_cache()

    def test_ensure_platform_embeddings_delegates_to_singleton(self):
        from app.pipeline.analyze import party_platform

        party_platform.clear_platform_cache()
        with patch("app.pipeline.vector_store.get_embedding_model", return_value=_fake_model()):
            party_platform._ensure_platform_embeddings()

        assert party_platform._platform_cache.is_loaded is True
        party_platform.clear_platform_cache()
