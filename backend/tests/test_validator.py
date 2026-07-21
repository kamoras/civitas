"""Tests for the senator record validator.

Validates sanitization, defaults, clamping, and edge case handling
for assembled senator records before they're persisted.
"""


from app.pipeline.assemble.validator import validate_senator, clamp


class TestClamp:
    def test_within_range(self):
        assert clamp(50.3) == 50

    def test_below_min(self):
        assert clamp(-10.0) == 0

    def test_above_max(self):
        assert clamp(150.0) == 100

    def test_custom_range(self):
        assert clamp(200.0, 0, 1000) == 200
        assert clamp(-5.0, 0, 1000) == 0


def _make_senator(**overrides):
    """Build a minimal valid senator record."""
    base = {
        "id": "S001",
        "name": "Jane Doe",
        "state": "NY",
        "party": "D",
        "yearsInOffice": 6,
        "initials": "JD",
        "representationScore": {
            "fundingIndependence": 60,
            "promisePersistence": 55,
            "independentVoting": 70,
            "fundingDiversity": 65,
            "legislativeEffectiveness": 50,
        },
        "funding": {
            "totalRaised": 1_000_000,
            "totalFromPACs": 200_000,
            "smallDonorPercentage": 30,
            "topDonors": [],
            "industryBreakdown": [],
        },
        "votingRecord": {
            "totalVotes": 10,
            "scoreableVotes": 5,
            "donorAlignedVotes": 2,
            "donorOpposedVotes": 3,
            "policyBreakdown": [],
            "keyVotes": [],
        },
        "lobbyingMatches": [],
    }
    base.update(overrides)
    return base


class TestValidateSenator:
    """Core validation and sanitization."""

    def test_valid_senator_passes(self):
        senator = _make_senator()
        result = validate_senator(senator)
        assert result["id"] == "S001"
        assert result["representationScore"]["fundingIndependence"] == 60

    def test_invalid_party_defaults_to_independent(self):
        senator = _make_senator(party="X")
        result = validate_senator(senator)
        assert result["party"] == "I"

    def test_valid_parties_preserved(self):
        for party in ("D", "R", "I"):
            senator = _make_senator(party=party)
            assert validate_senator(senator)["party"] == party

    def test_negative_years_zeroed(self):
        senator = _make_senator(yearsInOffice=-5)
        result = validate_senator(senator)
        assert result["yearsInOffice"] == 0

    def test_missing_years_zeroed(self):
        senator = _make_senator(yearsInOffice=None)
        result = validate_senator(senator)
        assert result["yearsInOffice"] == 0

    def test_scores_clamped(self):
        senator = _make_senator(representationScore={
            "fundingIndependence": 150,
            "promisePersistence": -20,
            "independentVoting": 50,
            "fundingDiversity": 200,
            "legislativeEffectiveness": 110,
        })
        result = validate_senator(senator)
        scores = result["representationScore"]
        assert scores["fundingIndependence"] == 100
        assert scores["promisePersistence"] == 0
        assert scores["independentVoting"] == 50
        assert scores["fundingDiversity"] == 100
        assert scores["legislativeEffectiveness"] == 100

    def test_missing_scores_default_to_neutral(self):
        # An un-scored member is "unknown", not "fully captured": absent
        # score dimensions default to the neutral 50, never 0. Matches the
        # scoring standard (score_calculator: "Missing data yields a neutral
        # 50, never a perfect 100 or 0").
        senator = _make_senator(representationScore={})
        result = validate_senator(senator)
        for v in result["representationScore"].values():
            assert v == 50

    def test_none_scores_default_to_neutral(self):
        senator = _make_senator(representationScore=None)
        result = validate_senator(senator)
        for v in result["representationScore"].values():
            assert v == 50

    def test_no_funding_member_is_neutral_not_zero(self):
        # Regression: a member with no computed funding data (e.g. no FEC
        # candidate match) must surface Funding Independence as the neutral
        # 50 ("unknown"), never a 0 that reads as "fully captured". Mirrors
        # the base stub normalize_members seeds for an un-scored member.
        from app.pipeline.transform.normalize_members import (
            _NEUTRAL_REPRESENTATION_SCORE,
        )

        senator = _make_senator(
            representationScore=dict(_NEUTRAL_REPRESENTATION_SCORE)
        )
        result = validate_senator(senator)
        assert result["representationScore"]["fundingIndependence"] == 50

    def test_computed_zero_is_preserved(self):
        # The neutral default only fills ABSENT dimensions — a dimension that
        # was genuinely computed as 0 (a fully-captured profile) is kept.
        senator = _make_senator(representationScore={"fundingIndependence": 0})
        result = validate_senator(senator)
        assert result["representationScore"]["fundingIndependence"] == 0

    def test_confidence_survives_validation(self):
        confidence = {
            "fundingIndependence": "high",
            "promisePersistence": "low",
            "independentVoting": "medium",
            "fundingDiversity": "high",
            "legislativeEffectiveness": "low",
        }
        senator = _make_senator(representationScore={
            "fundingIndependence": 60,
            "promisePersistence": 55,
            "independentVoting": 70,
            "fundingDiversity": 65,
            "legislativeEffectiveness": 50,
            "confidence": confidence,
        })
        result = validate_senator(senator)
        assert result["representationScore"]["confidence"] == confidence

    def test_missing_confidence_omitted_not_defaulted(self):
        senator = _make_senator()
        result = validate_senator(senator)
        assert "confidence" not in result["representationScore"]

    def test_negative_funding_zeroed(self):
        senator = _make_senator(funding={
            "totalRaised": -500,
            "totalFromPACs": -100,
            "smallDonorPercentage": -10,
            "topDonors": [],
            "industryBreakdown": [],
        })
        result = validate_senator(senator)
        assert result["funding"]["totalRaised"] == 0
        assert result["funding"]["totalFromPACs"] == 0
        assert result["funding"]["smallDonorPercentage"] == 0

    def test_invalid_donor_type_defaults_to_org_employees(self):
        senator = _make_senator(funding={
            "totalRaised": 100_000,
            "totalFromPACs": 0,
            "smallDonorPercentage": 0,
            "topDonors": [
                {"name": "Corp", "total": 5000, "type": "INVALID", "industry": "FINANCE"},
            ],
            "industryBreakdown": [],
        })
        result = validate_senator(senator)
        assert result["funding"]["topDonors"][0]["type"] == "Org/Employees"

    def test_valid_donor_types_preserved(self):
        valid_types = ["PAC", "Individual", "SuperPAC", "Org/Employees", "Party/Ideological", "CandidateAffiliated", "Self-Funded"]
        for dt in valid_types:
            senator = _make_senator(funding={
                "totalRaised": 100_000,
                "totalFromPACs": 0,
                "smallDonorPercentage": 0,
                "topDonors": [{"name": "X", "total": 1000, "type": dt, "industry": "FINANCE"}],
                "industryBreakdown": [],
            })
            result = validate_senator(senator)
            assert result["funding"]["topDonors"][0]["type"] == dt

    def test_invalid_industry_defaults_to_other(self):
        senator = _make_senator(funding={
            "totalRaised": 100_000,
            "totalFromPACs": 0,
            "smallDonorPercentage": 0,
            "topDonors": [
                {"name": "Corp", "total": 5000, "type": "PAC", "industry": "SPACE_MINING"},
            ],
            "industryBreakdown": [
                {"industry": "UNICORNS", "name": "Unicorns", "total": 5000, "percentage": 50},
            ],
        })
        result = validate_senator(senator)
        assert result["funding"]["topDonors"][0]["industry"] == "OTHER"
        assert result["funding"]["industryBreakdown"][0]["industry"] == "OTHER"

    def test_invalid_vote_defaults_to_not_voting(self):
        senator = _make_senator(votingRecord={
            "totalVotes": 1,
            "scoreableVotes": 0,
            "donorAlignedVotes": 0,
            "donorOpposedVotes": 0,
            "policyBreakdown": [],
            "keyVotes": [
                {"billName": "Bill", "billId": "HR.1", "date": "2025-01-01",
                 "vote": "Present", "policyArea": "HEALTHCARE", "stance": "reform",
                 "stanceVote": "Yea"},
            ],
        })
        result = validate_senator(senator)
        assert result["votingRecord"]["keyVotes"][0]["vote"] == "Not Voting"


    def test_lobbying_match_industry_validated(self):
        senator = _make_senator(lobbyingMatches=[
            {
                "lobbyistOrg": "Corp",
                "industry": "FAKE_INDUSTRY",
                "lobbyingSpend": 100,
                "donationToSenator": 5000,
                "billsInfluenced": ["HR.1"],
                "senatorVoteAligned": True,
                "description": "test",
            }
        ])
        result = validate_senator(senator)
        assert result["lobbyingMatches"][0]["industry"] == "OTHER"

    def test_bioguide_id_preserved(self):
        senator = _make_senator(bioguideId="B001230")
        result = validate_senator(senator)
        assert result["bioguideId"] == "B001230"

    def test_initials_generated_if_missing(self):
        senator = _make_senator(initials="", name="Ted Cruz")
        result = validate_senator(senator)
        assert result["initials"] == "TC"

    def test_none_funding_handled(self):
        senator = _make_senator(funding=None)
        result = validate_senator(senator)
        assert result["funding"]["totalRaised"] == 0

    def test_none_voting_record_handled(self):
        senator = _make_senator(votingRecord=None)
        result = validate_senator(senator)
        assert result["votingRecord"]["totalVotes"] == 0

    def test_none_lobbying_matches_handled(self):
        senator = _make_senator(lobbyingMatches=None)
        result = validate_senator(senator)
        assert result["lobbyingMatches"] == []

