"""Tests for Action Center deduplication and national monitor creation logic."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models import ActionIssue, ExploreDocument, NationalMonitor
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
    _fix_impossible_senate_vote_counts,
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

    def test_corrects_impossible_senate_vote_to_house(self):
        text = "The bill passed the Senate with a vote of 226-195."
        assert _fix_impossible_senate_vote_counts(text) == "The bill passed the House with a vote of 226-195."

    def test_corrects_across_word_variants_of_tally(self):
        text = "The proposal gained traction in the Senate, where it passed with a vote of 226 to 195."
        result = _fix_impossible_senate_vote_counts(text)
        assert "House" in result
        assert "passed with a vote of 226 to 195" in result

    def test_leaves_plausible_senate_vote_unchanged(self):
        # 51 + 49 = 100, exactly at the Senate's ceiling — plausible.
        text = "The bill passed the Senate with a vote of 51-49."
        assert _fix_impossible_senate_vote_counts(text) == text

    def test_leaves_already_correct_house_mention_unchanged(self):
        text = "The bill passed the House 226-195 and now moves to the Senate for consideration."
        assert _fix_impossible_senate_vote_counts(text) == text

    def test_empty_string_returns_unchanged(self):
        assert _fix_impossible_senate_vote_counts("") == ""

    def test_no_vote_tally_returns_unchanged(self):
        text = "The Senate is expected to take up the bill next week."
        assert _fix_impossible_senate_vote_counts(text) == text


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

    @patch("app.pipeline.analyze.action_center._embed_texts")
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

    @patch("app.pipeline.analyze.action_center._embed_texts")
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

    @patch("app.pipeline.analyze.action_center._embed_texts")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_distance_at_old_threshold_now_rejected(self, mock_search, mock_embed, db_session):
        self._seed_doc(db_session, 1, "Certain Steel Products From China: Preliminary Results")
        mock_search.return_value = [
            {"id": 1, "title": "Certain Steel Products From China: Preliminary Results", "distance": 0.95},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs("Sports story", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_similarity_at_old_threshold_now_rejected(self, mock_search, mock_embed, db_session):
        self._seed_doc(db_session, 1, "Certain Steel Products From China: Preliminary Results")
        mock_search.return_value = [
            {"id": 1, "title": "Certain Steel Products From China: Preliminary Results", "distance": 0.5},
        ]
        # cos_sim ~= 0.60 — well above the old 0.40 floor, well below the
        # new 0.75 one.
        mock_embed.return_value = np.array([[1.0, 0.0], [0.60, 0.80]])

        result = _find_related_explore_docs("Sports story", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts")
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

    @patch("app.pipeline.analyze.action_center._embed_texts")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_information_collection_notice_rejected_despite_high_similarity(
        self, mock_search, mock_embed, db_session,
    ):
        self._seed_doc(db_session, 1, "Agency Information Collection Activities; Proposed eCollection")
        mock_search.return_value = [
            {"id": 1, "title": "Agency Information Collection Activities; Proposed eCollection", "distance": 0.70},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs("Some unrelated issue", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts")
    @patch("app.pipeline.analyze.action_center.search_explore_documents")
    def test_advisory_committee_meeting_notice_rejected_despite_high_similarity(
        self, mock_search, mock_embed, db_session,
    ):
        self._seed_doc(db_session, 1, "Notice of Public Meeting of the Montana Advisory Committee")
        mock_search.return_value = [
            {"id": 1, "title": "Notice of Public Meeting of the Montana Advisory Committee", "distance": 0.70},
        ]
        mock_embed.return_value = np.array([[1.0, 0.0], [0.95, 0.05]])

        result = _find_related_explore_docs("Attorney General independence", "summary", [], db_session)
        assert result == []

    @patch("app.pipeline.analyze.action_center._embed_texts")
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
