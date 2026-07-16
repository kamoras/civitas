"""Tests for data-driven highlight generation.

build_highlights takes a senator/representative detail response dict
(camelCase keys, matching the wire format) and produces factual,
prioritized insights about funding, voting, lobbying, and promises. These
run without any LLM or database access. Shared by both chambers — see
app/api/highlights.py's module docstring for why one function covers both
(the Senate route passes senator.model_dump(by_alias=True); the House
route already gets a dict of the same shape from get_representative_by_id).
"""


from app.api.highlights import build_highlights
from app.schemas import (
    CampaignPromiseSchema,
    DonorSchema,
    FundingSchema,
    LobbyingMatchSchema,
    RepresentationScoreSchema,
    SenatorSchema,
    VotingRecordSchema,
)


def _make_senator(**overrides) -> dict:
    """Build a complete senator detail dict (camelCase keys) for testing highlights."""
    defaults = dict(
        id="S001",
        name="Jane Doe",
        state="NY",
        party="D",
        years_in_office=6,
        initials="JD",
        representation_score=RepresentationScoreSchema(
            funding_independence=60,
            promise_persistence=55,
            independent_voting=70,
            funding_diversity=65,
        ),
        funding=FundingSchema(
            total_raised=2_000_000,
            total_from_pacs=400_000,
            small_donor_percentage=30,
            top_donors=[],
            industry_breakdown=[],
        ),
        voting_record=VotingRecordSchema(
            total_votes=50,
            voted_with_party_count=30,
            voted_against_party_count=20,
            party_loyalty_pct=60.0,
            voting_summary="",
            recent_vote_count=25,
            key_vote_count=25,
        ),
        lobbying_matches=[],
        campaign_promises=[],
    )
    defaults.update(overrides)
    return SenatorSchema(**defaults).model_dump(by_alias=True)


class TestFundingHighlights:

    def test_grassroots_funded(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=5_000_000,
                total_from_pacs=100_000,
                small_donor_percentage=60,
                top_donors=[],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "Grassroots" in h]
        assert len(match) == 1
        assert "60%" in match[0]

    def test_low_small_donors_flagged(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=5_000_000,
                total_from_pacs=2_000_000,
                small_donor_percentage=5,
                top_donors=[],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "small donors" in h.lower()]
        assert len(match) == 1

    def test_pac_heavy_flagged(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=1_000_000,
                total_from_pacs=500_000,
                small_donor_percentage=10,
                top_donors=[],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "PAC-heavy" in h]
        assert len(match) == 1

    def test_pac_free_flagged(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=5_000_000,
                total_from_pacs=10_000,
                small_donor_percentage=30,
                top_donors=[],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "PAC-free" in h]
        assert len(match) == 1

    def test_top_industry_donor_shown(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=2_000_000,
                total_from_pacs=400_000,
                small_donor_percentage=30,
                top_donors=[
                    DonorSchema(name="Goldman Sachs", total=50000, type="PAC", industry="FINANCE"),
                ],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "Goldman Sachs" in h]
        assert len(match) == 1

    def test_candidate_affiliated_donor_excluded(self):
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=2_000_000,
                total_from_pacs=400_000,
                small_donor_percentage=30,
                top_donors=[
                    DonorSchema(name="Friends of Doe", total=100000, type="CandidateAffiliated", industry="POLITICAL"),
                ],
                industry_breakdown=[],
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "Friends of Doe" in h]
        assert len(match) == 0


class TestLobbyingHighlights:

    def test_many_aligned_matches_flagged(self):
        matches = [
            LobbyingMatchSchema(
                lobbyist_org=f"Corp {i}",
                industry="PHARMA",
                lobbying_spend=0,
                donation_to_senator=50000,
                bills_influenced=["HR.1"],
                senator_vote_aligned=True,
                description="test",
            )
            for i in range(5)
        ]
        senator = _make_senator(lobbying_matches=matches)
        highlights = build_highlights(senator)
        match = [h for h in highlights if "donor-vote connections" in h]
        assert len(match) == 1

    def test_no_matches_noted(self):
        senator = _make_senator(lobbying_matches=[])
        highlights = build_highlights(senator)
        match = [h for h in highlights if "No direct donor-vote" in h]
        assert len(match) == 1


class TestPromiseHighlights:

    def test_all_kept_highlighted(self):
        promises = [
            CampaignPromiseSchema(promise_text="P1", category="healthcare", alignment="kept"),
            CampaignPromiseSchema(promise_text="P2", category="economy", alignment="kept"),
        ]
        senator = _make_senator(campaign_promises=promises)
        highlights = build_highlights(senator)
        match = [h for h in highlights if "follow-through" in h.lower()]
        assert len(match) == 1
        assert "none broken" in match[0]

    def test_more_broken_than_kept_highlighted(self):
        promises = [
            CampaignPromiseSchema(promise_text="P1", category="healthcare", alignment="broken"),
            CampaignPromiseSchema(promise_text="P2", category="economy", alignment="broken"),
            CampaignPromiseSchema(promise_text="P3", category="defense", alignment="kept"),
        ]
        senator = _make_senator(campaign_promises=promises)
        highlights = build_highlights(senator)
        match = [h for h in highlights if "Promise gap" in h]
        assert len(match) == 1


class TestOverallScoreHighlights:

    def test_high_score_highlighted(self):
        senator = _make_senator(
            representation_score=RepresentationScoreSchema(
                funding_independence=90,
                promise_persistence=85,
                independent_voting=80,
                funding_diversity=85,
                legislative_effectiveness=85,
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "strong marks" in h]
        assert len(match) == 1

    def test_low_score_highlighted(self):
        senator = _make_senator(
            representation_score=RepresentationScoreSchema(
                funding_independence=20,
                promise_persistence=25,
                independent_voting=30,
                funding_diversity=20,
                legislative_effectiveness=15,
            )
        )
        highlights = build_highlights(senator)
        match = [h for h in highlights if "significant concerns" in h]
        assert len(match) == 1


class TestHighlightPriority:

    def test_sorted_by_priority_descending(self):
        """Higher-priority highlights should appear first in the list."""
        senator = _make_senator(
            funding=FundingSchema(
                total_raised=5_000_000,
                total_from_pacs=2_500_000,
                small_donor_percentage=60,
                top_donors=[
                    DonorSchema(name="Big Corp", total=100000, type="PAC", industry="FINANCE"),
                ],
                industry_breakdown=[],
            ),
        )
        highlights = build_highlights(senator)
        assert len(highlights) >= 2
        grassroots_idx = next((i for i, h in enumerate(highlights) if "Grassroots" in h), None)
        donor_idx = next((i for i, h in enumerate(highlights) if "Big Corp" in h), None)
        if grassroots_idx is not None and donor_idx is not None:
            assert grassroots_idx < donor_idx


class TestRepresentativeHighlights:
    """GET /representatives/{id}/highlights didn't exist before — House
    profile pages always 404'd against the Senate-only route. This proves
    build_highlights works directly on a representative_service.
    build_rep_response()-shaped dict (raw dict, not a Pydantic schema),
    not just on senator.model_dump(by_alias=True)."""

    def _make_rep(self, **overrides) -> dict:
        rep = {
            "id": "R001",
            "name": "Pat Rivera",
            "state": "CA",
            "district": 12,
            "party": "D",
            "representationScore": {
                "fundingIndependence": 60,
                "promisePersistence": 55,
                "independentVoting": 70,
                "fundingDiversity": 65,
                "legislativeEffectiveness": 50,
            },
            "funding": {
                "totalRaised": 2_000_000,
                "totalFromPACs": 400_000,
                "smallDonorPercentage": 30,
                "topDonors": [],
                "industryBreakdown": [],
            },
            "lobbyingMatches": [],
            "campaignPromises": [],
        }
        rep.update(overrides)
        return rep

    def test_grassroots_funded_rep(self):
        rep = self._make_rep(funding={
            "totalRaised": 5_000_000, "totalFromPACs": 100_000,
            "smallDonorPercentage": 60, "topDonors": [], "industryBreakdown": [],
        })
        highlights = build_highlights(rep)
        match = [h for h in highlights if "Grassroots" in h]
        assert len(match) == 1
        assert "Pat Rivera" in match[0]

    def test_top_donor_uses_shared_dict_keys(self):
        rep = self._make_rep(funding={
            "totalRaised": 2_000_000, "totalFromPACs": 400_000, "smallDonorPercentage": 30,
            "topDonors": [{"name": "Lockheed Martin", "total": 30000, "type": "PAC", "industry": "DEFENSE"}],
            "industryBreakdown": [],
        })
        highlights = build_highlights(rep)
        match = [h for h in highlights if "Lockheed Martin" in h]
        assert len(match) == 1

    def test_lobbying_match_alignment_key_is_senatorVoteAligned_not_representative(self):
        """LobbyingMatchSchema's field is named senator_vote_aligned even on
        the House side (representative_service.build_rep_response maps
        representative_vote_aligned into the same "senatorVoteAligned" JSON
        key — see its "key shared with senator schema" comment)."""
        rep = self._make_rep(lobbyingMatches=[
            {"lobbyistOrg": f"Corp {i}", "industry": "PHARMA", "lobbyingSpend": 0,
             "donationToSenator": 50000, "billsInfluenced": ["HR.1"],
             "senatorVoteAligned": True, "description": "test"}
            for i in range(5)
        ])
        highlights = build_highlights(rep)
        match = [h for h in highlights if "donor-vote connections" in h]
        assert len(match) == 1
