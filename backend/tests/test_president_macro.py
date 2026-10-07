"""Unemployment and inflation in presidential Effectiveness (president v10)."""

from app.pipeline.analyze import president_scorer
from app.pipeline.analyze.president_scorer import (
    POSTWAR_ECONOMY_WEIGHTS,
    _effectiveness_core,
    macro_reference,
    macro_window,
)
from app.pipeline.fetch.macro_series import annual_averages, inflation_by_year

UNEMP = {y: 5.0 + (y % 7) * 0.4 for y in range(1950, 2030)}
INFL = {y: 2.0 + (y % 5) * 0.5 for y in range(1950, 2030)}


def test_a_year_counts_once_december_is_out_and_unpublished_months_are_skipped():
    months = [f"2020-{m:02d}-01,{m}" for m in range(1, 13)]
    months[9] = "2020-10-01,"  # never published (as October 2025)
    text = "observation_date,UNRATE\n" + "\n".join(months) + "\n2021-01-01,.\n2021-02-01,6\n"
    assert annual_averages(text) == {2020: (sum(range(1, 13)) - 10) / 11}  # 2021: no December yet
    assert abs(inflation_by_year({2020: 100.0, 2021: 103.0})[2021] - 3.0) < 1e-9


def test_window_credits_the_years_after_the_first():
    w = macro_window(UNEMP, INFL, 2009, 2017)
    assert w["years"] == 8 and w["unemp_start"] == UNEMP[2009]
    assert abs(w["unemp_change"] - (UNEMP[2017] - UNEMP[2009])) < 1e-9
    assert abs(w["infl_avg"] - sum(INFL[y] for y in range(2010, 2018)) / 8) < 1e-9
    assert macro_window(UNEMP, INFL, 2009, 2017, years=2)["years"] == 2
    assert macro_window(UNEMP, INFL, 2025, 2025) is None  # no credited year yet
    assert macro_window({}, INFL, 2009, 2017) is None


def _windows():
    # Twelve presidencies: the change falls 1.3 points per point of starting
    # unemployment, inflation keeps half the inherited rate, each a little
    # above or below in turn.
    out = []
    for i in range(12):
        u0, i0, off = 3.0 + 0.5 * i, 1.0 + 0.8 * i, (0.5 if i % 2 else -0.5)
        out.append({"unemp_start": u0, "unemp_change": 7.7 - 1.3 * u0 + off,
                    "infl_start": i0, "infl_avg": 1.0 + 0.5 * i0 + off, "years": 8})
    return out


def test_each_is_judged_against_what_its_start_predicts():
    ref = macro_reference(_windows())
    assert abs(ref["unemployment_fit"]["slope"] - -1.3) < 0.05
    assert abs(ref["inflation_fit"]["slope"] - 0.5) < 0.05
    reference = {"macro": ref}
    # Same rise in unemployment; the one that started low (where rises are
    # usual) does better than the one that started high (where falls are).
    low = _effectiveness_core(None, None, 4.0, 1990, reference,
                              macro={"unemp_start": 4.0, "unemp_change": 1.0, "infl_start": 2.0, "infl_avg": 2.0, "years": 4})
    high = _effectiveness_core(None, None, 4.0, 1990, reference,
                               macro={"unemp_start": 8.0, "unemp_change": 1.0, "infl_start": 2.0, "infl_avg": 2.0, "years": 4})
    score = lambda core, label: next(c["score"] for c in core["components"] if c["label"] == label)  # noqa: E731
    assert score(low, "Unemployment") > score(high, "Unemployment")
    assert low["facts"]["unemploymentExpected"] > high["facts"]["unemploymentExpected"]


def test_postwar_parts_weigh_equally_and_prewar_keeps_growth_and_jobs(monkeypatch):
    reference = {"macro": macro_reference(_windows())}
    w = {"unemp_start": 5.0, "unemp_change": 0.0, "infl_start": 3.0, "infl_avg": 2.5, "years": 4}
    core = _effectiveness_core(1.2351 * 3, 2.0, 4.0, 1990, reference, gdp_per_person=2.0, gdp_peer_median=1.0,
                               gdp_relative=1.0, macro=w)
    assert {c["label"]: c["weight"] for c in core["components"]} == {
        "GDP growth vs. peer economies": 0.25, "Jobs created": 0.25, "Unemployment": 0.25, "Inflation": 0.25,
    }
    assert set(POSTWAR_ECONOMY_WEIGHTS) == {"gdp", "jobs", "unemployment", "inflation"}
    prewar = _effectiveness_core(None, 3.317, 4.0, 1890, reference, macro=w)
    assert [c["label"] for c in prewar["components"]] == ["GDP growth"]


def test_a_shorter_presidency_is_judged_over_its_own_years(monkeypatch):
    windows = {"macro_windows": {"short-1": {"unemployment_fit": {"intercept": 0.0, "slope": 0.0, "resid_sd": 1.0, "n": 12}}},
               "macro": macro_reference(_windows())}
    assert president_scorer._macro_reference(windows, "short-1")["unemployment_fit"]["intercept"] == 0.0
    assert president_scorer._macro_reference(windows, "other")["unemployment_fit"]["slope"] < 0
