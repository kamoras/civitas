"""The daily Congress post: its text, and which days it may post."""

from datetime import date

import pytest

from app import broadcast
from app.models import BroadcastPost, CongressDay
from app.pipeline.analyze import congress_bluesky as cb
from app.pipeline.cache import api_cache_set


def _report(senate_passed, house_passed=()):
    def ev(bill, res=False):
        return {"billId": bill, "isResolution": res}
    return {
        "date": "2026-09-24",
        "sentence": "The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes. "
                    "The House met for 3 minutes and took no record votes.",
        "chambers": {
            "senate": {"passed": [ev(b, b.startswith("SRES")) for b in senate_passed],
                       "counts": {"recordVotes": 3, "billsPassed": len(senate_passed)}},
            "house": {"passed": [ev(b) for b in house_passed], "counts": {"recordVotes": 0}},
        },
    }


def test_the_post_is_the_template_sentence_and_bill_numbers():
    text = cb.compose_post(_report(["S.3257", "S.3258", "HR.2388", "SRES.902"]))
    assert text == (
        "Congress, Thursday, September 24. The Senate passed 3 bills, agreed to 4 resolutions and took 3 "
        "record votes. The House met for 3 minutes and took no record votes. "
        "Senate passed: S. 3257, S. 3258, H.R. 2388."
    )


def test_a_long_list_says_how_many_more():
    text = cb.compose_post(_report([f"S.{n}" for n in range(1, 8)]))
    assert text.endswith("Senate passed: S. 1, S. 2, S. 3, S. 4 and 3 more.")


def _day(db, iso, chamber, in_session=True, final=True):
    db.add(CongressDay(chamber=chamber, date=iso, in_session=in_session, is_final=final, source="digest"))


_WEEK_SENTENCE = "The Senate met 3 days and took 12 record votes. The House did not meet."


def _card(description):
    return {"title": "Congress", "description": description, "image": "", "image_alt": ""}


@pytest.fixture
def posting(db_session, monkeypatch, link_cards):
    """Returns a function listing the urls published so far, in order. Every
    Congress page already shows its report (page_shows)."""
    monkeypatch.setattr(cb, "day_report", lambda db, day: {**_report(["S.1"]), "date": day.isoformat()})
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: _card(
        _WEEK_SENTENCE if "/week/" in url else _report([])["sentence"]))
    return lambda: [r.url for r in db_session.query(BroadcastPost).order_by(BroadcastPost.id)]


def test_a_day_waits_until_its_page_shows_the_final_record(db_session, posting, monkeypatch):
    # The page still rendered from before the Digest (2026-10-01's card read
    # "No record of the Senate for this day yet" beside a post saying it met).
    _day(db_session, "2026-09-24", "senate")
    _day(db_session, "2026-09-24", "house")
    db_session.commit()
    stale = "No record of the Senate for this day yet. The House met for 3 minutes and took no record votes."
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: _card(stale))
    assert cb.post_daily_congress(db_session, date(2026, 9, 26)) is None
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: None)  # unreadable: waits too
    assert cb.post_daily_congress(db_session, date(2026, 9, 26)) is None
    assert posting() == []
    # The page catches up: a description cut at 160 characters still matches.
    sentence = _report([])["sentence"]
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: _card(sentence[:80].rstrip() + "…"))
    assert cb.post_daily_congress(db_session, date(2026, 9, 26)) == date(2026, 9, 24)


@pytest.mark.parametrize("shown,ok", [
    ("The Senate met and took no record votes.", False),
    ("", False),
    ("The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes. The House met for 3 minutes and took no record votes.", True),
])
def test_page_shows(monkeypatch, shown, ok):
    monkeypatch.setattr(broadcast, "fetch_og_card", lambda url: _card(shown))
    assert cb.page_shows("https://civitas-research.org/congress/2026-09-24", _report([])["sentence"]) is ok


def test_posts_the_newest_final_session_day_once(db_session, posting):
    for iso in ("2026-09-23", "2026-09-24"):
        _day(db_session, iso, "senate")
        _day(db_session, iso, "house", in_session=False)
    db_session.commit()
    today = date(2026, 9, 26)
    assert cb.post_daily_congress(db_session, today) == date(2026, 9, 24)
    assert cb.post_daily_congress(db_session, today) == date(2026, 9, 23)
    assert cb.post_daily_congress(db_session, today) is None
    assert posting() == ["https://civitas-research.org/congress/2026-09-24", "https://civitas-research.org/congress/2026-09-23"]


def test_never_a_day_still_on_the_floor_log_a_day_nobody_met_or_an_old_day(db_session, posting):
    _day(db_session, "2026-09-25", "senate", final=False)   # live, not final
    _day(db_session, "2026-09-25", "house")
    _day(db_session, "2026-09-26", "senate", in_session=False)  # neither met
    _day(db_session, "2026-09-26", "house", in_session=False)
    _day(db_session, "2026-06-10", "senate")                # back-filled history
    _day(db_session, "2026-06-10", "house")
    db_session.commit()
    assert cb.post_daily_congress(db_session, date(2026, 9, 27)) is None
    assert posting() == []


def test_a_day_bluesky_refused_is_still_published_once(db_session, posting, bluesky_configured):
    """The feed is the record: Bluesky refusing a post doesn't unpublish it
    or publish it again (app.broadcast retries the Bluesky send itself)."""
    _day(db_session, "2026-09-24", "senate")
    _day(db_session, "2026-09-24", "house")
    db_session.commit()
    bluesky_configured.ok = False
    assert cb.post_daily_congress(db_session, date(2026, 9, 25)) == date(2026, 9, 24)
    assert cb.post_daily_congress(db_session, date(2026, 9, 25)) is None
    assert db_session.query(BroadcastPost).one().bsky_status == "failed"


def test_published_without_a_bluesky_account(db_session, posting):
    _day(db_session, "2026-09-24", "senate")
    _day(db_session, "2026-09-24", "house")
    db_session.commit()
    assert cb.post_daily_congress(db_session, date(2026, 9, 25)) == date(2026, 9, 24)
    post = db_session.query(BroadcastPost).one()
    assert (post.kind, post.subject, post.title, post.bsky_status) == (
        "congress_day", "congress-day:2026-09-24", "Congress, Thursday, September 24", "off")
    assert post.text.startswith("Congress, Thursday, September 24. ")


def test_a_day_posted_to_bluesky_before_the_feed_existed_is_not_posted_again(db_session, posting):
    _day(db_session, "2026-09-24", "senate")
    _day(db_session, "2026-09-24", "house")
    db_session.commit()
    api_cache_set(db_session, "bsky-congress", "2026-09-24", {"posted": True}, normal_ttl_hours=24 * 30)
    assert cb.post_daily_congress(db_session, date(2026, 9, 25)) is None
    assert posting() == []


def _week(sentence="The Senate met 3 days and took 12 record votes. The House did not meet.", laws=()):
    return {"start": "2026-09-21", "end": "2026-09-27", "sentence": sentence,
            "totals": {"senate": {"recordVotes": 12}, "house": {"recordVotes": 0}},
            "becameLaw": [{"billId": b} for b in laws], "passedOneChamber": []}


def test_the_weekly_post_is_the_week_sentence_and_new_laws():
    assert cb.compose_week_post(_week(laws=["HR.1", "S.2"])) == (
        "Congress, week of September 21–27. The Senate met 3 days and took 12 record votes. "
        "The House did not meet. Became law: H.R. 1, S. 2."
    )


def test_a_week_across_two_months_names_both():
    report = {**_week(), "start": "2026-09-28", "end": "2026-10-04"}
    assert cb.compose_week_post(report).startswith("Congress, week of September 28–October 4. ")


@pytest.fixture
def week_posting(posting, monkeypatch):
    monkeypatch.setattr(cb, "week_report", lambda db, day: {**_week(), "start": day.isoformat()})
    return posting


def test_last_week_posts_once_when_every_day_of_it_is_final(db_session, week_posting):
    _day(db_session, "2026-09-22", "senate")
    _day(db_session, "2026-09-24", "senate", final=False)
    db_session.commit()
    monday = date(2026, 9, 28)
    assert cb.post_weekly_congress(db_session, monday) is None  # Thursday still the floor log
    db_session.query(CongressDay).update({"is_final": True})
    db_session.commit()
    assert cb.post_weekly_congress(db_session, monday) == date(2026, 9, 21)
    assert cb.post_weekly_congress(db_session, date(2026, 9, 30)) is None  # once
    assert week_posting() == ["https://civitas-research.org/congress/week/2026-09-21"]


def test_a_week_nobody_met_or_an_older_week_is_not_posted(db_session, week_posting):
    _day(db_session, "2026-09-22", "senate", in_session=False)
    _day(db_session, "2026-09-15", "senate")
    db_session.commit()
    assert cb.post_weekly_congress(db_session, date(2026, 9, 28)) is None
    assert week_posting() == []


def test_the_pre_feed_marker_is_still_written_for_a_rollback(db_session, posting):
    """The image before this one knows a posted day only by its api_cache
    marker; a rollback to it must not post the day again."""
    from app.pipeline.cache import api_cache_get

    _day(db_session, "2026-09-24", "senate")
    _day(db_session, "2026-09-24", "house")
    db_session.commit()
    cb.post_daily_congress(db_session, date(2026, 9, 25))
    assert api_cache_get(db_session, "bsky-congress", "2026-09-24", max_age_hours=24 * 30)


# ── Days with nothing on the record ───────────────────────────────

@pytest.mark.parametrize("counts,expected", [
    ([{"recordVotes": 0, "billsPassed": 0}, {}], False),  # a pro forma session
    ([{"recordVotes": 1}, {}], True),
    ([{}, {"resolutionsPassed": 2}], True),
    ([{"confirmed": 3}, {}], True),
    ([{"confirmedLists": 1}, {}], True),
    ([{"reported": 4, "committeeMeetings": 6}, {}], False),  # not floor business
])
def test_has_business(counts, expected):
    assert cb.has_business(counts) is expected


def test_a_day_with_nothing_on_the_record_is_skipped_for_the_one_before(db_session, posting, monkeypatch):
    for iso in ("2026-10-08", "2026-10-09"):
        _day(db_session, iso, "senate")
        _day(db_session, iso, "house")
    db_session.commit()

    def report(db, day):
        r = {**_report(["S.1"]), "date": day.isoformat()}
        if day == date(2026, 10, 9):
            r["chambers"]["senate"]["counts"] = {"recordVotes": 0, "billsPassed": 0}
        return r

    monkeypatch.setattr(cb, "day_report", report)
    assert cb.post_daily_congress(db_session, date(2026, 10, 11)) == date(2026, 10, 8)
    assert cb.post_daily_congress(db_session, date(2026, 10, 11)) is None
    assert posting() == ["https://civitas-research.org/congress/2026-10-08"]


def test_a_week_with_nothing_on_the_record_posts_only_if_a_bill_became_law(db_session, week_posting, monkeypatch):
    _day(db_session, "2026-09-22", "senate")
    db_session.commit()
    idle = {**_week(), "totals": {"senate": {"recordVotes": 0}, "house": {}}}
    monkeypatch.setattr(cb, "week_report", lambda db, day: {**idle, "start": day.isoformat()})
    assert cb.post_weekly_congress(db_session, date(2026, 9, 28)) is None
    monkeypatch.setattr(cb, "week_report", lambda db, day: {
        **idle, "start": day.isoformat(), "becameLaw": [{"billId": "HR.1"}]})
    assert cb.post_weekly_congress(db_session, date(2026, 9, 28)) == date(2026, 9, 21)


# ── The look-ahead ────────────────────────────────────────────────

_MON, _SUN = date(2026, 10, 12), date(2026, 10, 18)


def test_the_lookahead_names_each_chamber_and_the_bills_waiting():
    text, business = cb.compose_lookahead(_MON, _SUN, {
        "senate": (date(2026, 10, 13), "Program for Tuesday: Senate will resume consideration of S. 5."),
        "house": (date(2026, 11, 9), ""),
    }, [{"billId": "S.12", "nextStep": "house"}, {"billId": "HR.7", "nextStep": "senate"},
        {"billId": "S.12", "nextStep": "house"}])
    assert business
    assert text == (
        "Next week in Congress, October 12–18. The Senate next meets Tuesday, October 13. "
        "The House is out until Monday, November 9. Now before the Senate, from the House: H.R. 7. "
        "Now before the House, from the Senate: S. 12."
    )


def test_a_week_of_pro_forma_sessions_is_not_business():
    text, business = cb.compose_lookahead(_MON, _SUN, {
        "senate": (date(2026, 10, 13), "Program for Tuesday: Senate will meet in a pro forma session."),
        "house": (date(2026, 10, 16), "Program for Friday: House will meet in pro forma session at 9 a.m."),
    }, [])
    assert not business
    assert "The Senate next meets Tuesday, October 13, in pro forma session." in text
    assert cb._chamber_ahead("house", None, "", _MON, _SUN) == ("The House's next meeting is not on record yet.", False)
    # No program, or "To be announced": not known to be business.
    for program in ("", "Program for Monday: To be announced."):
        assert cb._chamber_ahead("house", _MON, program, _MON, _SUN) == ("The House next meets Monday, October 12.", False)


def test_a_long_lookahead_names_fewer_bills_to_go_out_whole():
    passed = [{"billId": f"HR.{9000 + n}", "nextStep": "senate"} for n in range(9)]
    passed += [{"billId": f"SJRES.{100 + n}", "nextStep": "house"} for n in range(9)]
    text, _ = cb.compose_lookahead(_MON, _SUN, {
        "senate": (date(2026, 10, 13), ""), "house": (date(2026, 10, 14), ""),
    }, passed)
    assert len(text) + 1 + len(cb._LOOKAHEAD_URL) <= 300
    assert text.count(" more.") == 2


def _notice(db, iso, chamber, when, program=""):
    db.add(CongressDay(chamber=chamber, date=iso, in_session=True, is_final=True, source="digest",
                       next_meeting=when, next_program=program))


@pytest.fixture
def lookahead(db_session, monkeypatch, link_cards):
    monkeypatch.setattr(cb, "week_report", lambda db, day: {
        "passedOneChamber": [{"billId": "S.4", "nextStep": "house"}]})
    return lambda: [(p.kind, p.subject, p.url, p.text)
                    for p in db_session.query(BroadcastPost).order_by(BroadcastPost.id)]


def test_the_lookahead_posts_once_from_saturday(db_session, lookahead):
    _notice(db_session, "2026-10-08", "senate", "10 a.m., Friday, October 9")
    _notice(db_session, "2026-10-09", "senate", "3 p.m., Monday, October 12",
            "Program for Monday: Senate will vote on S. 4.")
    _notice(db_session, "2026-10-09", "house",
            "The House is scheduled to reconvene at 2 p.m. Tuesday, October 13, 2026.")
    db_session.commit()
    assert cb.post_congress_lookahead(db_session, date(2026, 10, 9)) is None  # Friday: not yet
    assert cb.post_congress_lookahead(db_session, date(2026, 10, 10)) == _MON
    assert cb.post_congress_lookahead(db_session, date(2026, 10, 11)) is None  # once
    [(kind, subject, url, text)] = lookahead()
    assert (kind, subject, url) == (
        "congress_ahead", "congress-ahead:2026-10-12", "https://civitas-research.org/congress")
    assert text == (
        "Next week in Congress, October 12–18. The Senate next meets Monday, October 12. "
        "The House next meets Tuesday, October 13. Now before the House, from the Senate: S. 4."
    )


def test_the_lookahead_reads_the_program_from_the_digest_when_the_floor_log_has_none(db_session, lookahead):
    # The floor log's notice (no program) is newer than the Digest's for the
    # same meeting, which says it is pro forma: nothing ahead, nothing posted.
    _notice(db_session, "2026-10-09", "senate", "3:30 p.m., Tuesday, October 13",
            "Program for Tuesday: Senate will meet in a pro forma session.")
    _notice(db_session, "2026-10-10", "senate",
            "The Senate is scheduled to reconvene at 3:30 p.m. Tuesday, October 13, 2026.")
    _notice(db_session, "2026-10-09", "house", "1:30 p.m., Tuesday, October 13",
            "Program for Tuesday: House will meet in pro forma session at 1:30 p.m.")
    db_session.commit()
    assert cb.post_congress_lookahead(db_session, date(2026, 10, 11)) is None
    assert lookahead() == []
