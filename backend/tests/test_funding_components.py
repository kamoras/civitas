"""Funding Independence counts each signal once (v6.13).

Measured on FEC bulk data for 2020-2024 incumbents
(scripts/audit_funding_components.py, docs/research/funding-independence.md):

- Outside spending tracked race competitiveness, not PAC dependency, and
  was removed from the PAC share.
- Source breadth was a second copy of the small-donor share (R^2 0.79-0.86)
  and was removed.
- The industry-concentration fallback toward "50 + 50 x small-donor share"
  was a third copy; Funding Independence now falls back to a neutral 50,
  and only when too little money is classified to measure a mix (v6.26).
"""

from app.pipeline.analyze.score_calculator import (
    _funding_diversity_core,
    _funding_independence_core,
)
from app.pipeline.transform.normalize_finance import normalize_finance


def funding(small=20, industries=None, **extra):
    return {
        "totalContributions": 1_000_000, "totalRaised": 1_000_000,
        "totalFromPACs": 200_000, "smallDonorPercentage": small,
        "topDonors": [], "industryBreakdown": industries or [], **extra,
    }


def components(f):
    return {c["label"]: c for c in _funding_independence_core(f)["components"]}


THIN = [{"industry": "HEALTH", "total": 20_000}]  # 2% classified: below the HHI floor


def test_four_components_and_no_source_breadth():
    labels = list(components(funding()))
    assert labels == ["PAC dependency", "Small-donor share", "Top-donor concentration", "Industry concentration"]


def test_small_donor_share_reaches_the_score_through_one_component():
    low, high = components(funding(small=5, industries=THIN)), components(funding(small=60, industries=THIN))
    changed = [label for label in low if low[label]["score"] != high[label]["score"]]
    assert changed == ["Small-donor share"]


def test_unmeasurable_industry_concentration_is_neutral():
    c = components(funding(small=60, industries=THIN))["Industry concentration"]
    assert c["score"] == 50.0 and "neutral 50" in c["detail"]


def test_measurable_industry_money_is_scored_on_its_mix_alone():
    """v6.26: a measurable mix is scored on the mix, not pulled toward 50
    by how small a share of all funding it is. That pull read a small-gift
    campaign's industry money as unreliable however much of it there was."""
    one_industry = [{"industry": "HEALTHCARE", "total": 300_000}]
    assert components(funding(industries=one_industry))["Industry concentration"]["score"] == 0.0
    # Mostly small gifts, the itemized rest spread across many industries.
    spread_wide = [{"industry": ind, "total": 60_000} for ind in
                   ("EDUCATION", "HEALTHCARE", "LAWYERS", "MEDIA", "TECH", "REAL_ESTATE", "FINANCE", "CONSTRUCTION")]
    c = components(funding(small=68, industries=spread_wide))["Industry concentration"]
    assert c["score"] > 80  # HHI 0.125, near the bottom of the chamber (median 0.18 scores 50)


def test_outside_spending_does_not_move_the_score():
    assert _funding_independence_core(funding(outsideSpendingFor=5_000_000))["score"] == \
        _funding_independence_core(funding())["score"]


def test_outside_spending_is_no_longer_fetched_or_stored():
    from app.models import Representative, Senator
    from app.pipeline.fetch import fec

    assert not hasattr(fec, "fetch_outside_spending")
    assert not hasattr(Senator, "outside_spending_for") and not hasattr(Representative, "outside_spending_for")
    f = normalize_finance(None, [{"candidate_election_year": 2024, "receipts": 100, "contributions": 100}], [], [], [])
    assert "outsideSpendingFor" not in f


def test_funding_diversity_keeps_its_own_grassroots_fallback():
    # Funding Diversity is not a weighted dimension; its standalone story
    # still credits a grassroots campaign where industry money is too thin.
    fd = {c["label"]: c for c in _funding_diversity_core(funding(small=60, industries=THIN))["components"]}
    assert fd["Industry concentration"]["score"] == 80.0


class TestSenateSmallDonorBaselineIsMeasured:
    """v6.24: the Senate's small-donor baseline is fitted each run over the
    senators being scored, never a frozen fit."""

    def test_fit_recovers_a_known_line(self):
        import math

        from app.pipeline.analyze.score_calculator import small_donor_baseline_fit

        # 40 senators on 10 + 5*ln(pop): each of 20 states' pair sits 2
        # above and 2 below the line, so the residuals spread and cancel.
        pops = [0.6 + 0.9 * k for k in range(20)]
        pairs = [(pop, 10 + 5 * math.log(pop) + d) for pop in pops for d in (2, -2)]
        fit = small_donor_baseline_fit(pairs)
        assert round(fit["A"], 4) == 10.0 and round(fit["B"], 4) == 5.0
        assert fit["n"] == 40 and fit["saturation_pt"] == 3.0
        assert fit["min_expected_pct"] < fit["max_expected_pct"]

    def test_too_few_senators_keep_the_last_fit(self):
        from app.pipeline.analyze.score_calculator import small_donor_baseline_fit

        assert small_donor_baseline_fit([(1.0, 20.0)] * 5) is None

    def test_the_senate_reference_carries_the_fit_and_the_score_uses_it(self, monkeypatch):
        from app.pipeline.analyze import score_calculator as sc

        monkeypatch.setattr(sc, "_state_population", lambda: {f"S{i}": 0.5 + i for i in range(40)})
        fundings = [
            {"totalContributions": 1_000_000, "totalFromPACs": 100_000, "smallDonorPercentage": 30.0}
            for _ in range(40)
        ]
        # Everyone at 30% but for a spread: the fit centres on the Senate.
        for i, f in enumerate(fundings):
            f["smallDonorPercentage"] = 30.0 + (3 if i % 2 else -3)
        ref = sc.compute_funding_reference(fundings, [f"S{i}" for i in range(40)])
        fit = ref["small_donor_fit"]
        assert round(fit["national_mean_pct"], 1) == 30.0
        # A senator exactly at what the run's fit expects for their state
        # scores neutral, whatever the bundled file says.
        expected = sc._state_small_donor_baseline("S5", fit)
        score, used = sc._small_donor_capacity_score(expected, "S5", None, ref)
        assert (score, used) == (50.0, expected)

    def test_the_house_reference_has_no_state_fit(self):
        from app.pipeline.analyze import score_calculator as sc

        fundings = [{"totalContributions": 1_000_000, "totalFromPACs": 100_000, "smallDonorPercentage": 20.0 + i % 5}
                    for i in range(40)]
        assert "small_donor_fit" not in sc.compute_funding_reference(fundings)
