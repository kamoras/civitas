"""Tests for Vermont's confirmed-general-candidate strategy
(state_candidates_vt.py).

All three fixtures are REAL data, trimmed, never fabricated:

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
        async def fake_json(client, rl, url, label, **kw):
            return ELECTIONS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm._current_primary_guid(None, "VT", 2026) == _REAL_GUID

    async def test_local_special_elections_are_not_matched(self, monkeypatch):
        # Real regression case: three other genuine 2026 elections share
        # this list with the real primary -- two local specials
        # (isStateWideElection: false) and one non-Primary type -- none
        # of which should ever be picked.
        async def fake_json(client, rl, url, label, **kw):
            return ELECTIONS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        result = await vtm._current_primary_guid(None, "VT", 2026)
        assert result == _REAL_GUID

    async def test_no_match_for_a_different_year_returns_none(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return ELECTIONS

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        assert await vtm._current_primary_guid(None, "VT", 2028) is None

    async def test_fetch_failure_raises_discovery_failed(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._current_primary_guid(None, "VT", 2026)

    async def test_an_empty_elections_list_raises_discovery_failed(self, monkeypatch):
        # The portal's own list always carries VT's full election history --
        # a genuinely empty list is a broken response, not a healthy state,
        # and must not read the same as "no match for this year".
        async def fake_json(client, rl, url, label, **kw):
            return []

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._current_primary_guid(None, "VT", 2026)

    async def test_a_matched_election_missing_its_guid_raises_discovery_failed(self, monkeypatch):
        missing_guid = [{**ELECTIONS[0], "electionGuid": None}]

        async def fake_json(client, rl, url, label, **kw):
            return missing_guid

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._current_primary_guid(None, "VT", 2026)

    async def test_more_than_one_match_raises_discovery_failed(self, monkeypatch):
        duped = [*ELECTIONS, {**ELECTIONS[0], "electionGuid": "another-guid"}]

        async def fake_json(client, rl, url, label, **kw):
            return duped

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

    async def test_detail_fetch_failure_raises_discovery_failed(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return None

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._federal_report_url(None, "VT", _REAL_GUID)

    async def test_enabled_with_no_path_raises_discovery_failed(self, monkeypatch):
        async def fake_json(client, rl, url, label, **kw):
            return {**DETAIL, "federal": {"isEnable": True}}

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._federal_report_url(None, "VT", _REAL_GUID)

    async def test_missing_election_date_raises_discovery_failed(self, monkeypatch):
        # Without this, a missing date would flow silently into _settled()
        # (which treats an unparseable date as "not settled") and look
        # forever like a healthy "waiting on settle_days", never a failure.
        async def fake_json(client, rl, url, label, **kw):
            return {**DETAIL, "electionDetails": {**DETAIL["electionDetails"], "electionDate": None}}

        monkeypatch.setattr(vtm, "fetch_json_with_retry", fake_json)
        with pytest.raises(vtm.DiscoveryFailed):
            await vtm._federal_report_url(None, "VT", _REAL_GUID)


class TestFetchConfirmedCandidates:
    async def test_real_primary_resolves_to_the_real_winners(self, monkeypatch):
        _patched(monkeypatch)
        result = await vtm.fetch_confirmed_candidates(None, 2026, "VT", {"settle_days": 1})
        assert {"office": "H", "district": None, "party": "D", "last_name": "BALINT"} in result
        assert {"office": "H", "district": None, "party": "R", "last_name": "MALLOY"} in result
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
        assert {"office": "H", "district": None, "party": "D", "last_name": "BALINT"} in result
        # Malloy's real 4-town R total (367/451 = 81.4%) falls under a 90%
        # bar -- proves the threshold actually withholds a real leader.
        assert {"office": "H", "district": None, "party": "R", "last_name": "MALLOY"} not in result
