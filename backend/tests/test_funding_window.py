"""Funding is measured on the right campaign, the right money, and the
right population reference (v6.13).

- Window: the most recent COMPLETED election. Before, a re-election
  campaign still in progress counted as "the most recent election", so
  through most of an election year every House member and a third of the
  Senate were scored on a half-finished cycle.
- Period: itemized detail covers the election's full period — six years for
  the Senate, two for the House — matching FEC's election-full totals.
- Denominator: contributions (+ candidate self-loans), not receipts, which
  include joint-fundraising-committee transfers and loans and so understate
  PAC dependency for the members who rely on JFCs most.
- Reference: the chamber's median PAC share, measured every run, replaces
  the hand-typed multipliers 3.2 / 1.35.
"""

from datetime import date, datetime
from unittest.mock import patch

from app.api.highlights import build_highlights
from app.pipeline.analyze.population_reference import ChamberReference
from app.pipeline.analyze.score_calculator import (
    _funding_independence_core,
    compute_funding_reference,
    funding_share_base,
)
from app.pipeline.assemble.validator import validate_senator
from app.pipeline.fetch.fec import (
    compute_recent_election_cycles,
    election_period_cycles,
    general_election_day,
    select_recent_elections,
)
from app.pipeline.transform.normalize_finance import normalize_finance, summarize_election_totals


def _at(y, m, d):
    return patch("app.pipeline.fetch.fec.utcnow", lambda: datetime(y, m, d))


class TestGeneralElectionDay:
    def test_tuesday_after_the_first_monday_in_november(self):
        assert general_election_day(2024) == date(2024, 11, 5)
        assert general_election_day(2026) == date(2026, 11, 3)
        assert general_election_day(2028) == date(2028, 11, 7)
        # Nov 1 is itself a Monday: election is Nov 2, not Nov 1.
        assert general_election_day(2021) == date(2021, 11, 2)
        # Nov 1 is a Tuesday: the first Monday is Nov 7, election Nov 8.
        assert general_election_day(2022) == date(2022, 11, 8)


ROWS = [
    {"candidate_election_year": 2026, "receipts": 900_000},
    {"candidate_election_year": 2024, "receipts": 5_000_000},
]


class TestMostRecentCompletedElection:
    def test_in_progress_campaign_is_not_the_current_mandate(self):
        with _at(2026, 9, 24):
            assert select_recent_elections(ROWS)[0]["candidate_election_year"] == 2024

    def test_election_day_itself_is_still_in_progress(self):
        with _at(2026, 11, 3):
            assert select_recent_elections(ROWS)[0]["candidate_election_year"] == 2024

    def test_counts_once_the_election_has_been_held(self):
        with _at(2026, 11, 4):
            assert select_recent_elections(ROWS)[0]["candidate_election_year"] == 2026

    def test_appointee_with_no_completed_race_uses_the_in_progress_one(self):
        with _at(2026, 9, 24):
            rows = [{"candidate_election_year": 2026, "receipts": 3_000_000}]
            assert select_recent_elections(rows)[0]["receipts"] == 3_000_000

    def test_future_rows_are_never_eligible(self):
        with _at(2026, 9, 24):
            rows = [{"candidate_election_year": 2030, "receipts": 1}, *ROWS]
            assert select_recent_elections(rows)[0]["candidate_election_year"] == 2024


class TestElectionPeriodCycles:
    def test_senate_period_is_six_years(self):
        assert election_period_cycles(2024, "S") == [2024, 2022, 2020]

    def test_house_period_is_one_cycle(self):
        # The old fixed [y, y-2] pulled a House member's PREVIOUS election
        # into the detail compared against this election's totals.
        assert election_period_cycles(2024, "H") == [2024]

    def test_window_follows_the_selected_election(self):
        with _at(2026, 9, 24):
            assert compute_recent_election_cycles(ROWS, "S") == [2024, 2022, 2020]
            assert compute_recent_election_cycles(ROWS, "H") == [2024]


class TestContributionsDenominator:
    # A leader-style committee: $50M receipts, $30M of it transfers in from
    # joint fundraising committees, $20M contributed directly, $5M of that
    # from PACs.
    ROW = {
        "candidate_election_year": 2024,
        "receipts": 50_000_000,
        "contributions": 20_000_000,
        "transfers_from_other_authorized_committee": 30_000_000,
        "other_political_committee_contributions": 5_000_000,
        "individual_unitemized_contributions": 2_000_000,
        "individual_itemized_contributions": 13_000_000,
    }

    def test_shares_exclude_transfers(self):
        totals = summarize_election_totals([self.ROW])
        assert totals["total_raised"] == 50_000_000
        assert totals["total_contributions"] == 20_000_000

    def test_candidate_self_loans_count_as_the_candidates_own_money(self):
        totals = summarize_election_totals([{**self.ROW, "loans_made_by_candidate": 1_000_000}])
        assert totals["total_contributions"] == 21_000_000

    def test_rows_without_the_field_fall_back_to_receipts(self):
        row = {k: v for k, v in self.ROW.items() if k != "contributions"}
        assert summarize_election_totals([row])["total_contributions"] == 50_000_000

    def test_normalized_funding_reports_both_and_shares_over_contributions(self):
        with _at(2026, 9, 24):
            f = normalize_finance(None, [self.ROW], [], [], [])
        assert f["totalRaised"] == 50_000_000
        assert f["totalContributions"] == 20_000_000
        assert f["smallDonorPercentage"] == 10  # 2M of 20M, not 4% of receipts
        assert f["totalFromPACs"] == 5_000_000

    def test_pac_dependency_is_measured_on_contributions(self):
        funding = {"totalRaised": 50_000_000, "totalContributions": 20_000_000,
                   "totalFromPACs": 5_000_000, "topDonors": [], "industryBreakdown": []}
        detail = _funding_independence_core(funding)["components"][0]["detail"]
        assert detail.startswith("25% of $20,000,000 in contributions came from PACs")
        receipts_only = {**funding, "totalContributions": None}
        assert "10% of $50,000,000" in _funding_independence_core(receipts_only)["components"][0]["detail"]

    def test_share_base_falls_back_for_older_records(self):
        assert funding_share_base({"totalRaised": 7, "totalContributions": None}) == 7
        assert funding_share_base({"totalRaised": 7, "totalContributions": 5}) == 5

    def test_validator_keeps_the_field(self):
        s = validate_senator({
            "id": "x", "name": "N", "state": "OH", "party": "D",
            "funding": {"totalRaised": 10, "totalContributions": 8, "totalFromPACs": 1,
                        "smallDonorPercentage": 5, "topDonors": [], "industryBreakdown": []},
        })
        assert s["funding"]["totalContributions"] == 8

    def test_highlights_describe_shares_of_contributions(self):
        entity = {
            "name": "Jane Doe",
            "funding": {"totalRaised": 50_000_000, "totalContributions": 20_000_000,
                        "totalFromPACs": 10_000_000, "smallDonorPercentage": 10,
                        "topDonors": [], "industryBreakdown": []},
            "representationScore": {"fundingIndependence": 50, "independentVoting": 50,
                                    "legislativeEffectiveness": 50},
            "votingRecord": {"totalVotes": 0}, "campaignPromises": [], "lobbyingMatches": [],
        }
        text = " ".join(build_highlights(entity))
        assert "$20.0M in contributions came from small donors" in text
        assert "PAC-heavy: 50% of contributions" in text


class TestFundingReference:
    def _fundings(self, shares):
        return [{"totalContributions": 100, "totalFromPACs": 100 * s} for s in shares]

    def test_median_pac_share_of_the_population(self):
        ref = compute_funding_reference(self._fundings([0.1] * 20 + [0.3] * 11 + [0.9] * 5))
        assert ref["pac_ratio_median"] == 0.1 and ref["n"] == 36

    def test_unfunded_members_are_not_in_the_population(self):
        fundings = self._fundings([0.2] * 30) + [{"totalContributions": 0, "totalRaised": 0}] * 10
        assert compute_funding_reference(fundings)["n"] == 30

    def test_too_few_members_yields_no_reference(self):
        assert compute_funding_reference(self._fundings([0.2] * 5)) is None

    def test_the_median_member_scores_fifty_on_pac_share(self):
        ref = {"senate": {"pac_ratio_median": 0.2}, "house": {"pac_ratio_median": 0.4}}
        for district, share in ((None, 0.2), (5, 0.4)):
            funding = {"totalContributions": 100, "totalFromPACs": 100 * share,
                       "topDonors": [], "industryBreakdown": []}
            core = _funding_independence_core(funding, "OH", district, ref)
            # No committee-type data -> the fallback volume factor applies;
            # divide it out to read the share-based score itself.
            pac = core["components"][0]
            factor = float(pac["detail"].split("scaled ×")[1].split(" ")[0])
            assert abs(pac["score"] / factor - 50.0) < 0.2

    def test_pinned_reference_reproduces_the_old_multipliers(self, pinned_funding_reference):
        # 0.5 / 0.157 ≈ 3.2 and 0.5 / 0.371 ≈ 1.35 — the values previously
        # hand-typed, so the pre-first-run fallback changes nothing.
        assert round(0.5 / pinned_funding_reference["senate"]["pac_ratio_median"], 1) == 3.2
        assert round(0.5 / pinned_funding_reference["house"]["pac_ratio_median"], 2) == 1.35


class TestChamberReferenceFile:
    def test_live_value_for_one_chamber_keeps_the_others(self, tmp_path):
        ref = ChamberReference("x")
        ref.bundled_path = tmp_path / "b.json"
        ref.live_path = tmp_path / "l.json"
        ref.bundled_path.write_text('{"senate": {"v": 1}, "house": {"v": 2}}')
        merged = ref.with_live("house", {"v": 3})
        assert merged == {"senate": {"v": 1}, "house": {"v": 3}}
        assert ref.load()["house"]["v"] == 3  # persisted
        assert ref.with_live("senate", None) == ref.load()  # nothing measured: keep last
