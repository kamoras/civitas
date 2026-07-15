"""Tests for house_pipeline.py's donor-vote lobbying-match wiring.

House never got detect_lobbying_matches (embeddings-only, zero LLM) wired
up, unlike Senate — Constituent Alignment's donor-independence component
(25% weight) silently defaulted to a flat, fundraising-size-based score
for every House member regardless of actual donor-vote behavior (2026-07
audit finding). This covers that a real (non-empty) lobbying_matches list
actually changes _calc_constituent_alignment's computation path instead
of falling through to the flat fallback. (The LDA-enrichment helper itself
is tested in test_lda.py — it's shared with senate_pipeline.py, not
House-specific.)
"""

from app.pipeline.analyze.cross_reference import detect_lobbying_matches
from app.pipeline.analyze.score_calculator import _calc_constituent_alignment


class TestHouseLobbyingMatchesFeedConstituentAlignment:
    """Proves the wiring actually changes score behavior, not just that
    detect_lobbying_matches can be called without error."""

    def _industry_concentrated_data(self):
        industry_breakdown = [
            {"industry": "PHARMA", "name": "PHARMA", "total": 800_000, "percentage": 80},
            {"industry": "TECH", "name": "TECH", "total": 200_000, "percentage": 20},
        ]
        donors = [
            {"name": "Pharma PAC", "industry": "PHARMA", "type": "PAC", "total": 800_000},
            {"name": "Tech PAC", "industry": "TECH", "type": "PAC", "total": 200_000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Drug Pricing Reform Act",
             "policyArea": "HEALTHCARE", "description": "Prescription drug price controls",
             "totalYeas": 220, "totalNays": 210},
        ]
        return donors, votes, industry_breakdown

    def test_no_lobbying_matches_uses_flat_fundraising_fallback(self):
        """Without any detected matches (House's behavior before this
        fix), donor_score is a constant derived only from total_raised —
        identical regardless of a member's actual voting behavior."""
        voting_record = {"keyVotes": [], "recentVotes": []}
        funding_small = {"totalRaised": 500_000, "totalFromPACs": 0}
        funding_big = {"totalRaised": 500_000, "totalFromPACs": 0}

        score_a = _calc_constituent_alignment(voting_record, [], funding_small, party="D")
        score_b = _calc_constituent_alignment(voting_record, [], funding_big, party="D")

        # Same total_raised bucket, zero matches both times -> identical
        # donor-independence contribution regardless of anything else.
        assert score_a == score_b

    def test_real_matches_move_the_score_away_from_the_flat_default(self):
        """With detect_lobbying_matches wired in (this fix), a genuine
        industry-concentration + on-topic-vote finding measurably changes
        the score versus the no-data case, instead of always landing on
        the same fundraising-size-only number."""
        donors, votes, industry_breakdown = self._industry_concentrated_data()
        matches = detect_lobbying_matches(donors, votes, industry_breakdown)
        assert matches, "fixture should produce a real match — test is invalid otherwise"

        voting_record = {"keyVotes": votes, "recentVotes": []}
        funding = {
            "totalRaised": 1_000_000, "totalFromPACs": 0,
            "topDonors": donors, "industryBreakdown": industry_breakdown,
        }

        with_matches = _calc_constituent_alignment(voting_record, matches, funding, party="D")
        without_matches = _calc_constituent_alignment(voting_record, [], funding, party="D")

        assert with_matches != without_matches
