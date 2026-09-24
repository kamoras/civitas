"""Funding Independence counts each signal once (v6.13).

Measured on FEC bulk data for 2020-2024 incumbents
(scripts/audit_funding_components.py, docs/research/funding-independence.md):

- Outside spending tracked race competitiveness, not PAC dependency, and
  was removed from the PAC share.
- Source breadth was a second copy of the small-donor share (R^2 0.79-0.86)
  and was removed.
- The industry-concentration fallback toward "50 + 50 x small-donor share"
  was a third copy; Funding Independence now falls back to a neutral 50.
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


def test_partially_classified_money_shrinks_toward_neutral():
    one_industry = [{"industry": "HEALTH", "total": 100_000}]  # 10% classified, HHI 1.0
    c = components(funding(industries=one_industry))["Industry concentration"]
    assert c["score"] == 37.5  # raw 0, a quarter of the way from 50


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
