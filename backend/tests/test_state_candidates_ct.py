"""Tests for Connecticut's confirmed-general-candidate strategy
(state_candidates_ct.py).

fixtures_ct_elections.json, fixtures_ct_111_lookup.json,
fixtures_ct_111_votes.json, fixtures_ct_112_lookup.json and
fixtures_ct_112_votes.json are REAL — trimmed JSON responses from
ctemspublic.tgstg.net, fetched live 2026-09-04 from the real, certified
2026-08-11 Democratic (111) and Republican (112) statewide primaries.
The lookup fixtures keep the full real officeList/candidateIds/partyIds
(town/county/polling-place data dropped -- unused by the parser). The
elections fixture is kept WHOLE and unfiltered: it carries real decoy
entries the discovery regex must correctly reject -- a same-year,
different-month named primary ("09/01/2026 -- September 1st Democratic
Primary"), a different year's primaries with DIFFERENT wording ("08/13/
2024 -- August 2024 Democratic Primary", proving the match is a
substring check, not an exact pattern), and several real named special
elections that share the "-- ... Primary"/"-- ... Election" name shape.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import http_utils, state_candidates_ct as ct

FIXTURES = Path(__file__).parent
ELECTIONS = json.loads((FIXTURES / "fixtures_ct_elections.json").read_text())
DEM_LOOKUP = json.loads((FIXTURES / "fixtures_ct_111_lookup.json").read_text())
DEM_VOTES = json.loads((FIXTURES / "fixtures_ct_111_votes.json").read_text())
REP_LOOKUP = json.loads((FIXTURES / "fixtures_ct_112_lookup.json").read_text())
REP_VOTES = json.loads((FIXTURES / "fixtures_ct_112_votes.json").read_text())

DEM_ID = "111"
REP_ID = "112"


def _resp(body):
    return SimpleNamespace(json=lambda: body)


class TestFindPrimary:
    def test_matches_the_real_2026_statewide_primaries_by_year_and_august(self):
        dem = ct._find_primary(ELECTIONS, 2026, "Democratic Primary")
        rep = ct._find_primary(ELECTIONS, 2026, "Republican Primary")
        assert dem == {"id": "111", "date": "2026-08-11"}
        assert rep == {"id": "112", "date": "2026-08-11"}

    def test_a_same_year_different_month_named_primary_is_not_matched(self):
        # The real fixture also carries "09/01/2026 -- September 1st
        # Democratic Primary" -- same year, ends with "Democratic
        # Primary" too, but is not the regular August primary.
        dem = ct._find_primary(ELECTIONS, 2026, "Democratic Primary")
        assert dem["id"] != "113"

    def test_the_wording_around_the_party_name_is_not_stable_year_to_year(self):
        # 2024's real entries say "-- August 2024 Democratic Primary" --
        # extra words the 2026 entries don't have -- proving the match
        # is a substring check against the whole name, not the exact
        # "-- {Party} Primary" suffix shape 2026 happens to use.
        dem = ct._find_primary(ELECTIONS, 2024, "Democratic Primary")
        rep = ct._find_primary(ELECTIONS, 2024, "Republican Primary")
        assert dem == {"id": "94", "date": "2024-08-13"}
        assert rep == {"id": "95", "date": "2024-08-13"}

    def test_a_special_primary_sharing_the_same_name_shape_is_excluded(self):
        # No real fixture entry both falls in August of a target year
        # AND contains "special", so this is a constructed check that
        # the "special" exclusion actually fires rather than a
        # currently-unreachable branch.
        elections = [
            {"ID": "999", "Name": "08/11/2026 -- Bridgeport Special Democratic Primary"},
        ]
        assert ct._find_primary(elections, 2026, "Democratic Primary") is None

    def test_no_match_for_a_year_not_in_the_list(self):
        assert ct._find_primary(ELECTIONS, 2030, "Democratic Primary") is None


@pytest.mark.asyncio
class TestPartyNominees:
    async def _patched(self, monkeypatch, *, election_id, version, lookup, votes):
        async def fake(client, rl, method, url, **kw):
            if url.endswith("Version.json"):
                return _resp({"Version": version})
            if url.endswith("Lookupdata.json"):
                return _resp(lookup)
            if url.endswith("stateVotes_Electiondata.json"):
                return _resp(votes)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_real_democratic_primary_resolves_to_the_real_upset_winner(self, monkeypatch):
        await self._patched(monkeypatch, election_id=DEM_ID, version=10138, lookup=DEM_LOOKUP, votes=DEM_VOTES)
        result = await ct._party_nominees(None, DEM_ID, 2026)
        assert result == [{"office": "H", "district": 1, "party": "D", "last_name": "Bronin", "display_name": "Luke Bronin"}]

    async def test_real_republican_primary_resolves_to_the_real_winners(self, monkeypatch):
        await self._patched(monkeypatch, election_id=REP_ID, version=10237, lookup=REP_LOOKUP, votes=REP_VOTES)
        result = await ct._party_nominees(None, REP_ID, 2026)
        assert sorted((r["district"], r["last_name"]) for r in result) == [
            (4, "Goldstein"), (5, "Shea"),
        ]

    async def test_version_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ct._party_nominees(None, DEM_ID, 2026) is None

    async def test_lookup_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if url.endswith("Version.json"):
                return _resp({"Version": 1})
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ct._party_nominees(None, DEM_ID, 2026) is None

    async def test_votes_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if url.endswith("Version.json"):
                return _resp({"Version": 1})
            if url.endswith("Lookupdata.json"):
                return _resp(DEM_LOOKUP)
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ct._party_nominees(None, DEM_ID, 2026) is None

    async def test_no_federal_house_race_this_party_is_a_healthy_empty_list(self, monkeypatch):
        empty_lookup = {**DEM_LOOKUP, "officeList": [
            o for o in DEM_LOOKUP["officeList"] if next(iter(o.values()))["OT"] != "C"
        ]}
        await self._patched(monkeypatch, election_id=DEM_ID, version=10138, lookup=empty_lookup, votes=DEM_VOTES)
        assert await ct._party_nominees(None, DEM_ID, 2026) == []

    async def test_an_unresolvable_top_choice_blocks_confirmation_rather_than_winning(self, monkeypatch):
        # If the real vote LEADER's own candidate id is missing from
        # candidateIds (a write-in bucket, a vendor data gap), that
        # leader's votes must still count toward the total -- dropping
        # the choice before ranking would let a lower-vote, resolvable
        # candidate win by default instead of correctly confirming
        # nobody for the seat.
        cd1_id = "19666"
        votes = json.loads(json.dumps(DEM_VOTES))
        votes[cd1_id] = [
            {"unknown-leader": {"V": "90000", "TO": "90%"}},
            {"43479": {"V": "10000", "TO": "10%"}},  # Luke Bronin, real candidate id
        ]
        await self._patched(monkeypatch, election_id=DEM_ID, version=10138, lookup=DEM_LOOKUP, votes=votes)
        result = await ct._party_nominees(None, DEM_ID, 2026)
        assert [r for r in result if r["district"] == 1] == []

    async def test_a_malformed_vote_count_is_skipped_not_a_crash(self, monkeypatch):
        cd1_id = "19666"
        votes = json.loads(json.dumps(DEM_VOTES))
        votes[cd1_id] = [
            {"43479": {"V": "not-a-number", "TO": "0%"}},
            {"44087": {"V": "5000", "TO": "100%"}},  # John Larson, real candidate id
        ]
        await self._patched(monkeypatch, election_id=DEM_ID, version=10138, lookup=DEM_LOOKUP, votes=votes)
        result = await ct._party_nominees(None, DEM_ID, 2026)
        assert [r for r in result if r["district"] == 1] == [
            {"office": "H", "district": 1, "party": "D", "last_name": "Larson", "display_name": "John B. Larson"},
        ]


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    def _patched(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            if url.endswith("Elections.json"):
                return _resp(ELECTIONS)
            if f"/election/{DEM_ID}/Version.json" in url:
                return _resp({"Version": 10138})
            if f"/election/{DEM_ID}/10138/Lookupdata.json" in url:
                return _resp(DEM_LOOKUP)
            if f"/election/{DEM_ID}/10138/stateVotes_Electiondata.json" in url:
                return _resp(DEM_VOTES)
            if f"/election/{REP_ID}/Version.json" in url:
                return _resp({"Version": 10237})
            if f"/election/{REP_ID}/10237/Lookupdata.json" in url:
                return _resp(REP_LOOKUP)
            if f"/election/{REP_ID}/10237/stateVotes_Electiondata.json" in url:
                return _resp(REP_VOTES)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)

    async def test_real_primaries_resolve_to_the_real_certified_winners(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ct.fetch_confirmed_candidates(None, 2026, "CT", {"settle_days": 21})
        assert sorted((r["office"], r["district"], r["party"], r["last_name"]) for r in result) == [
            ("H", 1, "D", "Bronin"),
            ("H", 4, "R", "Goldstein"),
            ("H", 5, "R", "Shea"),
        ]

    async def test_not_yet_scheduled_this_cycle_is_a_healthy_empty_list(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return _resp([])

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ct.fetch_confirmed_candidates(None, 2026, "CT", {}) == []

    async def test_election_list_fetch_failure_returns_none(self, monkeypatch):
        async def fake(client, rl, method, url, **kw):
            return None

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        assert await ct.fetch_confirmed_candidates(None, 2026, "CT", {}) is None

    async def test_a_stage_not_yet_settled_confirms_nothing(self, monkeypatch):
        # The vendor publishes no certification flag at all -- an
        # election dated in the far future must be treated as
        # not-yet-settled, exactly like a portal that never certifies.
        # A hardcoded far-future year stays "not settled" indefinitely,
        # unlike a near-past date that would eventually age past
        # settle_days as real time passes.
        future_year = 2099
        future_elections = [
            {"ID": "d1", "Name": f"08/11/{future_year} -- Democratic Primary"},
            {"ID": "r1", "Name": f"08/11/{future_year} -- Republican Primary"},
        ]
        requested = []

        async def fake(client, rl, method, url, **kw):
            requested.append(url)
            if url.endswith("Elections.json"):
                return _resp(future_elections)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        result = await ct.fetch_confirmed_candidates(None, future_year, "CT", {"settle_days": 21})
        # None, not []: a held primary still settling is not an answer.
        assert result is None
        assert all(u.endswith("Elections.json") for u in requested)

    async def test_one_party_pending_and_the_other_holding_no_primary_publishes_nothing(self, monkeypatch):
        # The Republicans held no primary (every endorsement stands) and the
        # Democratic primary is still inside settle_days. Publishing the
        # Republican endorsements alone used to be read as the whole
        # statewide ballot: the Democratic nominees were deleted and the
        # state was marked covered.
        future_year = 2099
        requested = []

        async def fake(client, rl, method, url, **kw):
            requested.append(url)
            if url.endswith("Elections.json"):
                return _resp([{"ID": "d1", "Name": f"08/11/{future_year} -- Democratic Primary"}])
            # The real certificates, served under the future year.
            if url == ENDORSEMENTS["index_url"]:
                return _text_resp(INDEX_HTML.replace("2026", str(future_year)))
            if url.endswith(f"{future_year}-certificate-of-endorsements"):
                return _text_resp(YEAR_HTML.replace("2026", str(future_year)))
            if "democratic-statewide-combined-ada.pdf" in url:
                return _text_resp(DEM_ENDORSEMENTS)
            if "statewide-republicans-combined.pdf" in url:
                return _text_resp(REP_ENDORSEMENTS)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        monkeypatch.setattr(ct, "fetch_with_retry", fake)
        source = {"settle_days": 21, "statewide_offices": True, "endorsements": ENDORSEMENTS}
        assert await ct.fetch_confirmed_candidates(None, future_year, "CT", source) is None


# ── Statewide offices: primary results + convention endorsements ─────
#
# fixtures_ct_endorsements_dem_2026.pdf and fixtures_ct_endorsements_rep_
# 2026.pdf are REAL pages, cut (pypdf, pages copied unchanged) from the
# Secretary of the State's own combined 2026 statewide certificates of
# party endorsement, fetched live 2026-09-28 from portal.ct.gov
# (democratic-statewide-combined-ada.pdf, statewide-republicans-
# combined.pdf). Democratic: Ned Lamont's Governor endorsement, Josh
# Elliott's Governor certificate with "15% Eligibility" checked rather
# than "Endorsed", and Erick Russell's Treasurer endorsement, whose
# typed office prints over the form's tab stops and reads "Tre asurer"
# as a plain line of text. Republican: Ryan Fazio (Governor) and Fred
# Wilms (Treasurer, "Treasu rer" as a plain line).

DEM_ENDORSEMENTS = (FIXTURES / "fixtures_ct_endorsements_dem_2026.pdf").read_bytes()
REP_ENDORSEMENTS = (FIXTURES / "fixtures_ct_endorsements_rep_2026.pdf").read_bytes()

ENDORSEMENTS = {
    "index_url": "https://portal.ct.gov/sots/election-services/certificate-of-endorsement/certificates-of-endorsement",
    "year_page_regex": 'href="([^"]*/certificate-of-endorsement/{year}-certificates?-of-endorsements?(?:\\?[^"]*)?)"',
    "statewide_link_regex": 'href="([^"]*/certificates_of_party_endorsement/{year}/statewide-congressional/[^"]+?\\.pdf[^"]*)"',
}

# The real index's year links (2024's slug is spelled differently from
# 2026's, and older years carry ?archived=true), and the real 2026 page's
# statewide links, with a congressional certificate beside them that the
# statewide pattern must not pick up.
INDEX_HTML = """
<a href="https://portal.ct.gov/sots/election-services/certificate-of-endorsement/2026-certificate-of-endorsements">2026</a>
<a href="https://portal.ct.gov/sots/election-services/certificate-of-endorsement/2024-certificates-of-endorsement">2024</a>
<a href="https://portal.ct.gov/sots/election-services/certificate-of-endorsement/2022-certificate-of-endorsements?archived=true">2022</a>
"""
YEAR_HTML = """
<a href="https://portal.ct.gov/-/media/sots/electionservices/certificates_of_party_endorsement/2026/statewide-congressional/democratic-statewide-combined-ada.pdf?rev=15b8&amp;hash=1E94">D</a>
<a href="https://portal.ct.gov/-/media/sots/electionservices/certificates_of_party_endorsement/2026/statewide-congressional/statewide-republicans-combined.pdf?rev=0951&amp;hash=8D02">R</a>
<a href="https://portal.ct.gov/-/media/sots/electionservices/certificates_of_party_endorsement/2026/us-congress/1st-cd-dem--rep-ada.pdf?rev=c08a">CD 1</a>
"""


def _text_resp(body):
    return SimpleNamespace(text=body, content=body.encode() if isinstance(body, str) else body)


class TestParseEndorsements:
    def test_real_democratic_certificates(self):
        # Elliott's certificate is 15% eligibility -- the right to force
        # a primary, not a nomination -- and is not read as one.
        assert ct.parse_endorsements(DEM_ENDORSEMENTS) == [
            {"office": "governor", "district": None, "party": "D", "last_name": "Ned Lamont"},
            {"office": "treasurer", "district": None, "party": "D", "last_name": "Erick Russell"},
        ]

    def test_real_republican_certificates(self):
        assert ct.parse_endorsements(REP_ENDORSEMENTS) == [
            {"office": "governor", "district": None, "party": "R", "last_name": "Ryan Fazio"},
            {"office": "treasurer", "district": None, "party": "R", "last_name": "Fred Wilms"},
        ]


class TestMergeStatewide:
    ENDORSED = [
        {"office": "governor", "district": None, "party": "D", "last_name": "Ned Lamont"},
        {"office": "treasurer", "district": None, "party": "D", "last_name": "Erick Russell"},
        {"office": "governor", "district": None, "party": "R", "last_name": "Ryan Fazio"},
    ]

    def test_a_primaried_office_is_decided_by_its_primary_even_with_no_winner(self):
        # A tie leaves no primary record, but the endorsement is still not
        # the nominee: the primary decides that office, not the convention.
        merged = ct._merge_statewide({"D": {"governor"}, "R": set()}, [], self.ENDORSED)
        assert {(r["office"], r["party"], r["last_name"]) for r in merged} == {
            ("treasurer", "D", "Erick Russell"), ("governor", "R", "Ryan Fazio"),
        }

    def test_two_endorsements_for_one_nomination_publish_neither(self):
        doubled = [*self.ENDORSED, {"office": "treasurer", "district": None, "party": "D", "last_name": "Someone Else"}]
        merged = ct._merge_statewide({"D": set(), "R": set()}, [], doubled)
        assert ("treasurer", "D") not in {(r["office"], r["party"]) for r in merged}


@pytest.mark.asyncio
class TestStatewideFetch:
    def _patched(self, monkeypatch, *, endorsement_pages=True):
        async def fake(client, rl, method, url, **kw):
            if url.endswith("Elections.json"):
                return _resp(ELECTIONS)
            if f"/election/{DEM_ID}/Version.json" in url:
                return _resp({"Version": 10138})
            if f"/election/{DEM_ID}/10138/Lookupdata.json" in url:
                return _resp(DEM_LOOKUP)
            if f"/election/{DEM_ID}/10138/stateVotes_Electiondata.json" in url:
                return _resp(DEM_VOTES)
            if f"/election/{REP_ID}/Version.json" in url:
                return _resp({"Version": 10237})
            if f"/election/{REP_ID}/10237/Lookupdata.json" in url:
                return _resp(REP_LOOKUP)
            if f"/election/{REP_ID}/10237/stateVotes_Electiondata.json" in url:
                return _resp(REP_VOTES)
            if url == ENDORSEMENTS["index_url"]:
                return _text_resp(INDEX_HTML) if endorsement_pages else None
            if url.endswith("2026-certificate-of-endorsements"):
                return _text_resp(YEAR_HTML)
            if "democratic-statewide-combined-ada.pdf" in url:
                return _text_resp(DEM_ENDORSEMENTS)
            if "statewide-republicans-combined.pdf" in url:
                return _text_resp(REP_ENDORSEMENTS)
            raise AssertionError(f"unexpected URL: {url}")

        monkeypatch.setattr(http_utils, "fetch_with_retry", fake)
        monkeypatch.setattr(ct, "fetch_with_retry", fake)

    async def test_real_2026_statewide_ballot(self, monkeypatch):
        # Governor was the only statewide office either party primaried
        # (the real Democratic primary; Lamont 67.83%), so it comes from
        # the primary and Lamont's endorsement is not read twice. The rest
        # come from the endorsements. The House nominees are unchanged.
        self._patched(monkeypatch)
        source = {"settle_days": 21, "statewide_offices": True, "endorsements": ENDORSEMENTS}
        result = await ct.fetch_confirmed_candidates(None, 2026, "CT", source)
        assert sorted((r["office"], r["party"], r["last_name"]) for r in result) == [
            ("H", "D", "Bronin"), ("H", "R", "Goldstein"), ("H", "R", "Shea"),
            ("governor", "D", "Ned Lamont"), ("governor", "R", "Ryan Fazio"),
            ("treasurer", "D", "Erick Russell"), ("treasurer", "R", "Fred Wilms"),
        ]

    async def test_without_the_opt_in_no_statewide_office_is_read(self, monkeypatch):
        self._patched(monkeypatch)
        result = await ct.fetch_confirmed_candidates(None, 2026, "CT", {"settle_days": 21, "endorsements": ENDORSEMENTS})
        assert {r["office"] for r in result} == {"H"}

    async def test_unreadable_endorsements_fail_the_run_rather_than_list_only_primaries(self, monkeypatch):
        # Publishing Governor alone would tell a reader Connecticut elects
        # no Treasurer this year.
        self._patched(monkeypatch, endorsement_pages=False)
        source = {"settle_days": 21, "statewide_offices": True, "endorsements": ENDORSEMENTS}
        assert await ct.fetch_confirmed_candidates(None, 2026, "CT", source) is None
