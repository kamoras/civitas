"""Tests for Action Center deduplication and national monitor creation logic."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models import ActionIssue, ExploreDocument, Justice, NationalMonitor, Representative, Senator
from app.pipeline.fetch.news_feeds import NewsArticle
from app.pipeline.analyze.action_center import (
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
    _fix_impossible_senate_vote_counts,
    _is_exact_content_duplicate,
    _issue_signature,
    _largest_coherent_subgroup,
    _signatures_match,
    _surname_owned_by_other_name,
    _validate_facts,
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
        # confirmed reversal should trigger a retry.
        mock_call_llm.return_value = "not valid json and no accurate key"
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_empty_response_fails_open(self, mock_call_llm):
        mock_call_llm.return_value = None
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_llm_exception_fails_open(self, mock_call_llm):
        mock_call_llm.side_effect = RuntimeError("LLM backend unreachable")
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True

    @patch("app.pipeline.analyze.ollama_client.call_llm")
    def test_missing_accurate_key_defaults_to_true(self, mock_call_llm):
        # A dict response with no "accurate" key at all shouldn't be treated
        # as a reversal — only an explicit accurate:false should.
        mock_call_llm.return_value = json.dumps({"reason": "unrelated"})
        mock_db = MagicMock()

        accurate, reason = _check_summary_roles("Some summary.", "source text", mock_db)

        assert accurate is True


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

    def test_find_matching_issue_returns_none_when_already_claimed_this_run(self):
        title = "Republicans introduce crypto legislation with ethical clause"
        facts = ["A new bill text was released by Republican representatives."]
        existing = ActionIssue(id=420, date="2026-07-23", rank=2, title=title, facts=json.dumps(facts))
        recent_embs = np.array([[1.0, 0.0]])
        title_emb = np.array([1.0, 0.0])

        match = _find_matching_issue(title, facts, [existing], recent_embs, title_emb, {420})
        assert match is None


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
