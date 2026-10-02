"""Calibrated constants that used to be hand-typed are measured (v6.13).

AGENTS.md §3a: a value a script or audit produced is generated data, never
a literal pasted into source. Each reference below is measured by the
pipeline from the population it scores, with a bundled fallback file for
before the first run.
"""

from unittest.mock import patch

from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.president_scorer import (
    _historical_legacy_core,
    _public_mandate_core,
    compute_president_reference,
)
from app.pipeline.analyze.score_calculator import (
    _advancement_baseline,
    _industry_concentration,
    _measure_advancement_rates,
    _small_donor_capacity_score,
    compute_funding_reference,
)


def _funding(pac, base=1_000_000, small=20, donors=None):
    return {
        "totalContributions": base, "totalRaised": base, "totalFromPACs": pac,
        "smallDonorPercentage": small, "topDonors": donors or [],
    }


def _donors(top_share, pool=1_000_000):
    top = round(top_share * pool)
    return [{"total": top // 10} for _ in range(10)] + [{"total": (pool - top) // 100} for _ in range(100)]


class TestFundingReferenceStats:
    def test_measures_small_donor_and_concentration(self):
        fundings = [
            _funding(pac=100_000 * (i % 7 + 1), small=10 + i % 20, donors=_donors(0.20 + 0.005 * (i % 30)))
            for i in range(40)
        ]
        ref = compute_funding_reference(fundings)
        assert ref["small_donor_p10"] < ref["small_donor_median"] < ref["small_donor_p90"]
        assert ref["top10_share_n"] == 40
        deciles = ref["top10_share_deciles"]
        assert len(deciles) == 9 and deciles == sorted(deciles)
        assert deciles[0] < ref["top10_share_median"] < deciles[-1]

    def test_concentration_omitted_when_too_few_pools_are_measurable(self):
        ref = compute_funding_reference([_funding(pac=1) for _ in range(40)])
        assert "top10_share_median" not in ref and "pac_ratio_median" in ref

    def test_house_small_donor_share_is_relative_to_the_house_median(self):
        ref = {"small_donor_p10": 10.0, "small_donor_median": 20.0, "small_donor_p90": 30.0}
        assert _small_donor_capacity_score(20.0, "CA", 12, ref)[0] == 50.0
        assert _small_donor_capacity_score(40.0, "CA", 12, ref)[0] == 100.0
        assert _small_donor_capacity_score(0.0, "CA", 12, ref)[0] == 0.0

    def test_unreadable_small_donor_fit_is_neutral_not_a_second_copy(self, monkeypatch):
        monkeypatch.setattr(score_calculator, "_small_donor_baseline_fit_cache", {})
        assert _small_donor_capacity_score(40.0, "CA", None)[0] == 50.0


class TestIndustryConcentrationWithinParty:
    """v6.26: a party's donors' occupations cluster in fewer, broader
    industries than the other's, so a chamber-wide rank scored a party's
    coalition, not the member. Each member is ranked within their party."""

    def _with_mix(self, *shares):
        return {"industryBreakdown": [{"industry": f"IND{i}", "total": 1_000_000 * x} for i, x in enumerate(shares)]}

    def test_each_party_with_enough_members_gets_its_own_deciles(self):
        fundings = [_funding(pac=100_000)] * 40
        for i, f in enumerate(fundings):
            f = dict(f, **self._with_mix(0.5 + 0.01 * (i % 20), 0.5 - 0.01 * (i % 20)))
            fundings[i] = f
        parties = ["D"] * 20 + ["R"] * 15 + ["I"] * 5
        ref = compute_funding_reference(fundings, None, parties)
        assert set(ref["industry_hhi_by_party"]) == {"D", "R"}  # 5 independents: too few
        assert ref["industry_hhi_by_party"]["D"]["n"] == 20

    def test_a_member_is_ranked_within_their_party_and_an_independent_against_the_chamber(self):
        ref = {
            "industry_hhi_deciles": [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6], "industry_hhi_median": 0.4,
            "industry_hhi_by_party": {"D": {"deciles": [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8],
                                            "median": 0.6, "n": 20}},
        }
        funding = self._with_mix(0.723607, 0.276393)  # HHI 0.6
        d_score, d_detail = _industry_concentration(funding, 1_000_000, 50.0, reference=ref, party="D")
        i_score, _ = _industry_concentration(funding, 1_000_000, 50.0, reference=ref, party="I")
        assert d_score == 50.0 and "D median" in d_detail  # the D median
        assert i_score == 10.0  # the chamber's top decile


class TestAdvancementRates:
    def _members(self, maj_adv, min_adv, n=200):
        def bills(adv, party):
            return [
                {"billType": "s", "congress": 120, "isLaw": False,
                 "stage": "IN_COMMITTEE" if i < adv else "INTRODUCED"}
                for i in range(n)
            ]
        return [(bills(maj_adv, "D"), "D"), (bills(min_adv, "R"), "R")]

    def test_measured_from_the_chambers_own_bills(self):
        rates = _measure_advancement_rates(self._members(40, 20), (120, "D"))
        assert rates["majority"] == 0.2 and rates["minority"] == 0.1
        assert rates["pooled"] == 0.15

    def test_too_few_advanced_bills_keeps_the_previous_rates(self):
        assert _measure_advancement_rates(self._members(40, 5), (120, "D")) is None

    def test_only_the_ratio_matters_for_the_baseline(self):
        rates = {"majority": 0.2, "minority": 0.1, "pooled": 0.15}
        maj = _advancement_baseline("s", 120, "D", (120, "D"), rates)
        mino = _advancement_baseline("s", 120, "R", (120, "D"), rates)
        assert maj / mino == 2.0


class TestPresidentReference:
    def _row(self, i, **kw):
        return {"id": f"p{i}", "name": f"P{i}", "avg_approval": None, "approval_trend": None,
                "election_margin": None, "historical_legacy_score": None, **kw}

    def test_measures_each_stat_over_the_presidents_that_have_it(self):
        rows = [self._row(i, avg_approval=40 + i, approval_trend=-10.0 + i) for i in range(12)]
        rows += [self._row(100 + i, election_margin=float(i)) for i in range(20)]
        ref = compute_president_reference(rows)
        assert ref["avg_approval"]["n"] == 12 and ref["avg_approval"]["mean"] == 45.5
        assert ref["election_margin"]["n"] == 20
        assert "historical_legacy" not in ref  # too few rated

    def test_a_split_term_president_is_rated_once(self):
        rows = [self._row(i, historical_legacy_score=500 + i) for i in range(10)]
        rows += [
            {**self._row(22, historical_legacy_score=700), "name": "Grover Cleveland"},
            {**self._row(24, historical_legacy_score=700), "name": "Grover Cleveland"},
        ]
        assert compute_president_reference(rows)["historical_legacy"]["n"] == 11

    def test_scoring_uses_the_given_reference(self):
        ref = {"historical_legacy": {"mean": 500.0, "stdev": 100.0},
               "avg_approval": {"mean": 50.0, "stdev": 10.0},
               "approval_trend": {"mean": 0.0, "stdev": 10.0}}
        assert _historical_legacy_core(500, ref)["score"] == 50
        assert _public_mandate_core(50.0, 0.0, None, ref)["score"] == 50

    def test_pinned_fallback_reproduces_the_old_constants(self):
        # conftest pins the 2026-07 values that were hand-typed: a C-SPAN
        # score equal to the old mean still scores 50.
        assert _historical_legacy_core(549)["score"] == 50


class TestPipelineKeepsUnmeasurableStats:
    def test_funding_stat_missing_this_run_keeps_its_last_value(self, db_session):
        from app.pipeline.analyze.population_reference import FUNDING_REFERENCE
        from app.pipeline.live_references import live_funding_reference

        FUNDING_REFERENCE.write("senate", {"pac_ratio_median": 0.1, "top10_share_median": 0.33,
                                           "top10_share_deciles": [0.2] * 9})
        merged = live_funding_reference("senate", [_funding(pac=200_000) for _ in range(40)])
        assert merged["senate"]["pac_ratio_median"] == 0.2  # measured this run
        assert merged["senate"]["top10_share_median"] == 0.33  # kept


def test_nothing_hand_typed_remains_for_these_constants():
    """The literals these references replaced must not creep back in."""
    import inspect

    from app.pipeline.analyze import president_scorer

    sc = inspect.getsource(score_calculator)
    for literal in ("1_325_000", "0.40 - concentration", "0.064", "12.26", "pac_ratio_multiplier = 1.35"):
        assert literal not in sc, literal
    ps = inspect.getsource(president_scorer)
    for literal in ("50.93", "549.14", "-13.72", "8.39"):
        assert f"= {literal}" not in ps, literal


@patch.object(score_calculator, "_small_donor_baseline_fit_cache", None)
def test_bundled_small_donor_fit_still_loads():
    assert score_calculator._small_donor_baseline_fit()["A"] > 0
