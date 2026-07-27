"""Unit tests for bluesky_spotlight helpers.

_most_notable_score is a pure function (no LLM, no network) — it decides
server-side which of the five score dimensions is worth emphasizing,
instead of leaving that choice and its framing to the model.
"""

from unittest.mock import MagicMock, patch

from app.models import Senator, WeekSummary
from app.pipeline.analyze.bluesky_spotlight import (
    MAX_WEEKLY_CHARS,
    _generate_spotlight_post,
    _generate_weekly_post,
    _most_notable_score,
    _publish_spotlight,
    _publish_weekly,
    _week_label,
)
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS


def _scores(**overrides):
    base = {
        "Funding independence": 50.0,
        "Promise persistence": 50.0,
        "Independent voting": 50.0,
        "Funding diversity": 50.0,
        "Legislative effectiveness": 50.0,
    }
    base.update(overrides)
    return base


class TestMostNotableScore:
    def test_high_score_is_notable(self):
        key, value, notable = _most_notable_score(_scores(**{"Funding independence": 89.0}))
        assert key == "Funding independence"
        assert value == 89.0
        assert notable is True

    def test_low_score_is_notable(self):
        key, value, notable = _most_notable_score(_scores(**{"Independent voting": 22.0}))
        assert key == "Independent voting"
        assert notable is True

    def test_all_middling_scores_not_notable(self):
        # This is the shape a shrinkage-compressed dimension produces: every
        # score sits close to 50, so even the furthest-from-neutral one
        # isn't a real standout and shouldn't be praised as one.
        scores = _scores(**{
            "Funding independence": 49.0,
            "Promise persistence": 56.0,
            "Independent voting": 49.0,
            "Funding diversity": 49.0,
            "Legislative effectiveness": 49.0,
        })
        key, value, notable = _most_notable_score(scores)
        assert key == "Promise persistence"
        assert value == 56.0
        assert notable is False

    def test_deviation_exactly_at_threshold_is_notable(self):
        _, _, notable = _most_notable_score(_scores(**{"Funding diversity": 70.0}))
        assert notable is True

    def test_deviation_just_under_threshold_not_notable(self):
        _, _, notable = _most_notable_score(_scores(**{"Funding diversity": 69.9}))
        assert notable is False

    def test_ties_pick_a_consistent_dimension(self):
        # Two dimensions equally deviant — max() picks the first in
        # iteration order deterministically, not arbitrarily per-call.
        scores = _scores(**{"Funding independence": 80.0, "Legislative effectiveness": 80.0})
        key, _, _ = _most_notable_score(scores)
        assert key == "Funding independence"


class TestFormerOfficialStatusGrounding:
    """2026-07 stale-training-data class ("former President Donald Trump"
    published while the source said "President Trump") — same mechanical
    backstop wired into the spotlight and weekly-summary posters as the
    issue poster and full-story generator."""

    def test_spotlight_rejects_ungrounded_former_status(self):
        senator = Senator(
            id="chuck-grassley", name="Chuck Grassley", state="IA", party="R",
            score_funding_independence=50.0, score_independent_voting=50.0,
            score_legislative_effectiveness=50.0,
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "Former Senator Chuck Grassley ranks #1 of 100 senators."},
        ):
            text = _generate_spotlight_post(senator, rank=1, total=100)

        assert text is None

    def test_weekly_post_rejects_ungrounded_former_status(self):
        week = WeekSummary(
            start_date="2026-07-13", end_date="2026-07-19",
            summary="The Senate passed a funding bill on a 68-32 vote.",
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "Former Senator Smith praised the funding bill this week."},
        ):
            text = _generate_weekly_post(week)

        assert text is None


class TestPublishSpotlightUrl:
    """The spotlight post's link previously pointed at the old
    /scorecard?branch=senate&state=..&senator=.. query-param route instead
    of the current /politicians/{id} profile page (reported live via a
    Bluesky post 2026-07-13)."""

    def test_links_to_politicians_profile_not_old_scorecard_route(self):
        senator = Senator(id="chuck-grassley", name="Chuck Grassley", state="IA", party="R")

        # _publish_spotlight delegates to the shared bluesky_utils.publish_post,
        # which reads its own `settings` import and calls build_link_card
        # within its own module — patch both there, not on bluesky_spotlight
        # (which no longer references either directly for this path).
        with patch("app.pipeline.analyze.bluesky_utils.settings") as mock_settings, \
             patch("app.pipeline.analyze.bluesky_utils.build_link_card", return_value=None), \
             patch("atproto.Client") as mock_client_cls:
            mock_settings.BSKY_HANDLE = "civitas-research.org"
            mock_settings.BSKY_APP_PASSWORD = "unused-in-test"
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            result = _publish_spotlight("Some spotlight text.", senator)

        assert result is True
        posted_text = mock_client.send_post.call_args.args[0]
        assert "/politicians/chuck-grassley" in posted_text
        assert "/scorecard?" not in posted_text


def _post_weekly(text: str, week: WeekSummary) -> str:
    """Run _publish_weekly against a stubbed Bluesky client, returning the
    text that would have been posted. Mirrors the patch set used above."""
    with patch("app.pipeline.analyze.bluesky_utils.settings") as mock_settings, \
         patch("app.pipeline.analyze.bluesky_utils.build_link_card", return_value=None), \
         patch("atproto.Client") as mock_client_cls:
        mock_settings.BSKY_HANDLE = "civitas-research.org"
        mock_settings.BSKY_APP_PASSWORD = "unused-in-test"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        assert _publish_weekly(text, week) is True

    return mock_client.send_post.call_args.args[0]


class TestPublishWeeklyUrl:
    """The weekly summary linked to a bare /timeline path, which is not a
    route — the timeline is a tab on the action page, so the link 404'd."""

    def test_links_to_the_timeline_tab_not_a_bare_timeline_path(self):
        week = WeekSummary(year=2026, week_num=29, start_date="2026-07-13", end_date="2026-07-19")

        posted_text = _post_weekly("Congress voted on a funding bill.", week)

        assert "/action?tab=timeline" in posted_text
        assert "civitas-research.org/timeline" not in posted_text

    def test_header_and_url_leave_the_post_under_the_bluesky_limit(self):
        # The header and URL are spent before the model's text is; a
        # max-length body must still fit without the shared publisher having
        # to truncate it.
        week = WeekSummary(year=2025, week_num=1, start_date="2025-12-29", end_date="2026-01-04")
        body = f"{_week_label(week)}: " + "a" * MAX_WEEKLY_CHARS

        posted_text = _post_weekly(body, week)

        assert len(posted_text) <= BSKY_MAX_CHARS


class TestWeeklyPostFraming:
    """A weekly post read as a stray news bulletin — nothing in it said the
    text was a recap of the week just ended."""

    def test_post_is_labeled_as_a_summary_of_the_completed_week(self):
        week = WeekSummary(
            year=2026, week_num=29, start_date="2026-07-13", end_date="2026-07-19",
            summary="The Senate passed a funding bill.",
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "The Senate passed a funding bill."},
        ):
            text = _generate_weekly_post(week)

        assert text.startswith("Last week in review (Jul 13–19):")
        assert text.endswith("The Senate passed a funding bill.")


class TestWeekLabel:
    def test_single_month_week_names_the_month_once(self):
        week = WeekSummary(start_date="2026-07-13", end_date="2026-07-19")
        assert _week_label(week) == "Jul 13–19"

    def test_month_spanning_week_names_both_months(self):
        # The label is published, not just prompt context — "Jun 29–5" would
        # read as a five-week span.
        week = WeekSummary(start_date="2026-06-29", end_date="2026-07-05")
        assert _week_label(week) == "Jun 29–Jul 5"

    def test_unparseable_dates_fall_back_to_the_raw_range(self):
        week = WeekSummary(start_date="", end_date="")
        assert _week_label(week) == " – "
