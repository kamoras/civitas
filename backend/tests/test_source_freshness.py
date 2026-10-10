"""A source that keeps answering with nothing new is a dead source, not a
quiet one. The AP mirror served the same stale items for a month
(2026-09-09 to 2026-10-10) while the fetch loop logged "Fetched 0 articles"
at INFO; check_source_freshness turns that into one ops alert per stale
stretch, with the bar set by the source's own rhythm."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import ops_alerts
from app.models import ApiCache
from app.ops_alerts import SOURCE_FRESHNESS_TIER, check_source_freshness
from app.pipeline.fetch import news_feeds, trending

NOW = datetime(2026, 10, 10, 22, 0)
FLOOR = timedelta(hours=48)


@pytest.fixture
def check(db_session):
    """Run the check at a given instant; returns (send, resolve) mocks."""
    def run(observed, now=NOW, floor=FLOOR):
        with patch.object(ops_alerts, "SessionLocal", return_value=db_session), \
             patch.object(ops_alerts, "utcnow", return_value=now), \
             patch.object(ops_alerts, "send_ops_alert") as send, \
             patch.object(ops_alerts, "resolve_ops_alert") as resolve:
            check_source_freshness(observed, floor=floor, kind="news feed")
        return send, resolve
    return run


def _hourly(end: datetime, n: int) -> list[datetime]:
    return [end - timedelta(hours=i) for i in range(n)]


def test_a_feed_answering_with_month_old_items_alerts_once(check):
    stale = _hourly(NOW - timedelta(days=31), 10)
    send, _ = check({"https://mirror/ap.xml": ("AP News", stale)})
    send.assert_called_once()
    subject, body = send.call_args.args
    assert "AP News" in subject
    assert "answering with old items" in body
    assert send.call_args.kwargs["condition"] == "source-stale:https://mirror/ap.xml"
    # The next hourly fetch sees the same items: the alert is already open.
    send, _ = check({"https://mirror/ap.xml": ("AP News", stale)}, now=NOW + timedelta(hours=1))
    send.assert_not_called()


def test_a_quiet_spell_inside_the_floor_does_not_alert(check):
    send, _ = check({"f": ("Feed", _hourly(NOW - timedelta(hours=30), 20))})
    send.assert_not_called()


def test_the_feeds_own_rhythm_raises_the_bar(check):
    """A feed whose history includes 30-hour weekend gaps isn't stale after
    50 quiet hours, though that is past the 48-hour floor."""
    newest = NOW - timedelta(hours=50)
    history = [newest, newest - timedelta(hours=30), newest - timedelta(hours=31), newest - timedelta(hours=61)]
    send, _ = check({"f": ("Feed", history)})
    send.assert_not_called()
    # Past twice its longest gap (60 hours), it is.
    send, _ = check({"f": ("Feed", history)}, now=newest + timedelta(hours=61))
    send.assert_called_once()


def test_a_feed_that_never_answers_goes_stale_from_first_check(check):
    send, _ = check({"f": ("Feed", None)})
    send.assert_not_called()
    send, _ = check({"f": ("Feed", None)}, now=NOW + timedelta(hours=49))
    send.assert_called_once()
    assert "fetch failed" in send.call_args.args[1]
    assert "no dated item" in send.call_args.args[1]


def test_history_survives_between_checks(check, db_session):
    """A short feed (10 items) shows only its latest stretch each time; the
    stored history is what measures its rhythm."""
    check({"f": ("Feed", _hourly(NOW, 10))})
    check({"f": ("Feed", None)}, now=NOW + timedelta(hours=1))
    row = db_session.query(ApiCache).filter_by(tier=SOURCE_FRESHNESS_TIER, cache_key="f").one()
    assert len(json.loads(row.data_json)["seen"]) == 10


def test_recovery_resolves_and_forgets_the_stale_stretch(check, db_session):
    old = _hourly(NOW - timedelta(days=31), 10)
    check({"f": ("Feed", old)})
    send, resolve = check({"f": ("Feed", old + [NOW + timedelta(hours=1)])}, now=NOW + timedelta(hours=2))
    send.assert_not_called()
    resolve.assert_called_once_with("source-stale:f")
    # The month-long gap is not the feed's rhythm: kept, the next outage
    # would need two months of silence to alert.
    state = json.loads(db_session.query(ApiCache).filter_by(cache_key="f").one().data_json)
    assert state["seen"] == [(NOW + timedelta(hours=1)).isoformat()]
    send, _ = check({"f": ("Feed", [])}, now=NOW + timedelta(hours=60))
    send.assert_called_once()


def test_aware_times_are_compared_in_utc(check):
    eastern = datetime(2026, 10, 10, 17, 0, tzinfo=ZoneInfo("America/New_York"))  # 21:00 UTC
    send, _ = check({"f": ("Feed", [eastern])}, now=datetime(2026, 10, 12, 21, 30))
    send.assert_called_once()  # 48.5 hours


def test_an_unreadable_database_never_raises():
    with patch.object(ops_alerts, "SessionLocal", side_effect=RuntimeError("locked")):
        check_source_freshness({"f": ("Feed", None)}, floor=FLOOR, kind="news feed")


class TestFeedsReportWhatTheyShowed:
    def _rss(self, *dates: str) -> bytes:
        items = "".join(
            f"<item><title>Story {i}</title><link>https://x/{i}</link><pubDate>{d}</pubDate></item>"
            for i, d in enumerate(dates)
        )
        return f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>".encode()

    def test_old_items_count_toward_freshness_but_are_not_returned(self):
        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        old = datetime.now(timezone.utc) - timedelta(days=30)
        response = MagicMock(content=self._rss(
            fresh.strftime("%a, %d %b %Y %H:%M:%S +0000"), old.strftime("%a, %d %b %Y %H:%M:%S +0000"),
        ))
        feeds = [{"name": "Ok", "url": "https://ok/feed"}, {"name": "Down", "url": "https://down/feed"}]

        def get(url, **_):
            if "down" in url:
                raise OSError("connection refused")
            return response

        with patch.object(news_feeds.httpx, "get", side_effect=get), \
             patch.object(news_feeds, "check_source_freshness") as checked:
            articles = news_feeds.fetch_news_articles(feeds=feeds)
        assert [a.title for a in articles] == ["Story 0"]
        observed = checked.call_args.args[0]
        assert len(observed["https://ok/feed"][1]) == 2
        assert observed["https://down/feed"] == ("Down", None)
        assert checked.call_args.kwargs["floor"] == timedelta(hours=news_feeds.MAX_ARTICLE_AGE_HOURS)

    def test_a_feeds_named_zone_reads_its_offsetless_dates(self):
        """Roll Call writes local Eastern time with no offset."""
        parsed = news_feeds._parse_pub_date("2026-10-09 17:12:54", ZoneInfo("America/New_York"))
        assert parsed == datetime(2026, 10, 9, 21, 12, 54, tzinfo=timezone.utc)
        roll_call = next(f for f in news_feeds.NEWS_FEEDS if f["name"] == "Roll Call")
        assert roll_call["timezone"] == "America/New_York"

    def test_every_configured_feed_names_a_real_zone(self):
        for feed in news_feeds.NEWS_FEEDS + news_feeds.STATE_NEWS_FEEDS:
            ZoneInfo(feed.get("timezone", "UTC"))


class TestTrendingSourcesReportWhatTheyShowed:
    def test_failed_empty_and_answering_sources_are_told_apart(self):
        topic = trending.TrendingTopic(title="x", source="bluesky", traffic_score=1.0)
        with patch.object(trending, "_fetch_google_trends", return_value=None), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=[topic]), \
             patch.object(trending, "check_source_freshness") as checked:
            trending.fetch_trending_topics()
        observed = checked.call_args.args[0]
        assert observed["trending:google_trends"] == ("google_trends", None)
        assert len(observed["trending:bluesky"][1]) == 1
        with patch.object(trending, "_fetch_google_trends", return_value=[]), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=[]), \
             patch.object(trending, "check_source_freshness") as checked:
            trending.fetch_trending_topics()
        assert checked.call_args.args[0]["trending:google_trends"] == ("google_trends", [])
