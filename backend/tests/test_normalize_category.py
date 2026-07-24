"""Tests for _normalize_category (platform-review O5, fixed 2026-07-24).

Two bugs, fixed together since changing one changes what the other
means: the query embedding was missing prompt_name="query" (required by
the asymmetric snowflake-arctic-embed-xs model — industry_classifier.py's
classify_industry documents the same requirement), and the accept
threshold (0.25) sat below the model's real similarity floor regardless,
making the "ghost class deletion" path in normalize_learning_store dead
code. See _normalize_category's own docstring for the live-measured
derivation of the new 0.65 threshold.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from app.pipeline.analyze import nn_classifier


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _make_fake_model(query_response: np.ndarray):
    """A fake SentenceTransformer whose .encode() records whether
    prompt_name="query" was passed, and always returns query_response."""
    model = MagicMock()
    calls = []

    def encode(texts, prompt_name=None, show_progress_bar=False):
        calls.append({"texts": texts, "prompt_name": prompt_name})
        return np.array([query_response])

    model.encode.side_effect = encode
    model._calls = calls
    return model


class TestNormalizeCategoryQueryEncoding:
    def test_encodes_with_prompt_name_query(self):
        """The asymmetric-model requirement classify_industry already
        documents — a query encoded as a plain document shifts the whole
        similarity scale, per O5."""
        nn_classifier._category_norm_cache.clear()
        fake_industry_emb = _unit(np.array([1.0, 0.0, 0.0]))
        fake_model = _make_fake_model(fake_industry_emb)

        with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=fake_model), \
             patch(
                 "app.pipeline.transform.industry_classifier._get_industry_embeddings",
                 return_value={"MEDIA": fake_industry_emb},
             ):
            nn_classifier._normalize_category("SPORTS")

        assert fake_model._calls[0]["prompt_name"] == "query"


class TestNormalizeCategoryThreshold:
    def _run(self, query_vec, industry_embs):
        nn_classifier._category_norm_cache.clear()
        fake_model = _make_fake_model(query_vec)
        with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=fake_model), \
             patch(
                 "app.pipeline.transform.industry_classifier._get_industry_embeddings",
                 return_value=industry_embs,
             ):
            return nn_classifier._normalize_category("SOME_STALE_VALUE")

    def test_strong_match_above_threshold_resolves_to_that_industry(self):
        target = _unit(np.array([1.0, 0.0, 0.0]))
        # Query nearly identical to MEDIA's prototype -> cosine ~1.0, well above 0.65.
        result = self._run(target, {"MEDIA": target, "TECH": _unit(np.array([0.0, 1.0, 0.0]))})
        assert result == "MEDIA"

    def test_weak_match_below_threshold_falls_back_to_other(self):
        # Orthogonal vectors -> cosine 0.0, far below 0.65 — must not be
        # force-assigned to the nearest (still-wrong) industry.
        query = _unit(np.array([0.0, 0.0, 1.0]))
        result = self._run(query, {
            "MEDIA": _unit(np.array([1.0, 0.0, 0.0])),
            "TECH": _unit(np.array([0.0, 1.0, 0.0])),
        })
        assert result == "OTHER"

    def test_already_valid_industry_returned_unchanged_no_encoding(self):
        nn_classifier._category_norm_cache.clear()
        fake_model = _make_fake_model(_unit(np.array([1.0, 0.0])))
        with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=fake_model):
            result = nn_classifier._normalize_category("MEDIA")
        assert result == "MEDIA"
        assert fake_model.encode.call_count == 0  # short-circuited before any embedding call

    def test_unknown_and_empty_fall_back_to_other_without_encoding(self):
        nn_classifier._category_norm_cache.clear()
        fake_model = _make_fake_model(_unit(np.array([1.0, 0.0])))
        with patch("app.pipeline.analyze.nn_classifier._get_model", return_value=fake_model):
            assert nn_classifier._normalize_category("UNKNOWN") == "OTHER"
            assert nn_classifier._normalize_category("") == "OTHER"
        assert fake_model.encode.call_count == 0
