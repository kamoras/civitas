"""Unit tests for bluesky_spotlight helpers.

_most_notable_score is a pure function (no LLM, no network) — it decides
server-side which of the five score dimensions is worth emphasizing,
instead of leaving that choice and its framing to the model.
"""

from unittest.mock import MagicMock, patch

from app.models import Senator
from app.pipeline.analyze.bluesky_spotlight import _most_notable_score, _publish_spotlight


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
