"""Tests for the cross-reference analysis module.

Covers the embedding-based (non-LLM) components:
  - Key vote selection scoring
  - Lobbying match detection via donor↔vote embedding similarity
"""

from app.pipeline.analyze.cross_reference import (
    select_key_votes,
    detect_lobbying_matches,
)
from app.pipeline.analyze.policy_alignment import (
    get_related_policies,
    industry_policy_similarity,
)


# ── Industry-policy embedding similarity ─────────────────────────


class TestIndustryPolicySimilarity:
    """Embedding-based replacement for hardcoded _INDUSTRY_POLICY_MAP."""

    def test_pharma_healthcare_related(self):
        score = industry_policy_similarity("PHARMA", "HEALTHCARE")
        assert score > 0.3

    def test_pharma_defense_unrelated(self):
        score = industry_policy_similarity("PHARMA", "DEFENSE")
        pharma_health = industry_policy_similarity("PHARMA", "HEALTHCARE")
        assert score < pharma_health

    def test_defense_defense_related(self):
        score = industry_policy_similarity("DEFENSE", "DEFENSE")
        assert score > 0.4

    def test_get_related_policies_returns_set(self):
        related = get_related_policies("PHARMA")
        assert isinstance(related, set)
        assert "HEALTHCARE" in related

    def test_unknown_industry_returns_zero(self):
        score = industry_policy_similarity("NONEXISTENT", "HEALTHCARE")
        assert score == 0.0


# ── Key vote selection ───────────────────────────────────────────


class TestSelectKeyVotes:

    def _make_vote(self, bill_id, policy="HEALTHCARE", with_party=True, vote="Yea"):
        return {
            "billId": bill_id,
            "policyArea": policy,
            "votedWithParty": with_party,
            "vote": vote,
        }

    def test_against_party_prioritized(self):
        votes = [
            self._make_vote("HR.1", with_party=True),
            self._make_vote("HR.2", with_party=False),
            self._make_vote("HR.3", with_party=True),
        ]
        ids = select_key_votes(votes, [])
        assert ids[0] == "HR.2"

    def test_procedural_excluded(self):
        votes = [
            self._make_vote("HR.1", policy="PROCEDURAL"),
            self._make_vote("HR.2", policy="HEALTHCARE"),
        ]
        ids = select_key_votes(votes, [])
        assert "HR.1" not in ids

    def test_not_voting_excluded(self):
        votes = [
            self._make_vote("HR.1", vote="Not Voting"),
            self._make_vote("HR.2", vote="Yea"),
        ]
        ids = select_key_votes(votes, [])
        assert "HR.1" not in ids
        assert "HR.2" in ids

    def test_max_keys_respected(self):
        votes = [self._make_vote(f"HR.{i}") for i in range(20)]
        ids = select_key_votes(votes, [], max_keys=5)
        assert len(ids) <= 5

    def test_empty_votes(self):
        assert select_key_votes([], []) == []

    def test_candidate_affiliated_donors_ignored(self):
        votes = [self._make_vote("HR.1", policy="HEALTHCARE")]
        donors = [{"name": "Friends of Cruz", "industry": "PHARMA", "type": "CandidateAffiliated"}]
        ids_with = select_key_votes(votes, donors)
        ids_without = select_key_votes(votes, [])
        assert ids_with == ids_without


# ── Lobbying match detection (embedding-based) ───────────────────


class TestDetectLobbyingMatches:
    """Donor-vote connection detection: substantial industry funding share
    (not any donor, any size) matched against policy-area-anchored vote
    similarity (not raw free-text similarity). See
    detect_donor_vote_connections's docstring for the full 2026-07
    redesign rationale — this replaced a per-donor/raw-text-similarity
    approach that flagged essentially every vote near any donor."""

    def _healthcare_votes(self, n=1):
        return [
            {"billId": f"HR.{i}", "vote": "Yea",
             "billName": f"Prescription Drug Pricing Reform Act {i}",
             "policyArea": "HEALTHCARE",
             "description": "Reduce prescription drug costs for Medicare recipients"}
            for i in range(n)
        ]

    def test_no_industry_breakdown_returns_empty(self):
        """Without industry_breakdown there's no way to compute a funding
        share, so no connection can be flagged — never silently fall back
        to per-donor matching."""
        donors = [{"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000}]
        matches = detect_lobbying_matches(donors, self._healthcare_votes())
        assert matches == []

    def test_below_threshold_industry_share_excluded(self):
        """A donor whose industry is a small slice of classifiable funding
        must not surface as a 'connection' — this is the exact bug report:
        every topically-adjacent vote was matching regardless of size."""
        donors = [{"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000}]
        industry_breakdown = [
            {"industry": "PHARMA", "name": "PHARMA", "total": 10000, "percentage": 5},
            {"industry": "FINANCE", "name": "FINANCE", "total": 190000, "percentage": 95},
        ]
        matches = detect_lobbying_matches(donors, self._healthcare_votes(), industry_breakdown)
        assert matches == []

    def test_substantial_industry_share_with_related_vote_matched(self):
        donors = [{"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000}]
        industry_breakdown = [
            {"industry": "PHARMA", "name": "PHARMA", "total": 50000, "percentage": 50},
            {"industry": "FINANCE", "name": "FINANCE", "total": 50000, "percentage": 50},
        ]
        matches = detect_lobbying_matches(donors, self._healthcare_votes(), industry_breakdown)
        assert len(matches) == 1
        assert matches[0]["lobbyistOrg"] == "Pfizer Inc"
        assert matches[0]["industry"] == "PHARMA"
        assert matches[0]["donationToSenator"] == 50000

    def test_substantial_share_but_unrelated_votes_not_matched(self):
        """Substantial funding alone isn't enough — the votes available
        must actually be in that industry's policy domain."""
        donors = [{"name": "NRA PAC", "industry": "GUNS", "type": "PAC", "total": 50000}]
        industry_breakdown = [
            {"industry": "GUNS", "name": "GUNS", "total": 50000, "percentage": 50},
            {"industry": "FINANCE", "name": "FINANCE", "total": 50000, "percentage": 50},
        ]
        matches = detect_lobbying_matches(donors, self._healthcare_votes(), industry_breakdown)
        assert matches == []

    def test_non_industry_codes_excluded_from_denominator(self):
        """SMALL_DONORS/LARGE_INDIVIDUAL/UNCLASSIFIED/OTHER/POLITICAL are
        structurally not 'an industry' (unitemized small donors, no-
        employer-data individuals, non-contribution receipts like loans/
        transfers) — they must not dilute the classified-industry-only
        denominator, and must never themselves qualify as a 'connection'."""
        donors = [{"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000}]
        industry_breakdown = [
            {"industry": "PHARMA", "name": "PHARMA", "total": 50000, "percentage": 5},
            {"industry": "SMALL_DONORS", "name": "SMALL_DONORS", "total": 500000, "percentage": 50},
            {"industry": "LARGE_INDIVIDUAL", "name": "LARGE_INDIVIDUAL", "total": 300000, "percentage": 30},
            {"industry": "UNCLASSIFIED", "name": "UNCLASSIFIED", "total": 150000, "percentage": 15},
        ]
        # Against total_raised (~$1M) PHARMA is 5%; against classified-only
        # (just the $50k PHARMA bucket, since it's the only real industry)
        # it's 100% — must use the classified-only denominator.
        matches = detect_lobbying_matches(donors, self._healthcare_votes(), industry_breakdown)
        assert len(matches) == 1
        assert matches[0]["industry"] == "PHARMA"

    def test_empty_votes(self):
        donors = [{"name": "Corp", "industry": "PHARMA", "type": "PAC", "total": 5000}]
        industry_breakdown = [{"industry": "PHARMA", "name": "PHARMA", "total": 5000, "percentage": 100}]
        assert detect_lobbying_matches(donors, [], industry_breakdown) == []

    def test_max_eight_matches(self):
        industries = [
            "PHARMA", "FINANCE", "TECH", "ENERGY", "DEFENSE",
            "GUNS", "LABOR_UNIONS", "REAL_ESTATE", "TELECOM", "AGRIBUSINESS",
        ]
        industry_breakdown = [
            {"industry": ind, "name": ind, "total": 1000, "percentage": 10}
            for ind in industries
        ]
        donors = [
            {"name": f"{ind} Corp", "industry": ind, "type": "PAC", "total": 1000}
            for ind in industries
        ]
        # Broad vote set so most industries find at least one policy-area match.
        votes = (
            self._healthcare_votes()
            + [
                {"billId": "HR.100", "vote": "Yea", "billName": "Energy Independence Act",
                 "policyArea": "ENERGY", "description": "Domestic energy production"},
                {"billId": "HR.101", "vote": "Yea", "billName": "Defense Authorization Act",
                 "policyArea": "DEFENSE", "description": "Military spending authorization"},
                {"billId": "HR.102", "vote": "Yea", "billName": "Financial Reform Act",
                 "policyArea": "FINANCIAL", "description": "Bank regulation reform"},
                {"billId": "HR.103", "vote": "Yea", "billName": "Tech Privacy Act",
                 "policyArea": "TECH", "description": "Data privacy regulation"},
                {"billId": "HR.104", "vote": "Yea", "billName": "Labor Rights Act",
                 "policyArea": "LABOR", "description": "Worker protections"},
                {"billId": "HR.105", "vote": "Yea", "billName": "Gun Safety Act",
                 "policyArea": "GUNS", "description": "Firearm background checks"},
            ]
        )
        matches = detect_lobbying_matches(donors, votes, industry_breakdown)
        assert len(matches) <= 8

    def test_lobbyists_industry_never_matches(self):
        """LOBBYISTS is a service profession, not a policy domain — a
        lobbying firm's PAC represents undisclosed clients across every
        policy area, so it doesn't reveal a specific interest the way
        TECH/ENERGY/DEFENSE do. Live-data finding (2026-07): it was the
        only industry anchor that crossed the policy-similarity gate at
        all (0.751 vs TAXES, right at the edge) — not genuine tax focus,
        just broad enough wording to drift near every policy area. Real
        impact: excluding it dropped 24/100 senators with a flagged
        connection down to 7/100, all far more concrete (3-bill FINANCE
        matches, not 30-bill LOBBYISTS matches on every tax-adjacent
        vote)."""
        donors = [{"name": "Big Lobbying Firm PAC", "industry": "LOBBYISTS", "type": "PAC", "total": 500000}]
        industry_breakdown = [
            {"industry": "LOBBYISTS", "name": "LOBBYISTS", "total": 500000, "percentage": 90},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Tax Reform Act",
             "policyArea": "TAXES", "description": "Comprehensive tax code overhaul"},
        ]
        matches = detect_lobbying_matches(donors, votes, industry_breakdown)
        assert matches == []


