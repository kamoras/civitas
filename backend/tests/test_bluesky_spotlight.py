"""Unit tests for bluesky_spotlight helpers.

_most_notable_score is a pure function (no LLM, no network) — it decides
server-side which of the five score dimensions is worth emphasizing,
instead of leaving that choice and its framing to the model.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models import Senator, TimelineEntry, WeekSummary
from app.pipeline.analyze.bluesky_spotlight import (
    _generate_spotlight_post,
    _generate_weekly_post,
    _most_notable_score,
    _publish_spotlight,
    _publish_weekly,
    _week_label,
    _week_timeline_context,
    _weekly_body_budget,
    _weekly_header,
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

    def test_weekly_post_rejects_ungrounded_former_status(self, db_session):
        week = WeekSummary(
            start_date="2026-07-13", end_date="2026-07-19",
            summary="The Senate passed a funding bill on a 68-32 vote.",
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "Former Senator Smith praised the funding bill this week."},
        ):
            text = _generate_weekly_post(week, db_session)

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

    @pytest.mark.parametrize("start,end", [
        ("2026-07-13", "2026-07-19"),   # same-month label
        ("2025-12-29", "2026-01-04"),   # month- and year-spanning label
        ("", ""),                        # unparseable — header drops the parenthetical
    ])
    def test_a_full_length_body_is_published_without_truncation(self, start, end):
        # publish_post truncates unconditionally to fit the 300-char cap, so
        # asserting len(posted) <= 300 can never fail and says nothing. What
        # matters is that a body at the advertised budget survives intact:
        # if the header or URL is reworded past the budget, the tail of a real
        # post starts silently disappearing.
        week = WeekSummary(year=2026, week_num=29, start_date=start, end_date=end)
        header = _weekly_header(week)
        body = "a" * _weekly_body_budget(header)

        posted_text = _post_weekly(f"{header} {body}", week)

        assert len(posted_text) <= BSKY_MAX_CHARS
        assert body in posted_text, "the body was truncated — the budget is too generous"
        assert posted_text.startswith(header)


class TestWeeklyPostFraming:
    """A weekly post read as a stray news bulletin — nothing in it said the
    text was a recap of a week."""

    def test_post_is_labeled_as_a_week_summary(self, db_session):
        week = WeekSummary(
            year=2026, week_num=29, start_date="2026-07-13", end_date="2026-07-19",
            summary="The Senate passed a funding bill.",
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "The Senate passed a funding bill."},
        ):
            text = _generate_weekly_post(week, db_session)

        assert text.startswith("Week in review (Jul 13–19):")
        assert text.endswith("The Senate passed a funding bill.")

    def test_header_does_not_claim_last_week(self, db_session):
        # post_weekly_summary posts the most recent *summarized* week, and a
        # gap in timeline entries leaves a week with no WeekSummary at all —
        # so the week going out is not reliably the week just gone.
        week = WeekSummary(
            year=2026, week_num=29, start_date="2026-07-13", end_date="2026-07-19",
            summary="The Senate passed a funding bill.",
        )
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "The Senate passed a funding bill."},
        ):
            text = _generate_weekly_post(week, db_session)

        assert "last week" not in text.lower()

    def test_unparseable_dates_drop_the_parenthetical_rather_than_publish_it(self):
        # The label is published now, so the old raw-range fallback would put
        # "(  –  )" in the feed and overflow the character budget.
        header = _weekly_header(WeekSummary(start_date="", end_date=""))

        assert header == "Week in review:"

    @pytest.mark.parametrize("post", [
        "Last week the Senate passed a funding bill.",
        "This week the Senate passed a funding bill.",
        "Jul 13–19 saw the Senate pass a funding bill.",
    ])
    def test_bodies_that_restate_the_header_timeframe_are_rejected(self, db_session, post):
        # Rule 6 is prompt-only otherwise, and this module's own comments say
        # prompt-only rules aren't reliably followed. The visible failure is
        # "Week in review (Jul 13–19): Last week, the Senate…".
        week = _seed_week(db_session)
        with patch("app.pipeline.analyze.bluesky_spotlight.call_llm", return_value={"post": post}):
            assert _generate_weekly_post(week, db_session) is None


def _seed_week(db_session, **overrides) -> WeekSummary:
    """A week summary plus the three timeline days it was built from."""
    for day, title, summary in (
        ("2026-07-13", "Senate passes funding bill", "The chamber cleared it on a 68-32 vote."),
        ("2026-07-15", "Court blocks tariff order", "A federal judge stayed the order pending review."),
        ("2026-07-17", "House opens oversight inquiry", "The committee requested documents."),
    ):
        db_session.add(TimelineEntry(
            date=day, title=title, summary=summary, policy_areas='["Economics", "Law"]',
        ))
    fields = {
        "year": 2026, "week_num": 29,
        "start_date": "2026-07-13", "end_date": "2026-07-19",
        "summary": "Spending and trade dominated the week.",
        "top_policy_areas": '["Economics", "Law"]',
        "entry_count": 3,
    }
    fields.update(overrides)
    week = WeekSummary(**fields)
    db_session.add(week)
    db_session.commit()
    return week


class TestWeekTimelineContext:
    """The post used to see only week.summary — itself a 2-3 sentence digest
    of these same days — so the specific votes and rulings it was asked to
    name had already been compressed out before the model saw anything."""

    def test_prompt_carries_the_published_week_summary(self, db_session):
        week = _seed_week(db_session)
        assert "Spending and trade dominated the week." in _week_timeline_context(week, db_session).prompt

    def test_prompt_carries_the_days_the_summary_was_built_from(self, db_session):
        week = _seed_week(db_session)

        prompt = _week_timeline_context(week, db_session).prompt

        assert "Senate passes funding bill" in prompt
        assert "Court blocks tariff order" in prompt
        assert "House opens oversight inquiry" in prompt

    def test_prompt_carries_the_weeks_policy_areas(self, db_session):
        week = _seed_week(db_session)
        assert "Economics, Law" in _week_timeline_context(week, db_session).prompt

    def test_days_are_labelled_by_weekday_not_iso_date(self, db_session):
        # ISO dates read robotically when the model echoes them, and their
        # digits used to leak into the grounding source (see
        # TestWeeklyGroundingSource).
        week = _seed_week(db_session)

        prompt = _week_timeline_context(week, db_session).prompt

        assert "[Mon]" in prompt
        assert "2026-07-13" not in prompt

    def test_days_outside_the_week_are_left_out(self, db_session):
        week = _seed_week(db_session)
        db_session.add(TimelineEntry(date="2026-07-20", title="Next week's news", summary=""))
        db_session.commit()

        assert "Next week's news" not in _week_timeline_context(week, db_session).prompt

    def test_unparseable_week_bounds_do_not_sweep_in_unrelated_days(self, db_session):
        # The range filter is a string comparison, so an empty start_date is
        # <= every date on record and would pull years of unrelated entries
        # into the prompt labelled "that week".
        week = _seed_week(db_session, start_date="")
        db_session.add(TimelineEntry(date="2019-03-04", title="Ancient history", summary=""))
        db_session.commit()

        prompt = _week_timeline_context(week, db_session).prompt

        assert "Ancient history" not in prompt
        assert "days that week" not in prompt

    def test_days_still_reach_the_model_when_the_summary_is_missing(self, db_session):
        # generate_period_summaries stores summary="" when its own LLM call
        # fails, which used to leave the post with nothing to work from.
        week = _seed_week(db_session, summary="")

        assert "Senate passes funding bill" in _week_timeline_context(week, db_session).prompt

    @pytest.mark.parametrize("stored", ["not json", '"Economics"', "5"])
    def test_unusable_policy_areas_do_not_break_the_prompt(self, db_session, stored):
        # A JSON scalar parses fine but would be joined character by
        # character into "E, c, o, n, o, m, i, c, s".
        week = _seed_week(db_session, top_policy_areas=stored)

        prompt = _week_timeline_context(week, db_session).prompt

        assert "Dominant policy areas" not in prompt
        assert "Senate passes funding bill" in prompt


class TestWeeklyGroundingSource:
    """Grounding reads its source as a bag of digit tokens, so checking the
    post against the whole prompt let anything printed in the prompt vouch
    for a figure — including the model's own character-limit rule and the
    day-entry dates. A fabricated vote tally rode out on an ISO date."""

    def test_sources_exclude_the_day_dates(self, db_session):
        sources = _week_timeline_context(_seed_week(db_session), db_session).sources

        assert "2026" not in sources
        assert "Senate passes funding bill" in sources

    @pytest.mark.parametrize("post", [
        "The Senate cleared the funding bill 17-13 and a judge stayed the tariff order.",
        "The oversight inquiry covers 15 percent of the agency's contracts.",
        "The judge stayed a tariff order first issued in 2026.",
    ])
    def test_figures_licensed_only_by_a_date_are_rejected(self, db_session, post):
        # Every digit here appears in the week's ISO dates (2026-07-13..19)
        # and nowhere in the timeline text.
        week = _seed_week(db_session)
        with patch("app.pipeline.analyze.bluesky_spotlight.call_llm", return_value={"post": post}):
            assert _generate_weekly_post(week, db_session) is None

    def test_an_official_not_in_the_timeline_is_rejected(self, db_session):
        # Rule 3 asks the model to name who acted, so the weekly path needs
        # the same titled-name check the other posters run.
        week = _seed_week(db_session)
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "Sen. Wexlerton led the funding bill through the chamber."},
        ):
            assert _generate_weekly_post(week, db_session) is None


class TestWeeklyPostUsesTimelineDays:
    def test_a_figure_from_a_day_entry_is_grounded(self, db_session):
        # The heart of the change: "68-32" appears only in Monday's timeline
        # entry, never in the week summary. Feeding the model just the
        # summary made any such figure look invented, so the grounding check
        # rejected exactly the specific detail rule 3 asks for.
        week = _seed_week(db_session)
        with patch(
            "app.pipeline.analyze.bluesky_spotlight.call_llm",
            return_value={"post": "The Senate cleared a funding bill 68-32 and a judge stayed the tariff order."},
        ):
            text = _generate_weekly_post(week, db_session)

        assert text is not None
        assert "68-32" in text

    def test_no_timeline_material_skips_the_post_without_calling_the_model(self, db_session):
        week = WeekSummary(
            year=2026, week_num=30, start_date="2026-07-20", end_date="2026-07-26",
            summary="", top_policy_areas="[]",
        )
        with patch("app.pipeline.analyze.bluesky_spotlight.call_llm") as mock_llm:
            text = _generate_weekly_post(week, db_session)

        assert text is None
        mock_llm.assert_not_called()


class TestWeekLabel:
    def test_single_month_week_names_the_month_once(self):
        week = WeekSummary(start_date="2026-07-13", end_date="2026-07-19")
        assert _week_label(week) == "Jul 13–19"

    def test_month_spanning_week_names_both_months(self):
        # The label is published, not just prompt context — "Jun 29–5" would
        # read as a five-week span.
        week = WeekSummary(start_date="2026-06-29", end_date="2026-07-05")
        assert _week_label(week) == "Jun 29–Jul 5"

    @pytest.mark.parametrize("start,end", [("", ""), ("2026-13-45", "2026-13-99"), (None, None)])
    def test_unusable_dates_yield_no_label(self, start, end):
        assert _week_label(WeekSummary(start_date=start, end_date=end)) is None
