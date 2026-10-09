"""Approval by party against the era's polarization (president v9)."""

from datetime import date, timedelta

from app.pipeline.analyze.president_scorer import (
    _public_mandate_core,
    approval_by_group,
    approval_vs_era,
    partisan_reference,
    term_polarization,
)
from app.pipeline.fetch.voteview import party_distance


def test_party_distance_reads_float_party_codes():
    rows = [
        {"party_code": "100.0", "nominate_dim1": "-0.4"},
        {"party_code": "100", "nominate_dim1": "-0.2"},
        {"party_code": "200.0", "nominate_dim1": "0.5"},
        {"party_code": "328", "nominate_dim1": "0.0"},  # an independent: not a party mean
    ]
    assert abs(party_distance(rows) - 0.8) < 1e-9
    assert party_distance([{"party_code": "100", "nominate_dim1": "-0.4"}]) is None


def test_groups_are_the_presidents_party_the_other_and_independents():
    start = date(2001, 1, 22)
    series = [(start, {"D": 20.0, "I": 50.0, "R": 90.0}), (start + timedelta(days=400), {"D": 10.0, "I": 40.0, "R": 80.0})]
    assert approval_by_group(series, "R") == {"own": 85.0, "opp": 15.0, "ind": 45.0}
    assert approval_by_group(series, "R", days=30) == {"own": 90.0, "opp": 20.0, "ind": 50.0}
    assert approval_by_group(series, "W") is None


def test_term_polarization_averages_the_congresses_a_term_spans():
    distance = {111: 0.80, 112: 0.84, 113: 0.86, 114: 0.88}
    assert abs(term_polarization(2009, 2017, distance) - 0.845) < 1e-9
    assert term_polarization(2009, 2019, distance) is None  # the 115th is missing: no guess


def _population():
    # Twelve presidencies whose groups move with polarization exactly as
    # planted, each a point above or below it in turn (every group alike);
    # the median of pairwise slopes then recovers the planted slope.
    points, offsets = [], [1, -1] * 6
    for i, off in enumerate(offsets):
        pol = 0.5 + 0.035 * i
        points.append((pol, {"own": 60 + 30 * pol + off, "opp": 90 - 90 * pol + off, "ind": 70 - 35 * pol + off}))
    return points


def test_reference_recovers_each_groups_era_and_scores_what_is_left():
    ref = partisan_reference(_population())
    assert abs(ref["fits"]["opp"]["slope"] - -90) < 1e-6
    assert abs(ref["fits"]["own"]["slope"] - 30) < 1e-6
    # Two presidents with the same offset but a generation apart in
    # polarization come out the same against their eras.
    early = {"own": 60 + 30 * 0.55 + 2, "opp": 90 - 90 * 0.55 + 2, "ind": 70 - 35 * 0.55 + 2}
    late = {"own": 60 + 30 * 0.90 + 2, "opp": 90 - 90 * 0.90 + 2, "ind": 70 - 35 * 0.90 + 2}
    assert abs(approval_vs_era(early, 0.55, ref["fits"]) - approval_vs_era(late, 0.90, ref["fits"])) < 1e-6
    assert partisan_reference(_population()[:5]) is None


def test_public_mandate_scores_the_era_comparison_when_it_can():
    ref = partisan_reference(_population())
    reference = {"partisan_approval": ref, "avg_approval": {"mean": 55.0, "stdev": 9.0, "n": 12}}
    late = {"own": 60 + 30 * 0.90 + 2, "opp": 90 - 90 * 0.90 + 2, "ind": 70 - 35 * 0.90 + 2}
    core = _public_mandate_core(45.0, None, None, reference, approval_groups=late, polarization=0.90)
    assert core["components"][0]["score"] > 50  # above its era, though below the raw average
    assert core["facts"]["approvalVsEra"] == 2.0
    raw = _public_mandate_core(45.0, None, None, reference)
    assert raw["components"][0]["score"] < 50  # without the breakdown: overall approval, as before
