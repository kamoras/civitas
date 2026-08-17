"""Tests for reading a state's PRIMARY ballot out of its candidate filing
list (state_candidate_filings.py).

Rows are the real shape of North Carolina's 2026 filing list, reduced: one
row per county per candidate, an election_dt that distinguishes the March
primary from the November general, and a party_contest that is empty for
the unaffiliated candidates who face no primary at all.
"""

import pytest

from app.pipeline.fetch import state_candidate_filings as filings

_HEADER = (
    "election_dt,county_name,contest_name,name_on_ballot,party_contest,street_address\n"
)
_CSV = (
    _HEADER
    + '"03/03/2026","ALAMANCE","US SENATE","Michael Dublin","DEM","1 Main St"\n'
    + '"03/03/2026","WAKE","US SENATE","Michael Dublin","DEM","1 Main St"\n'
    + '"03/03/2026","WAKE","US SENATE","Don Brown","REP","2 Oak Ave"\n'
    + '"03/03/2026","WAKE","US HOUSE OF REPRESENTATIVES DISTRICT 04",'
      '"Valerie Foushee","DEM","3 Elm Rd"\n'
    + '"03/03/2026","WAKE","NC HOUSE OF REPRESENTATIVES DISTRICT 022",'
      '"Some Legislator","REP","4 Pine Ln"\n'
    + '"11/03/2026","WAKE","US SENATE","An Unaffiliated","","5 Cedar Ct"\n'
).encode()

_SOURCE = {
    "filings": {
        "discovery": {"mode": "direct_url", "url": "https://example.gov/filings.csv"},
        "format": {
            "delimiter": ",",
            "contest_column": "contest_name",
            "choice_column": "name_on_ballot",
            "party_column": "party_contest",
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
class TestFetchPrimaryCandidates:
    async def test_lists_everyone_on_the_primary_ballot(self, _served):
        records, _ = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert sorted((r["party"], r["last_name"]) for r in records) == [
            ("D", "Dublin"), ("D", "Foushee"), ("R", "Brown"),
        ]

    async def test_deduplicates_the_per_county_rows(self, _served):
        """A filing list is a set of people, not a tally — the same
        candidate appears once per county."""
        records, _ = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert [r["last_name"] for r in records].count("Dublin") == 1

    async def test_reads_the_primary_date_from_the_file(self, _served):
        """The other half of why this is worth reading: a primary date is
        not derivable from any statute, and here the state states it."""
        _, held = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert held == "2026-03-03"

    async def test_the_november_filers_do_not_set_the_primary_date(self, _served):
        """An unaffiliated candidate files straight for the general, so the
        file carries both dates and the EARLIER one is the primary."""
        _, held = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert held != "2026-11-03"

    async def test_a_candidate_with_no_party_is_not_put_in_a_primary(self, _served):
        """They face no primary at all — a real ballot fact, but not this
        one, so it is skipped rather than guessed into somebody's."""
        records, _ = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert all(r["last_name"] != "Unaffiliated" for r in records)

    async def test_a_state_race_that_looks_federal_is_still_refused(self, _served):
        """Same control the results adapters use: North Carolina's own
        legislature uses "HOUSE OF REPRESENTATIVES DISTRICT nnn" too."""
        records, _ = await filings.fetch_primary_candidates(None, 2026, "NC", _SOURCE)
        assert all(r["last_name"] != "Legislator" for r in records)

    async def test_an_undiscoverable_list_returns_none(self, monkeypatch):
        records = await filings.fetch_primary_candidates(
            None, 2026, "NC", {"filings": {"discovery": {"mode": "carrier_pigeon"}}},
        )
        assert records is None
