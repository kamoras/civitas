"""Tests for news feed pubDate parsing (timezone robustness)."""

from datetime import timezone

from app.pipeline.fetch.news_feeds import _parse_pub_date


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
