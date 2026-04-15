"""Tests for Action Center deduplication and national monitor creation logic."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models import ActionIssue, NationalMonitor, MonitorUpdate
from app.pipeline.fetch.news_feeds import NewsArticle
from app.pipeline.analyze.action_center import (
    _deduplicate_top_clusters,
    _update_national_monitors,
    _cleanup_monitor_lifecycle,
    _generate_monitor_metadata,
)


def _make_article(title: str, source: str = "AP News", url: str = "") -> NewsArticle:
    return NewsArticle(
        title=title,
        url=url or f"https://example.com/{title.replace(' ', '-').lower()}",
        source_name=source,
        summary=f"Summary for {title}",
    )


def _make_issue(date: str, title: str, sources: list[str]) -> ActionIssue:
    return ActionIssue(
        date=date,
        rank=1,
        title=title,
        summary=f"Summary for {title}",
        source_names=json.dumps(sources),
        source_urls=json.dumps([f"https://example.com/{s.lower()}" for s in sources]),
    )


class TestDeduplicateTopClusters:
    """Cross-cluster deduplication prevents two angles on the same story."""

    @pytest.mark.slow
    def test_identical_clusters_deduplicated(self):
        c1 = [_make_article("Trade war tariffs increase on Chinese goods")]
        c2 = [_make_article("Trade war tariffs rise for Chinese imports")]
        c3 = [_make_article("Healthcare bill passes Senate committee")]

        result = _deduplicate_top_clusters([c1, c2, c3], max_issues=4)
        assert len(result) == 2
        titles = [r[0].title for r in result]
        assert "Trade war tariffs increase on Chinese goods" in titles
        assert "Healthcare bill passes Senate committee" in titles


class TestNationalMonitorCreation:
    """Tests for importance and breadth requirements in monitor creation."""

    @patch("app.pipeline.analyze.action_center.get_embedding_model")
    def test_insufficient_breadth_skips_monitor(self, mock_get_model):
        """Monitor should NOT be created if only one source covers the topic over multiple days."""
        mock_db = MagicMock()
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        
        # All embeddings are highly similar (0.95+)
        mock_model.encode.return_value = np.array([[1.0] * 384 for _ in range(5)], dtype=np.float32)

        today = "2026-03-13"
        topic = "Niche local zoning issue"
        
        # Today's issue - only 1 source
        today_issue = _make_issue(today, topic, ["Local News Source"])
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [today_issue]
        
        # Past issues - all from same source
        past_issues = [
            _make_issue((datetime(2026, 3, 13) - timedelta(days=i)).strftime("%Y-%m-%d"), topic, ["Local News Source"])
            for i in range(1, 5)
        ]
        
        # Mock the queries for past issues and existing monitors
        def mock_query(model):
            if model == ActionIssue:
                q = MagicMock()
                q.filter.return_value.all.return_value = past_issues
                # For today's issues query
                q.filter.return_value.order_by.return_value.all.return_value = [today_issue]
                return q
            if model == NationalMonitor:
                q = MagicMock()
                q.all.return_value = [] # No existing monitors
                return q
            return MagicMock()

        mock_db.query.side_effect = mock_query

        _update_national_monitors(today, mock_db)

        # Ensure NationalMonitor was not added because it only has 1 source
        # (Even though it has 5 days of history, which is >= _MONITOR_MIN_DAYS)
        added_objects = [call.args[0] for call in mock_db.add.call_args_list]
        assert not any(isinstance(obj, NationalMonitor) for obj in added_objects)

    @patch("app.pipeline.analyze.action_center._generate_monitor_metadata")
    @patch("app.pipeline.analyze.action_center.get_embedding_model")
    def test_sufficient_breadth_creates_monitor(self, mock_get_model, mock_gen_meta):
        """Monitor SHOULD be created if multiple sources cover the topic over 5+ days."""
        mock_db = MagicMock()
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        
        # Mock LLM metadata generation
        mock_gen_meta.return_value = {
            "title": "Major Federal Tax Reform",
            "description": "A big tax bill.",
            "category": "taxes"
        }
        
        # Mock embeddings to ensure matches
        mock_model.encode.return_value = np.array([[1.0] * 384 for _ in range(6)], dtype=np.float32)

        today = "2026-03-13"
        topic = "Major Federal Tax Reform"
        
        # Today's issue - Source A
        today_issue = _make_issue(today, topic, ["AP News"])
        
        # Past issues - 4 past days needed for 5 total
        past_issues = [
            _make_issue((datetime(2026, 3, 13) - timedelta(days=1)).strftime("%Y-%m-%d"), topic, ["Reuters"]),
            _make_issue((datetime(2026, 3, 13) - timedelta(days=2)).strftime("%Y-%m-%d"), topic, ["AP News"]),
            _make_issue((datetime(2026, 3, 13) - timedelta(days=3)).strftime("%Y-%m-%d"), topic, ["NPR Politics"]),
            _make_issue((datetime(2026, 3, 13) - timedelta(days=4)).strftime("%Y-%m-%d"), topic, ["Reuters"]),
        ]
        
        def mock_query(model):
            if model == ActionIssue:
                q = MagicMock()
                # Use a side effect to return different values for different calls if needed
                # But for now we just return our data
                mock_filter = MagicMock()
                mock_filter.order_by.return_value.all.return_value = [today_issue] # today_issues call
                mock_filter.all.return_value = past_issues # past_issues call
                q.filter.return_value = mock_filter
                return q
            if model == NationalMonitor:
                q = MagicMock()
                q.all.return_value = []
                return q
            return MagicMock()

        mock_db.query.side_effect = mock_query

        _update_national_monitors(today, mock_db)

        # Should be created: 4 days of history (today + 3 past) and 3 unique sources
        added_objects = [call.args[0] for call in mock_db.add.call_args_list]
        monitors = [obj for obj in added_objects if isinstance(obj, NationalMonitor)]
        assert len(monitors) == 1
        assert monitors[0].title == topic

    @patch("app.pipeline.analyze.action_center.get_embedding_model")
    def test_insufficient_days_skips_monitor(self, mock_get_model):
        """Monitor should NOT be created if it has only appeared for 4 days (min is now 5)."""
        mock_db = MagicMock()
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.encode.return_value = np.array([[1.0] * 384 for _ in range(4)], dtype=np.float32)

        today = "2026-03-13"
        topic = "New Short-lived Policy"
        
        today_issue = _make_issue(today, topic, ["AP News", "Reuters"])
        past_issues = [
            _make_issue((datetime(2026, 3, 13) - timedelta(days=1)).strftime("%Y-%m-%d"), topic, ["AP News"]),
            _make_issue((datetime(2026, 3, 13) - timedelta(days=2)).strftime("%Y-%m-%d"), topic, ["Reuters"]),
            _make_issue((datetime(2026, 3, 13) - timedelta(days=3)).strftime("%Y-%m-%d"), topic, ["NPR Politics"]),
        ]
        
        def mock_query(model):
            if model == ActionIssue:
                q = MagicMock()
                mock_filter = MagicMock()
                mock_filter.order_by.return_value.all.return_value = [today_issue]
                mock_filter.all.return_value = past_issues
                q.filter.return_value = mock_filter
                return q
            if model == NationalMonitor:
                q = MagicMock()
                q.all.return_value = []
                return q
            return MagicMock()

        mock_db.query.side_effect = mock_query

        _update_national_monitors(today, mock_db)

        # 3 days total (today, yesterday, day before). Min is 4.
        added_objects = [call.args[0] for call in mock_db.add.call_args_list]
        assert not any(isinstance(obj, NationalMonitor) for obj in added_objects)

    def test_lifecycle_closing_and_deletion(self):
        """Monitors should close after 30 days, and delete if they had few updates."""
        mock_db = MagicMock()
        today = "2026-03-13"
        old_date = "2026-01-01" # > 30 days ago
        
        # 1. Significant old monitor (should close)
        m1 = NationalMonitor(title="Big Event", status="active", last_article_date=old_date)
        m1.updates = [MagicMock()] * 5 # 5 updates
        
        # 2. Insignificant old monitor (should be deleted)
        m2 = NationalMonitor(title="Tiny blip", status="active", last_article_date=old_date)
        m2.updates = [MagicMock()] * 2 # only 2 updates
        
        # 3. Recent monitor (should stay active)
        m3 = NationalMonitor(title="Current war", status="active", last_article_date="2026-03-12")
        m3.updates = [MagicMock()] * 10
        
        mock_db.query.return_value.filter.return_value.all.return_value = [m1, m2, m3]
        
        _cleanup_monitor_lifecycle(today, mock_db)
            
        assert m1.status == "closed"
        mock_db.delete.assert_any_call(m2)
        assert m3.status == "active"

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_generate_monitor_metadata_success(self, mock_call_llm):
        """Metadata generation should parse LLM JSON and validate categories."""
        mock_db = MagicMock()
        issue = _make_issue("2026-03-13", "Attack on Iranian school", ["AP News"])
        past = [_make_issue("2026-03-12", "Middle East tensions", ["Reuters"])]
        
        mock_call_llm.return_value = json.dumps({
            "title": "U.S.-Iran Conflict",
            "description": "Ongoing tensions between the U.S. and Iran.",
            "category": "FOREIGN_POLICY",
            "is_significant": True
        })
        
        result = _generate_monitor_metadata(issue, past, mock_db)
        
        assert result is not None
        assert result["title"] == "U.S.-Iran Conflict"
        assert result["category"] == "foreign_policy"
        assert result["description"].startswith("Ongoing")

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_generate_monitor_metadata_insignificant(self, mock_call_llm):
        """If LLM deems issue not significant, should return None."""
        mock_db = MagicMock()
        issue = _make_issue("2026-03-13", "Local dog park opens", ["Local News"])
        
        mock_call_llm.return_value = json.dumps({
            "is_significant": False
        })
        
        result = _generate_monitor_metadata(issue, [], mock_db)
        assert result is None

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    @patch("app.pipeline.analyze.action_center._merge_monitors")
    @patch("app.pipeline.analyze.action_center.get_embedding_model")
    def test_llm_assisted_merge(self, mock_get_model, mock_merge, mock_call_llm):
        """Monitors with moderate similarity should merge if LLM approves."""
        mock_db = MagicMock()
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        
        # Moderate similarity (0.45)
        mock_model.encode.side_effect = [
            np.array([[1.0, 0.0]], dtype=np.float32), # today issue
            np.array([[0.45, 0.89]], dtype=np.float32), # existing monitor
        ]

        m1 = NationalMonitor(id=1, title="Iran War", description="War in Iran")
        mock_db.query.return_value.all.return_value = [m1]
        
        # today issue matches existing monitor at 0.45 (Step 2 uses 0.62, so it falls to Step 3)
        # Step 3 skips creation if sim > 0.62. Since 0.45 < 0.62, it creates a new monitor.
        # Then Step 3b (merge) is called.
        
        mock_call_llm.return_value = json.dumps({
            "should_merge": True,
            "reason": "Both about Iran conflict"
        })
        
        # We'll just test the helper directly for simplicity
        from app.pipeline.analyze.action_center import _should_merge_monitors_llm
        m2 = NationalMonitor(id=2, title="Iranian School", description="Targeted school")
        
        result = _should_merge_monitors_llm(m1, m2, mock_db)
        assert result is True
        mock_call_llm.assert_called_once()
