"""Tests for reading a state's ballots out of its candidate filing list
(state_candidate_filings.py).

Rows are the real shape of North Carolina's 2026 filing list, reduced: one
row per county per candidate, an election_dt that distinguishes the March
primary from the November general, and the state's two party columns —
party_contest (which primary they ran in, empty for November) and
party_candidate (their own party, which is where LIB and GRE appear).
"""

import pytest

from app.pipeline.fetch import state_candidate_filings as filings

_HEADER = (
    "election_dt,county_name,contest_name,name_on_ballot,party_contest,"
    "party_candidate,street_address\n"
)
_CSV = (
    _HEADER
    + '"03/03/2026","ALAMANCE","US SENATE","Michael Dublin","DEM","DEM","1 Main St"\n'
    + '"03/03/2026","WAKE","US SENATE","Michael Dublin","DEM","DEM","1 Main St"\n'
    + '"03/03/2026","WAKE","US SENATE","Don Brown","REP","REP","2 Oak Ave"\n'
    + '"03/03/2026","WAKE","US HOUSE OF REPRESENTATIVES DISTRICT 04",'
      '"Valerie Foushee","DEM","DEM","3 Elm Rd"\n'
    + '"03/03/2026","WAKE","NC HOUSE OF REPRESENTATIVES DISTRICT 022",'
      '"Some Legislator","REP","REP","4 Pine Ln"\n'
    # The November ballot: the nominees, plus a Libertarian who never ran
    # in any primary, plus an unaffiliated candidate with no party at all.
    + '"11/03/2026","WAKE","US SENATE","Michael Dublin","","DEM","1 Main St"\n'
    + '"11/03/2026","WAKE","US SENATE","Tom Bailey","","LIB","5 Cedar Ct"\n'
    + '"11/03/2026","WAKE","US SENATE","An Unaffiliated","","","6 Birch Way"\n'
).encode()

_SOURCE = {
    "filings": {
        "discovery": {"mode": "direct_url", "url": "https://example.gov/filings.csv"},
        "format": {
            "delimiter": ",",
            "contest_column": "contest_name",
            "choice_column": "name_on_ballot",
            "party_column": "party_contest",
            "candidate_party_column": "party_candidate",
            "election_date_column": "election_dt",
        },
    },
}


class _Resp:
    def __init__(self, content):
        self.content = content


@pytest.fixture
def _served(monkeypatch):
    from app.pipeline.fetch import state_candidates_tabular as tb

    async def fake_get(client, url, label):
        return _Resp(_CSV)

    monkeypatch.setattr(tb, "_get", fake_get)


@pytest.mark.asyncio
class TestFetchBallotCandidates:
    async def test_lists_everyone_on_the_primary_ballot(self, _served):
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert sorted((r["party"], r["last_name"]) for r in found["primary"]) == [
            ("D", "Dublin"), ("D", "Foushee"), ("R", "Brown"),
        ]

    async def test_a_minor_party_candidate_reaches_the_general_ballot(self, _served):
        """The reason the general list is read at all: a Libertarian never
        appears in ANY primary, so nominees derived from primary results
        cannot see them — and a race with any confirmed candidate shows
        only confirmed candidates, so they vanish from the page."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert ("L", "Bailey") in {(r["party"], r["last_name"]) for r in found["general"]}

    async def test_an_unaffiliated_candidate_is_kept_for_the_general(self, _served):
        """They belong to no party and run in no primary, but they are on
        the November ballot — kept with an empty party the matcher falls
        back on surname for, never guessed into somebody's primary."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert ("", "Unaffiliated") in {(r["party"], r["last_name"]) for r in found["general"]}
        assert all(r["last_name"] != "Unaffiliated" for r in found["primary"])

    async def test_deduplicates_the_per_county_rows(self, _served):
        """A filing list is a set of people, not a tally — the same
        candidate appears once per county."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert [r["last_name"] for r in found["primary"]].count("Dublin") == 1

    async def test_reads_the_primary_date_from_the_file(self, _served):
        """The other half of why this is worth reading: a primary date is
        not derivable from any statute, and here the state states it."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert found["primary_date"] == "2026-03-03"

    async def test_the_november_filings_do_not_set_the_primary_date(self, _served):
        """The file carries both elections; only the earlier one is a
        primary."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        assert found["primary_date"] != "2026-11-03"

    async def test_a_state_race_that_looks_federal_is_still_refused(self, _served):
        """Same control the results adapters use: North Carolina's own
        legislature uses "HOUSE OF REPRESENTATIVES DISTRICT nnn" too."""
        found = await filings.fetch_ballot_candidates(None, 2026, "NC", _SOURCE)
        every = found["primary"] + found["general"]
        assert all(r["last_name"] != "Legislator" for r in every)

    async def test_an_undiscoverable_list_returns_none(self, monkeypatch):
        found = await filings.fetch_ballot_candidates(
            None, 2026, "NC", {"filings": {"discovery": {"mode": "carrier_pigeon"}}},
        )
        assert found is None
