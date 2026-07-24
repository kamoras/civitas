"""Tests for sponsorship_analysis.py — PageRank leadership + SVD ideology."""

import math

import pytest

from app.pipeline.analyze.sponsorship_analysis import (
    ADVANCED_EDGE_WEIGHT,
    ENACTED_EDGE_WEIGHT,
    STALLED_EDGE_WEIGHT,
    _build_cosponsorship_matrix,
    _cosponsorship_edge_weight,
    _rescale,
    compute_ideology_scores,
    compute_leadership_scores,
    describe_senator_position,
    party_ideology_bounds,
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


# ---------- cosponsorship-edge weighting ----------

class TestCosponsorshipEdgeWeight:
    """A cosponsorship of a bill that never advances shouldn't count as much
    toward PageRank leadership as one that becomes law — otherwise
    signing onto message bills with zero chance of passing is
    indistinguishable from real legislative collaboration."""

    @pytest.mark.parametrize(
        "is_law, latest_action, expected",
        [
            pytest.param(True, "Became Public Law No: 119-1.", ENACTED_EDGE_WEIGHT, id="enacted"),
            pytest.param(False, "Passed/agreed to in Senate.", ADVANCED_EDGE_WEIGHT, id="passed_chamber"),
            pytest.param(False, "Ordered to be reported by voice vote.", ADVANCED_EDGE_WEIGHT, id="ordered_reported"),
            pytest.param(False, "Referred to the Committee on Finance.", STALLED_EDGE_WEIGHT, id="stalled_in_committee"),
            pytest.param(False, "", STALLED_EDGE_WEIGHT, id="empty_action_treated_as_stalled"),
            # No outcome data at all (older enrichment path) — don't
            # penalize a data gap, fall back to the pre-fix flat weight.
            pytest.param(False, None, ENACTED_EDGE_WEIGHT, id="missing_data_defaults_to_original_flat_weight"),
        ],
    )
    def test_edge_weight(self, is_law, latest_action, expected):
        bill = {"isLaw": is_law, "latestAction": latest_action}
        assert _cosponsorship_edge_weight(bill) == expected


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

    def test_weight_fn_scales_edge_by_bill_outcome(self):
        """Same shape as test_simple_two_senators, but with a bill that
        never advanced — the cosponsorship still counts, just less."""
        bills, cosponsors = _make_bills_and_cosponsors({"A001": ["B002"]})
        bills[0]["isLaw"] = False
        bills[0]["latestAction"] = "Referred to the Committee on Finance."
        id_to_row, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001", "B002"},
            weight_fn=_cosponsorship_edge_weight,
        )
        sponsor_row = id_to_row["A001"]
        cosponsor_row = id_to_row["B002"]
        assert P[sponsor_row, cosponsor_row] == pytest.approx(math.sqrt(STALLED_EDGE_WEIGHT))

    def test_no_weight_fn_preserves_original_flat_weight(self):
        """Default behavior (no weight_fn) is unchanged — used by Ideology's
        SVD, which intentionally doesn't discount stalled bills."""
        bills, cosponsors = _make_bills_and_cosponsors({"A001": ["B002"]})
        bills[0]["isLaw"] = False
        bills[0]["latestAction"] = "Referred to the Committee on Finance."
        id_to_row, n, P = _build_cosponsorship_matrix(
            bills, cosponsors, {"A001", "B002"},
        )
        sponsor_row = id_to_row["A001"]
        cosponsor_row = id_to_row["B002"]
        assert P[sponsor_row, cosponsor_row] == pytest.approx(1.0)


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

    def test_advancing_bills_outrank_message_bills_at_equal_cosponsor_count(self):
        """The critique this fix addresses: without outcome-weighting, a
        senator who cosponsors ten bills with zero chance of passing
        accrues the same PageRank weight as one whose bills actually
        became law. Two sponsors here attract the identical set of
        cosponsors — the only difference is bill outcome — so any score
        gap is attributable to the weighting, not network size."""
        cosponsor_pool = [f"C{i:03d}" for i in range(8)]
        senators = {"LAWMAKER", "MESSENGER", *cosponsor_pool}

        bills_data = []
        cosponsors_map = {}
        for i in range(3):
            bill_id = f"S.LAW{i}"
            bills_data.append({
                "billId": bill_id, "sponsorBioguide": "LAWMAKER",
                "sponsorParty": "D", "isLaw": True, "latestAction": "Became Public Law.",
            })
            cosponsors_map[bill_id] = [{"bioguideId": c} for c in cosponsor_pool]

            stalled_id = f"S.MSG{i}"
            bills_data.append({
                "billId": stalled_id, "sponsorBioguide": "MESSENGER",
                "sponsorParty": "D", "isLaw": False,
                "latestAction": "Referred to the Committee on Finance.",
            })
            cosponsors_map[stalled_id] = [{"bioguideId": c} for c in cosponsor_pool]

        result = compute_leadership_scores(bills_data, cosponsors_map, senators)
        assert result["LAWMAKER"] > result["MESSENGER"]


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

    def test_withholds_scores_when_second_axis_is_not_partisan(self):
        """O6: the sign-pin only fixes orientation — it doesn't confirm the
        second singular vector is actually the partisan axis. Two cliques
        that split cosponsorship along a line with NOTHING to do with
        party (each clique has equal R/D representation) is a real
        structural signal an unguarded sign-pin would still confidently
        orient. R/D means separation should be ~0 here, so scores must be
        withheld rather than published as a coin-flip axis."""
        d_senators = [f"D{i:03d}" for i in range(6)]
        r_senators = [f"R{i:03d}" for i in range(6)]
        all_senators = set(d_senators + r_senators)
        parties = {s: "D" for s in d_senators}
        parties.update({s: "R" for s in r_senators})

        # Two mixed-party cliques, each with equal R/D representation —
        # cosponsorship splits senators along clique membership, not party.
        clique1 = [d_senators[0], d_senators[1], d_senators[2], r_senators[0], r_senators[1], r_senators[2]]
        clique2 = [d_senators[3], d_senators[4], d_senators[5], r_senators[3], r_senators[4], r_senators[5]]
        sponsor_map: dict[str, list[str]] = {}
        for clique in (clique1, clique2):
            for s in clique:
                sponsor_map[s] = [other for other in clique if other != s]

        bills, cosponsors = _make_bills_and_cosponsors(sponsor_map)
        result = compute_ideology_scores(bills, cosponsors, all_senators, parties)
        assert result == {}


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

    def test_freshman_low_pagerank_is_not_labeled_follower(self):
        """A first-year member with a near-zero raw leadership score reflects
        no time to build a cosponsorship network, not follower behavior —
        the tenure shrink should keep them out of the 'follower' bucket
        (GovTrack refuses to score <10-bill members for the same reason;
        AGENTS.md 'seniority alone is never penalized')."""
        desc = describe_senator_position(0.9, 0.02, "R", years_in_office=0.5)
        assert "follower" not in desc
        assert "conservative" in desc  # ideology label still applies

    def test_veteran_low_pagerank_is_still_a_follower(self):
        """A long-tenured member with a genuinely low leadership score has
        had the time — that IS a behavioral signal, so the label stands."""
        desc = describe_senator_position(0.9, 0.02, "R", years_in_office=20)
        assert "follower" in desc

    def test_veteran_high_pagerank_is_still_a_leader(self):
        """The tenure shrink is toward neutral, so it never manufactures a
        'leader' — a genuine high scorer with full tenure keeps the label."""
        assert "leader" in describe_senator_position(0.1, 0.95, "D", years_in_office=18)

    def test_unknown_tenure_preserves_prior_behavior(self):
        """years_in_office=None (unknown) applies no shrink — backwards
        compatible with call sites/tests that don't pass tenure."""
        assert describe_senator_position(0.9, 0.02, "R") == describe_senator_position(
            0.9, 0.02, "R", years_in_office=None
        )
        assert "follower" in describe_senator_position(0.9, 0.02, "R")


class TestPartyRelativeIdeologyLabels:
    """Ideology labels are relative to the member's own party's distribution
    (party_ideology_bounds), so progressive/moderate/centrist each capture ~a
    third of the party instead of collapsing to party identity."""

    def test_bounds_are_party_terciles_and_skip_small_parties(self):
        # Democrats span 0.00..0.20; the 33rd/67th percentiles sit inside that.
        members = [(i / 100.0, "D") for i in range(0, 21)]  # 21 D from 0.00..0.20
        members += [(0.9, "R"), (0.95, "R")]  # only 2 R -> below min size
        bounds = party_ideology_bounds(members)
        assert "D" in bounds
        lo, hi = bounds["D"]
        assert 0.0 < lo < hi < 0.20
        assert "R" not in bounds  # too few to define a stable distribution

    def test_relative_bounds_spread_a_clustered_party(self):
        """The core fix: a party clustered in [0,0.2] (bimodal-rescaled) gets
        all three labels under party-relative bounds, where fixed 0.30/0.70
        cutoffs would have labeled every one of them 'progressive'."""
        d_scores = [i / 100.0 for i in range(0, 21)]
        bounds = party_ideology_bounds([(s, "D") for s in d_scores])["D"]
        labels = set()
        for s in d_scores:
            # fixed cutoffs: everyone is "progressive"
            assert "progressive" in describe_senator_position(s, 0.5, "D")
            # party-relative: the label varies across the party
            labels.add(
                describe_senator_position(s, 0.5, "D", ideology_bounds=bounds).split()[0]
            )
        assert {"progressive", "moderate", "centrist"} <= labels

    def test_most_left_democrat_is_progressive_most_right_is_centrist(self):
        bounds = party_ideology_bounds([(i / 100.0, "D") for i in range(0, 21)])["D"]
        assert describe_senator_position(0.00, 0.5, "D", ideology_bounds=bounds).startswith("progressive")
        assert describe_senator_position(0.20, 0.5, "D", ideology_bounds=bounds).startswith("centrist")

    def test_republican_direction_is_reversed(self):
        r_scores = [0.80 + i / 100.0 for i in range(0, 21)]  # 0.80..1.00
        bounds = party_ideology_bounds([(s, "R") for s in r_scores])["R"]
        # least-right R reads centrist, most-right reads conservative
        assert describe_senator_position(0.80, 0.5, "R", ideology_bounds=bounds).startswith("centrist")
        assert describe_senator_position(1.00, 0.5, "R", ideology_bounds=bounds).startswith("conservative")

    def test_no_bounds_falls_back_to_fixed_cutoffs(self):
        assert describe_senator_position(0.1, 0.5, "D").startswith("progressive")
        assert describe_senator_position(0.5, 0.5, "D", ideology_bounds=None).startswith("moderate")


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

    def test_receive_direction_counts_only_attracted_cosponsors(self):
        """direction='receive' (v6.11, for Legislative Effectiveness's
        coalition-attraction component): only cosponsors ATTRACTED to a
        member's own bills count — the HVW 2023 construct. D0 offers six
        cross-party cosponsorships (all giving) but attracts only
        same-party cosponsors to their own bill, so D0's receive-only
        rate is 0 even though their blended rate tops the cohort. R0, who
        ATTRACTS the D0 cosponsorship to their own bill, scores above the
        cohort's zero-crossing members."""
        bills, cos, parties = self._cohort()
        # min_interactions=1: receive-side totals are much smaller than
        # blended totals in this fixture (one bill each), and what's under
        # test is the direction split, not the thin-data guard (covered by
        # test_no_fabrication_for_thin_data).
        recv = compute_bipartisanship_scores(
            bills, cos, parties, min_interactions=1, direction="receive"
        )
        blend = compute_bipartisanship_scores(bills, cos, parties, min_interactions=3)
        assert blend["D0"] == 1.0
        if "D0" in recv:
            assert recv["D0"] == 0.0
        assert "R0" in recv and recv["R0"] > 0.0

    def test_receive_direction_rejects_unknown_value(self):
        import pytest
        bills, cos, parties = self._cohort()
        with pytest.raises(ValueError):
            compute_bipartisanship_scores(bills, cos, parties, direction="give")
