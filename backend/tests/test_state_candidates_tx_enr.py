"""Texas's live reader (state_candidates_tx.fetch_general_results).

Fixtures, read 2026-11-09 from goelect.txelections.civixapps.com, trimmed:

  fixtures_tx_elections_2026.json  The candidate portal's election list
      for 2026 (getElectionsByYear), four fields kept per election.
  fixtures_tx_enr_general_2026.json  The results app's answer for the
      2026 general (s3/enr/election/53815): its Version, its Home, and its
      Federal part cut to the Senate race and districts 19 and 28, each
      part base64-encoded JSON as served.
"""

import base64
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import election_results
from app.pipeline.fetch import state_candidates_tx as tx
from app.pipeline.fetch.election_results import FeedUnreadable, UntrustedCount
from app.pipeline.fetch.state_candidate_sources import source_for_state

FIXTURES = Path(__file__).parent
DAY = date(2026, 11, 3)
ELECTIONS = json.loads((FIXTURES / "fixtures_tx_elections_2026.json").read_text())
GENERAL = json.loads((FIXTURES / "fixtures_tx_enr_general_2026.json").read_text())
SOURCE = election_results.live_source(source_for_state("TX"))
LIST_URL = f"{tx.CIVIX_BASE}/getElectionsByYear/2026"
RESULTS_URL = f"{tx.ENR_BASE}/election/53815"


def _serve(monkeypatch, pages: dict):
    async def fake(client, limiter, method, url, **kwargs):
        got = pages.get(url)
        if got is None:
            return None
        return SimpleNamespace(status_code=200, json=lambda: got)

    monkeypatch.setattr(tx, "fetch_with_retry", fake)


def _with(part: str, value) -> dict:
    out = dict(GENERAL)
    out[part] = base64.b64encode(json.dumps(value).encode()).decode()
    return out


def _seats(count):
    return {(c.office, c.district): c for c in count.contests}


async def test_the_registered_reader_reads_the_general_by_its_date(monkeypatch):
    _serve(monkeypatch, {LIST_URL: ELECTIONS, RESULTS_URL: GENERAL})
    assert election_results._reader_for(source_for_state("TX")) is tx.fetch_general_results
    count = await tx.fetch_general_results(None, DAY, "TX", SOURCE)
    assert count.official is False and count.source_version == "53815:117"
    seats = _seats(count)
    assert set(seats) == {("S", None), ("H", 19), ("H", 28)}
    senate = seats[("S", None)]
    assert ("JAMES TALARICO", "D", 5444982) in senate.candidates
    assert ("KEN PAXTON", "R", 4249373) in senate.candidates
    # Write-in candidates keep their votes with no party.
    assert ("AVERY AYERS", None, 1925) in senate.candidates
    assert senate.total_votes == 9889704 == senate.votes_counted
    # No reporting figure: the source's precinct field doesn't say a count is complete.
    assert all(c.reporting_units is None and c.total_units is None for c in count.contests)


async def test_a_day_with_no_election_is_none(monkeypatch):
    _serve(monkeypatch, {LIST_URL: ELECTIONS})
    assert await tx.fetch_general_results(None, date(2026, 11, 4), "TX", SOURCE) is None


async def test_an_unreadable_list_or_count_is_unreadable(monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(FeedUnreadable):
        await tx.fetch_general_results(None, DAY, "TX", SOURCE)
    _serve(monkeypatch, {LIST_URL: ELECTIONS})
    with pytest.raises(FeedUnreadable):
        await tx.fetch_general_results(None, DAY, "TX", SOURCE)
    _serve(monkeypatch, {LIST_URL: ELECTIONS, RESULTS_URL: {**GENERAL, "Federal": "not base64 json"}})
    with pytest.raises(FeedUnreadable):
        await tx.fetch_general_results(None, DAY, "TX", SOURCE)


async def test_a_count_for_another_day_or_election_stores_nothing(monkeypatch):
    home = json.loads(base64.b64decode(GENERAL["Home"]))
    _serve(monkeypatch, {LIST_URL: ELECTIONS, RESULTS_URL: _with("Home", {**home, "ElecDate": "03032026"})})
    with pytest.raises(UntrustedCount):
        await tx.fetch_general_results(None, DAY, "TX", SOURCE)
    _serve(monkeypatch, {LIST_URL: ELECTIONS, RESULTS_URL: {**GENERAL, "Version": "enr/53813/135/"}})
    with pytest.raises(UntrustedCount):
        await tx.fetch_general_results(None, DAY, "TX", SOURCE)
