"""Tests for sponsorship_analysis.py — PageRank leadership + SVD ideology."""

import pytest

from app.pipeline.analyze.sponsorship_analysis import (
    _build_cosponsorship_matrix,
    _rescale,
    compute_ideology_scores,
    compute_leadership_scores,
    describe_senator_position,
)


# ---------- helpers ----------

def _make_bills_and_cosponsors(
    sponsor_map: dict[str, list[str]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Build bills_data and cosponsors_map from sponsor→cosponsors dict."""
    bills_data = []
    cosponsors_map: dict[str, list[dict]] = {}
    for i, (sponsor, cosponsors) in enumerate(sponsor_map.items()):
        bill_id = f"S.{100 + i}"
        bills_data.append({
            "billId": bill_id,
            "sponsorBioguide": sponsor,
            "sponsorParty": "D",
        })
        cosponsors_map[bill_id] = [
            {"bioguideId": c} for c in cosponsors
        ]
    return bills_data, cosponsors_map


# ---------- matrix construction ----------

class TestBuildCosponsorshipMatrix:
    def test_simple_two_senators(self):
        bills, cosponsors = _make_bills_and_cosponsors({
            "A001": ["B002"],
        })
        id_to_row, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001", "B002"},
        )
        assert n == 2
        sponsor_row = id_to_row["A001"]
        cosponsor_row = id_to_row["B002"]
        assert P[sponsor_row, cosponsor_row] == pytest.approx(1.0)  # sqrt(0+1)

    def test_identity_diagonal(self):
        """Diagonal starts at 1.0 (identity matrix), square-rooted."""
        bills, cosponsors = _make_bills_and_cosponsors({"A001": []})
        _, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001"},
        )
        assert P[0, 0] == pytest.approx(1.0)

    def test_ignores_non_senator_cosponsors(self):
        bills, cosponsors = _make_bills_and_cosponsors({
            "A001": ["HOUSE_REP_999"],
        })
        _, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001"},
        )
        assert n == 1

    def test_self_cosponsorship_excluded(self):
        """A senator cosponsoring their own bill should not add extra weight."""
        bills, cosponsors = _make_bills_and_cosponsors({
            "A001": ["A001", "B002"],
        })
        id_to_row, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001", "B002"},
        )
        row_a = id_to_row["A001"]
        assert P[row_a, row_a] == pytest.approx(1.0)


# ---------- rescale ----------

class TestRescale:
    def test_basic_rescale(self):
        import numpy as np
        result = _rescale(np.array([0.0, 5.0, 10.0]))
        assert result[0] == pytest.approx(0.0)
        assert result[2] == pytest.approx(1.0)

    def test_constant_input(self):
        import numpy as np
        result = _rescale(np.array([3.0, 3.0, 3.0]))
        assert all(v == pytest.approx(0.5) for v in result)


# ---------- leadership (PageRank) ----------

class TestLeadership:
    def test_too_few_senators(self):
        bills, cosponsors = _make_bills_and_cosponsors({"A001": ["B002"]})
        result = compute_leadership_scores(
            bills, cosponsors, {"A001", "B002"},
        )
        assert result == {}

    def test_leader_gets_higher_score(self):
        """A senator whose bills everyone cosponsors should rank higher."""
        senators = {f"S{i:03d}" for i in range(10)}
        leader = "S000"
        sponsor_map: dict[str, list[str]] = {}
        for s in senators:
            if s != leader:
                sponsor_map.setdefault(leader, []).append(s)
        for s in senators:
            if s != leader:
                sponsor_map[s] = []
        bills, cosponsors = _make_bills_and_cosponsors(sponsor_map)
        result = compute_leadership_scores(bills, cosponsors, senators)
        assert len(result) == 10
        assert result[leader] > 0.5

    def test_returns_all_senators(self):
        senators = {f"S{i:03d}" for i in range(6)}
        sponsor_map = {s: [] for s in senators}
        sponsor_map["S000"] = ["S001", "S002"]
        bills, cosponsors = _make_bills_and_cosponsors(sponsor_map)
        result = compute_leadership_scores(bills, cosponsors, senators)
        assert set(result.keys()) == senators


# ---------- ideology (SVD) ----------

class TestIdeology:
    def test_too_few_senators(self):
        bills, cosponsors = _make_bills_and_cosponsors({"A001": ["B002"]})
        result = compute_ideology_scores(
            bills, cosponsors, {"A001", "B002"}, {"A001": "D", "B002": "R"},
        )
        assert result == {}

    def test_partisan_clusters_separate(self):
        """D-leaning and R-leaning senators should get different ideology scores
        when they cosponsor only within their party."""
        d_senators = [f"D{i:03d}" for i in range(6)]
        r_senators = [f"R{i:03d}" for i in range(6)]
        all_senators = set(d_senators + r_senators)
        parties = {s: "D" for s in d_senators}
        parties.update({s: "R" for s in r_senators})

        sponsor_map: dict[str, list[str]] = {}
        for d in d_senators:
            sponsor_map[d] = [other for other in d_senators if other != d]
        for r in r_senators:
            sponsor_map[r] = [other for other in r_senators if other != r]

        bills, cosponsors = _make_bills_and_cosponsors(sponsor_map)
        result = compute_ideology_scores(bills, cosponsors, all_senators, parties)
        assert len(result) == 12

        d_scores = [result[s] for s in d_senators]
        r_scores = [result[s] for s in r_senators]
        d_mean = sum(d_scores) / len(d_scores)
        r_mean = sum(r_scores) / len(r_scores)
        assert r_mean > d_mean


# ---------- describe position ----------

class TestDescribePosition:
    def test_progressive_leader(self):
        desc = describe_senator_position(0.1, 0.9, "D")
        assert "progressive" in desc
        assert "leader" in desc

    def test_conservative_follower(self):
        desc = describe_senator_position(0.9, 0.1, "R")
        assert "conservative" in desc
        assert "follower" in desc

    def test_moderate_no_role(self):
        desc = describe_senator_position(0.5, 0.5, "D")
        assert "moderate" in desc
        assert "leader" not in desc
        assert "follower" not in desc

    def test_independent(self):
        desc = describe_senator_position(0.2, 0.5, "I")
        assert "Independent" in desc
