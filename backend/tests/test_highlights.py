"""Tests for data-driven highlight generation.

_build_highlights takes a SenatorSchema and produces factual,
prioritized insights about funding, voting, lobbying, and promises.
These run without any LLM or database access.
"""

import pytest

from app.api.senators import _build_highlights
from app.schemas import (
    CampaignPromiseSchema,
    DonorSchema,
    FundingSchema,
    LobbyingMatchSchema,
    RepresentationScoreSchema,
    SenatorSchema,
    VotingRecordSchema,
)


def _make_senator(**overrides) -> SenatorSchema:
    """Build a complete SenatorSchema for testing highlights."""
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
    return SenatorSchema(**defaults)


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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
        match = [h for h in highlights if "donor-vote connections" in h]
        assert len(match) == 1

    def test_no_matches_noted(self):
        senator = _make_senator(lobbying_matches=[])
        highlights = _build_highlights(senator)
        match = [h for h in highlights if "No direct donor-vote" in h]
        assert len(match) == 1


class TestPromiseHighlights:

    def test_all_kept_highlighted(self):
        promises = [
            CampaignPromiseSchema(promise_text="P1", category="healthcare", alignment="kept"),
            CampaignPromiseSchema(promise_text="P2", category="economy", alignment="kept"),
        ]
        senator = _make_senator(campaign_promises=promises)
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
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
            )
        )
        highlights = _build_highlights(senator)
        match = [h for h in highlights if "strong marks" in h]
        assert len(match) == 1

    def test_low_score_highlighted(self):
        senator = _make_senator(
            representation_score=RepresentationScoreSchema(
                funding_independence=20,
                promise_persistence=25,
                independent_voting=30,
                funding_diversity=20,
            )
        )
        highlights = _build_highlights(senator)
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
        highlights = _build_highlights(senator)
        assert len(highlights) >= 2
        grassroots_idx = next((i for i, h in enumerate(highlights) if "Grassroots" in h), None)
        donor_idx = next((i for i, h in enumerate(highlights) if "Big Corp" in h), None)
        if grassroots_idx is not None and donor_idx is not None:
            assert grassroots_idx < donor_idx
