"""Tests for news feed parsing: pubDate timezone robustness and the
HTML stripping applied to feed descriptions."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from app.pipeline.fetch.news_feeds import (
    MAX_ARTICLE_AGE_HOURS,
    MAX_SUMMARY_CHARS,
    _extract_body_text,
    _parse_pub_date,
    _parse_rss_feed,
    _strip_html,
    fetch_news_articles,
)


def _recent_iso(hours_ago: float) -> str:
    """An ISO timestamp relative to now — the parser drops anything older
    than MAX_ARTICLE_AGE_HOURS, so fixtures cannot use a fixed date."""
    stamp = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return stamp.replace(microsecond=0).isoformat()


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

    def test_atom_xhtml_content_is_not_lost(self):
        """<content type="xhtml"> holds real child elements, so .text is
        empty — the same title-and-nothing-else outcome the truthiness trap
        produced, and just as silent."""
        entry = _parse_rss_feed(
            """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Committee advances the nomination</title>
                <link href="https://example.com/c"/>
                <content type="xhtml">
                  <div xmlns="http://www.w3.org/1999/xhtml">
                    <p>The committee voted Thursday.</p>
                  </div>
                </content>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.summary == "The committee voted Thursday."

    def test_adjacent_blocks_are_not_welded_together(self):
        """A plain "".join(itertext()) yields "Thursday.Grassley" — two
        sentences fused into one token that no downstream stage can split,
        since the action center's item splitter needs whitespace after the
        period to see a boundary at all."""
        entry = _parse_rss_feed(
            """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Committee advances the nomination</title>
                <link href="https://example.com/f"/>
                <content type="xhtml">
                  <div xmlns="http://www.w3.org/1999/xhtml">
                    <p>The committee voted Thursday.</p>
                    <p>Grassley set the floor date.</p>
                  </div>
                </content>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.summary == (
            "The committee voted Thursday. Grassley set the floor date."
        )

    def test_block_after_sentence_punctuation_does_not_double_the_boundary(self):
        """"voted Thursday.; Grassley objected" states the break twice. Both
        forms split identically downstream, so this is about the text that
        reaches the embedding and the LLM prompt."""
        assert _strip_html("<p>A vote was held.</p><p>B objected.</p>") == "A vote was held. B objected."
        # A block that did NOT end a sentence still needs the separator.
        assert _strip_html("<li>Ukraine aid clears</li><li>Powell pauses</li>") == (
            "Ukraine aid clears; Powell pauses"
        )

    def test_semicolon_written_by_the_newsroom_is_left_alone(self):
        """The redundant-boundary cleanup must only touch separators WE
        inserted. Applied to source prose it rewrites the sentence:
        "resumed in the U.S.; officials said" became "resumed in the U.S.
        officials said", which reads as a different claim and reaches the
        LLM prompt that way."""
        article = _parse_rss_feed(
            """<?xml version="1.0"?><rss><channel><item>
              <title>Trade talks resume</title>
              <link>https://example.com/t</link>
              <description>Talks resumed in the U.S.; officials said a deal was near.</description>
            </item></channel></rss>""".encode(),
            "Test",
        )[0]
        assert article.summary == "Talks resumed in the U.S.; officials said a deal was near."

    def test_malformed_xml_yields_no_articles(self):
        """A feed serving garbage must not take the run down with it."""
        assert _parse_rss_feed(b"<rss><channel><item>truncated", "Test") == []

    def test_comment_text_is_not_treated_as_content(self):
        """This parser drops comments, but the extractor should not depend
        on that to avoid quoting "<!-- tracking beacon -->" into an article."""
        el = ElementTree.Element("description")
        el.text = "The Senate voted."
        comment = ElementTree.Comment("tracking beacon")
        comment.tail = " It goes to the House."
        el.append(comment)
        assert _extract_body_text(el) == "The Senate voted. It goes to the House."

    def test_atom_categories_are_read_from_the_term_attribute(self):
        """Atom labels categories with @term, not element text. A field that
        is silently always-empty for one feed format is how the first reader
        of it gets a wrong answer with no error."""
        entry = _parse_rss_feed(
            """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Committee advances the nomination</title>
                <link href="https://example.com/g"/>
                <summary>The committee voted.</summary>
                <category term="Politics"/>
                <category term="Senate"/>
                <category/>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.categories == ["Politics", "Senate"]

    def test_atom_prefers_published_over_updated(self):
        """<updated> is when the entry was last touched; <published> is when
        the story ran. Reading <updated> alone made a lightly-edited old
        story look fresh."""
        entry = _parse_rss_feed(
            f"""<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Senate returns from recess</title>
                <link href="https://example.com/d"/>
                <summary>The Senate reconvened.</summary>
                <published>{_recent_iso(hours_ago=3)}</published>
                <updated>{_recent_iso(hours_ago=1)}</updated>
              </entry>
            </feed>""".encode(),
            "Test",
        )[0]
        assert entry.published == datetime.fromisoformat(_recent_iso(hours_ago=3))

    def test_atom_entry_with_only_published_is_still_dated(self):
        """Many feeds emit <published> alone. Left undated, an entry skips
        the MAX_ARTICLE_AGE_HOURS cutoff entirely and sorts last."""
        stale = _recent_iso(hours_ago=MAX_ARTICLE_AGE_HOURS + 24)
        entries = _parse_rss_feed(
            f"""<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>An article from last week</title>
                <link href="https://example.com/e"/>
                <summary>Old news.</summary>
                <published>{stale}</published>
              </entry>
            </feed>""".encode(),
            "Test",
        )
        assert entries == []

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


def _feed_response(xml: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = xml
    resp.raise_for_status = MagicMock()
    return resp


def _rss(*items: str) -> bytes:
    body = "".join(items)
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'.encode()


def _item(title: str, link: str, desc: str | None = None) -> str:
    body = f"<description>{desc}</description>" if desc is not None else ""
    return f"<item><title>{title}</title><link>{link}</link>{body}</item>"


class TestMissingDescriptionIsAudible:
    """The Atom truthiness bug survived because its failure mode was
    invisible: the articles still existed, still clustered, still reached
    the LLM prompt — with a headline and nothing else. A feed whose items
    ALL arrive body-less is the signature of that class of parse mismatch,
    so it gets a warning of its own rather than a silent degradation."""

    def test_body_text_of_a_missing_element_is_empty(self):
        assert _extract_body_text(None) == ""

    def test_feed_with_no_descriptions_at_all_warns(self, caplog):
        feed = _rss(
            _item("Senate passes the bill", "https://example.com/a"),
            _item("House takes it up", "https://example.com/b"),
        )
        with patch(
            "app.pipeline.fetch.news_feeds.httpx.get",
            return_value=_feed_response(feed),
        ), caplog.at_level(logging.INFO):
            articles = fetch_news_articles([{"name": "Test", "url": "https://x/f"}])

        assert len(articles) == 2
        assert "2 without a description" in caplog.text
        assert "probably not being read" in caplog.text

    def test_feed_with_some_descriptions_counts_but_does_not_warn(self, caplog):
        feed = _rss(
            _item("Senate passes the bill", "https://example.com/a", "It cleared 60-40."),
            _item("House takes it up", "https://example.com/b"),
        )
        with patch(
            "app.pipeline.fetch.news_feeds.httpx.get",
            return_value=_feed_response(feed),
        ), caplog.at_level(logging.INFO):
            articles = fetch_news_articles([{"name": "Test", "url": "https://x/f"}])

        assert len(articles) == 2
        assert "1 without a description" in caplog.text
        # A partly-bodyless feed is normal; only an all-or-nothing feed is
        # evidence of a parse mismatch.
        assert "probably not being read" not in caplog.text
