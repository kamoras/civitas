"""Tests for bluesky_utils.publish_post — the shared posting body every
Bluesky poster (issue posts, spotlight, weekly summary, election posts)
funnels through."""

from unittest.mock import MagicMock, patch

from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, publish_post


def _post(text: str, url: str = "https://civitas-research.org/issue/i9e3779b1") -> str:
    """Run publish_post against a stubbed Bluesky client, returning the
    text that would have been posted. Same patch set as
    test_bluesky_spotlight.py's _post_weekly helper."""
    with patch("app.pipeline.analyze.bluesky_utils.settings") as mock_settings, \
         patch("app.pipeline.analyze.bluesky_utils.build_link_card", return_value=None), \
         patch("atproto.Client") as mock_client_cls:
        mock_settings.BSKY_HANDLE = "civitas-research.org"
        mock_settings.BSKY_APP_PASSWORD = "unused-in-test"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        result = publish_post(text, url, success_msg="posted", error_context="test")

    assert result is True
    return mock_client.send_post.call_args.args[0]


class TestLinkSeparator:
    # Confirmed live against NPR's own Bluesky posts (public.api.bsky.app
    # getAuthorFeed): a single space before the trailing link, not a
    # blank line — e.g. "...at age 53. n.pr/4h4XVR4".
    def test_a_short_post_separates_text_and_url_with_a_single_space(self):
        posted = _post("A short post.")
        assert posted == "A short post. https://civitas-research.org/issue/i9e3779b1"

    def test_no_blank_line_before_the_url(self):
        posted = _post("A short post.")
        assert "\n\n" not in posted


class TestTruncation:
    def test_a_body_over_budget_is_truncated_and_still_fits(self):
        url = "https://civitas-research.org/issue/i9e3779b1"
        long_body = "This is a sentence. " * 30  # comfortably over 300 chars with the url
        posted = _post(long_body, url=url)

        assert len(posted) <= BSKY_MAX_CHARS
        assert posted.endswith(url)
        # Exactly one space, not two, immediately before the url.
        assert posted[: -len(url)].endswith(" ")
        assert not posted[: -len(url)].endswith("  ")

    def test_a_body_that_fits_within_budget_is_not_truncated(self):
        url = "https://civitas-research.org/issue/i9e3779b1"
        body = "a" * (BSKY_MAX_CHARS - len(url) - 1)  # exactly at the budget boundary
        posted = _post(body, url=url)

        assert body in posted
        assert len(posted) <= BSKY_MAX_CHARS


class TestMissingCredentials:
    def test_returns_false_and_never_calls_the_client_without_credentials(self):
        with patch("app.pipeline.analyze.bluesky_utils.settings") as mock_settings, \
             patch("atproto.Client") as mock_client_cls:
            mock_settings.BSKY_HANDLE = ""
            mock_settings.BSKY_APP_PASSWORD = ""

            result = publish_post("text", "https://example.com", success_msg="posted", error_context="test")

        assert result is False
        mock_client_cls.assert_not_called()
