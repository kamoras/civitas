"""Tests for news feed parsing: pubDate timezone robustness and the
HTML stripping applied to feed descriptions."""

from datetime import timezone
from xml.sax.saxutils import escape

from app.pipeline.fetch.news_feeds import (
    MAX_SUMMARY_CHARS,
    _parse_pub_date,
    _parse_rss_feed,
    _strip_html,
)


class TestParsePubDate:
    def test_rfc2822_with_offset(self):
        d = _parse_pub_date("Mon, 20 Jul 2026 12:00:00 +0000")
        assert d is not None and d.tzinfo is not None

    def test_minus_zero_zone_is_made_aware(self):
        """parsedate_to_datetime returns a NAIVE datetime for '-0000'-style
        zones; comparing that to the aware cutoff raised TypeError and the
        caller's blanket except dropped the entire feed as 'failed to
        fetch'. Naive results must be coerced to UTC."""
        d = _parse_pub_date("Mon, 20 Jul 2026 12:00:00 -0000")
        assert d is not None
        assert d.tzinfo is not None
        assert d.utcoffset() == timezone.utc.utcoffset(None)

    def test_iso_date_only(self):
        d = _parse_pub_date("2026-07-20")
        assert d is not None and d.tzinfo is not None

    def test_garbage_returns_none(self):
        assert _parse_pub_date("not a date") is None
        assert _parse_pub_date(None) is None
        assert _parse_pub_date("") is None


class TestStripHtml:
    """WordPress-backed feeds (Roll Call, The Hill) put real markup in
    <description>. It reached three consumers that all read the description
    as prose: the policy-relevance embedding (which scores only the first
    200 characters — for an image-led item, entirely markup), the LLM prompt
    for issue generation, and the digest detector's entity extraction."""

    def test_tags_are_removed(self):
        assert _strip_html("<p>The House passed the bill.</p>") == "The House passed the bill."

    def test_image_filename_is_not_left_behind_as_an_entity(self):
        """An <img> lead is the common WordPress shape, and "Trump-Rally.jpg"
        reads as a named entity that appears in no other sentence — which is
        exactly what the digest detector's disjointness test keys on."""
        raw = (
            '<img src="https://thehill.com/wp-content/Trump-Rally.jpg"/>'
            "<p>The Senate voted on Tuesday.</p>"
        )
        assert _strip_html(raw) == "The Senate voted on Tuesday."

    def test_block_boundaries_survive_as_punctuation(self):
        """</p> and <li> are where one thought ends. Dropping them silently
        welds two sentences into one run-on with no item boundary left for
        the digest detector to split on."""
        raw = "<ul><li>Ukraine aid clears</li><li>Powell signals a pause</li></ul>"
        assert _strip_html(raw) == "Ukraine aid clears; Powell signals a pause"

    def test_entities_are_decoded(self):
        assert _strip_html("Ways &amp; Means marks up the bill") == "Ways & Means marks up the bill"

    def test_double_escaped_markup_is_still_stripped(self):
        """The XML parser decodes one layer, so a feed that escaped its
        markup twice still holds "&lt;p&gt;" by the time this runs. Strip
        before unescape and that tag text lands in the summary verbatim."""
        assert _strip_html("&lt;p&gt;The Senate voted.&lt;/p&gt;") == "The Senate voted."

    def test_comparison_operators_in_prose_are_not_treated_as_tags(self):
        """A bare "<" is not markup. Matching "<[^>]*>" swallowed the middle
        of any sentence that used both comparison signs."""
        text = "Turnout ran < 6 > the 2024 figure, analysts said."
        assert _strip_html(text) == text

    def test_plain_text_is_returned_unchanged(self):
        text = "The House passed the bill. It now goes to the Senate."
        assert _strip_html(text) is text

    def test_atom_entries_are_stripped_too(self):
        """The Atom branch builds its NewsArticle separately from the RSS
        one, so it is its own chance to forget the strip.

        This also pins the ElementTree truthiness trap it uncovered:
        `find(a) or find(b)` always took b, because a childless element is
        falsy whatever its text, so every Atom summary was discarded."""
        entry = _parse_rss_feed(
            """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Senate clears the measure</title>
                <link rel="self" href="https://example.com/feed"/>
                <link rel="alternate" href="https://example.com/a"/>
                <summary>&lt;p&gt;The Senate voted Tuesday.&lt;/p&gt;</summary>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.summary == "The Senate voted Tuesday."
        # The rel="alternate" preference never applied for the same reason.
        assert entry.url == "https://example.com/a"

    def test_atom_falls_back_to_bare_link_and_content(self):
        """Not every Atom feed labels its link or names the body <summary>;
        both fallbacks are real feed shapes and both are on the path the
        truthiness fix rewrote."""
        entry = _parse_rss_feed(
            """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>House sends the bill onward</title>
                <link href="https://example.com/b"/>
                <content>&lt;p&gt;The House voted Wednesday.&lt;/p&gt;</content>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.url == "https://example.com/b"
        assert entry.summary == "The House voted Wednesday."

    def test_summary_cap_is_applied_after_stripping(self):
        """Truncating first spends the budget on markup. The cap has to
        measure real prose, so the strip runs before the slice."""
        prose = "Senators debated the measure for hours. " * 30
        raw = f'<p><img src="https://example.com/a.jpg"/>{prose}</p>'
        item = _parse_rss_feed(
            f"""<?xml version="1.0"?><rss><channel><item>
              <title>Senate debates the measure</title>
              <link>https://example.com/a</link>
              <description>{escape(raw)}</description>
            </item></channel></rss>""".encode(),
            "Test",
        )[0]
        assert len(item.summary) == MAX_SUMMARY_CHARS
        assert "<" not in item.summary
        assert item.summary.startswith("Senators debated")
