"""Tests for the score-breakdown ("show the math") feature.

Two things are verified per entity type:
1. Each _core breakdown function's components are internally consistent
   with the public _calc_*/calc_* function's bare-int result, for the
   same input — guards against the _core extraction accidentally changing
   behavior (the extraction is meant to be pure refactoring).
2. The new service-layer assembler functions (get_senator_score_breakdown,
   get_representative_score_breakdown, get_president_score_breakdown,
   get_justice_score_breakdown) correctly reconstruct their inputs from
   ORM relationships and return a real breakdown, using the in-memory
   SQLite db_session fixture.
"""

from app.models import (
    Donor,
    IndustryDonation,
    Justice,
    JusticeVote,
    KeyVote,
    LobbyingMatch,
    President,
    RepDonor,
    RepKeyVote,
    RepLobbyingMatch,
    Representative,
    Senator,
    SponsoredBill,
)
from app.pipeline.analyze.president_scorer import (
    _agency_alignment_core,
    _competence_core,
    _effectiveness_core,
    _historical_legacy_core,
    calc_agency_alignment,
    calc_competence,
    calc_effectiveness,
    calc_historical_legacy,
)
from app.pipeline.analyze.score_calculator import (
    _calc_constituent_alignment,
    _calc_funding_diversity,
    _calc_funding_independence,
    _calc_legislative_effectiveness,
    _constituent_alignment_core,
    _funding_diversity_core,
    _funding_independence_core,
    _legislative_effectiveness_core,
    explain_scores,
)
from app.services.justice_service import get_justice_score_breakdown
from app.services.president_service import get_president_score_breakdown
from app.services.representative_service import get_representative_score_breakdown
from app.services.senator_service import get_senator_score_breakdown


class TestSenatorCoreConsistency:
    """_core functions must return the exact same score as the public
    _calc_* wrapper for identical input — the extraction is pure
    refactoring, not a behavior change."""

    FUNDING = {
        "totalRaised": 1_000_000,
        "totalFromPACs": 300_000,
        "smallDonorPercentage": 17,
        "topDonors": [{"total": 20_000, "type": "PAC"} for _ in range(10)],
    }

    def test_funding_independence_core_matches_calc(self):
        # v6.5: 5 components — the original 3 plus Funding Diversity's
        # source breadth/industry concentration, folded in (see
        # config_definitions.SCORE_WEIGHTS's r=0.72 rationale).
        breakdown = _funding_independence_core(self.FUNDING)
        assert breakdown["score"] == _calc_funding_independence(self.FUNDING)
        assert len(breakdown["components"]) == 5
        assert abs(sum(c["weight"] for c in breakdown["components"]) - 1.0) < 1e-6

    def test_constituent_alignment_core_matches_calc(self):
        # v6.5: Donor independence removed (see config_definitions.
        # SCORE_WEIGHTS's r=0.72 rationale) — with no bipartisanship data
        # in this fixture, only seat-relative vote alignment remains.
        voting_record = {
            "keyVotes": [{"votedWithParty": True} for _ in range(8)]
            + [{"votedWithParty": False} for _ in range(2)],
        }
        args = (voting_record, [], self.FUNDING, "CA", "D")
        breakdown = _constituent_alignment_core(*args)
        assert breakdown["score"] == _calc_constituent_alignment(*args)
        assert len(breakdown["components"]) == 1

    def test_funding_diversity_core_matches_calc(self):
        funding = {
            **self.FUNDING,
            "industryBreakdown": [
                {"industry": "TECH", "total": 400_000},
                {"industry": "FINANCE", "total": 300_000},
                {"industry": "SMALL_DONORS", "total": 170_000},
            ],
        }
        breakdown = _funding_diversity_core(funding)
        assert breakdown["score"] == _calc_funding_diversity(funding)
        assert len(breakdown["components"]) == 2

    def test_legislative_effectiveness_core_matches_calc(self):
        bills = [
            {"billType": "s", "congress": 118, "isLaw": True},
            {"billType": "s", "congress": 118, "latestAction": "passed the senate"},
            {"billType": "sres", "congress": 118},  # ceremonial, weight-1 in V&W's real scheme
        ]
        breakdown = _legislative_effectiveness_core(bills, leadership_score=0.5, party="D", years_in_office=6)
        assert breakdown["score"] == _calc_legislative_effectiveness(
            bills, leadership_score=0.5, party="D", years_in_office=6,
        )
        # V&W-based (significance & advancement) + leadership — the old
        # 3-component design (advancement/leadership/volume) was replaced
        # 2026-07 by a single significance-weighted, cumulative-stage
        # component that folds what used to be two components into one.
        assert len(breakdown["components"]) == 2

    def test_legislative_effectiveness_core_no_bills(self):
        breakdown = _legislative_effectiveness_core([], leadership_score=None)
        assert breakdown["score"] == 50
        assert breakdown["components"] == []

    def test_explain_scores_covers_four_scored_dimensions(self):
        senator = {
            "funding": self.FUNDING,
            "votingRecord": {"keyVotes": [{"votedWithParty": True}] * 5},
            "lobbyingMatches": [],
            "sponsoredBills": [],
            "state": "CA",
            "party": "D",
        }
        breakdown = explain_scores(senator)
        assert set(breakdown.keys()) == {
            "fundingIndependence", "independentVoting", "fundingDiversity", "legislativeEffectiveness",
        }
        assert "promisePersistence" not in breakdown  # removed dimension, no bar to explain


class TestPresidentCoreConsistency:
    def test_competence_core_matches_calc(self):
        args = (48, None, None, 4.0, 2000)
        breakdown = _competence_core(*args)
        assert breakdown["score"] == calc_competence(*args)

    def test_competence_core_none_when_no_live_data(self):
        breakdown = _competence_core(None, None, None, 4.0, 2000)
        assert breakdown["score"] is None
        assert breakdown["components"] == []
        assert "note" in breakdown

    def test_effectiveness_core_matches_calc(self):
        args = (5.0, 3.5, 4.0)
        breakdown = _effectiveness_core(*args)
        assert breakdown["score"] == calc_effectiveness(*args)

    def test_agency_alignment_core_matches_calc(self):
        args = (1200, 65.0, 4.0)
        breakdown = _agency_alignment_core(*args)
        assert breakdown["score"] == calc_agency_alignment(*args)

    def test_historical_legacy_core_matches_calc(self):
        breakdown = _historical_legacy_core(897)
        assert breakdown["score"] == calc_historical_legacy(897)

    def test_historical_legacy_core_none_when_no_live_data(self):
        breakdown = _historical_legacy_core(None)
        assert breakdown["score"] is None
        assert breakdown["components"] == []
        assert "note" in breakdown


class TestJusticeBreakdownEnrichment:
    def test_analyze_justice_votes_breakdown_present(self):
        from app.pipeline.analyze.justice_analyzer import analyze_justice_votes

        votes = [
            {
                "case_id": f"case-{i}", "vote": "majority", "opinion_type": "none",
                "is_unanimous": False, "is_close": True, "majority_votes": 5, "minority_votes": 4,
            }
            for i in range(5)
        ]
        all_case_votes = {
            v["case_id"]: [
                {**v, "justice_id": "me"},
                {**v, "justice_id": "ally", "vote": "majority"},
                {**v, "justice_id": "rival", "vote": "minority"},
            ]
            for v in votes
        }
        result = analyze_justice_votes(
            justice_id="me", appointing_party="R",
            votes=[{**v, "justice_id": "me"} for v in votes],
            all_case_votes=all_case_votes,
            party_map={"me": "R", "ally": "R", "rival": "D"},
        )
        assert "breakdown" in result
        assert result["breakdown"]["consistency"]["own_bloc_agreement_rate"] is not None
        assert result["breakdown"]["judicial_restraint"]["dissent_rate"] is not None


class TestSenatorScoreBreakdownService:
    def _seed_senator(self, db_session) -> None:
        senator = Senator(
            id="test-senator", name="Test Senator", state="CA", party="D",
            total_raised=1_000_000, total_from_pacs=300_000, small_donor_percentage=17,
            years_in_office=6,
        )
        db_session.add(senator)
        db_session.add(Donor(senator_id="test-senator", name="Acme PAC", total=20_000, type="PAC", rank=1))
        db_session.add(IndustryDonation(senator_id="test-senator", industry="TECH", name="Tech", total=100_000, percentage=10))
        for i in range(8):
            db_session.add(KeyVote(
                senator_id="test-senator", bill_name=f"Bill {i}", bill_id=f"S.{i}",
                date="2026-01-01", vote="Yea", voted_with_party=True, vote_category="key",
            ))
        db_session.add(LobbyingMatch(
            senator_id="test-senator", lobbyist_org="Acme Lobby", industry="TECH",
            lobbying_spend=10_000, donation_to_senator=5_000, is_consensus_vote=False,
        ))
        db_session.add(SponsoredBill(
            senator_id="test-senator", bill_id="S.100", title="Test Act",
            bill_type="s", congress=118, is_law=True,
        ))
        db_session.commit()

    def test_returns_none_for_missing_senator(self, db_session):
        assert get_senator_score_breakdown(db_session, "nope") is None

    def test_returns_full_breakdown_for_real_senator(self, db_session):
        self._seed_senator(db_session)
        breakdown = get_senator_score_breakdown(db_session, "test-senator")
        assert breakdown is not None
        assert set(breakdown.keys()) == {
            "fundingIndependence", "independentVoting", "fundingDiversity", "legislativeEffectiveness",
        }
        fi = breakdown["fundingIndependence"]
        assert 0 <= fi["score"] <= 100
        assert len(fi["components"]) == 5  # v6.5: Funding Diversity folded in


class TestRepresentativeScoreBreakdownService:
    def test_returns_full_breakdown_for_real_rep(self, db_session):
        rep = Representative(
            id="test-rep", name="Test Rep", state="CA", district=12, party="D",
            total_raised=500_000, total_from_pacs=100_000, small_donor_percentage=25,
            years_in_office=2,
        )
        db_session.add(rep)
        db_session.add(RepDonor(representative_id="test-rep", name="Acme PAC", total=10_000, type="PAC", rank=1))
        for i in range(5):
            db_session.add(RepKeyVote(
                representative_id="test-rep", bill_name=f"Bill {i}", bill_id=f"HR.{i}",
                date="2026-01-01", vote="Yea", voted_with_party=True, vote_category="key",
            ))
        db_session.add(RepLobbyingMatch(
            representative_id="test-rep", lobbyist_org="Acme Lobby", industry="TECH",
            lobbying_spend=5_000, donation_to_representative=2_000, is_consensus_vote=True,
        ))
        db_session.commit()

        breakdown = get_representative_score_breakdown(db_session, "test-rep")
        assert breakdown is not None
        assert 0 <= breakdown["fundingIndependence"]["score"] <= 100

    def test_returns_none_for_missing_rep(self, db_session):
        assert get_representative_score_breakdown(db_session, "nope") is None


class TestPresidentScoreBreakdownService:
    def test_president_with_live_data_gets_a_real_breakdown(self, db_session):
        p = President(
            id="obama-44", name="Barack Obama", party="D", number=44,
            term_start="2009-01-20", term_end="2017-01-20",
            eo_count=276, gdp_growth_avg=2.1, jobs_created_millions=11.6,
            gdp_growth_adjusted=2.3, rulemaking_count=1800, rulemaking_finalized_pct=68.0,
        )
        db_session.add(p)
        db_session.commit()

        breakdown = get_president_score_breakdown(db_session, "obama-44")
        assert "independence" not in breakdown
        assert "followThrough" not in breakdown
        assert breakdown["publicMandate"]["score"] is None  # no approval/election data stored
        assert breakdown["competence"]["score"] is not None
        assert len(breakdown["competence"]["components"]) >= 1
        assert breakdown["effectiveness"]["score"] is not None
        assert breakdown["agencyAlignment"]["score"] is not None
        assert breakdown["historicalLegacy"]["score"] is None  # no C-SPAN score stored

    def test_president_with_no_stored_data_is_fully_none(self, db_session):
        p = President(
            id="lincoln-16", name="Abraham Lincoln", party="R", number=16,
            term_start="1861-03-04", term_end="1865-04-15",
        )
        db_session.add(p)
        db_session.commit()

        breakdown = get_president_score_breakdown(db_session, "lincoln-16")
        assert "independence" not in breakdown
        assert "followThrough" not in breakdown
        for dim in ("publicMandate", "competence", "effectiveness", "agencyAlignment", "historicalLegacy"):
            assert breakdown[dim]["score"] is None, f"{dim} should be None with no stored data"

    def test_returns_none_for_missing_president(self, db_session):
        assert get_president_score_breakdown(db_session, "nope") is None


class TestJusticeScoreBreakdownService:
    def test_returns_full_breakdown_for_real_justice(self, db_session):
        j1 = Justice(id="j1", name="Justice One", last_name="One", appointing_party="R", is_active=True)
        j2 = Justice(id="j2", name="Justice Two", last_name="Two", appointing_party="D", is_active=True)
        db_session.add_all([j1, j2])
        db_session.add(JusticeVote(
            justice_id="j1", case_id="case-1", vote="majority", opinion_type="majority",
            is_unanimous=False, is_close=True, majority_votes=5, minority_votes=4,
        ))
        db_session.add(JusticeVote(
            justice_id="j2", case_id="case-1", vote="minority", opinion_type="dissent",
            is_unanimous=False, is_close=True, majority_votes=5, minority_votes=4,
        ))
        db_session.commit()

        breakdown = get_justice_score_breakdown(db_session, "j1")
        assert breakdown is not None
        assert breakdown["cases_decided"] == 1
        assert "breakdown" in breakdown

    def test_returns_none_for_missing_justice(self, db_session):
        assert get_justice_score_breakdown(db_session, "nope") is None
