"""South Dakota reads a certified BALLOT, not primary results.

SD sat on `totalvote_enr` and returned nothing for 116 days after its
primary. Neither the adapter nor the vendor was broken:
electionresults.sd.gov serves whatever election is CURRENT, and SD held
a 2026-07-28 runoff carrying only Governor and Secretary of State, so
the June primary's federal results left the view with no archive to
reach them. Montana and Nebraska run the same strategy unaffected —
neither held a runoff.

The fixture is trimmed from the real page fetched 2026-09-26.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.pipeline.fetch.state_candidates_common import normalize_party
from app.pipeline.fetch.state_candidates_sd_vip import (
    _discover_eid,
    _rows,
    fetch_confirmed_candidates,
)

FIXTURE = (Path(__file__).parent / "fixtures_sd_vip_candidates.html").read_text()


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _client(*pages):
    return AsyncMock(get=AsyncMock(side_effect=[_Resp(p) for p in pages]))


class TestTheGrid:
    def test_reads_office_name_and_party_from_the_real_shape(self):
        rows = _rows(FIXTURE)
        assert ("United States Senator", "Mike Rounds", "REP") in rows

    def test_no_grid_yields_nothing_rather_than_raising(self):
        assert _rows("<html><body><p>nope</p></body></html>") == []


class TestEidDiscovery:
    def test_finds_the_election_id_the_sos_links_to(self):
        assert _discover_eid('<a href="https://vip.sdsos.gov/candidatelist.aspx?eid=774">x</a>') == "774"

    def test_takes_the_newest_when_several_are_linked(self):
        html = 'href="candidatelist.aspx?eid=701" href="candidatelist.aspx?eid=774"'
        assert _discover_eid(html) == "774"

    def test_no_link_is_a_miss_not_a_guess(self):
        assert _discover_eid("<html>nothing here</html>") is None


@pytest.mark.asyncio
class TestFetch:
    async def test_returns_the_federal_ballot(self):
        got = await fetch_confirmed_candidates(_client(FIXTURE), 2026, "SD", {"eid": "774"})
        federal = {(r["office"], r["party"], r["last_name"]) for r in got}
        assert ("S", "R", "Rounds") in federal
        assert ("H", "D", "Gronli") in federal
        assert ("H", "R", "Jackley") in federal

    async def test_keeps_the_independent_a_primary_could_never_show(self):
        """Brian Bengs is on the November Senate ballot as an IND. A
        primary-results source structurally cannot see him, which is the
        whole reason this reads a ballot instead."""
        got = await fetch_confirmed_candidates(_client(FIXTURE), 2026, "SD", {"eid": "774"})
        assert ("S", "I", "Bengs") in {(r["office"], r["party"], r["last_name"]) for r in got}

    async def test_drops_a_withdrawn_candidate_still_printed_on_the_page(self):
        """The portal keeps withdrawn filers listed, marked in the name.
        They are on the page and not on the ballot."""
        got = await fetch_confirmed_candidates(_client(FIXTURE), 2026, "SD", {"eid": "774"})
        assert all("Beaudion" != r["last_name"] for r in got)

    async def test_statewide_offices_only_when_the_state_opts_in(self):
        base = await fetch_confirmed_candidates(_client(FIXTURE), 2026, "SD", {"eid": "774"})
        opted = await fetch_confirmed_candidates(
            _client(FIXTURE), 2026, "SD", {"eid": "774", "statewide_offices": True},
        )
        assert all(r["office"] in ("S", "H") for r in base)
        assert any(r["office"] == "governor" for r in opted)

    async def test_a_page_with_no_federal_contest_is_a_failure_not_an_empty_ballot(self):
        """Ballot order puts the federal races on page 1. If that ever
        stops being true, None says so — [] would read as "nobody is
        running for Congress in South Dakota", which is the SD bug all
        over again in a new costume."""
        only_state = FIXTURE.replace("United States Senator", "County Commissioner").replace(
            "United States Representative", "County Auditor",
        )
        assert await fetch_confirmed_candidates(_client(only_state), 2026, "SD", {"eid": "774"}) is None

    async def test_discovers_the_eid_when_not_pinned(self):
        landing = '<a href="https://vip.sdsos.gov/candidatelist.aspx?eid=774">2026 candidates</a>'
        got = await fetch_confirmed_candidates(_client(landing, FIXTURE), 2026, "SD", {})
        assert any(r["last_name"] == "Rounds" for r in got)


class TestIndependentsOnlyOnABallot:
    def test_a_primary_source_still_refuses_to_read_one(self):
        """The default must not change: for primary RESULTS an
        "Independent" label is one this cannot interpret, and that guard
        protects every other adapter."""
        assert normalize_party("IND") is None
        assert normalize_party("Independent") is None

    def test_a_ballot_source_reads_one(self):
        assert normalize_party("IND", ballot_list=True) == "I"
        assert normalize_party("No Party Affiliation", ballot_list=True) == "I"

    def test_a_party_name_containing_a_state_is_not_mistaken_for_one(self):
        assert normalize_party("INDIANA REPUBLICAN", ballot_list=True) == "R"
