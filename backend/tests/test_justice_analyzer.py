"""Tests for justice bloc derivation and scoring.

The no-hand-fed rule (2026-07-04): comparison blocs must be derived
from each justice's appointing party — data on the fetched records —
never from hardcoded membership sets. These tests use entirely
fictional justice IDs to prove nothing in the scoring path depends on
real names.
"""

from app.pipeline.analyze.justice_analyzer import analyze_justice_votes


def _case(case_id, votes_by_justice, majority_side="majority"):
    """Build per-justice vote records for one case.

    votes_by_justice: {justice_id: "majority" | "minority"}
    """
    n_maj = sum(1 for v in votes_by_justice.values() if v == "majority")
    n_min = len(votes_by_justice) - n_maj
    records = []
    for jid, side in votes_by_justice.items():
        records.append({
            "case_id": case_id,
            "justice_id": jid,
            "vote": side,
            "opinion_type": "none",
            "is_unanimous": n_min == 0,
            "is_close": abs(n_maj - n_min) <= 1,
            "majority_votes": n_maj,
            "minority_votes": n_min,
        })
    return records


PARTY_MAP = {
    "r1": "R", "r2": "R", "r3": "R",
    "d1": "D", "d2": "D", "d3": "D",
}


def _build_court(n_cases, crosser_id=None):
    """Synthetic court: R bloc vs D bloc on every case; the optional
    crosser (an R appointee) votes with the D bloc on odd cases."""
    all_case_votes = {}
    per_justice = {jid: [] for jid in PARTY_MAP}
    for i in range(n_cases):
        votes = {}
        for jid, party in PARTY_MAP.items():
            if jid == crosser_id and i % 2 == 1:
                votes[jid] = "minority"  # joins the D side
            else:
                votes[jid] = "majority" if party == "R" else "minority"
        records = _case(f"c{i}", votes)
        all_case_votes[f"c{i}"] = records
        for rec in records:
            per_justice[rec["justice_id"]].append(rec)
    return all_case_votes, per_justice


class TestBlocDerivation:
    def test_loyalist_scores_low_independence(self):
        all_votes, per_justice = _build_court(30)
        result = analyze_justice_votes(
            justice_id="r1",
            appointing_party="R",
            votes=per_justice["r1"],
            all_case_votes=all_votes,
            party_map=PARTY_MAP,
        )
        assert result["score_independence"] <= 20
        assert result["score_consistency"] <= 20  # agrees only with own bloc

    def test_crosser_earns_high_independence_from_data(self):
        """A justice who behaves as the court's median EARNS high
        bloc-based scores — no hand-coded 'swing' status exists."""
        all_votes, per_justice = _build_court(30, crosser_id="r1")
        result = analyze_justice_votes(
            justice_id="r1",
            appointing_party="R",
            votes=per_justice["r1"],
            all_case_votes=all_votes,
            party_map=PARTY_MAP,
        )
        assert result["score_independence"] >= 70
        assert result["score_consistency"] >= 50

    def test_no_party_map_means_neutral_bloc_scores(self):
        """Without appointing-party data there are no blocs to compare
        against; bloc-based scores fall back to neutral."""
        all_votes, per_justice = _build_court(10)
        result = analyze_justice_votes(
            justice_id="r1",
            appointing_party="R",
            votes=per_justice["r1"],
            all_case_votes=all_votes,
            party_map=None,
        )
        assert result["score_consistency"] == 50.0
        assert result["score_independence"] == 50.0


class TestConsistencyUnclamp:
    """2026-07: a systematically counter-partisan justice must NOT score a
    perfect consistency (the old max(0, own-opp) clamped it to 100)."""

    def _counter_partisan_court(self, n_cases):
        # r1 (R-appointed) always joins the D side; R bloc is the majority.
        all_case_votes = {}
        per_justice = {jid: [] for jid in PARTY_MAP}
        for i in range(n_cases):
            votes = {}
            for jid, party in PARTY_MAP.items():
                if jid == "r1":
                    votes[jid] = "minority"  # always with D
                else:
                    votes[jid] = "majority" if party == "R" else "minority"
            records = _case(f"c{i}", votes)
            all_case_votes[f"c{i}"] = records
            for rec in records:
                per_justice[rec["justice_id"]].append(rec)
        return all_case_votes, per_justice

    def test_counter_partisan_is_not_perfect_consistency(self):
        all_votes, per_justice = self._counter_partisan_court(30)
        result = analyze_justice_votes(
            justice_id="r1", appointing_party="R",
            votes=per_justice["r1"], all_case_votes=all_votes, party_map=PARTY_MAP,
        )
        # Perfectly party-predictable (just inverted) → low consistency,
        # not the spurious 100 the old clamp produced.
        assert result["score_consistency"] <= 20


class TestSmallSampleShrinkage:
    """2026-07: bloc rates from very few cases shrink toward neutral 50."""

    def test_one_split_decision_does_not_yield_extreme_independence(self):
        # A single split decision in which r1 crosses would raw-score
        # independence at 100; shrinkage must pull it well toward 50.
        votes = {jid: ("majority" if p == "R" else "minority") for jid, p in PARTY_MAP.items()}
        votes["r1"] = "minority"  # r1 crosses to the D side
        records = _case("c0", votes)
        all_votes = {"c0": records}
        per_justice = [r for r in records if r["justice_id"] == "r1"]
        result = analyze_justice_votes(
            justice_id="r1", appointing_party="R",
            votes=per_justice, all_case_votes=all_votes, party_map=PARTY_MAP,
        )
        # raw would be 100; with n=1 and threshold 15, conf≈0.067 →
        # 100*0.067 + 50*0.933 ≈ 53.3
        assert 50 <= result["score_independence"] <= 60


class TestRecusalNotCountedAsAgreement:
    def test_recused_other_justice_excluded_from_agreement(self):
        # d1 recused (non-participation value); must not count as agreeing
        # with r1 nor sit in the denominator.
        votes = {"r1": "majority", "r2": "majority", "r3": "majority",
                 "d1": "recused", "d2": "minority", "d3": "minority"}
        records = []
        n_maj = 3
        n_min = 2  # d2, d3 (d1 recused, excluded from tallies here)
        for jid, side in votes.items():
            records.append({
                "case_id": "c0", "justice_id": jid, "vote": side,
                "opinion_type": "none", "is_unanimous": False, "is_close": False,
                "majority_votes": n_maj, "minority_votes": n_min,
            })
        result = analyze_justice_votes(
            justice_id="r1", appointing_party="R",
            votes=[r for r in records if r["justice_id"] == "r1"],
            all_case_votes={"c0": records}, party_map=PARTY_MAP,
        )
        # d1 must be absent from the agreement matrix (never a real pairing).
        assert "d1" not in result["agreement_matrix"]
