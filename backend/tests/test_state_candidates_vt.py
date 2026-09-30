"""Tests for Vermont's confirmed-general-candidate strategy
(state_candidates_vt.py).

All fixtures are REAL data, trimmed, never fabricated (the statewide ones
are described above their own tests, at the end of this file):

fixtures_vt_elections.json -- the real 2026 "AUGUST PRIMARY" entry from
the live elections.json (fetched 2026-09-08), plus three other real
entries (two local special elections, one non-P electionTypeCode) kept
specifically to prove the isStateWideElection/electionTypeCode/year
filter actually excludes them rather than only ever being tested against
a single matching row.

fixtures_vt_election_detail.json -- the real election-detail response for
that same guid, trimmed to just the two top-level keys this module reads
(electionDetails, federal); the 247-town roster the real file also
carries is dropped since nothing here needs it.

fixtures_vt_federal_results.json -- a real trimmed EXCERPT of the actual
federal results file (fetched 2026-09-08), covering exactly four real
Vermont towns (Addison, Alburgh, Vergennes, Bradford) chosen because
between them they carry every real quirk this module's own docstring
documents: Alburgh's real write-in noise under the Democratic column
(Gerald Malloy, Mickey Mouse, Sylvia Jensen picking up a handful of
write-in votes each), a real "OTHER WRITE-INS" bucket with its own
nonzero id (Addison), a real "BLANK" placeholder (Bradford, Republican),
and Vergennes's real "JANOO" write-in. Because only four of Vermont's
247 towns are included, the totals below do NOT match the live
statewide count -- they are the correct sum for THESE four towns only,
verified by direct arithmetic against the fixture, not recalled.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import state_candidates_vt as vtm

FIXTURES = Path(__file__).parent
ELECTIONS = json.loads((FIXTURES / "fixtures_vt_elections.json").read_text())
DETAIL = json.loads((FIXTURES / "fixtures_vt_election_detail.json").read_text())
RESULTS = json.loads((FIXTURES / "fixtures_vt_federal_results.json").read_text())

_REAL_GUID = "a18f77e0-89f8-4a01-8d97-61a7c75ba200"


def _patched(monkeypatch, elections=ELECTIONS, detail=DETAIL, results=RESULTS):
    async def fake_json(client, rl, url, label, **kw):
        if url == vtm._ELECTIONS_URL:
            return elections
        if url == f"{vtm._BASE_URL}/elections/{_REAL_GUID}.json":
            return detail
        return results

    monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)


class TestFederalContests:
    def test_finds_the_real_democratic_winner_and_write_in_noise(self):
        choices = vtm._fetch_federal_choices(RESULTS)
        by_name = dict(choices[("H", None, "D")])
        assert by_name["BECCA BALINT"] == 1027
        assert by_name["GERALD MALLOY"] == 6
        assert by_name["MICKEY MOUSE"] == 1
        assert by_name["JANOO"] == 1

    def test_finds_the_real_republican_field(self):
        choices = vtm._fetch_federal_choices(RESULTS)
        by_name = dict(choices[("H", None, "R")])
        assert by_name["GERALD MALLOY"] == 367
        assert by_name["MARK COESTER"] == 83

    def test_progressive_party_is_skipped_entirely(self):
        # normalize_party refuses "PROGRESSIVE" -- no group for it at all,
        # not an empty group.
        choices = vtm._fetch_federal_choices(RESULTS)
        assert not any(party == "PR" or party == "P" for _o, _d, party in choices)
        assert len(choices) == 2

    def test_non_candidate_placeholders_are_excluded(self):
        choices = vtm._fetch_federal_choices(RESULTS)
        all_names = {n for group in choices.values() for n, _v in group}
        assert "OTHER WRITE-IN" not in all_names
        assert "OTHER WRITE-INS" not in all_names
        assert "BLANK" not in all_names

    def test_flowery_placeholder_is_in_the_exclusion_set(self):
        # "FLOWERY" only ever appears live under Vermont's real Progressive
        # Party block (verified against the full live 2026 report), which
        # normalize_party already refuses upstream of this check -- so this
        # module's own trimmed fixture can never exercise it end-to-end.
        # Tested directly against the exclusion set instead of pretending
        # a real per-party fixture proves it.
        assert "FLOWERY" in vtm._NON_CANDIDATE_NAMES

    def test_same_candidate_different_cid_across_parties_does_not_merge(self):
        # Becca Balint carries a DIFFERENT cid in the D, R, and PR blocks
        # (her real declared D ballot line vs. scattered write-in tallies
        # elsewhere) -- her real R write-in total (1) must never be added
        # onto her real D total (1027).
        choices = vtm._fetch_federal_choices(RESULTS)
        assert dict(choices[("H", None, "D")])["BECCA BALINT"] == 1027
        assert dict(choices[("H", None, "R")])["BECCA BALINT"] == 1

    def test_a_reused_cid_with_a_different_name_logs_a_warning_but_still_sums(self, caplog):
        # The cid-scoped-per-(office,district,party) assumption is trusted,
        # not enforced -- if it ever breaks, this must be loud (a log
        # line), not a silent vote-count discrepancy with no diagnostic
        # trail.
        report = {
            "d": [{
                "pn": "DEMOCRATIC", "pc": "D", "o": [{
                    "on": "REPRESENTATIVE TO CONGRESS", "cs": [
                        {"tn": "TOWN A", "rc": [{"cid": 1, "cn": "ALICE ONE", "vc": 10}], "wc": []},
                        {"tn": "TOWN B", "rc": [{"cid": 1, "cn": "BOB TWO", "vc": 5}], "wc": []},
                    ],
                }],
            }],
        }
        with caplog.at_level("WARNING"):
            choices = vtm._fetch_federal_choices(report)
        assert dict(choices[("H", None, "D")])["ALICE ONE"] == 15
        assert any("cid" in r.message and "1" in r.message for r in caplog.records)


class TestCurrentPrimaryGuid:
    async def test_finds_the_real_statewide_primary(self, monkeypatch):
        # Real regression case: three other genuine 2026 elections share
        # this list with the real primary -- two local specials
        # (isStateWideElection: false) and one non-Primary type -- none
        # of which should ever be picked (a second match would raise).
        async def fake_json(client, rl, url, label, **kw):
            return ELECTIONS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm._current_primary_guid(None, "VT", 2026) == _REAL_GUID

    async def test_no_match_for_a_different_year_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return ELECTIONS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm._current_primary_guid(None, "VT", 2028) is None

    @pytest.mark.parametrize("elections", [
        pytest.param(None, id="fetch_failure"),
        # The portal's own list always carries VT's full election history --
        # a genuinely empty list is a broken response, not a healthy state,
        # and must not read the same as "no match for this year".
        pytest.param([], id="empty_elections_list"),
        pytest.param([{**ELECTIONS[0], "electionGuid": None}], id="matched_election_missing_its_guid"),
        pytest.param([*ELECTIONS, {**ELECTIONS[0], "electionGuid": "another-guid"}], id="more_than_one_match"),
    ])
    async def test_raises_discovery_failed(self, monkeypatch, elections):
        async def fake_json(client, rl, url, label, **kw):
            return elections

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._current_primary_guid(None, "VT", 2026)


class TestFederalReportUrl:
    async def test_reads_the_real_path_and_date(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return DETAIL

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        result = await vtm._federal_report_url(None, "VT", _REAL_GUID)
        assert result == (
            f"{vtm._BASE_URL}/elections/{_REAL_GUID}-f-20260908223641.json",
            "2026-08-11",
        )

    async def test_federal_not_enabled_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return {**DETAIL, "federal": {"isEnable": False}}

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm._federal_report_url(None, "VT", _REAL_GUID) is None

    @pytest.mark.parametrize("detail", [
        pytest.param(None, id="detail_fetch_failure"),
        pytest.param({**DETAIL, "federal": {"isEnable": True}}, id="enabled_with_no_path"),
        # Without this, a missing date would flow silently into _settled()
        # (which treats an unparseable date as "not settled") and look
        # forever like a healthy "waiting on settle_days", never a failure.
        pytest.param({**DETAIL, "electionDetails": {**DETAIL["electionDetails"], "electionDate": None}},
                     id="missing_election_date"),
    ])
    async def test_raises_discovery_failed(self, monkeypatch, detail):
        async def fake_json(client, rl, url, label, **kw):
            return detail

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._federal_report_url(None, "VT", _REAL_GUID)


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_winners(self, monkeypatch):
        _patched(monkeypatch)
        result = await vtm.fetch_confirmed_candidates(None, 2026, "VT", {"settle_days": 1})
        assert {"office": "H", "district": None, "party": "D", "last_name": "BALINT", "display_name": "BECCA BALINT"} in result
        assert {"office": "H", "district": None, "party": "R", "last_name": "MALLOY", "display_name": "GERALD MALLOY"} in result
        assert len(result) == 2

    async def test_no_matching_election_confirms_nothing_yet(self, monkeypatch):
        _patched(monkeypatch)
        result = await vtm.fetch_confirmed_candidates(None, 2028, "VT", {"settle_days": 1})
        assert result == []

    async def test_not_yet_settled_confirms_nothing(self, monkeypatch):
        _patched(monkeypatch)
        result = await vtm.fetch_confirmed_candidates(None, 2026, "VT", {"settle_days": 36500})
        assert result == []

    async def test_elections_list_failure_returns_none_not_empty(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm.fetch_confirmed_candidates(None, 2026, "VT", {}) is None

    async def test_report_fetch_failure_returns_none(self, monkeypatch):
        _patched(monkeypatch, results=None)
        assert await vtm.fetch_confirmed_candidates(None, 2026, "VT", {"settle_days": 1}) is None

    async def test_a_configured_runoff_threshold_withholds_a_sub_threshold_leader(self, monkeypatch):
        # Real data: Balint's real 4-town D total (1027) is a huge
        # majority already, so a generous threshold still confirms her --
        # proves runoff_threshold_pct is actually wired through, not just
        # harmlessly present at null (Vermont itself has no such rule).
        _patched(monkeypatch)
        result = await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "runoff_threshold_pct": 90.0},
        )
        assert {"office": "H", "district": None, "party": "D", "last_name": "BALINT", "display_name": "BECCA BALINT"} in result
        # Malloy's real 4-town R total (367/451 = 81.4%) falls under a 90%
        # bar -- proves the threshold actually withholds a real leader.
        assert {"office": "H", "district": None, "party": "R", "last_name": "MALLOY", "display_name": "GERALD MALLOY"} not in result


# ── Statewide executive offices ───────────────────────────────────────
#
# Real data, trimmed, never fabricated:
#
# fixtures_vt_statewide_primary.json -- the real 2026 August Primary's
# stateWide report (read 2026-09-28 from the path the primary manifest
# names, the `stateWide` key added to fixtures_vt_election_detail.json),
# every party block and all six offices kept, trimmed to two towns
# (Addison, Alburgh). Totals below are THOSE two towns' sums.
#
# fixtures_vt_statewide_general.json -- the real 2026 General Election's
# stateWide report (read 2026-09-28, the same day, from the manifest in
# fixtures_vt_general_detail.json), same two towns. No votes yet: it is
# the printed ballot, one `rc` line per candidate, each with its own `pn`.
#
# Statewide, the primary named H. Brooke Paige the Republican winner for
# Treasurer, Secretary of State, Auditor of Accounts and Attorney General
# (35,936 / 35,084 / 33,740 / 33,374 votes); the general ballot carries
# Lynn LaFleur, Ivar Kronick and Edwin Howell Kemon on three of those
# lines. That difference is what these tests pin.

STATEWIDE_PRIMARY = json.loads((FIXTURES / "fixtures_vt_statewide_primary.json").read_text())
STATEWIDE_GENERAL = json.loads((FIXTURES / "fixtures_vt_statewide_general.json").read_text())
GENERAL_DETAIL = json.loads((FIXTURES / "fixtures_vt_general_detail.json").read_text())
_GENERAL_GUID = "68d61e96-1d28-4210-8c1d-8e90512ae105"


def _patched_statewide(monkeypatch, *, general=STATEWIDE_GENERAL, primary=STATEWIDE_PRIMARY,
                       general_detail=GENERAL_DETAIL):
    async def fake_json(client, rl, url, label, **kw):
        if url == vtm._ELECTIONS_URL:
            return ELECTIONS
        if url == f"{vtm._BASE_URL}/elections/{_REAL_GUID}.json":
            return DETAIL
        if url == f"{vtm._BASE_URL}/elections/{_GENERAL_GUID}.json":
            return general_detail
        if f"{_GENERAL_GUID}-s-" in url:
            return general
        if f"{_REAL_GUID}-s-" in url:
            return primary
        return RESULTS

    monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)


def _seats(records):
    """(office, party) -> name for the recognised parties; OTHER_PARTY
    lines share one code, so _others reads them by printed label."""
    return {
        (r["office"], r["party"]): r["last_name"] for r in records
        if r["office"] not in ("S", "H") and r["party"] != "O"
    }


def _others(records):
    return {
        (r["office"], r["party_label"], r["last_name"]) for r in records
        if r["office"] not in ("S", "H") and r["party"] == "O"
    }


class TestBallotFinal:
    def test_final_from_the_uocava_mailing_deadline(self):
        from datetime import date
        assert vtm._ballot_final("2026-11-03T00:00:00", today=date(2026, 9, 19))
        assert not vtm._ballot_final("2026-11-03T00:00:00", today=date(2026, 9, 18))

    def test_an_unreadable_date_is_never_final(self):
        assert not vtm._ballot_final("")


class TestGeneralBallotStatewide:
    def test_reads_every_line_of_the_real_general_ballot(self):
        seats = _seats(vtm._general_ballot_statewide(STATEWIDE_GENERAL))
        assert seats == {
            ("governor", "D"): "AMANDA JANOO",       # printed "DEM/PROG": the first party is the line's
            ("governor", "R"): "PHIL SCOTT",
            ("governor", "I"): "BRIAN JUDD",         # an independent is an ordinary ballot line
            ("lt_governor", "D"): "MOLLY GRAY",
            ("lt_governor", "R"): "JOHN S. RODGERS",
            ("treasurer", "D"): "MIKE PIECIAK",
            ("treasurer", "R"): "LYNN LAFLEUR",
            ("secretary_of_state", "D"): "SARAH COPELAND HANZAS",
            ("secretary_of_state", "R"): "H. BROOKE PAIGE",
            ("auditor", "D"): "TIM ASHE",            # "AUDITOR OF ACCOUNTS"
            ("auditor", "R"): "IVAR KRONICK",
            ("attorney_general", "D"): "CHARITY R. CLARK",
            ("attorney_general", "R"): "EDWIN HOWELL KEMON",
            # Progressive has an FEC code of its own (PRO); on a ballot
            # list it is read, on primary results it never is.
            ("treasurer", "P"): "ZACHARY HAMPL",
            ("secretary_of_state", "P"): "RACHEL SHAW",
        }

    def test_a_party_with_no_code_keeps_its_printed_label(self):
        """Peace and Justice and Freedom and Unity are really on the 2026
        ballot and have no FEC code: kept as the state printed them, not
        dropped and not guessed into a code."""
        records = vtm._general_ballot_statewide(STATEWIDE_GENERAL)
        assert _others(records) == {
            ("governor", "PEACE AND JUSTICE", "JUNE GOODBAND"),
            ("governor", "FREEDOM AND UNITY", "DEAN ROY"),
        }
        # The real report's 17 ballot lines, every one kept.
        assert len(records) == 17
        # A recognised party carries no label: its code is the whole fact.
        assert all("party_label" not in r for r in records if r["party"] != "O")

    def test_no_district_on_any_vermont_executive_office(self):
        assert {r["district"] for r in vtm._general_ballot_statewide(STATEWIDE_GENERAL)} == {None}


class TestStatewideFetch:
    async def test_the_final_general_ballot_names_the_nominees(self, monkeypatch):
        _patched_statewide(monkeypatch)
        monkeypatch.setattr(vtm, "_ballot_final", lambda held, today=None: True)
        records = await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        )
        seats = _seats(records)
        assert seats[("treasurer", "R")] == "LYNN LAFLEUR"
        assert seats[("auditor", "R")] == "IVAR KRONICK"
        assert seats[("attorney_general", "R")] == "EDWIN HOWELL KEMON"
        assert len(seats) == 15
        assert len(_others(records)) == 2
        # The federal seat is still read from the primary, unchanged.
        assert {"office": "H", "district": None, "party": "D", "last_name": "BALINT",
                "display_name": "BECCA BALINT"} in records

    async def test_before_the_ballot_is_final_the_primary_winners_stand(self, monkeypatch):
        _patched_statewide(monkeypatch)
        monkeypatch.setattr(vtm, "_ballot_final", lambda held, today=None: False)
        records = await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        )
        seats = _seats(records)
        # Paige leads every Republican line in these two towns too.
        assert seats[("treasurer", "R")] == "H. BROOKE PAIGE"
        assert seats[("attorney_general", "R")] == "H. BROOKE PAIGE"
        assert seats[("governor", "R")] == "PHIL SCOTT"
        # Addison + Alburgh: Aly Richards 141, Amanda Janoo 126. Both
        # towns' rows carry isWinner=true on JANOO -- the feed's flag is
        # the STATEWIDE result (Janoo 79,228 to 75,140), so this proves
        # the winner comes from the sums actually read, not the flag.
        assert seats[("governor", "D")] == "ALY RICHARDS"
        assert seats[("auditor", "D")] == "TIM ASHE"
        # Progressive: a party block with no party code, never a nominee.
        assert {party for _office, party in seats} == {"D", "R"}

    async def test_statewide_is_only_read_when_the_state_opts_in(self, monkeypatch):
        _patched_statewide(monkeypatch)
        records = await vtm.fetch_confirmed_candidates(None, 2026, "VT", {"settle_days": 1})
        assert _seats(records) == {}

    async def test_no_statewide_contest_anywhere_is_a_failure_not_a_none(self, monkeypatch):
        """Vermont elects all six every even year; an empty read under the
        opt-in would be published as "no statewide offices"."""
        _patched_statewide(monkeypatch, general={"d": []}, primary={"d": []})
        monkeypatch.setattr(vtm, "_ballot_final", lambda held, today=None: True)
        assert await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        ) is None

    async def test_an_empty_final_general_falls_back_to_the_primary(self, monkeypatch):
        _patched_statewide(monkeypatch, general={"d": []})
        monkeypatch.setattr(vtm, "_ballot_final", lambda held, today=None: True)
        records = await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        )
        assert _seats(records)[("treasurer", "R")] == "H. BROOKE PAIGE"

    async def test_a_general_report_fetch_failure_fails_the_fetch(self, monkeypatch):
        _patched_statewide(monkeypatch, general=None)
        monkeypatch.setattr(vtm, "_ballot_final", lambda held, today=None: True)
        assert await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        ) is None

    async def test_no_general_election_listed_yet_uses_the_primary(self, monkeypatch):
        _patched_statewide(monkeypatch)
        without_general = [e for e in ELECTIONS if e["electionTypeCode"] != "G"]

        async def fake_json(client, rl, url, label, **kw):
            if url == vtm._ELECTIONS_URL:
                return without_general
            if url == f"{vtm._BASE_URL}/elections/{_REAL_GUID}.json":
                return DETAIL
            if f"{_REAL_GUID}-s-" in url:
                return STATEWIDE_PRIMARY
            return RESULTS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        records = await vtm.fetch_confirmed_candidates(
            None, 2026, "VT", {"settle_days": 1, "statewide_offices": True},
        )
        assert _seats(records)[("governor", "R")] == "PHIL SCOTT"
