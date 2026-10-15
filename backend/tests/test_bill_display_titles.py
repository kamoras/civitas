"""A bill's short or popular title, from Congress.gov's titles list.

H.R. 1 of the 119th Congress read "An act to provide for reconciliation
pursuant to title II of H. Con. Res. 14." in the bill list, on its page and in
search, where its titles list says "One Big Beautiful Bill Act". The fixture
below is that list's shape, trimmed.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.models import RollCall, Senator, SponsoredBill
from app.pipeline.cache import api_cache_set
from app.pipeline.fetch import bill_display_titles
from app.pipeline.fetch.bill_display_titles import display_title, refresh_display_titles, titles_key
from app.services.bill_record import roll_call_summaries
from app.services.bill_service import clear_bill_collection_cache, get_bill_detail, get_bills_in_flight

OFFICIAL = "An act to provide for reconciliation pursuant to title II of H. Con. Res. 14."
HR1_TITLES = [
    {"titleType": "Display Title", "title": OFFICIAL, "updateDate": "2025-12-04T15:37:19Z"},
    # A part of the bill, never its name.
    {"titleType": "Short Titles as Enacted for portions of this bill", "title": "FEHB Protection Act of 2025",
     "updateDate": "2025-07-22T20:43:24Z"},
    {"titleType": "Official Title as Enacted", "title": OFFICIAL, "updateDate": "2025-07-22T20:43:07Z"},
    {"titleType": "Popular Titles", "title": "One Big Beautiful Bill Act", "updateDate": "2025-07-09T20:08:42Z"},
    {"titleType": "Short Title(s) as Passed House", "title": "One Big Beautiful Bill Act",
     "updateDate": "2025-06-14T04:38:16Z"},
    {"titleType": "Short Title(s) as Reported to House", "title": "One Big Beautiful Bill Act",
     "updateDate": "2025-05-21T10:53:18Z"},
]


def test_the_newest_whole_bill_short_title_never_a_portion():
    assert display_title(HR1_TITLES, OFFICIAL) == (
        "One Big Beautiful Bill Act", "Short Title(s) as Passed House", OFFICIAL)


def test_enacted_wins_then_popular_is_the_last_resort():
    enacted = [*HR1_TITLES, {"titleType": "Short Titles as Enacted", "title": "Big Act of 2025",
                             "updateDate": "2025-01-01T00:00:00Z"}]
    assert display_title(enacted, OFFICIAL)[:2] == ("Big Act of 2025", "Short Titles as Enacted")
    popular_only = [t for t in HR1_TITLES if "Short Title(s)" not in t["titleType"]]
    assert display_title(popular_only, OFFICIAL)[:2] == ("One Big Beautiful Bill Act", "Popular Titles")


def test_the_official_title_is_the_lists_not_the_listing():
    # A listing title can be a short title the bill no longer goes by: the
    # official title shown under the short one comes from the list itself.
    titles = [
        {"titleType": "Short Title(s) as Introduced", "title": "New Name Act", "updateDate": "2025-08-09"},
        {"titleType": "Official Title as Introduced", "title": "A bill to settle certain claims.",
         "updateDate": "2025-08-09"},
    ]
    assert display_title(titles, "Old Name Act") == ("New Name Act", "Short Title(s) as Introduced",
                                                     "A bill to settle certain claims.")


def test_none_when_the_listing_already_shows_it_or_there_is_none():
    assert display_title(HR1_TITLES, "One Big Beautiful Bill Act") is None
    # An acronym and its expansion are both short titles: the listing's
    # acronym is kept, not swapped for the newer expansion.
    acronym = [
        {"titleType": "Short Title(s) as Introduced", "title": "TAKE IT DOWN Act", "updateDate": "2025-01-16"},
        {"titleType": "Short Titles from ENR (Enrolled) bill text", "updateDate": "2025-05-20",
         "title": "Tools to Address Known Exploitation by Immobilizing Technological Deepfakes on Websites and "
                  "Networks Act"},
    ]
    assert display_title(acronym, "TAKE IT DOWN Act") is None
    # So is a listing title the bill carries only as a portion's name.
    assert display_title([{"titleType": "Short Title(s) as Passed Senate for portions of this bill",
                           "title": "PRO Veterans Act of 2025"}], "PRO Veterans Act of 2025") is None
    assert display_title([t for t in HR1_TITLES if "Short" not in t["titleType"]
                          and "Popular" not in t["titleType"]], OFFICIAL) is None
    assert display_title([], OFFICIAL) is None


def _hr1(db_session, stage="ENACTED"):
    db_session.add(Senator(id="s1", name="Sen. Alpha", state="CA", party="R", is_current=True))
    db_session.add(SponsoredBill(senator_id="s1", bill_id="HR.1", title=OFFICIAL, stage=stage,
                                 congress=settings.CURRENT_CONGRESS, bill_type="HR", latest_action_date="2025-07-04"))
    db_session.commit()


def test_the_bill_list_page_and_search_carry_it(db_session):
    _hr1(db_session)
    api_cache_set(db_session, "congress", titles_key(settings.CURRENT_CONGRESS, "HR.1"), HR1_TITLES)
    clear_bill_collection_cache()

    listed = get_bills_in_flight(db_session).bills[0]
    assert (listed.title, listed.short_title, listed.short_title_type, listed.official_title) == (
        OFFICIAL, "One Big Beautiful Bill Act", "Short Title(s) as Passed House", OFFICIAL)
    assert get_bill_detail(db_session, "HR.1").short_title == "One Big Beautiful Bill Act"
    # Findable by the name people use, and still by the official title.
    assert [b.bill_id for b in get_bills_in_flight(db_session, q="big beautiful bill").bills] == ["HR.1"]
    assert [b.bill_id for b in get_bills_in_flight(db_session, q="reconciliation").bills] == ["HR.1"]


def test_a_bill_with_no_cached_titles_has_none(db_session):
    _hr1(db_session)
    clear_bill_collection_cache()
    assert get_bills_in_flight(db_session).bills[0].short_title is None


def test_a_vote_list_names_the_bill_by_it(db_session):
    api_cache_set(db_session, "congress", titles_key(119, "HR.1"), HR1_TITLES)
    db_session.add(RollCall(chamber="senate", congress=119, session=1, number=372, date="2025-07-01",
                            question="On Passage of the Bill", title=OFFICIAL.replace("An act", "A bill"),
                            result="Passed", yeas=51, nays=50, bill_id="HR.1"))
    db_session.commit()
    summary = roll_call_summaries(db_session, ["senate-119-1-372"])["senate-119-1-372"]
    assert summary["billShortTitle"] == "One Big Beautiful Bill Act"
    assert summary["title"].startswith("A bill to provide")  # the chamber's own, kept


def test_the_hourly_pass_fetches_only_moved_bills_without_a_fresh_copy(db_session):
    _hr1(db_session)
    db_session.add(SponsoredBill(senator_id="s1", bill_id="S.9", title="A bill", stage="REFERRED",
                                 congress=settings.CURRENT_CONGRESS, bill_type="S", latest_action_date="2025-01-01"))
    db_session.commit()
    fetch = AsyncMock(return_value=HR1_TITLES)
    with patch.object(bill_display_titles, "fetch_bill_titles_or_none", fetch):
        summary = asyncio.run(refresh_display_titles(db_session))
    # H.R. 1 (enacted) is fetched; S. 9, still in committee, is not.
    assert [c.args[2:] for c in fetch.await_args_list] == [(settings.CURRENT_CONGRESS, "hr", 1)]
    assert summary == {"fetched": 1, "failed": 0, "pending": 0}


def test_a_failed_fetch_is_counted_not_stored(db_session):
    _hr1(db_session)
    with patch.object(bill_display_titles, "fetch_bill_titles_or_none", AsyncMock(return_value=None)):
        summary = asyncio.run(refresh_display_titles(db_session))
    assert summary == {"fetched": 0, "failed": 1, "pending": 0}
