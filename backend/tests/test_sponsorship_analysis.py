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
        assert abs(r_mean - d_mean) > 0.1, (
            f"Partisan clusters should be separated: D_mean={d_mean:.4f}, R_mean={r_mean:.4f}"
        )


# ---------- describe position ----------

class TestDescribePosition:
    @pytest.mark.parametrize(
        "ideology, leadership, party, expected_in, expected_not_in",
        [
            pytest.param(0.1, 0.9, "D", ["progressive", "leader"], [], id="progressive_leader"),
            pytest.param(0.9, 0.1, "R", ["conservative", "follower"], [], id="conservative_follower"),
            pytest.param(0.5, 0.5, "D", ["moderate"], ["leader", "follower"], id="moderate_no_role"),
            pytest.param(0.2, 0.5, "I", ["Independent"], [], id="independent"),
        ],
    )
    def test_describe_position(self, ideology, leadership, party, expected_in, expected_not_in):
        desc = describe_senator_position(ideology, leadership, party)
        for substr in expected_in:
            assert substr in desc
        for substr in expected_not_in:
            assert substr not in desc


# ── Bipartisanship (v5) ──────────────────────────────────────────

from app.pipeline.analyze.sponsorship_analysis import compute_bipartisanship_scores


class TestBipartisanship:
    def _cohort(self):
        """12 members, 6 per party; M1(D) works across the aisle, M2(D) never does."""
        parties = {f"D{i}": "D" for i in range(6)} | {f"R{i}": "R" for i in range(6)}
        bills, cosponsors = [], {}
        # Every member sponsors one bill
        for bio, p in parties.items():
            bills.append({"billId": f"B.{bio}", "sponsorBioguide": bio, "sponsorParty": p})
        # D0 cosponsors all R bills; D1 cosponsors only D bills; everyone
        # else cosponsors two same-party bills + R0's bill gets D cosponsors
        for i in range(6):
            cosponsors.setdefault(f"B.R{i}", []).append({"bioguideId": "D0", "party": "D"})
        for i in range(2, 6):
            cosponsors.setdefault(f"B.D{i}", []).append({"bioguideId": "D1", "party": "D"})
            cosponsors[f"B.D{i}"].append({"bioguideId": f"D{(i+1)%4+2}", "party": "D"})
        for i in range(2, 6):
            cosponsors.setdefault(f"B.R{i}", []).append({"bioguideId": f"R{(i+1)%4+2}", "party": "R"})
        # give everyone a few more same-party interactions to clear min_interactions
        for i in range(6):
            cosponsors.setdefault(f"B.D{i%6}", []).append({"bioguideId": f"D{(i+2)%6}", "party": "D"})
            cosponsors.setdefault(f"B.R{i%6}", []).append({"bioguideId": f"R{(i+2)%6}", "party": "R"})
        return bills, cosponsors, parties

    def test_crossing_member_outranks_loyalist(self):
        bills, cos, parties = self._cohort()
        scores = compute_bipartisanship_scores(bills, cos, parties, min_interactions=3)
        assert scores, "cohort should produce scores"
        assert scores["D0"] > scores.get("D1", 0.0)
        assert scores["D0"] == 1.0  # far above cohort median caps at 1.0

    def test_zero_crossing_scores_zero(self):
        bills, cos, parties = self._cohort()
        scores = compute_bipartisanship_scores(bills, cos, parties, min_interactions=3)
        if "D1" in scores:
            assert scores["D1"] == 0.0

    def test_no_fabrication_for_thin_data(self):
        bills, cos, parties = self._cohort()
        scores = compute_bipartisanship_scores(bills, cos, parties, min_interactions=50)
        assert scores == {}

    def test_party_symmetric(self):
        """Mirroring every party label leaves the score set unchanged."""
        bills, cos, parties = self._cohort()
        flip = {"D": "R", "R": "D"}
        bills2 = [{**b, "sponsorParty": flip[b["sponsorParty"]]} for b in bills]
        cos2 = {
            k: [{**c, "party": flip[c["party"]]} for c in v] for k, v in cos.items()
        }
        parties2 = {k: flip[v] for k, v in parties.items()}
        s1 = compute_bipartisanship_scores(bills, cos, parties, min_interactions=3)
        s2 = compute_bipartisanship_scores(bills2, cos2, parties2, min_interactions=3)
        assert s1 == s2
