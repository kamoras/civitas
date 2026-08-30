"""Tests for Action Center deduplication and national monitor creation logic."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models import (
    ActionIssue,
    ExploreDocument,
    Justice,
    LlmGenerationSample,
    NationalMonitor,
    Representative,
    Senator,
)
from app.time_utils import utcnow
from app.pipeline.fetch.news_feeds import NewsArticle
from app.pipeline.analyze.action_center import (
    _rank_clusters,
    _deduplicate_top_clusters,
    _update_national_monitors,
    _cleanup_monitor_lifecycle,
    _generate_monitor_metadata,
    _story_word_target,
    _full_story_should_invalidate,
    _check_summary_roles,
    _find_related_explore_docs,
    _find_related_senators,
    _find_related_officials,
    _find_matching_issue,
    dedupe_near_identical_issues,
    _apply_matched_issue_update,
    _retire_untouched_issues,
    _retry_until_grounded,
    _record_generation_sample,
    _bsky_repost_has_new_information,
    _fix_impossible_senate_vote_counts,
    _is_exact_content_duplicate,
    _issue_signature,
    _largest_coherent_subgroup,
    _mentions_full_name,
    _signatures_match,
    _surname_owned_by_other_name,
    _validate_facts,
    _log_summary_source_consistency,
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


def _block_sim_matrix(group_sizes: list[int], within: float = 0.9, across: float = 0.1) -> np.ndarray:
    """A similarity matrix made of dense within-group blocks and a sparse
    cross-group fill — synthetic stand-in for one coherent topic (within)
    vs. an unrelated one (across), without needing real embeddings."""
    n = sum(group_sizes)
    m = np.full((n, n), across)
    start = 0
    for size in group_sizes:
        m[start:start + size, start:start + size] = within
        start += size
    np.fill_diagonal(m, 1.0)
    return m


class TestLargestCoherentSubgroup:
    def test_single_coherent_group_is_not_split(self):
        matrix = _block_sim_matrix([4])
        assert _largest_coherent_subgroup(matrix, 0.4) == [0, 1, 2, 3]

    def test_genuine_bimodal_split_keeps_larger_group(self):
        # 3 articles about one topic, 2 about an unrelated one — the "Iran
        # war" / "ICE tension" scenario this function exists to catch.
        matrix = _block_sim_matrix([3, 2])
        assert _largest_coherent_subgroup(matrix, 0.4) == [0, 1, 2]

    def test_lone_outlier_is_not_treated_as_a_second_topic(self):
        # One stray article (size 1) is below _CLUSTER_SPLIT_MIN_SUBGROUP_SIZE
        # — SOURCE_SIM_FLOOR's own centroid-distance filter handles this case.
        matrix = _block_sim_matrix([4, 1])
        assert _largest_coherent_subgroup(matrix, 0.4) == [0, 1, 2, 3, 4]

    def test_small_minority_group_is_not_treated_as_a_second_topic(self):
        # 2 of 10 articles (20%) is below _CLUSTER_SPLIT_MIN_SUBGROUP_SHARE.
        matrix = _block_sim_matrix([8, 2])
        assert _largest_coherent_subgroup(matrix, 0.4) == list(range(10))


class TestRankClusters:
    """2026-08 quality audit: MAX_ISSUES was lowered as a capacity choice,
    not a calibrated one — the combined score has no absolute floor to
    check against since it's normalized per-run. The score-distribution
    metric that would let a real floor be derived later lives in
    _deduplicate_top_clusters, not here (see TestDeduplicateTopClusters) —
    that function is what actually decides final selection, since it can
    merge away a higher-ranked cluster and promote a lower-ranked one.
    _rank_clusters' own job is just producing the ranking those decisions
    are based on."""

    @patch("app.pipeline.analyze.action_center._compute_action_link_boost")
    @patch("app.pipeline.analyze.action_center._compute_trending_boost")
    def test_returns_clusters_and_scores_in_matching_ranked_order(
        self, mock_trending, mock_civic,
    ):
        clusters = [[_make_article(f"Story {i}")] for i in range(4)]
        mock_trending.return_value = [0.0, 0.0, 0.0, 0.0]
        # Deliberately out of input order — civic is the only nonzero
        # component, so it alone determines rank.
        mock_civic.return_value = [0.2, 0.8, 0.4, 0.6]

        ranked_clusters, ranked_scores = _rank_clusters(clusters, trending=[], db=MagicMock())

        assert [c[0].title for c in ranked_clusters] == ["Story 1", "Story 3", "Story 2", "Story 0"]
        assert ranked_scores == sorted(ranked_scores, reverse=True)
        assert len(ranked_scores) == len(ranked_clusters)


class TestDeduplicateTopClusters:
    """Cross-cluster deduplication prevents two angles on the same story."""

    @pytest.mark.slow
    def test_identical_clusters_deduplicated(self):
        c1 = [_make_article("Trade war tariffs increase on Chinese goods")]
        c2 = [_make_article("Trade war tariffs rise for Chinese imports")]
        c3 = [_make_article("Healthcare bill passes Senate committee")]

        result = _deduplicate_top_clusters([c1, c2, c3], ranked_scores=[0.9, 0.8, 0.5], max_issues=4)
        assert len(result) == 2
        titles = [r[0].title for r in result]
        assert "Trade war tariffs increase on Chinese goods" in titles
        assert "Healthcare bill passes Senate committee" in titles

    @pytest.mark.slow
    def test_merge_decisions_are_logged_for_future_threshold_calibration(self):
        # 2026-08 audit: DEDUP_THRESHOLD has never been measured against a
        # real same-story/different-story sample (unlike this file's other
        # similarity gates) — every merge/keep decision must record a
        # bucketed action_metrics counter so that data accumulates
        # automatically instead of needing a one-off manual production pull.
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        c1 = [_make_article("Trade war tariffs increase on Chinese goods")]
        c2 = [_make_article("Trade war tariffs rise for Chinese imports")]
        c3 = [_make_article("Healthcare bill passes Senate committee")]

        _deduplicate_top_clusters([c1, c2, c3], ranked_scores=[0.9, 0.8, 0.5], max_issues=4)

        counts = action_metrics.snapshot()
        merged = sum(v for k, v in counts.items() if k.startswith("cluster_dedup_merged_sim_bucket_"))
        kept = sum(v for k, v in counts.items() if k.startswith("cluster_dedup_kept_sim_bucket_"))
        # 3 candidates, first has nothing to compare against yet (no counter),
        # so exactly 2 decisions get logged: one merge, one keep.
        assert merged == 1
        assert kept == 1

    @patch("app.pipeline.analyze.action_center._embed_texts")
    def test_promoted_lower_ranked_cluster_is_logged_selected_not_the_merged_one(self, mock_embed):
        # 2026-08 quality audit (independent review of #443): logging
        # selected/rejected by raw rank position — as an earlier version
        # of this feature did in _rank_clusters — mislabels this exact
        # case. c1/c2 are near-duplicates (c2 gets merged into c1 despite
        # outranking c3); with max_issues=2, c3 is then promoted into the
        # 2nd slot even though it ranks below c2. The metric must reflect
        # what ACTUALLY got selected (c1, c3), not raw rank (c1, c2).
        # Mocked embeddings (not @pytest.mark.slow, unlike this class's
        # other tests) so this exercises the fast test suite CI's
        # diff-coverage gate actually measures — c1/c2 identical raw
        # vectors guarantee post-centering cosine ~1.0 (merge), c3
        # orthogonal-ish guarantees a clear miss, deterministically.
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_embed.return_value = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        c1 = [_make_article("Trade war tariffs increase on Chinese goods")]
        c2 = [_make_article("Trade war tariffs rise for Chinese imports")]
        c3 = [_make_article("Healthcare bill passes Senate committee")]

        result = _deduplicate_top_clusters([c1, c2, c3], ranked_scores=[0.9, 0.8, 0.5], max_issues=2)

        result_titles = {a.title for cluster in result for a in cluster}
        assert "Healthcare bill passes Senate committee" in result_titles

        # Bucket-level check, not just aggregate counts: counting selected
        # vs rejected alone can't tell "c1, c3 selected" apart from the
        # raw-rank-position bug's "c1, c2 selected" when both mislabelings
        # happen to produce the same totals (2 selected, 1 rejected) for
        # this cluster count and max_issues — this caught a test that
        # passed under the bug on first write. Checking WHICH score
        # (c3's 0.5, not c2's 0.8) lands in "selected" is what actually
        # proves the real outcome was logged.
        counts = action_metrics.snapshot()
        assert counts.get(f"cluster_rank_score_selected_{action_metrics.decile_bucket(0.5)}") == 1
        assert counts.get(f"cluster_rank_score_rejected_{action_metrics.decile_bucket(0.8)}") == 1
        assert f"cluster_rank_score_selected_{action_metrics.decile_bucket(0.8)}" not in counts
        assert f"cluster_rank_score_rejected_{action_metrics.decile_bucket(0.5)}" not in counts


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


class TestStoryWordTarget:
    """Word-count band scales with fact count instead of forcing every
    issue to the same length regardless of how much reporting backs it."""

    def test_one_fact_gets_short_band(self):
        low, high = _story_word_target(1)
        assert low < 250
        assert high < 400

    def test_more_facts_widen_the_band(self):
        low_1, high_1 = _story_word_target(1)
        low_5, high_5 = _story_word_target(5)
        assert low_5 > low_1
        assert high_5 > high_1

    def test_zero_facts_still_returns_a_valid_band(self):
        low, high = _story_word_target(0)
        assert 0 < low < high

    def test_band_is_bounded_at_high_fact_counts(self):
        low, high = _story_word_target(50)
        assert high <= 750


class TestFullStoryShouldInvalidate:
    """A topic-similarity match can land two substantively different stories
    on the same row (e.g. two senators' health events). full_story must be
    regenerated when that happens, not left describing the old event."""

    def test_unchanged_title_and_facts_does_not_invalidate(self):
        assert _full_story_should_invalidate(
            "Senator X hospitalized", '["fact a"]',
            "Senator X hospitalized", '["fact a"]',
        ) is False

    def test_changed_title_invalidates(self):
        # The real 2026-07 bug: a McConnell hospitalization story's row got
        # re-matched onto a later, different senator's death.
        assert _full_story_should_invalidate(
            "Mitch McConnell hospitalized", '["fact a"]',
            "Lindsey Graham dies at 71", '["fact a"]',
        ) is True

    def test_changed_facts_alone_invalidates(self):
        # Same headline, but the underlying facts were updated (story
        # evolved) — the old full_story may cite facts no longer true.
        assert _full_story_should_invalidate(
            "Senator X hospitalized", '["fact a"]',
            "Senator X hospitalized", '["fact a", "fact b"]',
        ) is True

    def test_only_rank_or_date_changing_is_not_passed_here(self):
        # Rank/date churn alone (no title/facts change) must not invalidate —
        # this function only ever sees title/facts, confirming callers don't
        # need to regenerate on every hourly refresh of an unchanged story.
        assert _full_story_should_invalidate(
            "Senator X hospitalized", '["fact a"]',
            "Senator X hospitalized", '["fact a"]',
        ) is False

class TestCheckSummaryRoles:
    """Second-pass check for subject/object role reversal in a generated
    summary (see docstring on _check_summary_roles — confirmed live 2026-07:
    issue #376 stated the plaintiff in a defamation case "was found guilty",
    when the defendant was the one a jury found liable)."""

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_accurate_summary_passes(self, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"accurate": True})
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("A correct summary.", "source text", mock_db)

        assert accurate is True
        assert reason == ""

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_reversed_roles_flagged_with_reason(self, mock_call_llm):
        mock_call_llm.return_value = json.dumps({
            "accurate": False,
            "reason": "The plaintiff was described as the one found guilty.",
        })
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("A reversed summary.", "source text", mock_db)

        assert accurate is False
        assert "plaintiff" in reason

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_unparseable_response_fails_open(self, mock_call_llm):
        # A broken verification call must not block issue creation — only a
        # confirmed reversal should trigger a retry. It must, however,
        # become visible (2026-08 AI/ML audit): this fails open silently
        # otherwise, the opposite posture of every other gate in this file.
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_call_llm.return_value = "not valid json and no accurate key"
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True
        assert action_metrics.snapshot().get("role_check_inconclusive_published") == 1

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_empty_response_fails_open(self, mock_call_llm):
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_call_llm.return_value = None
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True
        assert action_metrics.snapshot().get("role_check_inconclusive_published") == 1

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_llm_exception_fails_open(self, mock_call_llm):
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_call_llm.side_effect = RuntimeError("LLM backend unreachable")
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True
        assert action_metrics.snapshot().get("role_check_inconclusive_published") == 1

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_genuinely_accurate_result_does_not_count_as_inconclusive(self, mock_call_llm):
        # A real accurate:true verdict is a successful check, not a failure
        # to check — it must not pollute the fail-open counter.
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_call_llm.return_value = json.dumps({"accurate": True})
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("A correct summary.", "source text", mock_db)

        assert accurate is True
        assert "role_check_inconclusive_published" not in action_metrics.snapshot()

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_missing_accurate_key_defaults_to_true(self, mock_call_llm):
        # A dict response with no "accurate" key at all shouldn't be treated
        # as a reversal — only an explicit accurate:false should.
        mock_call_llm.return_value = json.dumps({"reason": "unrelated"})
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True


class TestLogSummarySourceConsistency:
    """Automated, non-blocking semantic-consistency signal (2026-08 AI/ML
    audit): grounding.py's checks are all regex-based and only catch a
    failure class after a human has already seen it live. This starts
    collecting a real distribution instead — bucketed decile counters via
    the same action_metrics pattern every other validator uses — so a
    threshold can eventually be derived the way every other one in this
    codebase was. Never rejects or retries."""

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_records_the_correct_similarity_decile_bucket(self, mock_embed):
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        # cosine 0.7 between the two (already-normalized) mock vectors.
        mock_embed.return_value = np.array([[1.0, 0.0], [0.7, (1 - 0.7 ** 2) ** 0.5]])

        _log_summary_source_consistency("a generated summary", "the source article text")

        assert action_metrics.snapshot() == {"summary_source_similarity_bucket_70": 1}

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_never_blocks_on_embedding_failure(self, mock_embed):
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        mock_embed.side_effect = RuntimeError("model not loaded")

        _log_summary_source_consistency("a generated summary", "the source article text")

        assert action_metrics.snapshot() == {}


class TestFixImpossibleSenateVoteCounts:
    """The Senate has 100 members, so any reported vote tally >100 total
    is physically impossible for the Senate — it can only be a House
    roll call. Confirmed live 2026-07: a generated fact read 'The bill
    passed the Senate with a vote of 226-195' for a story where the bill
    passed the House 226-195 and was later taken up in the Senate."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param(
                "The bill passed the Senate with a vote of 226-195.",
                "The bill passed the House with a vote of 226-195.",
                id="corrects_impossible_senate_vote_to_house",
            ),
            pytest.param(
                "The proposal gained traction in the Senate, where it passed with a vote of 226 to 195.",
                "The proposal gained traction in the House, where it passed with a vote of 226 to 195.",
                id="corrects_across_word_variants_of_tally",
            ),
            # 51 + 49 = 100, exactly at the Senate's ceiling — plausible.
            pytest.param(
                "The bill passed the Senate with a vote of 51-49.",
                "The bill passed the Senate with a vote of 51-49.",
                id="leaves_plausible_senate_vote_unchanged",
            ),
            pytest.param(
                "The bill passed the House 226-195 and now moves to the Senate for consideration.",
                "The bill passed the House 226-195 and now moves to the Senate for consideration.",
                id="leaves_already_correct_house_mention_unchanged",
            ),
            pytest.param("", "", id="empty_string_returns_unchanged"),
            pytest.param(
                "The Senate is expected to take up the bill next week.",
                "The Senate is expected to take up the bill next week.",
                id="no_vote_tally_returns_unchanged",
            ),
        ],
    )
    def test_fix_impossible_senate_vote_counts(self, text, expected):
        assert _fix_impossible_senate_vote_counts(text) == expected


class TestFindRelatedExploreDocsGenericTitleFilter:
    """Confirmed live 2026-07: 'LEGISLATIVE SESSION' — a boilerplate title
    shared by hundreds of Senate floor-speech records covering completely
    different bills, whose real content ('Mr. President, I move to
    proceed to Calendar No. X') carries no topic-specific signal — got
    linked to both a Ukraine-aid story and an unrelated budget-resolution
    story on the same day, because the title-only re-ranking this
    function uses can't discriminate a title that doesn't actually
    describe its own content."""

    def _seed_docs(self, db_session, generic_count: int = 6):
        db_session.add(ExploreDocument(
            id=1, doc_type="Final Rule", source="Federal Register",
            title="Bank Secrecy Act and Stablecoin Issuer Standards",
            summary="", body="", date="2026-01-01",
        ))
        for i in range(2, 2 + generic_count):
            db_session.add(ExploreDocument(
                id=i, doc_type="Senate Floor Speech", source="Congressional Record",
                title="LEGISLATIVE SESSION", summary="", body="", date="2026-01-01",
            ))
        db_session.commit()

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_boilerplate_title_excluded_even_with_higher_raw_similarity(
        self, mock_search, mock_embed, db_session,
    ):
        self._seed_docs(db_session, generic_count=6)
        mock_search.return_value = [
            {"id": 1, "title": "Bank Secrecy Act and Stablecoin Issuer Standards", "distance": 0.5},
            {"id": 2, "title": "LEGISLATIVE SESSION", "distance": 0.5},
        ]
        # Embeddings crafted so the generic doc (id=2) scores a HIGHER raw
        # cosine similarity than the genuinely relevant doc (id=1) — proving
        # the genericness filter, not just similarity ranking, is what
        # excludes it.
        mock_embed.return_value = np.array([
            [1.0, 0.0],    # issue title embedding
            [0.90, 0.10],  # doc 1 (relevant, real signal)
            [0.99, 0.01],  # doc 2 (generic title, spuriously higher similarity)
        ])

        result = _find_related_explore_docs(
            "Crypto stablecoin legislation", "summary", ["FINANCIAL"], db_session,
        )

        ids = [d["id"] for d in result]
        assert 1 in ids
        assert 2 not in ids

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_title_below_repeat_threshold_is_not_filtered(
        self, mock_search, mock_embed, db_session,
    ):
        # Same shape, but the "generic" title only appears twice — below
        # GENERIC_TITLE_REPEAT_THRESHOLD (5) — so it's a real match, not
        # boilerplate, and should be kept.
        self._seed_docs(db_session, generic_count=2)
        mock_search.return_value = [
            {"id": 2, "title": "LEGISLATIVE SESSION", "distance": 0.5},
        ]
        mock_embed.return_value = np.array([
            [1.0, 0.0],
            [0.95, 0.05],
        ])

        result = _find_related_explore_docs(
            "Some issue title", "summary", [], db_session,
        )

        assert [d["id"] for d in result] == [2]


class TestExploreDocThresholds:
    """Confirmed live 2026-07: at the prior distance/similarity thresholds
    (1.10 / 0.40), nearly every Action Center issue linked 2-3 unrelated
    explore docs — e.g. a World Cup soccer story matched to Chinese steel
    anti-dumping notices at distance 0.87 and title-similarity 0.74, both
    comfortably inside the old bounds. Tightened based on real production
    score distributions (see _EXPLORE_DOC_MAX_DISTANCE's comment)."""

    def _seed_doc(self, db_session, doc_id: int, title: str):
        db_session.add(ExploreDocument(
            id=doc_id, doc_type="Notice", source="Federal Register",
            title=title, summary="", body="", date="2026-01-01",
        ))
        db_session.commit()

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_distance_at_old_threshold_now_rejected(self, mock_search, mock_embed, db_session):
        self._seed_doc(db_session, 1, "Certain Steel Products From China: Preliminary Results")
        mock_search.return_value = [
            {"id": 1, "title": "Certain Steel Products From China: Preliminary Results", "distance": 0.95},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs("Sports story", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_similarity_at_old_threshold_now_rejected(self, mock_search, mock_embed, db_session):
        self._seed_doc(db_session, 1, "Certain Steel Products From China: Preliminary Results")
        mock_search.return_value = [
            {"id": 1, "title": "Certain Steel Products From China: Preliminary Results", "distance": 0.5},
        ]
        # cos_sim ~= 0.25 — above zero, below the similarity-model bar
        # (0.33, measured 2026-07: genuine matches 0.467+, noise <=0.183).
        mock_embed.return_value = np.array([[1.0, 0.0], [0.25, 0.968]])

        result = _find_related_explore_docs("Sports story", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_genuine_close_match_survives_tightened_thresholds(self, mock_search, mock_embed, db_session):
        self._seed_doc(db_session, 1, "EO 14318: Accelerating Federal Permitting of Data Center Infrastructure")
        mock_search.return_value = [
            {"id": 1, "title": "EO 14318: Accelerating Federal Permitting of Data Center Infrastructure", "distance": 0.80},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.80, 0.20]])

        result = _find_related_explore_docs("China data center buildout", "summary", [], db_session)
        assert [d["id"] for d in result] == [1]


class TestAdministrativeNoticeTitleFilter:
    """Confirmed live 2026-07: Paperwork Reduction Act information-collection
    notices and FACA advisory-committee meeting notices — legally-templated
    titles that are never substantively about any particular news story —
    scored well inside the "genuine match" distance/similarity bands for
    completely unrelated issues (e.g. an Attorney General story matched to
    a "Notice of Public Meeting of the Montana Advisory Committee"). Unlike
    LEGISLATIVE SESSION, each of these notices is uniquely titled, so
    GENERIC_TITLE_REPEAT_THRESHOLD's repeat-count check can't catch them —
    this matches the fixed legal template phrasing instead."""

    def _seed_doc(self, db_session, doc_id: int, title: str):
        db_session.add(ExploreDocument(
            id=doc_id, doc_type="Notice", source="Federal Register",
            title=title, summary="", body="", date="2026-01-01",
        ))
        db_session.commit()

    @pytest.mark.parametrize(
        "issue_title, doc_title",
        [
            pytest.param(
                "Some unrelated issue",
                "Agency Information Collection Activities; Proposed eCollection",
                id="information_collection_notice_rejected_despite_high_similarity",
            ),
            pytest.param(
                "Attorney General independence",
                "Notice of Public Meeting of the Montana Advisory Committee",
                id="advisory_committee_meeting_notice_rejected_despite_high_similarity",
            ),
            pytest.param(
                "Some unrelated issue",
                "Proposed Collection; 60-day Comment Request; Generic Clearance for NIH",
                id="proposed_collection_comment_request_variant_rejected",
            ),
            pytest.param(
                "Some unrelated issue",
                "Solicitation of Nominations for Membership on the Ocean Exploration Advisory Board",
                id="solicitation_of_nominations_variant_rejected",
            ),
        ],
    )
    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_administrative_notice_rejected_despite_high_similarity(
        self, mock_search, mock_embed, issue_title, doc_title, db_session,
    ):
        self._seed_doc(db_session, 1, doc_title)
        mock_search.return_value = [{"id": 1, "title": doc_title, "distance": 0.70}]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs(issue_title, "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_notice_without_administrative_template_phrasing_is_kept(
        self, mock_search, mock_embed, db_session,
    ):
        self._seed_doc(db_session, 1, "Notice of OFAC Sanctions Actions")
        mock_search.return_value = [
            {"id": 1, "title": "Notice of OFAC Sanctions Actions", "distance": 0.70},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs("Sanctions on foreign officials", "summary", [], db_session)
        assert [d["id"] for d in result] == [1]


class TestFindRelatedSenatorsCommonWordSurnames:
    """Rep. Shomari Figures (surname "Figures", seated Jan 2025) was
    getting tagged on any article using the common word "figures" ("the
    data figures show...") — a bare last-name substring/word-boundary
    match with no context requirement strong enough to filter it out
    reliably. _COMMON_WORD_SURNAMES already existed for exactly this
    failure mode (it already had "justice", "banks", "young", etc.) but
    "figures" wasn't on it, and there was no test coverage to catch a
    regression either way."""

    def test_common_word_surname_not_matched_by_bare_word(self, db_session):
        db_session.add(Representative(
            id="s-figures", name="Shomari Figures", state="AL", party="D",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Economic outlook", "The latest data figures show inflation cooling.", [], db_session,
        )
        assert result == []

    def test_common_word_surname_still_matched_by_full_name(self, db_session):
        """The stoplist only blocks bare last-name matching — a full-name
        hit is always high-confidence and must still work."""
        db_session.add(Representative(
            id="s-figures", name="Shomari Figures", state="AL", party="D",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Alabama delegation", "Rep. Shomari Figures announced a new bill today.", [], db_session,
        )
        assert [r["id"] for r in result] == ["s-figures"]

    @patch("app.pipeline.analyze.action_center._embed_texts")
    def test_disambiguation_phrase_uses_representative_title_not_senator(
        self, mock_embed, db_session,
    ):
        """Every House candidate's disambiguation prototype was hardcoded
        to "Senator {name} from {state}" regardless of chamber — weakening
        the embedding signal for every one of the ~435 Representatives,
        not just common-word-surname cases. "Delacroix" (>=4 chars, not a
        common word) exercises the disambiguation path directly."""
        db_session.add(Representative(
            id="r-delacroix", name="Amara Delacroix", state="TX", party="R",
        ))
        db_session.commit()
        mock_embed.return_value = np.array([[1.0, 0.0], [1.0, 0.0]])

        _find_related_senators(
            "Texas news", "Rep. Delacroix spoke at the event.", [], db_session,
        )

        texts_embedded = mock_embed.call_args[0][0]
        assert "Representative Amara Delacroix from TX" in texts_embedded
        assert not any(t.startswith("Senator Amara Delacroix") for t in texts_embedded)


class TestFindRelatedSenatorsSameSurnameCollision:
    """2026-07: live production bug — the Action Center's #2-ranked issue
    ("Endorsements for South Carolina race", about SC candidate Darline
    Graham) also tagged Sen. Lindsey Graham as "referenced in coverage",
    even though he is never mentioned anywhere in the title, summary,
    facts, or full story. 30+ surnames are shared by 2+ current members
    (Smith x5, Johnson x5, Moore x5, Graham x2, etc.), so this wasn't a
    one-off: any story that fully-names one member also puts every OTHER
    member with the same surname through last-name-only disambiguation,
    and a same-state, same-general-topic collision (candidate Graham,
    Senator Graham, both South Carolina, both "politician" context) can
    read as similar enough to a generic "Senator {name} from {state}"
    prototype phrase to cross the similarity threshold — despite the
    surname's every appearance in the text being fully explained by the
    OTHER person's own confirmed full-name match already.
    """

    def test_unrelated_same_surname_member_is_not_matched(self, db_session):
        db_session.add(Senator(id="darline-graham", name="Darline Graham", state="SC", party="R"))
        db_session.add(Senator(id="lindsey-graham", name="Lindsey Graham", state="SC", party="R"))
        db_session.commit()

        result = _find_related_senators(
            "Endorsements for South Carolina race",
            "Several officials have publicly backed Darline Graham as a candidate "
            "for the South Carolina congressional seat.",
            [
                "Graham collected endorsements from political figures following her announcement.",
                "The race is scheduled for a full six-year term as outlined in her campaign plans.",
            ],
            db_session,
        )

        assert [r["id"] for r in result] == ["darline-graham"]

    def test_same_surname_member_still_matched_when_also_named_in_full(self, db_session):
        """The fix must only suppress the OTHER person sharing a surname —
        if both are genuinely named in full, both should still match."""
        db_session.add(Senator(id="darline-graham", name="Darline Graham", state="SC", party="R"))
        db_session.add(Senator(id="lindsey-graham", name="Lindsey Graham", state="SC", party="R"))
        db_session.commit()

        result = _find_related_senators(
            "South Carolina endorsement",
            "Darline Graham received an endorsement from Lindsey Graham today.",
            [],
            db_session,
        )

        assert {r["id"] for r in result} == {"darline-graham", "lindsey-graham"}

    @patch("app.pipeline.analyze.action_center._embed_texts")
    def test_last_name_only_reference_still_works_without_a_collision(self, mock_embed, db_session):
        """The fix must not break ordinary last-name-only disambiguation
        for a member with no same-surname collision in play at all."""
        db_session.add(Senator(id="lindsey-graham", name="Lindsey Graham", state="SC", party="R"))
        db_session.commit()
        mock_embed.return_value = np.array([[1.0, 0.0], [1.0, 0.0]])

        result = _find_related_senators(
            "South Carolina news", "Graham criticized the bill in a floor speech.", [], db_session,
        )

        assert [r["id"] for r in result] == ["lindsey-graham"]


class TestFindRelatedOfficialsJusticeCommonWordSurnames:
    """Same failure mode as senators/reps, applied to justice matching:
    Justice Ketanji Brown Jackson's surname is both a common place name
    ("Jackson, Mississippi") and an everyday word."""

    def test_justice_common_word_surname_not_matched_by_bare_word(self, db_session):
        db_session.add(Senator(id="dummy", name="Dummy Senator", state="CA", party="D"))
        db_session.add(Justice(id="jackson", name="Ketanji Brown Jackson", last_name="Jackson"))
        db_session.commit()

        result = _find_related_officials(
            "Travel feature", "Visitors flocked to Jackson, Mississippi this summer.", [], db_session,
        )
        assert result == []


class TestSurnameOwnedByOtherName:
    """2026-07 audit H3: a World Cup story tagged both Reps. Torres
    ("referenced in coverage") off soccer player Ferran Torres' surname —
    and that false tag was itself the action surface that let a sports
    story publish as a civic issue. The embedding disambiguation is
    provably unable to catch this (measured on the live case: 0.78-0.80
    vs. genuine civic references at 0.77-0.85 — fully overlapping), so
    the guard is deterministic: a surname occurrence immediately preceded
    by a different person's given name is not the member."""

    def _match(self, text, surname):
        import re
        return re.search(r"\b" + surname + r"\b", text)

    def test_live_ferran_torres_case(self):
        text = "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal."
        m = self._match(text, "Torres")
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is True

    def test_own_first_name_is_not_another_owner(self):
        text = "The bill from Ritchie Torres advanced on Tuesday."
        m = self._match(text, "Torres")
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is False

    def test_title_prefix_is_not_another_owner(self):
        text = "On the floor, Rep. Torres criticized the amendment."
        m = self._match(text, "Torres")
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is False

    def test_sentence_boundary_capitalized_word_is_not_an_owner(self):
        # "Georgia." ends the previous sentence — it does not own "Torres".
        text = "The delegation visited Georgia. Torres said the trip was productive."
        m = self._match(text, "Torres")
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is False

    def test_lowercase_preceding_word_is_not_an_owner(self):
        text = "A spokesman for Torres confirmed the schedule."
        m = self._match(text, "Torres")
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is False


class TestFindRelatedSenatorsSurnameOwnedByOtherPerson:
    def test_world_cup_ferran_torres_does_not_tag_reps_torres(self, db_session):
        db_session.add(Representative(id="r-torres", name="Ritchie Torres", state="NY", party="D"))
        db_session.add(Representative(id="n-torres", name="Norma J. Torres", state="CA", party="D"))
        db_session.commit()

        result = _find_related_senators(
            "Spanish and Argentine reactions to World Cup final",
            "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal.",
            ["Spanish spectators celebrated in Madrid following Spain's World Cup win."],
            db_session,
        )
        assert result == []

    def test_member_still_matched_when_some_occurrence_is_unowned(self, db_session):
        # One occurrence owned by another name, one genuinely bare — the
        # member stays a live candidate (the guard requires EVERY
        # occurrence to be someone else's).
        db_session.add(Representative(id="r-torres", name="Ritchie Torres", state="NY", party="D"))
        db_session.commit()
        with patch("app.pipeline.analyze.action_center._embed_texts") as mock_embed:
            mock_embed.return_value = np.array([[1.0, 0.0], [1.0, 0.0]])
            result = _find_related_senators(
                "Housing bill advances",
                "Rep. Torres introduced the measure. Ferran Torres played no role.",
                [],
                db_session,
            )
        assert [r["id"] for r in result] == ["r-torres"]


class TestIssueSignatureMatching:
    """2026-07 audit H1/H2: topic identity by raw title cosine at 0.82
    failed in both directions on real production rows — two same-story
    rows measured 0.80/0.85 (duplicate rows, duplicate Bluesky posts)
    while a different-story pair measured 0.88 (row content overwritten
    in place; the published post described a different story than its
    permalink). Every case below uses the real production rows' text."""

    def test_same_defense_bill_rows_match(self):
        # ids 394/405: same $95B bill, same 216-212 vote, two rows.
        sig_a = _issue_signature(
            "Defense policy bill passage and budget debates",
            ["A defense policy bill was passed with a narrow 216-212 vote.",
             "Six Democrats supported the bill, and seven Republicans opposed it.",
             "House Republicans approved a $95 billion framework for a third budget reconciliation package."],
        )
        sig_b = _issue_signature(
            "House approves Pentagon funding framework",
            ["A $95 billion framework was approved for defense spending.",
             "The vote resulted in a narrow 216-212 outcome.",
             "Six Democrats supported the measure while seven Republicans opposed it."],
        )
        assert _signatures_match(sig_a, sig_b) is True

    def test_same_outbreak_rows_match(self):
        # ids 396/401: same cyclospora outbreak on adjacent days.
        sig_a = _issue_signature(
            "FDA investigation continues over Taylor Farms lettuce",
            ["A lettuce sample from Taylor Farms was initially flagged as positive for cyclospora.",
             "Multiple states are reporting over 7,000 confirmed cases of cyclosporiasis nationwide."],
        )
        sig_b = _issue_signature(
            "Cyclosporiasis outbreak investigation updates",
            ["Over 7,000 cases have been reported across several states.",
             "The FDA has stated that a sample from Taylor Farms was later identified as a false positive."],
        )
        assert _signatures_match(sig_a, sig_b) is True

    def test_different_stories_with_similar_titles_do_not_match(self):
        # The drift shape: a shutdown stopgap story vs. the $95B package —
        # titles alike enough that raw cosine matched them (0.88 measured),
        # overwriting a posted row with a different story's content.
        sig_a = _issue_signature(
            "House advances funding bill to avoid government shutdown",
            ["The House passed a temporary funding measure to avoid a shutdown.",
             "Senators plan a response next week."],
        )
        sig_b = _issue_signature(
            "House approves Pentagon funding framework",
            ["A $95 billion framework was approved for defense spending.",
             "The vote resulted in a narrow 216-212 outcome."],
        )
        assert _signatures_match(sig_a, sig_b) is False

    def test_generic_civic_vocabulary_carries_no_identity(self):
        sig = _issue_signature(
            "House Republicans debate the bill",
            ["Lawmakers in Congress discussed the legislation."],
        )
        # Everything here is generic — the signature must be (nearly)
        # empty rather than full of House/Republicans/Congress tokens
        # that would match every other political story.
        assert "house" not in sig
        assert "republicans" not in sig
        assert "congress" not in sig

    def test_empty_signature_never_matches(self):
        assert _signatures_match(set(), {"taylor", "farms"}) is False

    def test_sparse_single_token_signature_cannot_match_even_itself(self):
        # Live 2026-07-23 bug: a story whose only extractable entity is one
        # name ("Trump") produces a 1-token signature. _SIGNATURE_MATCH_MIN_SHARED
        # (2) means it can never clear the floor, even compared to an
        # exact copy of itself — this is exactly why _run_refresh's loop
        # needs the exact-content check ahead of signature matching (see
        # test_byte_identical_issues_are_duplicates below), not a reason
        # to lower the shared-token floor (that would risk merging
        # different stories that happen to mention the same one person).
        facts = [
            "A new bill text was released by Republican representatives.",
            "The legislation includes a provision endorsed by former President Trump.",
        ]
        sig = _issue_signature("Republicans introduce crypto legislation with ethical clause", facts)
        assert sig == {"trump"}
        assert _signatures_match(sig, sig) is False

    def test_byte_identical_issues_are_duplicates(self):
        title = "Republicans introduce crypto legislation with ethical clause"
        facts = [
            "A new bill text was released by Republican representatives.",
            "The legislation includes a provision endorsed by former President Trump.",
        ]
        assert _is_exact_content_duplicate(title, facts, title, list(facts)) is True

    def test_different_content_is_not_a_duplicate(self):
        assert _is_exact_content_duplicate(
            "Title A", ["fact 1"], "Title B", ["fact 1"],
        ) is False
        assert _is_exact_content_duplicate(
            "Same title", ["fact 1"], "Same title", ["fact 2"],
        ) is False

    def test_find_matching_issue_catches_sparse_signature_exact_duplicate(self):
        # End-to-end reproduction of the live 2026-07-23 bug via the actual
        # matching function _run_refresh calls, not just the helper in
        # isolation: a byte-identical reprocessing of the same source
        # article must resolve to the existing row, never a new one.
        title = "Republicans introduce crypto legislation with ethical clause"
        facts = [
            "A new bill text was released by Republican representatives.",
            "The legislation includes a provision endorsed by former President Trump.",
        ]
        existing = ActionIssue(
            id=420, date="2026-07-23", rank=2, title=title, facts=json.dumps(facts),
        )
        # Identical title -> cosine similarity 1.0 against itself.
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, facts, [existing], recent_embs, title_emb, set())
        assert match is existing

    def test_find_matching_issue_catches_identical_title_with_reworded_facts(self):
        # Live 2026-08-22 bug: "Trump defends beef import plan amid GOP
        # criticism" was regenerated an hour apart with the same title but
        # "cattle producers"/"ranchers" swapped between generations — enough
        # to sink _issue_signature's sparse entity overlap below
        # _signatures_match's floor, and _is_exact_content_duplicate
        # requires facts to match too so it didn't catch this either. Two
        # rows created, two Bluesky posts for the same story.
        title = "Trump defends beef import plan amid GOP criticism"
        old_facts = [
            "Trump said the decision was driven by public pressure to lower beef prices.",
            "GOP lawmakers voiced alarm over the policy's effect on U.S. beef producers.",
        ]
        new_facts = [
            "Trump said the decision was driven by public pressure to lower beef prices.",
            "GOP lawmakers voiced alarm over the policy's effect on U.S. cattle producers.",
        ]
        existing = ActionIssue(
            id=603, date="2026-08-21", rank=3, title=title, facts=json.dumps(old_facts),
        )
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, new_facts, [existing], recent_embs, title_emb, set())
        assert match is existing

    def test_find_matching_issue_catches_near_identical_but_not_byte_identical_title(self):
        # Real production pair (2026-08-22 audit): "Senate funding patch
        # delays grant changes" vs "...grant overhaul" scored 0.957 title
        # cosine — clearly the same specific development, but not a byte-
        # identical title, so the exact-title check added for the beef
        # bug wouldn't catch it. Facts are written to share no signature
        # tokens, isolating that this match comes from the near-identical-
        # title path, not signature overlap.
        title = "Senate funding patch delays grant changes"
        cand_title = "Senate funding patch delays grant overhaul"
        facts = ["A provision affecting research grants was altered in the latest text."]
        cand_facts = ["A separate clause affecting award programs was modified in committee."]
        existing = ActionIssue(
            id=490, date="2026-08-20", rank=5, title=cand_title, facts=json.dumps(cand_facts),
        )
        cosine = 0.957
        recent_embs = np.array([[cosine, (1 - cosine**2) ** 0.5]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, facts, [existing], recent_embs, title_emb, set())
        assert match is existing

    def test_find_matching_issue_does_not_merge_distinct_developments_in_the_same_saga(self):
        # Real production title-cosine (0.795) between two genuinely
        # separate steps of the same funding-bill saga — related, but not
        # the same specific development. Facts are written to give both
        # sides a non-empty, non-overlapping signature (1 shared token,
        # below _SIGNATURE_MATCH_MIN_SHARED) so this isolates the new
        # near-identical-title path rather than tripping the pre-existing
        # empty-signature fallback. Below _NEAR_IDENTICAL_TITLE_THRESHOLD
        # (0.92), this must NOT auto-merge — collapsing every step of an
        # ongoing saga into one row would hide real distinct developments.
        title = "Senate passes funding bill to avert October shutdown"
        cand_title = "Senate approves funding bill to avoid shutdown"
        facts = ["Senator Grassley led the floor vote ahead of the October deadline."]
        cand_facts = ["Senator Collins negotiated the final procedural agreement with House leaders."]
        existing = ActionIssue(
            id=535, date="2026-08-18", rank=5, title=cand_title, facts=json.dumps(cand_facts),
        )
        cosine = 0.795
        recent_embs = np.array([[cosine, (1 - cosine**2) ** 0.5]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, facts, [existing], recent_embs, title_emb, set())
        assert match is None

    def test_find_matching_issue_returns_none_when_already_claimed_this_run(self):
        title = "Republicans introduce crypto legislation with ethical clause"
        facts = ["A new bill text was released by Republican representatives."]
        existing = ActionIssue(id=420, date="2026-07-23", rank=2, title=title, facts=json.dumps(facts))
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, facts, [existing], recent_embs, title_emb, {420})
        assert match is None

    def test_find_matching_issue_catches_a_shared_source_url_despite_a_reworded_title(self):
        # Live 2026-08-26 bug: "DHS data claims and think tank connections"
        # and "DHS data claims and state ballot measures", a day apart,
        # both cited the exact same single NPR URL — different enough
        # secondary framing that title cosine and signature overlap both
        # missed it, so it became a second row instead of an update.
        existing = ActionIssue(
            id=621, date="2026-08-25", rank=3,
            title="DHS data claims and think tank connections",
            facts=json.dumps(["DHS cited a report from a conservative think tank."]),
            source_urls=json.dumps(["https://npr.org/nx-s1-5940807"]),
        )
        # Deliberately dissimilar title embedding — this pair must match
        # on source URL alone, not by accidentally clearing the title
        # cosine floor.
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([0.0, 1.0])

        match = _find_matching_issue(
            "DHS data claims and state ballot measures",
            ["DHS data was cited in a state ballot measure debate."],
            [existing], recent_embs, title_emb, set(),
            source_urls=["https://npr.org/nx-s1-5940807"],
        )
        assert match is existing

    def test_find_matching_issue_does_not_match_on_url_when_none_are_shared(self):
        existing = ActionIssue(
            id=622, date="2026-08-25", rank=3, title="Unrelated story",
            facts=json.dumps(["Some other fact."]),
            source_urls=json.dumps(["https://apnews.com/other-story"]),
        )
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([0.0, 1.0])

        match = _find_matching_issue(
            "A totally different headline", ["A totally different fact."],
            [existing], recent_embs, title_emb, set(),
            source_urls=["https://npr.org/nx-s1-5940807"],
        )
        assert match is None

    def test_find_matching_issue_skips_a_shared_url_already_claimed_this_run(self):
        existing = ActionIssue(
            id=623, date="2026-08-25", rank=3, title="Some story",
            facts=json.dumps(["Some fact."]),
            source_urls=json.dumps(["https://npr.org/nx-s1-5940807"]),
        )
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([0.0, 1.0])

        match = _find_matching_issue(
            "Some story", ["Some fact."],
            [existing], recent_embs, title_emb, {623},
            source_urls=["https://npr.org/nx-s1-5940807"],
        )
        assert match is None


class TestDedupeNearIdenticalIssues:
    """The homepage's recent-issues endpoint deliberately shows issues
    regardless of is_current (see get_recent_action_issues) — so a row
    retired specifically for BEING a duplicate resurfaced there anyway
    (2026-08-22 report: "I see 3 copies of the beef import issue on the
    homepage"). This is the read-time dedup that fixes that without a
    second is_current flip."""

    def _issue(self, id, title, created_at):
        row = ActionIssue(date="2026-08-21", rank=1, title=title, facts="[]")
        row.id = id
        row.created_at = created_at
        return row

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_collapses_a_cluster_to_its_freshest_member(self, mock_embed):
        # Identical vectors -> cosine 1.0 (over threshold); orthogonal ->
        # cosine 0.0 (nowhere close). The threshold's own calibration is
        # tested in TestIssueSignatureMatching — this exercises clustering.
        a = self._issue(603, "Trump defends beef import plan amid GOP criticism", datetime(2026, 8, 21, 23))
        b = self._issue(604, "Trump defends beef import plan amid GOP criticism", datetime(2026, 8, 22, 0))
        c = self._issue(605, "Trump defends beef import plan after GOP criticism", datetime(2026, 8, 22, 2))
        d = self._issue(999, "Senate confirms new EPA administrator", datetime(2026, 8, 21, 10))
        mock_embed.return_value = np.array([
            [1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0],
        ])

        result = dedupe_near_identical_issues([a, b, c, d])

        assert result == [c, d]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_no_clusters_returns_everything_in_original_order(self, mock_embed):
        a = self._issue(1, "Senate confirms new EPA administrator", datetime(2026, 8, 20))
        b = self._issue(2, "House passes defense funding bill", datetime(2026, 8, 21))
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])

        result = dedupe_near_identical_issues([a, b])

        assert result == [a, b]

    def test_fewer_than_two_issues_is_a_no_op(self):
        a = self._issue(1, "Solo issue", datetime(2026, 8, 20))
        assert dedupe_near_identical_issues([a]) == [a]
        assert dedupe_near_identical_issues([]) == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_kept_issues_preserve_their_original_relative_order(self, mock_embed):
        # Freshest-of-cluster is issue b (middle position) — it must stay
        # in b's original slot relative to c, not jump to wherever a was.
        a = self._issue(1, "Trump defends beef import plan amid GOP criticism", datetime(2026, 8, 21))
        b = self._issue(2, "Senate confirms new EPA administrator", datetime(2026, 8, 20))
        c = self._issue(3, "Trump defends beef import plan amid GOP criticism", datetime(2026, 8, 20))
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])

        result = dedupe_near_identical_issues([a, b, c])

        assert result == [a, b]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_shared_source_url_collapses_regardless_of_title_similarity(self, mock_embed):
        # Same underlying article, LLM reworded the headline enough that the
        # titles are orthogonal in embedding space — same real-world case
        # _find_matching_issue's #434 fix handles (checked before title
        # cosine at all). The read-time pass must catch it too.
        a = self._issue(1, "DHS data claims and think tank connections", datetime(2026, 8, 21))
        b = self._issue(2, "DHS data claims and state ballot measures", datetime(2026, 8, 22))
        a.source_urls = json.dumps(["https://npr.org/dhs-story"])
        b.source_urls = json.dumps(["https://npr.org/dhs-story"])
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])

        result = dedupe_near_identical_issues([a, b])

        assert result == [b]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_signature_overlap_collapses_below_near_identical_title_threshold(self, mock_embed):
        # Same entities/numbers, title cosine sits in the gap between
        # TOPIC_CHANGE_THRESHOLD (0.65) and _NEAR_IDENTICAL_TITLE_THRESHOLD
        # (0.92) — only signature overlap should decide this one, same as
        # _find_matching_issue.
        a = self._issue(1, "Trump attorney general nominee advances 54-45", datetime(2026, 8, 21))
        b = self._issue(2, "Senate advances Trump attorney general pick 54-45", datetime(2026, 8, 22))
        mock_embed.return_value = np.array([[1.0, 0.0], [0.7, (1 - 0.7 ** 2) ** 0.5]])

        result = dedupe_near_identical_issues([a, b])

        assert result == [b]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_different_signatures_below_near_identical_stay_separate(self, mock_embed):
        # Sanity check for the new branches: a shared-vocabulary pair with
        # NO shared entities/numbers and no shared source URL must not
        # collapse just because title cosine clears TOPIC_CHANGE_THRESHOLD.
        a = self._issue(1, "Trump attorney general nominee advances 54-45", datetime(2026, 8, 21))
        b = self._issue(2, "Biden EPA administrator confirmed 60-38", datetime(2026, 8, 22))
        mock_embed.return_value = np.array([[1.0, 0.0], [0.7, (1 - 0.7 ** 2) ** 0.5]])

        result = dedupe_near_identical_issues([a, b])

        assert result == [a, b]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_a_weak_link_cannot_transitively_merge_an_unrelated_third_issue(self, mock_embed):
        # 2026-08 audit (independent review): single-linkage union-find let
        # a strong match (A-B, shared source URL) and an unrelated weak
        # match (B-C, signature overlap only) transitively merge A and C
        # even though A and C never matched anything directly — silently
        # dropping a legitimately separate issue. Clustering must be
        # complete-linkage: A-B merge (shared URL), but C stays on its own
        # since A-C shares neither a URL, a signature, nor meaningful
        # title similarity, regardless of C's link to B.
        a = self._issue(1, "DHS data claims and think tank connections", datetime(2026, 8, 21, 21))
        b = self._issue(2, "Trump attorney general nominee advances 54-45", datetime(2026, 8, 21, 22))
        c = self._issue(3, "Senate advances Trump attorney general pick 54-45", datetime(2026, 8, 22, 2))
        a.source_urls = json.dumps(["https://npr.org/dhs-story"])
        b.source_urls = json.dumps(["https://npr.org/dhs-story"])
        # A-B: orthogonal (merge is via shared URL alone, not cosine).
        # B-C: cosine 0.7, same gap that collapses via signature overlap
        # in test_signature_overlap_collapses_below_near_identical_title_threshold.
        # A-C: orthogonal — no shared URL, no shared signature either.
        mock_embed.return_value = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.7, (1 - 0.7 ** 2) ** 0.5],
        ])

        result = dedupe_near_identical_issues([a, b, c])

        # {a, b} collapse to the fresher of the two (b); c stays separate.
        # A buggy single-linkage pass would chain a-b-c into one cluster
        # and keep only c (the overall freshest), losing b's URL-verified
        # story entirely.
        assert result == [b, c]


def _new_values_for(title: str, facts: list[str], primary_article_date: str) -> dict:
    return {
        "title": title, "summary": "summary", "facts": json.dumps(facts),
        "actions": "[]", "source_urls": "[]", "source_names": "[]",
        "policy_areas": "[]", "related_bill_ids": "[]", "related_explore_ids": "[]",
        "related_senators": "[]", "related_officials": "[]",
        "primary_article_date": primary_article_date,
    }


class TestRetireUntouchedIssues:
    """_retire_untouched_issues, extracted from _run_refresh (2026-08) for
    direct testability — pins what _RETIREMENT_GRACE_HOURS (90min -> 24h,
    2026-08 audit) actually controls, not just what the comment claims."""

    def _issue(self, id: int, created_at: datetime | None) -> ActionIssue:
        row = ActionIssue(date="2026-08-20", rank=1, title=f"Issue {id}", summary="s")
        row.id = id
        row.created_at = created_at
        row.is_current = True
        return row

    def test_a_matched_issue_is_never_retired_regardless_of_age(self):
        now = utcnow()
        ancient = self._issue(1, now - timedelta(days=5))

        n_retired, n_graced = _retire_untouched_issues([ancient], {1}, now - timedelta(hours=24))

        assert (n_retired, n_graced) == (0, 0)
        assert ancient.is_current is True

    def test_unmatched_issue_past_the_grace_cutoff_is_retired(self):
        now = utcnow()
        old = self._issue(2, now - timedelta(hours=25))

        n_retired, n_graced = _retire_untouched_issues([old], set(), now - timedelta(hours=24))

        assert (n_retired, n_graced) == (1, 0)
        assert old.is_current is False

    def test_unmatched_issue_within_the_grace_cutoff_is_spared(self):
        now = utcnow()
        recent = self._issue(3, now - timedelta(hours=1))

        n_retired, n_graced = _retire_untouched_issues([recent], set(), now - timedelta(hours=24))

        assert (n_retired, n_graced) == (0, 1)
        assert recent.is_current is True

    def test_missing_created_at_is_treated_as_eligible_for_retirement(self):
        # Can't prove it's young without a timestamp — fails closed rather
        # than sparing indefinitely.
        now = utcnow()
        undated = self._issue(4, None)

        n_retired, n_graced = _retire_untouched_issues([undated], set(), now - timedelta(hours=24))

        assert (n_retired, n_graced) == (1, 0)
        assert undated.is_current is False

    def test_mixed_batch_counts_each_correctly(self):
        now = utcnow()
        matched = self._issue(1, now - timedelta(hours=48))
        old_unmatched = self._issue(2, now - timedelta(hours=25))
        recent_unmatched = self._issue(3, now - timedelta(hours=1))

        n_retired, n_graced = _retire_untouched_issues(
            [matched, old_unmatched, recent_unmatched], {1}, now - timedelta(hours=24),
        )

        assert (n_retired, n_graced) == (1, 1)
        assert matched.is_current is True
        assert old_unmatched.is_current is False
        assert recent_unmatched.is_current is True

    def test_records_retired_and_graced_counts_on_action_metrics(self):
        # admin_action_metrics is the only way to check _RETIREMENT_GRACE_HOURS
        # against real history — before this, retirement/grace were logged as
        # free text only, so the 24h value had nothing to be validated against.
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        now = utcnow()
        old_unmatched = self._issue(2, now - timedelta(hours=25))
        recent_unmatched = self._issue(3, now - timedelta(hours=1))

        _retire_untouched_issues([old_unmatched, recent_unmatched], set(), now - timedelta(hours=24))

        counts = action_metrics.snapshot()
        assert counts["issues_retired"] == 1
        assert counts["issues_graced"] == 1


class TestRetryUntilGrounded:
    """_retry_until_grounded, extracted from _run_refresh (2026-08) for
    direct testability — a single retry cleared close to none of these
    live (admin_action_metrics audit: 67 of 192 clusters considered over
    48h were rejected here, 39 of 48 hourly runs produced zero new
    topics), which is what motivated a second attempt in the first place."""

    def _mock_db(self):
        mock_db = MagicMock()
        # _validate_politician_roles queries Senator/Representative — empty
        # lists so the "Senator X" name-check finds nothing to strip and
        # doesn't error on a non-iterable MagicMock.
        mock_db.query.return_value.all.return_value = []
        return mock_db

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_first_attempt_succeeds(self, mock_call_llm):
        mock_call_llm.return_value = json.dumps({
            "summary": "The Senate confirmed the nominee.",
            "facts": ["The vote was unanimous."],
        })

        result = _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        assert result == ("Original Title", "The Senate confirmed the nominee.", ["The vote was unanimous."])
        assert mock_call_llm.call_count == 1

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_correction_prompt_covers_the_grounding_violation_categories(self, mock_call_llm):
        # 2026-08 quality audit: the correction text used to only address
        # hedging/former-status/vague-office — a rejection for an
        # ungrounded number, titled name, electoral claim, relationship,
        # or party label gave the retry no guidance on what to fix. This
        # doesn't prove a real LLM acts on it, but does prove the guidance
        # for every category the reasons list can now contain is actually
        # present in the prompt sent to it.
        mock_call_llm.return_value = json.dumps({
            "summary": "The Senate confirmed the nominee.",
            "facts": [],
        })

        _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["numbers not in source: 98-2"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        prompt = mock_call_llm.call_args_list[0].kwargs["user_prompt"]
        assert "do not state any number" in prompt
        assert "do not name any titled official" in prompt
        assert "do not describe any election" in prompt
        assert "do not state a family relationship" in prompt
        assert "do not attach a party label" in prompt
        assert "do not call any official 'former'" in prompt

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_second_attempt_succeeds_after_first_still_hedges(self, mock_call_llm):
        mock_call_llm.side_effect = [
            json.dumps({
                "summary": "Recent reports highlight the administration's plans.",
                "facts": ["The plan was discussed."],
            }),
            json.dumps({
                "summary": "The administration announced its plans.",
                "facts": ["The plan was discussed."],
            }),
        ]

        result = _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        assert result is not None
        assert result[1] == "The administration announced its plans."
        assert mock_call_llm.call_count == 2
        # The second attempt's prompt carries the worked example — the
        # whole point of trying a second time with a different correction.
        second_call_prompt = mock_call_llm.call_args_list[1].kwargs["user_prompt"]
        assert "Example fix" in second_call_prompt

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_both_attempts_still_hedging_returns_none(self, mock_call_llm):
        mock_call_llm.return_value = json.dumps({
            "summary": "Recent reports highlight the administration's plans.",
            "facts": ["The plan was discussed."],
        })

        result = _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        assert result is None
        assert mock_call_llm.call_count == 2

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_retry_that_fixes_hedging_but_introduces_an_ungrounded_number_is_still_rejected(self, mock_call_llm):
        # 2026-08 quality audit: this function's grounding check used to
        # cover hedging/editorializing and former-official status only — a
        # retry that fixed its hedging while fabricating a brand-new
        # ungrounded number sailed through as "grounded" because nothing
        # here ever ran the full grounding_violations() combinator
        # (ungrounded numbers/titled names/electoral claims/relationships/
        # party affiliation), even though the Bluesky post generated from
        # this same summary already did. Both attempts fabricate a vote
        # count absent from the source, so both must fail.
        mock_call_llm.return_value = json.dumps({
            "summary": "The Senate confirmed the nominee by a vote of 98-2.",
            "facts": ["The vote took place Tuesday."],
        })

        result = _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(),
            issue_source_text="The Senate confirmed the nominee on Tuesday.",
            title="Original Title",
        )

        assert result is None
        assert mock_call_llm.call_count == 2

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_second_attempts_correction_names_the_new_failure_not_the_original(self, mock_call_llm):
        # Attempt 1 fixes the original hedge but introduces a DIFFERENT one —
        # attempt 2's prompt must name what attempt 1 actually got wrong.
        mock_call_llm.side_effect = [
            json.dumps({
                "summary": "Analysts note the administration's plans.",
                "facts": ["The plan was discussed."],
            }),
            json.dumps({
                "summary": "The administration announced its plans.",
                "facts": ["The plan was discussed."],
            }),
        ]

        _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        # "reports say" (the ORIGINAL reason) also appears verbatim in the
        # correction template's own static example text, so check the
        # specific "rejected because it contained X" clause rather than
        # the prompt as a whole.
        second_call_prompt = mock_call_llm.call_args_list[1].kwargs["user_prompt"]
        assert "rejected because it contained hedging attribution phrases (Analysts note)" in second_call_prompt
        assert "rejected because it contained hedging attribution phrases (reports say)" not in second_call_prompt

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_unparseable_response_counts_as_a_failed_attempt(self, mock_call_llm):
        mock_call_llm.side_effect = [
            "not valid json at all",
            json.dumps({"summary": "The administration announced its plans.", "facts": []}),
        ]

        result = _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=self._mock_db(), issue_source_text="source article text",
            title="Original Title",
        )

        assert result is not None
        assert mock_call_llm.call_count == 2


class TestRecordGenerationSample:
    """_record_generation_sample: the fine-tuning data-capture helper. It
    must never let a persistence failure break issue generation, since the
    table only feeds a future training run, not anything the site serves."""

    def test_passed_attempt_is_recorded_with_no_violations(self, db_session):
        _record_generation_sample(
            db_session, "action_center_issue", rank=1, attempt=1,
            input_text="generate the issue",
            output={"title": "T", "summary": "S", "facts": ["F"]},
            passed=True,
        )
        db_session.commit()

        rows = db_session.query(LlmGenerationSample).all()
        assert len(rows) == 1
        assert rows[0].task == "action_center_issue"
        assert rows[0].passed is True
        assert rows[0].violations is None
        assert json.loads(rows[0].output_json) == {"title": "T", "summary": "S", "facts": ["F"]}

    def test_failed_attempt_records_violations(self, db_session):
        _record_generation_sample(
            db_session, "action_center_issue", rank=2, attempt=1,
            input_text="generate the issue",
            output={"title": "T", "summary": "Reports say X", "facts": []},
            passed=False, violations=["hedging attribution phrases (reports say)"],
        )
        db_session.commit()

        row = db_session.query(LlmGenerationSample).one()
        assert row.passed is False
        assert json.loads(row.violations) == ["hedging attribution phrases (reports say)"]

    def test_db_error_is_swallowed_not_raised(self, db_session):
        broken_db = MagicMock()
        broken_db.add.side_effect = RuntimeError("db is down")

        _record_generation_sample(
            broken_db, "action_center_issue", rank=1, attempt=1,
            input_text="x", output={"summary": "y"}, passed=True,
        )  # must not raise

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_retry_until_grounded_records_every_attempt(self, mock_call_llm, db_session):
        mock_call_llm.side_effect = [
            json.dumps({
                "summary": "Recent reports highlight the administration's plans.",
                "facts": ["The plan was discussed."],
            }),
            json.dumps({
                "summary": "The administration announced its plans.",
                "facts": ["The plan was discussed."],
            }),
        ]

        _retry_until_grounded(
            user_prompt="generate the issue",
            reasons=["hedging attribution phrases (reports say)"],
            rank=1, db=db_session, issue_source_text="source article text",
            title="Original Title",
        )
        db_session.commit()

        rows = db_session.query(LlmGenerationSample).order_by(LlmGenerationSample.attempt).all()
        assert [r.attempt for r in rows] == [2, 3]
        assert rows[0].passed is False
        assert rows[1].passed is True


class TestApplyMatchedIssueUpdate:
    """_apply_matched_issue_update, extracted from _run_refresh (2026-07)
    for direct testability — _run_refresh as a whole fetches real
    articles, runs an embedding model, and calls an LLM, so it can't
    reasonably be driven end-to-end in a unit test."""

    def test_updates_row_attributes_from_new_values(self):
        match = ActionIssue(
            id=1, date="2026-07-20", rank=3, title="Old title",
            facts=json.dumps(["An old fact."]), primary_article_date="2026-07-19",
        )
        new_values = _new_values_for("New title", ["An old fact.", "A new fact with Senator Susan Collins."], "2026-07-20")

        _apply_matched_issue_update(match, new_values, 1, "2026-07-20", "2026-07-20", json.loads(new_values["facts"]), "New title")

        assert match.title == "New title"
        assert match.rank == 1
        assert match.date == "2026-07-20"
        assert match.is_current is True

    def test_previous_facts_snapshots_the_outgoing_facts_when_they_change(self):
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["An old fact."]), primary_article_date="2026-07-19",
        )
        facts = ["An old fact.", "A brand new fact."]
        new_values = _new_values_for("Story", facts, "2026-07-20")

        _apply_matched_issue_update(match, new_values, 1, "2026-07-20", "2026-07-20", facts, "Story")

        assert json.loads(match.previous_facts) == ["An old fact."]
        assert json.loads(match.facts) == facts

    def test_previous_facts_untouched_when_nothing_actually_changed(self):
        # An hourly touch that reconfirms the same facts (LLM regenerates
        # the whole list every run) must not overwrite the baseline the
        # "new" marker diffs against with a copy of itself.
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["An old fact."]),
            previous_facts=json.dumps(["Something from two versions ago."]),
            primary_article_date="2026-07-19",
        )
        new_values = _new_values_for("Story", ["An old fact."], "2026-07-19")

        _apply_matched_issue_update(match, new_values, 1, "2026-07-19", "2026-07-19", ["An old fact."], "Story")

        assert json.loads(match.previous_facts) == ["Something from two versions ago."]

    def test_previous_facts_ignores_pure_reordering(self):
        # Set comparison, not string equality — the LLM makes no promise
        # about a stable order between runs, and a reshuffled list with
        # the same content isn't a content change.
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["fact a", "fact b"]),
            previous_facts=json.dumps(["baseline"]),
            primary_article_date="2026-07-19",
        )
        new_values = _new_values_for("Story", ["fact b", "fact a"], "2026-07-19")

        _apply_matched_issue_update(match, new_values, 1, "2026-07-19", "2026-07-19", ["fact b", "fact a"], "Story")

        assert json.loads(match.previous_facts) == ["baseline"]

    def test_new_date_with_new_information_allows_repost(self):
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["An old fact."]), primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )
        facts = ["An old fact.", "A new fact naming Senator Susan Collins."]
        new_values = _new_values_for("Story", facts, "2026-07-20")

        result = _apply_matched_issue_update(match, new_values, 1, "2026-07-20", "2026-07-20", facts, "Story")

        assert result is True
        assert match.bsky_posted_at is None
        assert match.bsky_posted_rank is None

    def test_new_date_but_no_new_information_does_not_allow_repost(self):
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["An old fact about the vote."]), primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )
        # Same facts, reworded — no new named entity or figure.
        facts = ["An old fact about the vote, restated."]
        new_values = _new_values_for("Story", facts, "2026-07-20")
        prior_posted_at = match.bsky_posted_at

        result = _apply_matched_issue_update(match, new_values, 1, "2026-07-20", "2026-07-20", facts, "Story")

        assert result is False
        assert match.bsky_posted_at == prior_posted_at
        assert match.bsky_posted_rank == 1

    def test_no_new_date_does_not_allow_repost(self):
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(["An old fact naming Senator Susan Collins."]),
            primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )
        facts = ["An old fact naming Senator Susan Collins.", "Brand new fact naming Senator Marco Rubio."]
        # primary_article_date NOT advanced past match's own — new facts
        # don't matter if the date itself never moved forward.
        new_values = _new_values_for("Story", facts, "2026-07-19")
        prior_posted_at = match.bsky_posted_at

        result = _apply_matched_issue_update(match, new_values, 1, "2026-07-19", "2026-07-19", facts, "Story")

        assert result is False
        assert match.bsky_posted_at == prior_posted_at

    def test_repost_baseline_is_the_last_post_not_the_last_run(self):
        """Regression: a development that surfaced on a non-posting run was
        absorbed into `facts` and then read as already-known on the one run
        that could have reposted it, so the update was lost for good.

        primary_article_date is day-granular, so the date edge fires on one
        of the ~24 hourly runs per day while `facts` is rewritten on all 24
        — which run the LLM happens to surface a given fact on is luck, and
        it decided whether the story could ever be followed up.
        """
        from app.pipeline.analyze import action_metrics

        posted_facts = ["A defense policy bill was passed with a narrow 216-212 vote."]
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(posted_facts),
            bsky_posted_facts=json.dumps(posted_facts),
            primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )

        # Run A — same article date, so no repost is possible here, but the
        # LLM's re-extraction surfaces a genuinely new named entity.
        developed = posted_facts + ["Senator Susan Collins announced opposition."]
        action_metrics.reset()
        assert _apply_matched_issue_update(
            match, _new_values_for("Story", developed, "2026-07-19"),
            1, "2026-07-19", "2026-07-19", developed, "Story",
        ) is False
        assert match.facts == json.dumps(developed)  # baseline for display moved
        assert match.bsky_posted_facts == json.dumps(posted_facts)  # repost baseline did not

        # Run B — the article date finally advances, facts unchanged since
        # run A. Collins is still new relative to what was actually posted.
        assert _apply_matched_issue_update(
            match, _new_values_for("Story", developed, "2026-07-20"),
            1, "2026-07-20", "2026-07-20", developed, "Story",
        ) is True
        assert match.bsky_posted_at is None
        assert action_metrics.snapshot().get("bsky_reposts_allowed") == 1

    def test_suppressed_repost_is_counted_separately_from_no_new_article(self):
        """Both cases logged the same "no new articles" line, so the share
        of reposts this gate suppressed was unmeasurable in production."""
        from app.pipeline.analyze import action_metrics

        facts = ["An old fact about the vote."]
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(facts), bsky_posted_facts=json.dumps(facts),
            primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )
        reworded = ["An old fact about the vote, restated."]

        action_metrics.reset()
        # Newer article date, nothing new to say -> suppressed by this gate.
        _apply_matched_issue_update(
            match, _new_values_for("Story", reworded, "2026-07-20"),
            1, "2026-07-20", "2026-07-20", reworded, "Story",
        )
        # No newer article at all -> not this gate's doing, not counted.
        _apply_matched_issue_update(
            match, _new_values_for("Story", reworded, "2026-07-20"),
            1, "2026-07-20", "2026-07-20", reworded, "Story",
        )

        counts = action_metrics.snapshot()
        assert counts.get("bsky_reposts_suppressed_no_new_information") == 1
        assert counts.get("bsky_reposts_allowed") is None

    def test_row_last_posted_before_the_baseline_column_falls_back_to_facts(self):
        # Rows posted before bsky_posted_facts existed have it NULL; they
        # keep the old behavior until their next post rather than treating
        # an empty baseline as "everything is new" and reposting all of them.
        # The facts here carry a named entity precisely so that an empty
        # baseline WOULD return True — this fails if the fallback is dropped.
        facts = ["Senator Susan Collins opposed the temporary funding measure."]
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Story",
            facts=json.dumps(facts), bsky_posted_facts=None,
            primary_article_date="2026-07-19",
            bsky_posted_at=utcnow(), bsky_posted_rank=1,
        )
        reworded = ["Senator Susan Collins opposed the funding measure."]
        assert _bsky_repost_has_new_information("", reworded) is True  # empty baseline

        assert _apply_matched_issue_update(
            match, _new_values_for("Story", reworded, "2026-07-20"),
            1, "2026-07-20", "2026-07-20", reworded, "Story",
        ) is False

    def test_invalidated_story_clears_cached_full_story(self):
        match = ActionIssue(
            id=1, date="2026-07-19", rank=1, title="Old story",
            facts=json.dumps(["An old fact."]), primary_article_date="2026-07-19",
            full_story="Cached long-form text about the old story.",
        )
        facts = ["A completely different fact."]
        new_values = _new_values_for("A different story entirely", facts, "2026-07-19")

        _apply_matched_issue_update(match, new_values, 1, "2026-07-19", "2026-07-19", facts, "A different story entirely")

        assert match.full_story is None


class TestBskyRepostHasNewInformation:
    """A matched issue's primary_article_date advancing used to be the only
    gate on allowing a Bluesky repost — but recap/ongoing coverage of the
    same story often just rewords the same names and numbers under a
    fresher timestamp, which let a story repost with nothing new to say
    (reported live 2026-07: Bluesky repeatedly posting about the same
    thing). This checks the actual new-information gate, not just the
    date comparison _run_refresh does before calling it."""

    def test_reworded_recap_of_the_same_facts_has_no_new_information(self):
        # Same pair as test_same_defense_bill_rows_match above (ids
        # 394/405) — same $95B framework, same 216-212 vote, purely
        # reworded with no new name or figure.
        old_facts = json.dumps([
            "A defense policy bill was passed with a narrow 216-212 vote.",
            "House Republicans approved a $95 billion framework for a third budget reconciliation package.",
        ])
        new_facts = [
            "A $95 billion framework was approved for defense spending.",
            "The vote resulted in a narrow 216-212 outcome.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is False

    def test_a_genuine_update_within_the_same_story_counts_as_new_information(self):
        # A sample initially flagged positive later confirmed a false
        # positive is a real narrative development (outbreak scare
        # downgraded), not just a reword — "FDA" as the named source of
        # that correction is new even though this reads as "the same
        # story" for matching purposes.
        old_facts = json.dumps([
            "A lettuce sample from Taylor Farms was initially flagged as positive for cyclospora.",
            "Multiple states are reporting over 7,000 confirmed cases of cyclosporiasis nationwide.",
        ])
        new_facts = [
            "Over 7,000 cases have been reported across several states.",
            "The FDA has stated that a sample from Taylor Farms was later identified as a false positive.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is True

    def test_a_new_named_entity_counts_as_new_information(self):
        old_facts = json.dumps(["The House passed a temporary funding measure to avoid a shutdown."])
        new_facts = [
            "The House passed a temporary funding measure to avoid a shutdown.",
            "Senator Susan Collins said she would support the measure in the Senate.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is True

    def test_a_new_figure_counts_as_new_information(self):
        old_facts = json.dumps(["A defense policy bill was passed with a narrow 216-212 vote."])
        new_facts = ["A defense policy bill was passed with a narrow 216-212 vote, costing $95 billion."]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is True

    def test_expanding_a_known_entity_to_its_full_name_is_not_new_information(self):
        # Live 2026-08-27 case (issue 624): old facts named "Saudi" (from
        # "The Saudi delegation referenced the agreement"); new facts
        # spelled the same country out as "Saudi Arabia". The signature
        # diff was the lone token "arabia" — read as a brand-new entity
        # when it's the same country already known.
        old_facts = json.dumps([
            "A nuclear cooperation agreement was presented to Congress this week.",
            "The agreement includes provisions for uranium enrichment activities.",
            "The Saudi delegation referenced the agreement in a recent briefing.",
        ])
        new_facts = [
            "A nuclear cooperation agreement was presented to Congress this week.",
            "The agreement allows Saudi Arabia to enrich uranium under specific conditions.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is False

    def test_a_genuinely_new_entity_still_counts_even_beside_a_known_one(self):
        # The entity-expansion filter must not swallow an unrelated new
        # entity just because it also appears in a two-word capitalized
        # phrase alongside an already-known word.
        old_facts = json.dumps(["The Saudi delegation attended the summit."])
        new_facts = [
            "The Saudi delegation attended the summit.",
            "Qatar Airways provided the delegation's transportation.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is True

    def test_missing_old_facts_json_treated_as_empty(self):
        assert _bsky_repost_has_new_information(
            "", ["Senator Susan Collins commented on the bill."],
        ) is True

    # The signature answers "is a new PARTICIPANT involved?", which is not
    # the same question as "did anything HAPPEN?" — a story can move
    # decisively without naming anyone or anything new, and those updates
    # were being suppressed as rewords (reported live 2026-07: the poster
    # stopped following stories through to their outcome). Each case below
    # deliberately introduces NO new capitalized entity and NO new figure,
    # so only the development-marker path can carry it.

    def test_a_veto_counts_as_new_information_without_a_new_name_or_figure(self):
        old_facts = [
            "A defense policy bill was passed with a narrow 216-212 vote.",
            "The measure includes a $95 billion framework.",
        ]
        new_facts = old_facts + ["The president vetoed the measure."]
        assert _issue_signature("", new_facts) - _issue_signature("", old_facts) == set()
        assert _bsky_repost_has_new_information(json.dumps(old_facts), new_facts) is True

    def test_a_court_blocking_the_measure_counts_as_new_information(self):
        old_facts = ["A temporary funding measure took effect this week."]
        new_facts = old_facts + ["A federal judge blocked the order."]
        assert _issue_signature("", new_facts) - _issue_signature("", old_facts) == set()
        assert _bsky_repost_has_new_information(json.dumps(old_facts), new_facts) is True

    def test_a_failed_override_counts_as_new_information(self):
        old_facts = json.dumps(["The president vetoed the defense measure."])
        new_facts = [
            "The president vetoed the defense measure.",
            "The override attempt failed.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is True

    def test_repeating_the_same_development_is_not_new_information(self):
        # The marker check is differential: a recap that says "vetoed"
        # again, having already said it, has still added nothing.
        old_facts = json.dumps([
            "The president vetoed the defense measure.",
            "The bill had passed with a 216-212 vote.",
        ])
        new_facts = [
            "The defense measure was vetoed by the president.",
            "It had passed 216-212.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is False

    def test_weak_reporting_verbs_are_not_developments(self):
        # "announced"/"reported"/"said" appear in every recap, so treating
        # them as developments would re-open the bug this gate exists for.
        old_facts = json.dumps(["The committee will review the funding measure."])
        new_facts = [
            "The committee will review the funding measure.",
            "The chair announced that the review is ongoing and reported no timetable.",
        ]
        assert _bsky_repost_has_new_information(old_facts, new_facts) is False

    def test_a_new_lawsuit_being_filed_counts_as_new_information(self):
        # Prompted by a live 2026-08-26 issue (id 615) whose real facts
        # included "Democratic-controlled states filed a new lawsuit
        # challenging the executive order" as a genuinely new
        # development — "filed" wasn't a tracked marker at all. That
        # exact sentence happens to ALSO add a new signature token via a
        # hyphenated-compound quirk ("Democratic-controlled" isn't
        # stripped the way bare "Democratic" is), so this uses a
        # deliberately cleaner example ("State officials", both stripped
        # as generic civic vocabulary) to isolate the marker path itself.
        old_facts = ["A court order had blocked the agency's new rule."]
        new_facts = old_facts + ["State officials filed a new lawsuit against the rule."]
        assert _issue_signature("", new_facts) - _issue_signature("", old_facts) == set()
        assert _bsky_repost_has_new_information(json.dumps(old_facts), new_facts) is True

    def test_a_court_lifting_an_order_counts_as_new_information(self):
        # "lifted" added the same day, same real issue, alongside "filed" —
        # both real judicial-outcome verbs missing from the tracked list.
        old_facts = ["A court order had blocked the agency's new rule."]
        new_facts = old_facts + ["A judge lifted the order blocking the rule."]
        assert _issue_signature("", new_facts) - _issue_signature("", old_facts) == set()
        assert _bsky_repost_has_new_information(json.dumps(old_facts), new_facts) is True


class TestValidateFactsAuditAdditions:
    """2026-07 audit: placeholder tokens, subject-form meta-facts, and
    ungrounded family relationships all published — each case below is
    the live text."""

    def test_placeholder_fact_dropped(self):
        facts = ["Thune announced the tribute details on [date].",
                 "The Senate held a vote on Thursday."]
        clean = _validate_facts(facts, source_text="Thune announced details. The Senate held a vote on Thursday.")
        assert clean == ["The Senate held a vote on Thursday."]

    def test_articles_as_subject_meta_fact_dropped(self):
        facts = ["The articles focused on internal party dynamics rather than public policy outcomes."]
        assert _validate_facts(facts) == []

    def test_articles_referenced_meta_fact_dropped(self):
        facts = ["The articles referenced specific names and dates related to the discussion."]
        assert _validate_facts(facts) == []

    def test_ungrounded_family_relationship_fact_dropped(self):
        facts = ["Senator Graham announced her candidacy for the seat left by her brother."]
        source = "Darline Graham announced her candidacy for the vacant seat."
        assert _validate_facts(facts, source_text=source) == []

    def test_grounded_family_relationship_fact_kept(self):
        facts = ["Senator Graham announced her candidacy for the seat left by her brother."]
        source = "Darline Graham, whose brother held the seat, announced her candidacy."
        assert _validate_facts(facts, source_text=source) == facts

    def test_ungrounded_former_status_fact_dropped(self):
        # 2026-07 live case: "former President Donald Trump" published while
        # the source material said "President Trump".
        facts = ["Former President Donald Trump announced new tariffs on steel imports."]
        source = "President Trump announced tariffs on steel imports."
        assert _validate_facts(facts, source_text=source) == []

    def test_grounded_former_status_fact_kept(self):
        facts = ["Former President Obama criticized the ruling on Tuesday."]
        source = "Former President Barack Obama criticized the court's ruling Tuesday."
        assert _validate_facts(facts, source_text=source) == facts


class TestValidateFactsAbsenceOfInformation:
    """2026-08-26 audit: ~30% of a sampled window of issues had a "key
    fact" that reports what the coverage DIDN'T say, rather than a real
    event — a second meta-fact shape _META_PHRASES (which only catches
    "the article(s)" as subject) didn't cover. Live examples below."""

    def test_no_specific_x_was_provided_is_dropped(self):
        facts = ["No specific date was provided for when the new block may take effect."]
        assert _validate_facts(facts) == []

    def test_specific_x_were_not_disclosed_is_dropped(self):
        facts = ["Specific names of officials were not disclosed in the provided articles."]
        assert _validate_facts(facts) == []

    def test_no_official_x_was_provided_is_dropped(self):
        facts = ["No official timeline was provided regarding when renovations would proceed."]
        assert _validate_facts(facts) == []

    def test_a_genuine_positive_fact_with_the_same_verb_is_kept(self):
        # Must not catch the ordinary positive form just because it shares
        # a qualifying word and a verb with the absence pattern.
        facts = ["The official statement was provided to reporters on Tuesday."]
        assert _validate_facts(facts) == facts

    def test_a_real_absence_fact_without_a_qualifier_is_kept(self):
        # "No injuries were reported" is a real, substantive fact, not
        # padding — the qualifier requirement (specific/official/further/
        # additional) is what keeps facts like this one out of scope.
        facts = ["No injuries were reported at the scene."]
        assert _validate_facts(facts) == facts


class TestSurnameGuardEdges:
    def test_surname_at_text_start_has_no_owner(self):
        import re
        text = "Torres said the housing bill would advance this week."
        m = re.search(r"\bTorres\b", text)
        assert _surname_owned_by_other_name(text, m, "Ritchie Torres") is False


class TestValidateFactsMetricPaths:
    def test_stale_future_dated_fact_dropped(self):
        facts = ["The ban will remain in effect until December 2025."]
        assert _validate_facts(facts) == []

    def test_fact_with_ungrounded_number_dropped(self):
        facts = ["The program cost $450 million last year."]
        clean = _validate_facts(facts, source_text="The program's cost rose sharply last year.")
        assert clean == []


class TestGenerateFullStoryRelationshipGuard:
    """Audit M8: the full-story generator must reject a story asserting a
    family relationship absent from the material the model was shown, and
    accept the clean retry."""

    def test_ungrounded_relationship_rejected_then_clean_retry_accepted(self, db_session):
        issue = ActionIssue(
            date="2026-07-22", rank=1, is_current=True,
            title="Senate Budget Committee convenes after leadership change",
            summary="The committee met for the first time since the vacancy opened.",
            facts=json.dumps([
                "The Senate Budget Committee held its first meeting since the vacancy.",
                "Senator Darline Graham announced her candidacy for the vacant seat.",
            ]),
            source_names=json.dumps(["AP News"]),
            policy_areas=json.dumps(["CONGRESS"]),
        )
        db_session.add(issue)
        db_session.commit()

        bad = (
            "The Senate Budget Committee convened for the first time since the vacancy "
            "opened, marking a somber return to regular business for its members. "
            "Senator Darline Graham announced her candidacy for the seat left by her "
            "brother, telling reporters she would focus on fiscal policy in the term ahead."
        )
        clean = (
            "The Senate Budget Committee convened for the first time since the vacancy "
            "opened, marking a somber return to regular business for its members. "
            "Senator Darline Graham announced her candidacy for the vacant seat, "
            "telling reporters she would focus on fiscal policy in the term ahead."
        )
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            return {"story": bad if len(calls) == 1 else clean}

        with patch("app.pipeline.analyze.ollama_client.call_llm", side_effect=fake_call_llm):
            from app.pipeline.analyze.action_center import _generate_full_story
            story = _generate_full_story(issue, db_session=db_session)

        assert len(calls) == 2  # first rejected, retry accepted
        assert "brother" not in story
        assert "family relationship" in str(calls[1]["user_prompt"])


class TestGenerateFullStoryFormerStatusGuard:
    """2026-07 stale-training-data class: a full story that demotes a
    sitting official to "former" without source basis must be rejected
    and retried, mirroring the relationship guard above."""

    def test_ungrounded_former_status_rejected_then_clean_retry_accepted(self, db_session):
        issue = ActionIssue(
            date="2026-07-22", rank=1, is_current=True,
            title="President Trump announces new tariffs",
            summary="President Trump announced tariffs on steel imports.",
            facts=json.dumps([
                "President Trump announced tariffs targeting steel imports.",
                "The tariffs take effect next month.",
            ]),
            source_names=json.dumps(["AP News"]),
            policy_areas=json.dumps(["TRADE"]),
        )
        db_session.add(issue)
        db_session.commit()

        bad = (
            "Former President Donald Trump announced new tariffs targeting steel "
            "imports, which are set to take effect next month. The announcement "
            "follows weeks of negotiations between administration officials and "
            "domestic steel producers who had pushed for expanded protections "
            "against foreign competition in the sector."
        )
        clean = (
            "President Trump announced new tariffs targeting steel imports, "
            "which are set to take effect next month. The announcement follows "
            "weeks of negotiations between administration officials and domestic "
            "steel producers who had pushed for expanded protections against "
            "foreign competition in the sector."
        )
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            return {"story": bad if len(calls) == 1 else clean}

        with patch("app.pipeline.analyze.ollama_client.call_llm", side_effect=fake_call_llm):
            from app.pipeline.analyze.action_center import _generate_full_story
            story = _generate_full_story(issue, db_session=db_session)

        assert len(calls) == 2  # first rejected, retry accepted
        assert "Former" not in story
        assert "former" in str(calls[1]["user_prompt"]).lower()


class TestDigestFiltering:
    """An outlet's recurring briefing ("Up First", "Morning news brief") is
    ONE feed item covering several unrelated stories. Left in, it becomes an
    issue whose summary presents separate events as one narrative — live
    2026-07 case (issue 483): a Hamas-Israel agreement, a Trump quote, and a
    slowing US economy joined by a causal "following the announcement" no
    source stated. These have to be dropped at ingest: once the items are in
    one article the topic boundary between them is not recoverable
    downstream (see ACTION_CENTER_PROMPT_VERSION's note on why the
    per-fact mechanical check does not separate them)."""

    @pytest.mark.parametrize("title", [
        "Up First briefing: Israel-Hamas deal, a slowing economy and the Court",
        "Morning news brief",
        "News Brief: Senate vote, border talks, Fed decision",
        "Politics chat: Congress returns from recess",
        "5 things to know about the tariff ruling",
        "Daily rundown for July 30",
        "First up: the Senate vote",
        "NPR News Now newscast",
        "The week in politics: shutdown standoff",
        "Weekly wrap-up of the appropriations fight",
        "Tariffs, the Fed and more:",
        "Congress this week, in brief",
        # A source tag ahead of the product name is still the product name.
        "NPR: Morning briefing",
        # Feeds emit the typographic apostrophe far more often than the
        # ASCII one; matching only the ASCII spelling missed this entirely.
        "Today\u2019s headlines from the Capitol",
        # Live-missed case (issue 492, 2026-08): NPR's real title format for
        # this product leads with "The", which is too long to be absorbed by
        # the source-tag group (no colon of its own within 20 chars) and was
        # not otherwise skipped, so the whole prefix pattern silently failed.
        "The Up First newsletter: Trump weighs birthright citizenship order",
        "An evening briefing on troop levels",
    ])
    def test_recurring_digest_titles_are_dropped(self, title):
        from app.pipeline.analyze.action_center import _digest_reason

        assert _digest_reason(_make_article(title)) == "recurring digest title"

    @pytest.mark.parametrize("title", [
        "House approves Pentagon funding framework in narrow vote",
        "Israel and Hamas reach agreement on hostage release",
        # Product names collide with real reporting mid-headline, so they
        # only count title-initial. All three published as ordinary
        # single-story headlines and were dropped before this was split out.
        "White House news briefing on the Gaza strikes",
        "Trump skipped the President's Daily Brief for a week, officials say",
        "Pentagon holds evening briefing on troop levels",
        "The building blew up first, then the roof collapsed",
        # A hyphenated word is not a source tag, so what follows it is not
        # title-initial either.
        "Wrap-up first look at the budget request",
        # "in brief" / "and more" are digest markers only as a trailing tag;
        # mid-sentence they are ordinary prose, and an unspaced dash is a
        # compound adjective rather than the tag boundary.
        "Senator Collins spoke in brief remarks after the vote",
        "Flooding kills 20 and more than 100 are missing in Texas",
        "Senate passes trade bill and more-restrictive tariff rules",
        # Single-topic forms deliberately left out of the pattern: an
        # explainer and a live blog each cover ONE event.
        "What to know about the new tariff rules",
        "Israel-Hamas war live updates: talks resume in Cairo",
        "The latest: FEMA administrator resigns",
    ])
    def test_single_story_titles_are_kept(self, title):
        from app.pipeline.analyze.action_center import _digest_reason

        assert _digest_reason(_make_article(title)) is None

    @pytest.mark.parametrize("title,body", [
        # Sentence-delimited: the shape of an NPR "Up First" body.
        ("Your Wednesday roundup",
         "Israel and Hamas agreed to a ceasefire framework. The Federal "
         "Reserve held interest rates steady. Wildfires forced evacuations "
         "across Oregon."),
        # Semicolons and bullets: after these, a capitalized first word is a
        # real name rather than grammar, and must not be stripped. Both
        # cases read as a single two-item blurb if it is.
        ("Wednesday",
         "Netanyahu addressed the Knesset; Powell defended the rate "
         "decision; Newsom declared a state of emergency."),
        ("The day ahead",
         "Ukraine aid clears the Senate • Powell signals a pause • Texas "
         "sues over the new map"),
    ])
    def test_bodies_listing_unrelated_stories_are_dropped(self, title, body):
        from app.pipeline.analyze.action_center import _digest_reason

        article = _make_article(title)
        article.summary = body
        assert _digest_reason(article) == "body lists unrelated stories"

    @pytest.mark.parametrize("title,body", [
        # A single story keeps returning to its own subject.
        ("Israel and Hamas reach ceasefire deal",
         "Israel and Hamas reached a deal on Tuesday. The agreement calls "
         "for a phased withdrawal. Hamas said it would release hostages."),
        ("Senate confirms Jane Doe to lead FDA",
         "The Senate voted 51-49 to confirm Jane Doe. Doe will lead the "
         "FDA. Trump praised the outcome."),
        # Sentence-initial capitals are grammar, not names — without the
        # forced-capital strip each of these sentences would look like it
        # introduced a different entity and the blurb would read as a list.
        ("Judge sets October sentencing in fraud case",
         "Prosecutors filed the charges Monday. Defense attorneys called "
         "the case weak. Sentencing is set for October."),
        # Three sentences, three disjoint entity sets, ONE story — caught
        # only by the title-coverage condition, since the headline accounts
        # for all three items.
        ("Grassley releases FBI transcript in Judiciary probe",
         "Sen. Chuck Grassley released the transcript Thursday. The FBI "
         "declined to comment. House Judiciary Democrats called for "
         "hearings."),
        # Bare numbers are not topics.
        ("House clears the funding bill",
         "The vote was 216-212. It came after 3 hours. Final passage is "
         "expected by 5 p.m."),
        ("House sends the bill to the Senate",
         "The House passed the bill. It now goes to the Senate."),
        ("Nothing here", ""),
    ])
    def test_single_story_bodies_are_kept(self, title, body):
        from app.pipeline.analyze.action_center import _digest_reason

        article = _make_article(title)
        article.summary = body
        assert _digest_reason(article) is None

    @pytest.mark.parametrize("title", [
        # A possessive-led headline is the most common shape there is, and
        # _issue_signature keeps "'s" inside the token — so "Trump's" shared
        # nothing with a body saying "Trump" and the headline read as
        # failing to describe its own story. Both apostrophe characters,
        # since the typographic one tokenizes differently again.
        "Trump's tariff order and the Bessent schedule fight",
        "Trump\u2019s tariff order and the Bessent schedule fight",
    ])
    def test_possessive_headline_still_covers_its_own_body(self, title):
        from app.pipeline.analyze.action_center import _multi_topic_body

        body = (
            "The order signed by Trump takes effect Monday; Roberts set "
            "argument for October; Bessent defended the schedule."
        )
        # Covers two of the three items once possessives are normalized.
        assert _multi_topic_body(body, title) is False
        # The same body under a headline that names none of it IS a list,
        # so the assertion above is about coverage, not a benign body.
        assert _multi_topic_body(body, "A quiet Tuesday") is True

    def test_items_sharing_an_entity_are_not_a_list(self):
        """Disjointness is the first of the two conditions and has to reject
        on its own: three items that keep naming the same person are one
        story told in three beats, whatever the headline says."""
        from app.pipeline.analyze.action_center import _multi_topic_body

        body = (
            "Reporters pressed Netanyahu on the deal; Powell defended the "
            "rate decision; Netanyahu answered again hours later."
        )
        assert _multi_topic_body(body, "A quiet Tuesday") is False
        # Same shape with the repeated name swapped out IS a list — the
        # only difference between the two is the shared entity.
        disjoint = body.replace("Netanyahu answered again", "Newsom spoke again")
        assert _multi_topic_body(disjoint, "A quiet Tuesday") is True

    def test_body_cut_at_the_summary_cap_does_not_invent_an_item(self):
        """A description that hit MAX_SUMMARY_CHARS was cut mid-item. The
        fragment names entities that by construction appear nowhere else,
        so counting it turns a two-item blurb into a three-item list."""
        from app.pipeline.analyze.action_center import _multi_topic_body
        from app.pipeline.fetch.news_feeds import MAX_SUMMARY_CHARS

        head = (
            "Israel and Hamas agreed to a ceasefire framework. Wildfires "
            "forced evacuations across Oregon. "
        )
        filler = "The framework runs to many pages of annexes and phased steps. "
        prefix = (head + filler * 10)[:MAX_SUMMARY_CHARS - 22].rstrip()
        body = (prefix + " Mediators from Qatar met in Cairo overnight")[:MAX_SUMMARY_CHARS]

        assert len(body) == MAX_SUMMARY_CHARS
        # Same text one character under the cap is NOT treated as cut, and
        # the trailing fragment does read as a third topic — which is what
        # makes the cap check load-bearing rather than cosmetic.
        assert _multi_topic_body(body[:MAX_SUMMARY_CHARS - 1], "A quiet Tuesday") is True
        assert _multi_topic_body(body, "A quiet Tuesday") is False

    def test_bulleted_body_without_terminal_punctuation_is_still_analyzed(self):
        """Trailing punctuation was the original truncation signal and it
        discarded the final item of every list that ends without a period —
        which is most bulleted lists."""
        from app.pipeline.analyze.action_center import _split_body_items

        body = "Ukraine aid clears the Senate • Powell signals a pause • Texas sues"
        assert len(_split_body_items(body)) == 3

    def test_html_bulleted_digest_survives_parsing_into_the_detector(self):
        """End to end across the two modules, which is where the original
        bug lived. A WordPress-style digest arrives as escaped <li> markup;
        the feed parser has to turn that into item boundaries the detector
        can split on, or the body check sees one undifferentiated blob."""
        from app.pipeline.analyze.action_center import _digest_reason, _split_body_items
        from app.pipeline.fetch.news_feeds import _parse_rss_feed

        article = _parse_rss_feed(
            """<?xml version="1.0"?><rss><channel><item>
              <title>Your Wednesday roundup</title>
              <link>https://example.com/d</link>
              <description>&lt;ul&gt;&lt;li&gt;Israel and Hamas agreed to a framework&lt;/li&gt;&lt;li&gt;The Federal Reserve held rates steady&lt;/li&gt;&lt;li&gt;Wildfires forced evacuations across Oregon&lt;/li&gt;&lt;/ul&gt;</description>
            </item></channel></rss>""".encode(),
            "Test",
        )[0]

        assert "<" not in article.summary
        assert len(_split_body_items(article.summary)) == 3
        assert _digest_reason(article) == "body lists unrelated stories"

    def test_empty_input_short_circuits(self):
        from app.pipeline.analyze.action_center import _filter_policy_relevant

        assert _filter_policy_relevant([]) == []

    def test_digests_are_dropped_before_embedding_and_counted(self):
        from app.pipeline.analyze import action_metrics
        from app.pipeline.analyze.action_center import _filter_policy_relevant

        digest = _make_article("Up First briefing: three stories to start your day")
        story = _make_article("House approves Pentagon funding framework")
        embedded: list[list[str]] = []

        def fake_embed(texts):
            embedded.append(list(texts))
            return np.array([[1.0, 0.0]] * len(texts))

        action_metrics.reset()
        with patch(
            "app.pipeline.analyze.action_center._embed_texts_sim",
            side_effect=fake_embed,
        ):
            kept = _filter_policy_relevant([digest, story])

        assert [a.title for a, _ in kept] == [story.title]
        # The digest never reaches the embedding model at all — the article
        # batch is the third _embed_texts_sim call (after the two prototype
        # sets) and contains only the real story.
        assert not any("Up First" in t for t in embedded[-1])
        assert action_metrics.snapshot()["articles_dropped_digest"] == 1


class TestSimilarityModelGates:
    """2026-07 embedding-swap (step 2): the measured symmetric-similarity
    gates run on the similarity model (see vector_store.
    get_similarity_model). These exercise the swapped call sites with the
    model patched — threshold values themselves were fit on real
    measured distributions (see each constant's comment)."""

    def _fake_model(self, vectors):
        model = MagicMock()
        model.encode.side_effect = vectors
        return model

    def test_embed_texts_sim_uses_similarity_model(self):
        from app.pipeline.analyze.action_center import _embed_texts_sim

        fake = MagicMock()
        fake.encode.return_value = np.array([[1.0, 0.0]])
        with patch("app.pipeline.vector_store.get_similarity_model", return_value=fake):
            out = _embed_texts_sim(["hello"])
        assert out.shape == (1, 2)
        fake.encode.assert_called_once()

    def test_policy_filter_separates_on_measured_scale(self):
        from app.pipeline.analyze.action_center import _filter_policy_relevant

        civic = _make_article("House approves Pentagon funding framework in narrow vote")
        sports = _make_article("Spain defeats Argentina 1-0 in World Cup final")

        def fake_embed(texts):
            # Prototype-space stub reproducing the MEASURED similarity-model
            # scale: civic headline ~0.38 vs prototypes, sports ~0.03.
            if len(texts) > 2 and "Congress" in texts[0]:
                return np.eye(len(texts), 4)[:, :4] if False else np.tile(np.array([1.0, 0.0]), (len(texts), 1))
            out = []
            for t in texts:
                if "Pentagon" in t:
                    out.append([0.38, 0.925])
                else:
                    out.append([0.03, 0.9995])
            return np.array(out)

        with patch("app.pipeline.analyze.action_center._embed_texts_sim", side_effect=fake_embed):
            kept = _filter_policy_relevant([civic, sports])
        assert [a.title for a, _ in kept] == [civic.title]

    def test_trending_boost_runs_on_sim_model(self):
        from app.pipeline.analyze.action_center import _compute_trending_boost
        from app.pipeline.fetch.trending import TrendingTopic

        def fake_embed(texts):
            return np.tile(np.array([1.0, 0.0]), (len(texts), 1))

        clusters = [[_make_article("Senate passes appropriations bill")]]
        trending = [TrendingTopic(title="Senate appropriations fight", source="test")]
        with patch("app.pipeline.analyze.action_center._embed_texts_sim", side_effect=fake_embed):
            boosts = _compute_trending_boost(clusters, trending)
        assert len(boosts) == 1
        assert boosts[0] > 0


def test_get_similarity_model_lazy_singleton():
    from app.pipeline import vector_store

    fake = MagicMock()
    with patch.object(vector_store, "SentenceTransformer", return_value=fake) as ctor:
        vector_store._similarity_model = None
        try:
            first = vector_store.get_similarity_model()
            second = vector_store.get_similarity_model()
        finally:
            vector_store._similarity_model = None
    assert first is fake and second is fake
    ctor.assert_called_once_with(vector_store._SIMILARITY_MODEL_NAME)


class TestCongressGovUrlBuilding:
    """congress.gov bill URLs embed the congress ordinal ("119th", "101st");
    a wrong suffix or wrong congress number is a dead link."""

    def test_ordinal_suffixes(self):
        from app.pipeline.analyze.action_center import _congress_ordinal

        assert _congress_ordinal(119) == "119th"
        assert _congress_ordinal(101) == "101st"
        assert _congress_ordinal(102) == "102nd"
        assert _congress_ordinal(103) == "103rd"
        assert _congress_ordinal(111) == "111th"
        assert _congress_ordinal(112) == "112th"
        assert _congress_ordinal(113) == "113th"
        assert _congress_ordinal(104) == "104th"

    def test_bill_record_uses_records_own_congress(self):
        from app.pipeline.analyze.action_center import _bill_record_to_result

        result = _bill_record_to_result(
            {"type": "HR", "number": "3055", "congress": 101,
             "title": "An old appropriations act"},
            query="appropriations", congress=119,
        )
        assert result is not None
        assert result["url"] == (
            "https://www.congress.gov/bill/101st-congress/house-bill/3055"
        )
        assert result["congress"] == 101
        assert result["id"] == "HR.3055"

    def test_bill_record_falls_back_to_search_congress(self):
        from app.pipeline.analyze.action_center import _bill_record_to_result

        result = _bill_record_to_result(
            {"type": "S", "number": "1234", "title": "A bill"},
            query="a bill", congress=119,
        )
        assert result is not None
        assert result["url"] == (
            "https://www.congress.gov/bill/119th-congress/senate-bill/1234"
        )
        assert result["congress"] == 119

    def test_bill_record_tolerates_string_congress(self):
        from app.pipeline.analyze.action_center import _bill_record_to_result

        result = _bill_record_to_result(
            {"type": "HR", "number": "22", "congress": "119", "title": "SAVE Act"},
            query="SAVE Act", congress=119,
        )
        assert result is not None
        assert "119th-congress" in result["url"]

    def test_resolved_regex_bills_record_current_congress(self):
        from app.config import settings
        from app.pipeline.analyze.action_center import _resolve_bills

        resolved = _resolve_bills([], ["The House passed H.R. 22 yesterday."])

        assert len(resolved) == 1
        assert resolved[0]["id"] == "HR.22"
        assert resolved[0]["congress"] == settings.CURRENT_CONGRESS
        assert (
            f"{settings.CURRENT_CONGRESS}th-congress" in resolved[0]["url"]
        )


class TestCleanupOldUnpostedIssues:
    """Extracted from _run_refresh (2026-07-23) for direct testability —
    also switched its cutoff computation from the module's own utcnow()
    (already correct) to confirm it stays on the canonical clock."""

    def test_old_unposted_issue_is_deleted(self, db_session):
        from app.pipeline.analyze.action_center import _cleanup_old_unposted_issues

        old_date = (utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
        db_session.add(ActionIssue(
            date=old_date, rank=1, title="Old unposted issue", bsky_posted_at=None,
        ))
        db_session.commit()

        deleted = _cleanup_old_unposted_issues(db_session)

        assert deleted == 1
        assert db_session.query(ActionIssue).count() == 0

    def test_old_but_posted_issue_is_preserved(self, db_session):
        from app.pipeline.analyze.action_center import _cleanup_old_unposted_issues

        old_date = (utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
        db_session.add(ActionIssue(
            date=old_date, rank=1, title="Old but posted issue",
            bsky_posted_at=utcnow() - timedelta(days=19),
        ))
        db_session.commit()

        deleted = _cleanup_old_unposted_issues(db_session)

        assert deleted == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_recent_unposted_issue_is_preserved(self, db_session):
        from app.pipeline.analyze.action_center import _cleanup_old_unposted_issues

        recent_date = (utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
        db_session.add(ActionIssue(
            date=recent_date, rank=1, title="Recent unposted issue", bsky_posted_at=None,
        ))
        db_session.commit()

        deleted = _cleanup_old_unposted_issues(db_session)

        assert deleted == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_published_issue_awaiting_a_repost_is_not_deleted(self, db_session):
        # bsky_posted_at is NULL for two different reasons: never published,
        # and published-then-flagged-for-a-repost (_apply_matched_issue_update
        # clears it to hand the issue back to the poster). If that repost then
        # fails to publish, or the story stops being matched, the row sits at
        # NULL while a real post pointing at /issue/<id> is live in the feed —
        # and deleting it 404s a link readers can still click.
        from app.pipeline.analyze.action_center import _cleanup_old_unposted_issues

        old_date = (utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
        db_session.add(ActionIssue(
            date=old_date, rank=1, title="Published, then flagged for a repost",
            bsky_posted_at=None,
            bsky_last_post_text="The House passed the defense bill 216-212.",
        ))
        db_session.commit()

        deleted = _cleanup_old_unposted_issues(db_session)

        assert deleted == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_near_duplicate_suppressed_issue_is_still_deleted(self, db_session):
        # The other side of the same rule: suppression marks the issue handled
        # without publishing anything, so it has no permalink to protect and
        # must not be retained forever by the fix above.
        from app.pipeline.analyze.action_center import _cleanup_old_unposted_issues

        old_date = (utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
        db_session.add(ActionIssue(
            date=old_date, rank=1, title="Suppressed as a near-duplicate",
            bsky_posted_at=None, bsky_last_post_text=None,
            bsky_posted_facts='["nothing new to say"]',
        ))
        db_session.commit()

        deleted = _cleanup_old_unposted_issues(db_session)

        assert deleted == 1
        assert db_session.query(ActionIssue).count() == 0


class TestPeriodicBlueskyPosts:
    """The spotlight and weekly summary read senator scores and the
    timeline, not the news — but they ran only as stage 6 of the refresh,
    downstream of the two early aborts, so a bad hour of feeds silenced
    them too."""

    def test_runs_on_the_no_articles_abort_path(self, db_session):
        from unittest.mock import patch

        import app.pipeline.analyze.action_center as ac

        with patch.object(ac, "fetch_news_articles", return_value=[]), \
                patch.object(ac, "_run_periodic_bluesky_posts") as periodic, \
                patch.object(ac, "_persist_metrics"), \
                patch.object(ac, "_set_refresh_state"):
            assert ac._run_refresh(db_session) == 0

        periodic.assert_called_once()

    def test_runs_on_the_no_relevant_articles_abort_path(self, db_session):
        from unittest.mock import patch

        import app.pipeline.analyze.action_center as ac

        with patch.object(ac, "fetch_news_articles", return_value=["an article"]), \
                patch.object(ac, "_filter_policy_relevant", return_value=[]), \
                patch.object(ac, "_run_periodic_bluesky_posts") as periodic, \
                patch.object(ac, "_persist_metrics"), \
                patch.object(ac, "_set_refresh_state"):
            assert ac._run_refresh(db_session) == 0

        periodic.assert_called_once()

    def test_a_failing_spotlight_never_takes_down_the_refresh(self, db_session):
        from unittest.mock import patch

        from app.pipeline.analyze.action_center import _run_periodic_bluesky_posts

        with patch(
            "app.pipeline.analyze.bluesky_spotlight.post_daily_spotlight",
            side_effect=RuntimeError("bluesky down"),
        ):
            _run_periodic_bluesky_posts(db_session)  # must not raise


class TestRefreshActionIssuesClearsIsRunningOnError:
    """2026-07-27: _run_refresh only cleared the in-memory is_running flag
    on its own explicit return paths, so an uncaught exception mid-run
    (e.g. the national_monitors.slug IntegrityError this session's
    incident hit) left it stuck true. That flag feeds
    /api/admin/pipeline/status, which wedged both this job's own 4h
    staleness override and check-and-deploy.sh's busy-check on the Pi for
    5+ hours, blocking deploys of CI-green commits already on main."""

    def test_is_running_cleared_when_run_refresh_raises(self, db_session):
        import app.pipeline.analyze.action_center as ac

        saved_state = ac.get_action_refresh_state()
        try:
            with patch.object(ac, "_run_refresh", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError):
                    ac.refresh_action_issues(db_session)

            state = ac.get_action_refresh_state()
            assert state["is_running"] is False
            assert state["stage"] is None
        finally:
            ac._set_refresh_state(**saved_state)


class TestPruneStaleApiCache:
    """Extracted from _run_refresh (2026-07-23) for direct testability."""

    def test_old_cache_entry_is_deleted(self, db_session):
        from app.models import ApiCache
        from app.pipeline.analyze.action_center import _prune_stale_api_cache

        db_session.add(ApiCache(
            tier="test-tier", cache_key="old-key", data_json="{}",
            cached_at=utcnow() - timedelta(days=90),
        ))
        db_session.commit()

        deleted = _prune_stale_api_cache(db_session)

        assert deleted == 1
        assert db_session.query(ApiCache).count() == 0

    def test_recent_cache_entry_is_preserved(self, db_session):
        from app.models import ApiCache
        from app.pipeline.analyze.action_center import _prune_stale_api_cache

        db_session.add(ApiCache(
            tier="test-tier", cache_key="recent-key", data_json="{}",
            cached_at=utcnow() - timedelta(days=5),
        ))
        db_session.commit()

        deleted = _prune_stale_api_cache(db_session)

        assert deleted == 0
        assert db_session.query(ApiCache).count() == 1


class TestFullNameMatchingIsWordAnchored:
    """A member's full name used to be matched with a plain substring test
    (`name.lower() in text.lower()`), which lets the name straddle word
    boundaries. That silently re-admitted the exact false positives the bare
    surname path is hardened against by _COMMON_WORD_SURNAMES — and did it
    with the *higher* confidence "named in coverage" reason, so the linked
    member looked more certain, not less.

    Both names below are current members whose surnames are already on the
    stoplist, so the surname pass refuses them correctly; only the full-name
    pass was letting them through."""

    def test_reported_cases_does_not_name_rep_ed_case(self, db_session):
        db_session.add(Representative(
            id="r-case", name="Ed Case", state="HI", party="D",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Measles outbreak widens",
            "Health officials said reported cases rose again this week.",
            [], db_session,
        )

        assert result == []

    def test_several_green_does_not_name_rep_al_green(self, db_session):
        db_session.add(Representative(
            id="r-green", name="Al Green", state="TX", party="D",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Energy package advances",
            "The bill funds several green energy projects across the state.",
            [], db_session,
        )

        assert result == []

    def test_genuine_full_name_mention_still_matches(self, db_session):
        """The anchoring must not cost real matches — these are the same two
        members, actually named."""
        db_session.add(Representative(
            id="r-case", name="Ed Case", state="HI", party="D",
        ))
        db_session.add(Representative(
            id="r-green", name="Al Green", state="TX", party="D",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Hawaii and Texas delegations split on the bill",
            "Rep. Ed Case backed the measure, while Rep. Al Green opposed it.",
            [], db_session,
        )

        assert sorted(r["id"] for r in result) == ["r-case", "r-green"]
        assert {r["match_reason"] for r in result} == {"named in coverage"}

    def test_suffixed_member_name_still_matches(self, db_session):
        """Regression guard for the anchoring itself: a \b-based version of
        this rule drops every member whose name ends in a Jr./Sr. suffix."""
        db_session.add(Senator(
            id="s-king", name="Angus King Jr.", state="ME", party="I",
        ))
        db_session.commit()

        result = _find_related_senators(
            "Maine delegation splits",
            "Sen. Angus King Jr. voted against the measure.",
            [], db_session,
        )

        assert [r["id"] for r in result] == ["s-king"]

    def test_justice_full_name_is_word_anchored_too(self, db_session):
        """Same helper, same guarantee, for the SCOTUS path."""
        db_session.add(Justice(
            id="j-1", name="Amy Coney Barrett", last_name="Barrett",
            appointing_party="R",
        ))
        db_session.commit()

        matched = _find_related_officials(
            "Trade policy shift",
            "The barrettes and other imported goods face new tariffs.",
            [], db_session,
        )

        assert [o for o in matched if o["id"] == "j-1"] == []


class TestMentionsFullName:
    """Unit-level coverage of the boundary rule itself."""

    @pytest.mark.parametrize("text,name,expected", [
        ("Health officials cited reported cases.", "Ed Case", False),
        ("It funds several green projects.", "Al Green", False),
        ("Rep. Ed Case said so.", "Ed Case", True),
        ("ED CASE voted no.", "Ed Case", True),
        ("Signed by Al Green, the letter...", "Al Green", True),
        ("A profile of Al Greene, the singer.", "Al Green", False),
        ("", "Ed Case", False),
        ("Some text", "", False),
        # A name ending in a non-word character: \b would assert a transition
        # *after* the period and refuse every one of these. Sen. Angus King's
        # own FEC record carries the suffix, so this is live data, not a
        # hypothetical.
        ("Sen. Angus King Jr. voted no.", "Angus King Jr.", True),
        ("Rep. Donald Payne Jr. introduced it.", "Donald Payne Jr.", True),
        ("A statement from Harold Rogers Jr.", "Harold Rogers Jr.", True),
        ("Rep. Robert F. Kennedy spoke.", "Robert F. Kennedy", True),
        ("Rep. Alexandria Ocasio-Cortez spoke.", "Alexandria Ocasio-Cortez", True),
        ("Sen. Beto O'Rourke spoke.", "Beto O'Rourke", True),
    ])
    def test_boundary_rule(self, text, name, expected):
        assert _mentions_full_name(text, name) is expected
