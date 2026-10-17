"""news_digest.digest_reason: the rules measured on 2026-10-17 over the
Action Center's stored feed articles and the stored election coverage."""

import pytest

from app.pipeline.fetch.news_digest import digest_reason
from app.pipeline.fetch.news_feeds import NewsArticle


def _article(title, summary="", url="https://example.com/2026/10/17/story"):
    return NewsArticle(title=title, url=url, source_name="Outlet", summary=summary)


@pytest.mark.parametrize("title", [
    "Corbin, Jarvis clash in first Maine Senate debate: 3 takeaways",
    "4 takeaways from the Gluesenkamp-Perez debate",
])
def test_one_events_takeaways_are_one_story(title):
    assert digest_reason(_article(title, "The two candidates met Tuesday.")) is None


def test_photos_of_the_week_is_a_digest():
    assert digest_reason(_article("Photos of the week | August 1-6, 2026")) == "recurring digest title"


def test_a_fragment_the_publisher_cut_is_not_an_item():
    """The Hill ends its descriptions mid-sentence with "...": the fragment
    names entities found nowhere else, which made one story read as a list."""
    body = (
        "Sen. Thom Tillis (R-N.C.) is backing Republican candidate Michael Whatley for the Senate seat. "
        "Whatley is in a tight race against former Gov. Roy Cooper (D), who is favored to win. "
        "Tillis told NewsNation, the Hill's sister network, on Tuesday that..."
    )
    assert digest_reason(_article("Tillis backs Whatley in North Carolina Senate race", body)) is None


def test_a_pbs_sign_off_is_not_an_item():
    body = (
        "The latest polling shows Democrats with a solid shot of regaining control of the House. "
        "If they do, House Minority Leader Hakeem Jeffries could become Speaker. "
        "Jeffries joined Geoff Bennett to discuss those issues."
    )
    assert digest_reason(_article("Top priorities if Democrats take House, Jeffries says", body)) is None


def test_a_newsletter_is_still_a_digest():
    article = _article(
        "Hale and Rivas-Cole face off in Michigan",
        "In today's issue: ▪ Zacharia confirms she didn't pass bar exam ▪ Livestreamed execution called off",
        url="https://thehill.com/newsletters/morning-report/6153128-vance/",
    )
    assert digest_reason(article) == "newsletter section"
