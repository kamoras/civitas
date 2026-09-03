"""Tests for Tennessee's confirmed-general-candidate strategy
(state_candidates_tn.py).

fixtures_tn_precinct_rows.json is REAL — `_xlsx_rows()` output (a list
of row dicts) from the real "20260806AllbyPrecinct.xlsx" workbook,
fetched live 2026-09-03 from sos.tn.gov, trimmed to 4 real counties
(Carter, Blount, Bledsoe, Benton — chosen because each is the FIRST
county listed for one of the 4 real districts sampled here: 1, 2, 4, 5)
and the Senate race, which is statewide so every county carries it.
Real results from this subset: US House District 1 Republican (Diana
Harshbarger, unopposed), District 2 (both parties unopposed), District
4 Republican (Scott DesJarlais, a real majority winner over 2 real
challengers), District 5 Republican (a real race decided by barely
more than the 50% majority line — Hatcher over Ogles at ~51.3%), and
three real races genuinely below the 50% majority Tennessee law
requires (District 4 Democratic, District 5 Democratic, Senate
Democratic in this county subset), which Tennessee sends to a runoff
this module correctly does not attempt to resolve on its own.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import state_candidates_tn as tn

ROWS = json.loads((Path(__file__).parent / "fixtures_tn_precinct_rows.json").read_text())


class TestOfficeAndDistrict:
    def test_senate_has_no_district(self):
        assert tn._office_and_district("United States Senate") == ("S", None)

    def test_house_district_number(self):
        assert tn._office_and_district("United States House of Representatives District 4") == ("H", 4)

    def test_non_federal_office_is_none(self):
        assert tn._office_and_district("Governor") is None
        assert tn._office_and_district("State Senate District 4") is None


class TestSumPrecinctVotes:
    def test_real_majority_winners_resolved(self):
        choices = tn._sum_precinct_votes(ROWS)
        from app.pipeline.fetch.state_candidates_common import pick_nominee
        assert pick_nominee(choices[("H", 1, "R")], 50.0) == ("Harshbarger", pytest.approx(100.0))
        assert pick_nominee(choices[("H", 4, "R")], 50.0)[0] == "DesJarlais"

    def test_real_below_majority_race_is_absent_from_pick_nominee(self):
        # District 4's real Democratic field is a genuine 3-way split
        # under 50% -- Tennessee law sends this to a runoff, and
        # pick_nominee must not name a leader here.
        choices = tn._sum_precinct_votes(ROWS)
        from app.pipeline.fetch.state_candidates_common import pick_nominee
        assert pick_nominee(choices[("H", 4, "D")], 50.0) is None

    def test_votes_are_summed_across_multiple_precinct_rows(self):
        # Real data: more than one Carter County precinct reports
        # District 1 Republican votes for the same candidate: summing
        # must combine them, not just read the last row seen.
        choices = tn._sum_precinct_votes(ROWS)
        harshbarger_votes = dict(choices[("H", 1, "R")])["Harshbarger"]
        precinct_rows = [
            r for r in ROWS
            if r.get("OFFICENAME") == "United States House of Representatives District 1"
            and r.get("ELECTTYPE") == "Republican Primary"
        ]
        assert len(precinct_rows) > 1
        assert harshbarger_votes == sum(int(r["PVTALLY1"]) for r in precinct_rows)

    def test_write_in_rows_are_excluded(self):
        # Real data: every contest carries a trailing "Write-In - ..."
        # slot with PARTY "0" -- must never be counted as a candidate.
        choices = tn._sum_precinct_votes(ROWS)
        for race_choices in choices.values():
            names = [name for name, _ in race_choices]
            assert not any("Write-In" in n for n in names)

    def test_non_federal_rows_are_ignored(self):
        governor_rows = [r for r in ROWS if r.get("OFFICENAME") == "Governor"]
        assert governor_rows == []  # fixture only carries federal rows
        choices = tn._sum_precinct_votes(ROWS)
        assert all(office in ("S", "H") for (office, _, _) in choices)


_REAL_URL = "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20260806AllbyPrecinct.xlsx"


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    """_discover_urls itself belongs to state_candidates_tabular.py and is
    tested there against real HTML-regex behavior; mocked wholesale here
    (matching the established pattern in test_state_candidates_ky.py) so
    these tests stay hermetic rather than depending on live network
    reachability, which a naive fetch_with_retry monkeypatch on this
    module alone would NOT achieve — _discover_urls calls tabular's own
    internal fetch helper, not this module's."""

    def _patched(self, monkeypatch, *, stages=None, xlsx_content=None):
        async def fake_discover_urls(client, state, year, discovery):
            return stages if stages is not None else [{"url": _REAL_URL, "held": "2026-08-06", "official": None}]

        async def fake_fetch_with_retry(client, rl, method, url, **kw):
            if url == _REAL_URL:
                return SimpleNamespace(content=xlsx_content or b"")
            return None

        monkeypatch.setattr(tn, "_discover_urls", fake_discover_urls)
        monkeypatch.setattr(tn, "fetch_with_retry", fake_fetch_with_retry)

    async def test_real_data_end_to_end(self, monkeypatch):
        # _xlsx_rows only understands real xlsx bytes, so this path is
        # exercised via the already-parsed ROWS fixture by monkeypatching
        # _xlsx_rows itself rather than round-tripping through a real
        # (large) workbook file in the test.
        monkeypatch.setattr(tn, "_xlsx_rows", lambda content: ROWS)
        self._patched(monkeypatch, xlsx_content=b"placeholder")
        result = await tn.fetch_confirmed_candidates(None, 2026, "TN", {})
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Harshbarger"} in result
        assert not any(r["district"] == 4 and r["party"] == "D" for r in result)

    async def test_no_stages_discovered_returns_none(self, monkeypatch):
        self._patched(monkeypatch, stages=[])
        assert await tn.fetch_confirmed_candidates(None, 2026, "TN", {}) is None

    async def test_not_yet_settled_returns_empty_list(self, monkeypatch):
        # A file dated within settle_days (21) of today reads as
        # published-but-not-yet-settled, confirming nobody rather than
        # trusting a still-being-counted count.
        from datetime import UTC, datetime, timedelta

        recent = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        self._patched(monkeypatch, stages=[{"url": _REAL_URL, "held": recent, "official": None}])
        assert await tn.fetch_confirmed_candidates(None, 2026, "TN", {}) == []

    async def test_unparseable_xlsx_returns_none(self, monkeypatch):
        monkeypatch.setattr(tn, "_xlsx_rows", lambda content: None)
        self._patched(monkeypatch, xlsx_content=b"not a real xlsx")
        assert await tn.fetch_confirmed_candidates(None, 2026, "TN", {}) is None
