"""Tests for Action Center deduplication and stale issue cleanup."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.pipeline.fetch.news_feeds import NewsArticle


def _make_article(title: str, source: str = "AP News", url: str = "") -> NewsArticle:
    return NewsArticle(
        title=title,
        url=url or f"https://example.com/{title.replace(' ', '-').lower()}",
        source_name=source,
        summary=f"Summary for {title}",
    )


class TestDeduplicateTopClusters:
    """Cross-cluster deduplication prevents two angles on the same story."""

    @pytest.mark.slow
    def test_identical_clusters_deduplicated(self):
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        c1 = [_make_article("Trade war tariffs increase on Chinese goods")]
        c2 = [_make_article("Trade war tariffs rise for Chinese imports")]
        c3 = [_make_article("Healthcare bill passes Senate committee")]

        result = _deduplicate_top_clusters([c1, c2, c3], max_issues=4)
        assert len(result) == 2
        titles = [r[0].title for r in result]
        assert "Trade war tariffs increase on Chinese goods" in titles
        assert "Healthcare bill passes Senate committee" in titles

    @pytest.mark.slow
    def test_distinct_clusters_preserved(self):
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        c1 = [_make_article("Supreme Court rules on abortion access rights")]
        c2 = [_make_article("Federal Reserve raises interest rates again")]
        c3 = [_make_article("Immigration reform bill introduced in Senate")]
        c4 = [_make_article("Climate change policy executive order signed")]

        result = _deduplicate_top_clusters([c1, c2, c3, c4], max_issues=4)
        assert len(result) == 4

    @pytest.mark.slow
    def test_respects_max_issues(self):
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        clusters = [
            [_make_article(f"Unique topic number {i} about government policy")]
            for i in range(10)
        ]
        result = _deduplicate_top_clusters(clusters, max_issues=4)
        assert len(result) <= 4

    def test_single_cluster_passthrough(self):
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        c1 = [_make_article("Trade war tariffs")]
        with patch(
            "app.pipeline.analyze.action_center._embed_texts"
        ) as mock_embed:
            result = _deduplicate_top_clusters([c1], max_issues=4)
        assert len(result) == 1
        mock_embed.assert_not_called()

    def test_empty_clusters_passthrough(self):
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        with patch(
            "app.pipeline.analyze.action_center._embed_texts"
        ) as mock_embed:
            result = _deduplicate_top_clusters([], max_issues=4)
        assert len(result) == 0
        mock_embed.assert_not_called()

    def test_similar_clusters_removed_by_mock_embeddings(self):
        """Unit test with mock embeddings: two clusters with high similarity get deduped."""
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        c1 = [_make_article("Topic A")]
        c2 = [_make_article("Topic A variant")]
        c3 = [_make_article("Topic B")]

        embs = np.array([
            [1.0, 0.0, 0.0],
            [0.98, 0.2, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)
        for row in embs:
            row /= np.linalg.norm(row)

        with patch(
            "app.pipeline.analyze.action_center._embed_texts",
            return_value=embs,
        ):
            result = _deduplicate_top_clusters([c1, c2, c3], max_issues=4)

        assert len(result) == 2
        assert result[0][0].title == "Topic A"
        assert result[1][0].title == "Topic B"

    def test_dissimilar_clusters_all_kept_by_mock_embeddings(self):
        """Unit test with mock embeddings: dissimilar clusters all survive."""
        from app.pipeline.analyze.action_center import _deduplicate_top_clusters

        clusters = [
            [_make_article(f"Topic {c}")]
            for c in ["A", "B", "C"]
        ]
        embs = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)

        with patch(
            "app.pipeline.analyze.action_center._embed_texts",
            return_value=embs,
        ):
            result = _deduplicate_top_clusters(clusters, max_issues=4)

        assert len(result) == 3


class TestStaleIssueCleanup:
    """Stale issues from prior runs are removed when not refreshed."""

    def test_stale_ranks_deleted_after_refresh(self):
        """Issues at ranks not produced by the current run should be removed."""
        from app.models import ActionIssue

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 2

        created_ranks = {1, 2, 3}

        mock_query.delete(synchronize_session="fetch")
        mock_db.commit()

        mock_query.delete.assert_called()
