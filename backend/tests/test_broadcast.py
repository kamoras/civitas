"""app/broadcast.py: every post is stored first, then delivered to Bluesky."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import broadcast
from app.models import BroadcastPost
from app.time_utils import utcnow


def _publish(db, **kw):
    args = dict(kind="issue", subject="issue:x", title="A title", text="A post.", url="https://civitas-research.org/issue/x")
    args.update(kw)
    return broadcast.publish(db, **args)


def test_stored_even_with_no_bluesky_account(db_session, bluesky_outbox):
    post = _publish(db_session, state="ga")
    assert db_session.query(BroadcastPost).one() is post
    assert (post.bsky_status, post.bsky_attempts, post.state) == ("off", 0, "GA")
    assert bluesky_outbox == []


def test_delivered_to_bluesky_when_configured(db_session, bluesky_configured):
    post = _publish(db_session)
    assert bluesky_configured == [("A post.", "https://civitas-research.org/issue/x")]
    assert (post.bsky_status, post.bsky_attempts) == ("sent", 1)
    assert post.bsky_sent_at is not None


def test_no_channel_carries_a_hashtag(db_session, bluesky_configured):
    post = _publish(db_session, title="The #Senate votes", text="The #Senate passed it.")
    assert (post.title, post.text) == ("The Senate votes", "The Senate passed it.")
    assert bluesky_configured == [("The Senate passed it.", post.url)]


def test_an_unknown_kind_is_refused(db_session):
    with pytest.raises(ValueError):
        _publish(db_session, kind="newsletter")
    assert db_session.query(BroadcastPost).count() == 0


def test_the_post_is_stored_before_bluesky_is_tried(db_session, monkeypatch, bluesky_configured):
    """A crash during the send must leave the post in the feed and marked
    as sending, so it is never sent a second time."""
    from sqlalchemy.orm import sessionmaker

    seen = {}

    def crash(text, url, **_kw):
        other = sessionmaker(bind=db_session.get_bind())()
        try:
            row = other.query(BroadcastPost).one()
            seen["status"], seen["attempts"] = row.bsky_status, row.bsky_attempts
        finally:
            other.close()
        raise RuntimeError("process died mid-send")

    monkeypatch.setattr(broadcast, "publish_post", crash)
    with pytest.raises(RuntimeError):
        _publish(db_session)
    assert seen == {"status": "sending", "attempts": 1}

    # A post left "sending" is never retried: it may have gone out.
    monkeypatch.setattr(broadcast, "publish_post", lambda *a, **k: pytest.fail("resent"))
    db_session.rollback()
    assert broadcast.deliver_pending(db_session) == 0


def _an_hour_passes(db, post):
    post.bsky_last_attempt_at = utcnow() - broadcast.RETRY_AFTER - timedelta(minutes=1)
    db.commit()


def test_a_refused_post_is_retried_the_same_day_up_to_the_limit(db_session, bluesky_configured):
    bluesky_configured.ok = False
    post = _publish(db_session)
    assert post.bsky_status == "failed"

    for _ in range(broadcast.MAX_BSKY_ATTEMPTS + 2):
        _an_hour_passes(db_session, post)
        broadcast.deliver_pending(db_session)
    assert post.bsky_attempts == broadcast.MAX_BSKY_ATTEMPTS

    post.bsky_attempts = 1
    _an_hour_passes(db_session, post)
    bluesky_configured.ok = True
    assert broadcast.deliver_pending(db_session) == 1
    assert post.bsky_status == "sent"
    assert broadcast.deliver_pending(db_session) == 0


def test_a_retry_waits_for_the_next_run_not_the_same_one(db_session, bluesky_configured):
    """The Action Center run that just failed to send a post calls
    deliver_pending seconds later; that must not spend a second try."""
    bluesky_configured.ok = False
    post = _publish(db_session)
    bluesky_configured.ok = True
    assert broadcast.deliver_pending(db_session) == 0
    assert post.bsky_attempts == 1
    _an_hour_passes(db_session, post)
    assert broadcast.deliver_pending(db_session) == 1
    assert (post.bsky_attempts, post.bsky_status) == (2, "sent")


def test_a_post_from_an_earlier_eastern_day_is_not_retried(db_session, bluesky_configured):
    """Its words may be day-relative ("Yesterday: ..."), which would be
    false on Bluesky a day late. The feed entry keeps its own date."""
    bluesky_configured.ok = False
    post = _publish(db_session)
    bluesky_configured.ok = True

    eastern_midnight = datetime.now(broadcast._POST_DAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    post.published_at = (eastern_midnight - timedelta(minutes=1)).astimezone(timezone.utc).replace(tzinfo=None)
    _an_hour_passes(db_session, post)

    assert broadcast.deliver_pending(db_session) == 0
    assert post.bsky_status == "failed"


def test_nothing_is_retried_without_an_account(db_session, bluesky_outbox, monkeypatch):
    db_session.add(BroadcastPost(kind="issue", subject="issue:x", title="t", text="x", url="u", published_at=utcnow(),
                                 bsky_status="failed", bsky_attempts=1))
    db_session.commit()
    assert broadcast.deliver_pending(db_session) == 0
    assert bluesky_outbox == []


def test_every_feed_names_only_real_kinds():
    for feed in broadcast.FEEDS.values():
        assert feed.kinds and set(feed.kinds) <= set(broadcast.KINDS), feed.slug
    assert set(broadcast.FEEDS["all"].kinds) == set(broadcast.KINDS)
    # Every kind is reachable from a topic feed other than "all".
    topical = {k for f in broadcast.FEEDS.values() if f.slug != "all" for k in f.kinds}
    assert topical == set(broadcast.KINDS)


def test_a_retry_never_sends_a_post_another_sender_has_claimed(db_session, bluesky_configured):
    """The hourly retry and a publish on another thread can both hold the
    row; only the one whose conditional update flips it to "sending" sends
    it. Here the row was claimed elsewhere after this session loaded it."""
    bluesky_configured.ok = False
    post = _publish(db_session)
    bluesky_configured.ok = True
    db_session.query(BroadcastPost).filter(BroadcastPost.id == post.id).update(
        {"bsky_status": "sending"}, synchronize_session=False)
    db_session.commit()
    post.bsky_status = "failed"  # this session's stale view of the row

    assert broadcast._deliver_to_bluesky(db_session, post) is False
    assert bluesky_configured == []


def test_was_published_and_subjects_since(db_session):
    _publish(db_session, kind="race", subject="race:2026-SEN-GA")
    _publish(db_session, kind="race", subject="race:2026-SEN-GA")
    assert broadcast.was_published(db_session, "race:2026-SEN-GA")
    assert not broadcast.was_published(db_session, "race:2026-SEN-NC")
    since = utcnow() - timedelta(hours=1)
    assert broadcast.subjects_published_since(db_session, "race", since) == ["race:2026-SEN-GA"] * 2
    assert broadcast.subjects_published_since(db_session, "issue", since) == []
    assert broadcast.subjects_published_since(db_session, "race", utcnow() + timedelta(hours=1)) == []


def test_a_withdrawn_issue_is_never_sent_late(db_session, bluesky_configured, monkeypatch):
    from app import retractions

    bluesky_configured.ok = False
    post = _publish(db_session, subject="issue:i00000bad")
    bluesky_configured.ok = True
    monkeypatch.setattr(retractions, "entries", lambda: [{"publicIds": ["i00000bad"], "issueIds": []}])
    _an_hour_passes(db_session, post)
    assert broadcast.deliver_pending(db_session) == 0
    assert bluesky_configured == []


def test_withdrawn_subjects_come_from_the_retraction_log():
    from app.retractions import entries

    ids = [pid for e in entries() for pid in e["publicIds"]]
    assert ids and broadcast.withdrawn_subjects() == {f"issue:{pid}" for pid in ids}


def _failed_posts(db, n):
    for i in range(n):
        _publish(db, kind="race", subject=f"race:x{i}", text=f"post {i}")
    db.query(broadcast.BroadcastPost).update(
        {"bsky_status": "failed", "bsky_attempts": 1, "bsky_last_attempt_at": utcnow() - timedelta(hours=1)})
    db.commit()


def test_a_retry_run_stops_after_two_refusals_in_a_row(db_session, bluesky_configured):
    """Each try is a login; a Bluesky still refusing an hour on is down, and
    trying the rest in the same burst only spends the login allowance."""
    _failed_posts(db_session, 4)
    tried = []
    with patch.object(broadcast, "publish_post", lambda text, url, **k: tried.append(text) or False):
        assert broadcast.deliver_pending(db_session) == 0
    assert tried == ["post 0", "post 1"]


def test_one_post_bluesky_rejects_does_not_hold_back_the_rest(db_session, bluesky_configured):
    _failed_posts(db_session, 3)
    with patch.object(broadcast, "publish_post", lambda text, url, **k: text != "post 0"):
        assert broadcast.deliver_pending(db_session) == 2


def test_a_post_another_sender_claimed_does_not_count_as_a_refusal(db_session, bluesky_configured, monkeypatch):
    _failed_posts(db_session, 3)
    real = broadcast._deliver_to_bluesky

    def deliver(db, post):
        if post.text == "post 0":  # claimed elsewhere between the query and this send
            db.query(broadcast.BroadcastPost).filter_by(id=post.id).update({"bsky_status": "sent"})
            db.commit()
            return False
        return real(db, post)

    monkeypatch.setattr(broadcast, "_deliver_to_bluesky", deliver)
    tried = []
    with patch.object(broadcast, "publish_post", lambda text, url, **k: tried.append(text) or False):
        broadcast.deliver_pending(db_session)
    assert tried == ["post 1", "post 2"]


def test_a_feed_only_post_is_never_sent(db_session, bluesky_configured):
    post = broadcast.publish(db_session, kind="result", subject="result:x", title="t", text="t", url="u",
                             bluesky=False)
    assert post.bsky_status == "off"
    assert broadcast.deliver_pending(db_session) == 0


def test_result_posts_are_never_retried(db_session, bluesky_configured):
    with patch.object(broadcast, "publish_post", return_value=False):
        broadcast.publish(db_session, kind="result", subject="result:x", title="t", text="t", url="u")
    db_session.query(broadcast.BroadcastPost).update({"bsky_last_attempt_at": utcnow() - timedelta(hours=1)})
    db_session.commit()
    with patch.object(broadcast, "publish_post", return_value=True) as send:
        assert broadcast.deliver_pending(db_session) == 0
    send.assert_not_called()


_CARD = {"title": "A page", "description": "What the page is.", "image": "https://civitas-research.org/api/og?issue=x",
         "image_alt": "A page"}


def test_the_pages_card_is_kept_for_the_feed(db_session, link_cards):
    link_cards["https://civitas-research.org/issue/x"] = _CARD
    post = _publish(db_session)
    assert (post.card_image, post.card_image_alt, post.card_description) == (
        "https://civitas-research.org/api/og?issue=x", "A page", "What the page is.",
    )


def test_a_page_that_sets_no_image_is_read_once_not_every_hour(db_session, link_cards):
    link_cards["https://civitas-research.org/issue/x"] = {**_CARD, "image": "", "image_alt": "", "description": ""}
    post = _publish(db_session)
    assert (post.card_image, post.card_image_alt, post.card_description) == ("", None, None)
    assert broadcast.fill_missing_cards(db_session) == 0


def test_an_unreadable_page_never_holds_the_post_and_is_filled_in_later(db_session, link_cards):
    post = _publish(db_session)  # the page can't be read yet
    assert db_session.query(BroadcastPost).one() is post
    assert post.card_image is None
    link_cards["https://civitas-research.org/issue/x"] = _CARD
    assert broadcast.fill_missing_cards(db_session) == 1
    assert post.card_image == "https://civitas-research.org/api/og?issue=x"


def test_the_backfill_leaves_old_and_withdrawn_posts_alone(db_session, link_cards, monkeypatch):
    from app import retractions

    old = _publish(db_session, subject="issue:old", url="https://civitas-research.org/issue/old")
    old.published_at = utcnow() - broadcast.CARD_BACKFILL_WINDOW - timedelta(hours=1)
    withdrawn = _publish(db_session, subject="issue:i00000bad", url="https://civitas-research.org/issue/bad")
    db_session.commit()
    monkeypatch.setattr(retractions, "entries", lambda: [{"publicIds": ["i00000bad"], "issueIds": []}])
    for url in ("https://civitas-research.org/issue/old", "https://civitas-research.org/issue/bad"):
        link_cards[url] = _CARD
    assert broadcast.fill_missing_cards(db_session) == 0
    assert old.card_image is None and withdrawn.card_image is None


# Posting windows (broadcast.SCHEDULED_KINDS, release_at). 09:00-22:00
# Eastern here; the shipped file's hours are checked separately below.
_WINDOWS = [(9, 22)]
_EDT = timedelta(hours=4)  # 2026-10 dates are on daylight time


@pytest.fixture()
def windows(monkeypatch):
    monkeypatch.setattr(broadcast, "_windows_cache", _WINDOWS)


def _eastern(day, hour, minute=0):
    """A naive-UTC instant at an Eastern wall time in October 2026 (EDT)."""
    return datetime(2026, 10, day, hour, minute) + _EDT


@pytest.mark.parametrize("kind", sorted(set(broadcast.KINDS) - broadcast.SCHEDULED_KINDS))
def test_news_is_never_held_for_a_window(db_session, bluesky_configured, windows, freeze_utcnow, kind):
    freeze_utcnow(_eastern(13, 3))
    post = _publish(db_session, kind=kind, subject=f"{kind}:x")
    assert (post.bsky_status, len(bluesky_configured)) == ("sent", 1)


def test_time_sensitive_kinds_are_not_scheduled():
    assert broadcast.SCHEDULED_KINDS <= set(broadcast.KINDS)
    assert not broadcast.SCHEDULED_KINDS & {"issue", "race", "result"}


def test_a_scheduled_post_waits_for_the_window_and_the_feed_does_not(db_session, bluesky_configured, windows,
                                                                     freeze_utcnow):
    written = freeze_utcnow(_eastern(13, 3))
    post = _publish(db_session, kind="congress_day", subject="congress-day:x")
    # In the feed now, with the time it was written; Bluesky waits.
    assert (post.bsky_status, post.published_at, bluesky_configured) == ("pending", written, [])

    freeze_utcnow(_eastern(13, 8, 15))
    assert broadcast.deliver_pending(db_session) == 0
    freeze_utcnow(_eastern(13, 9, 15))
    assert broadcast.deliver_pending(db_session) == 1
    assert (post.bsky_status, len(bluesky_configured)) == ("sent", 1)


def test_a_scheduled_post_inside_the_window_goes_at_once(db_session, bluesky_configured, windows, freeze_utcnow):
    freeze_utcnow(_eastern(13, 12))
    assert _publish(db_session, kind="spotlight", subject="member:x").bsky_status == "sent"


def test_with_no_window_left_that_day_it_goes_at_once(db_session, bluesky_configured, windows, freeze_utcnow):
    """Never held into the next day's posts."""
    freeze_utcnow(_eastern(13, 23))
    assert _publish(db_session, kind="congress_ahead", subject="congress-ahead:x").bsky_status == "sent"


def test_a_missed_window_sends_at_the_next_one_not_never(db_session, bluesky_configured, windows, freeze_utcnow):
    """The pipeline process was down for the whole window: the post goes in
    the next one, the next morning, and only once."""
    freeze_utcnow(_eastern(13, 3))
    post = _publish(db_session, kind="congress_week", subject="congress-week:x")
    freeze_utcnow(_eastern(13, 23, 15))
    assert broadcast.deliver_pending(db_session) == 0
    freeze_utcnow(_eastern(14, 9, 15))
    assert broadcast.deliver_pending(db_session) == 1
    assert broadcast.deliver_pending(db_session) == 0
    assert (post.bsky_status, len(bluesky_configured)) == ("sent", 1)


def test_a_held_post_is_not_sent_once_too_old(db_session, bluesky_configured, windows, freeze_utcnow):
    written = freeze_utcnow(_eastern(13, 3))
    post = _publish(db_session, kind="spotlight", subject="member:x")
    freeze_utcnow(written + broadcast.HELD_MAX_AGE + timedelta(hours=8))  # inside a window
    assert broadcast.in_window(broadcast.utcnow())
    assert broadcast.deliver_pending(db_session) == 0
    assert post.bsky_status == "pending"


def test_a_held_post_bluesky_refuses_is_retried_in_the_window(db_session, bluesky_configured, windows,
                                                               freeze_utcnow):
    freeze_utcnow(_eastern(13, 3))
    post = _publish(db_session, kind="congress_day", subject="congress-day:x")
    bluesky_configured.ok = False
    freeze_utcnow(_eastern(13, 9, 15))
    assert broadcast.deliver_pending(db_session) == 0
    bluesky_configured.ok = True
    freeze_utcnow(_eastern(13, 10, 15))
    assert broadcast.deliver_pending(db_session) == 1
    assert (post.bsky_status, post.bsky_attempts) == ("sent", 2)


def test_the_window_follows_daylight_saving(windows):
    # 03:00 Eastern; the window opens at 09:00 Eastern on both dates.
    assert broadcast.release_at("spotlight", datetime(2026, 10, 13, 7)) == datetime(2026, 10, 13, 13)  # EDT
    assert broadcast.release_at("spotlight", datetime(2026, 11, 13, 8)) == datetime(2026, 11, 13, 14)  # EST


def test_a_window_past_midnight(monkeypatch):
    monkeypatch.setattr(broadcast, "_windows_cache", [(20, 2)])
    assert broadcast.in_window(_eastern(13, 1))
    assert broadcast.in_window(_eastern(13, 21))
    assert not broadcast.in_window(_eastern(13, 2))


def test_an_unreadable_windows_file_holds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(broadcast, "_windows_cache", None)
    monkeypatch.setattr(broadcast, "_WINDOWS_PATH", tmp_path / "missing.json")
    assert broadcast.release_at("spotlight", _eastern(13, 3)) == _eastern(13, 3)


def test_the_shipped_windows_are_the_measured_above_average_hours(monkeypatch):
    """bluesky_post_windows.json as fetch_bluesky_activity.py writes it: the
    windows are exactly the Eastern hours whose English post volume is above
    the mean hour, every hour sampled."""
    monkeypatch.setattr(broadcast, "_windows_cache", None)
    data = json.loads(broadcast._WINDOWS_PATH.read_text())
    assert data["_source"] and data["timezone"] == "America/New_York"
    assert all(n > 0 for n in data["samplesPerEasternHour"]) and len(data["samplesPerEasternHour"]) == 24
    above = {h for h, v in enumerate(data["hourlyRelativeVolume"]["englishPosts"]) if v > 1}
    assert above and {h for h in range(24) if broadcast._hour_in_window(h)} == above
