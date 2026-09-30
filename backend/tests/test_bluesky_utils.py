"""Tests for bluesky_utils.publish_post — the shared posting body every
Bluesky poster (issue posts, spotlight, weekly summary, election posts)
funnels through."""

from unittest.mock import MagicMock, patch

from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, fetch_og_card, og_card, publish_post


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
    # blank line — e.g. "...at age 53. n.pr/4h4XVR4". The exact match
    # below also rules out a "\n\n" separator.
    def test_a_short_post_separates_text_and_url_with_a_single_space(self):
        posted = _post("A short post.")
        assert posted == "A short post. https://civitas-research.org/issue/i9e3779b1"


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

    def test_hard_cut_lands_exactly_at_the_budget_boundary(self):
        # No punctuation and no space anywhere in the body, so
        # truncate_on_boundary can't find a sentence or word boundary and
        # falls through to its hard-cut path: `trimmed` (== budget chars)
        # exactly. A body built from repeated short sentences (as in the
        # test above) lands on a sentence boundary well short of the real
        # budget regardless of a small off-by-one in the budget math —
        # this one pins the exact final length instead, so a
        # `- 1` vs `- 2` regression in publish_post's separator accounting
        # would actually fail it.
        url = "https://civitas-research.org/issue/i9e3779b1"
        long_body = "a" * 1000
        posted = _post(long_body, url=url)

        assert len(posted) == BSKY_MAX_CHARS
        assert posted.endswith(f" {url}")

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


def test_og_card_reads_the_card_both_attribute_orders_and_entities():
    html = (
        '<meta property="og:title" content="Maine Ballot 2026 &amp; More — Civitas"/>'
        '<meta content="What&#x27;s on the ballot." property="og:description"/>'
        '<meta property="og:image" content="https://civitas-research.org/api/og?state=ME"/>'
        '<meta property="og:image:alt" content="Maine Ballot 2026"/>'
    )
    assert og_card(html) == {
        "title": "Maine Ballot 2026 & More — Civitas",
        "description": "What's on the ballot.",
        "image": "https://civitas-research.org/api/og?state=ME",
        "image_alt": "Maine Ballot 2026",
    }
    assert og_card("<html></html>") == {"title": "", "description": "", "image": "", "image_alt": ""}


def test_fetch_og_card_reads_the_page_or_says_it_couldnt():
    page = MagicMock(text='<meta property="og:image" content="https://civitas-research.org/api/og?issue=x"/>')
    with patch("app.pipeline.analyze.bluesky_utils.httpx.get", return_value=page):
        assert fetch_og_card("https://civitas-research.org/issue/x")["image"] == "https://civitas-research.org/api/og?issue=x"
    with patch("app.pipeline.analyze.bluesky_utils.httpx.get", side_effect=OSError("down")):
        assert fetch_og_card("https://civitas-research.org/issue/x") is None
