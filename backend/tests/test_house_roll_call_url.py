"""The House Clerk names a roll call's file zero-padded to three digits
(roll001.xml) and answers an unpadded name (roll1.xml) with 200 and its HTML
site, not a 404 (verified live 2026-10-14). Every roll-call fetch builds its
URL through floor_logs.house_roll_call_url; a hand-built unpadded URL read
every roll below 100 as missing, so early in a year the House's recent votes
came back empty and a bill voted on in the first hundred roll calls had no
vote."""

import re
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch import congress
from app.pipeline.fetch.floor_logs import house_roll_call_url, senate_roll_call_url

_VOTE = """<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote><vote-metadata><congress>119</congress><session>1st</session>
<action-date>06-Jan-2025</action-date><vote-question>On Passage</vote-question>
<legis-num>H R 5</legis-num><vote-desc>A bill</vote-desc><vote-result>Passed</vote-result></vote-metadata>
<vote-data><recorded-vote><legislator name-id="A000001" sort-field="Doe" party="D" state="OH">Doe</legislator>
<vote>Yea</vote></recorded-vote></vote-data></rollcall-vote>"""


class _Resp:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


class _Clerk:
    """Rolls 1..last exist, served only under their padded name, as the Clerk does."""

    def __init__(self, last):
        self.last = last

    async def get(self, url, timeout=None):
        name = re.search(r"/roll(\d+)\.xml$", url).group(1)
        n = int(name)
        if len(name) < 3:
            return _Resp(200, "<!DOCTYPE html><html><body>Office of the Clerk</body></html>")
        if n <= self.last:
            return _Resp(200, _VOTE)
        return _Resp(200, f'<xml>Error sanitizing file "roll{name}.xml". Please try again.</xml>')


def test_urls_are_padded_as_each_chamber_names_its_files():
    assert house_roll_call_url(2025, 6) == "https://clerk.house.gov/evs/2025/roll006.xml"
    assert house_roll_call_url(2025, 314) == "https://clerk.house.gov/evs/2025/roll314.xml"
    assert senate_roll_call_url(119, 1, 6).endswith("/vote1191/vote_119_1_00006.xml")


@pytest.fixture()
def _no_rate_limit():
    with patch("app.pipeline.fetch.congress._rate_limiter.acquire", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.usefixtures("_no_rate_limit")
async def test_a_roll_below_100_is_read(db_session):
    vote = await congress.fetch_house_roll_call_vote(_Clerk(40), db_session, 2025, 6)
    assert vote is not None and vote["rollNumber"] == 6


@pytest.mark.usefixtures("_no_rate_limit")
async def test_a_young_year_has_its_votes(db_session):
    """With fewer than 100 rolls in the year, the recent votes were []."""
    votes = await congress.fetch_recent_house_roll_calls(_Clerk(40), db_session, year=2025, count=15)
    assert [v["rollNumber"] for v in votes] == list(range(40, 25, -1))
