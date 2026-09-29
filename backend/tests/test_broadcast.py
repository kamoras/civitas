"""app/broadcast.py: every post is stored first, then delivered to Bluesky."""

from datetime import datetime, timedelta, timezone

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
