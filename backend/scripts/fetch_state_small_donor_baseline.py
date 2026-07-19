"""Fit the state-population baseline for Funding Independence's small-donor
component (see score_calculator.py's _state_small_donor_baseline).

One-off calibration tool, not part of the live pipeline — mirrors
fetch_district_pvi.py in spirit (offline data-fitting script whose output
gets pasted into score_calculator.py as a constant), except population is
static enough between censuses that there's no JSON file to regenerate
on a schedule; this script only needs to be rerun if the regression looks
stale (a new census, or a multi-year drift in fundraising patterns).

Pulls live (state, smallDonorPercentage) pairs from the public API rather
than the DB directly, so this can run from any machine with network
access — same reasoning fetch_district_pvi.py gives for scraping over a
local data source.

Fits smallDonorPercentage = A + B * ln(population_millions) by ordinary
least squares (closed-form, stdlib only — matches score_calibration.py's
"no numpy" convention), then reports residual-based saturation constants
for the surplus/deficit scoring curve.

Run from the repo (network required):
    python3 backend/scripts/fetch_state_small_donor_baseline.py
"""

import json
import math
import statistics
import urllib.request

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (funding-baseline calibration; contact: mack.ryanm@gmail.com)"}

# 2020 Census populations, millions. Static between censuses — update
# after 2030. Same 50-state key set as STATE_PVI in score_calculator.py;
# DC/territories are intentionally omitted here (no voting senators) and
# fall back to the national mean in _state_small_donor_baseline.
STATE_POPULATION_M: dict[str, float] = {
    "CA": 39.5, "TX": 29.1, "FL": 21.5, "NY": 20.2, "PA": 13.0, "IL": 12.8,
    "OH": 11.8, "GA": 10.7, "NC": 10.4, "MI": 10.1, "NJ": 9.3, "VA": 8.6,
    "WA": 7.7, "AZ": 7.2, "MA": 7.0, "TN": 6.9, "IN": 6.8, "MO": 6.2,
    "MD": 6.2, "WI": 5.9, "CO": 5.8, "MN": 5.7, "SC": 5.1, "AL": 5.0,
    "LA": 4.6, "KY": 4.5, "OR": 4.2, "OK": 4.0, "CT": 3.6, "UT": 3.3,
    "IA": 3.2, "NV": 3.1, "AR": 3.0, "MS": 2.9, "KS": 2.9, "NM": 2.1,
    "NE": 2.0, "ID": 1.8, "WV": 1.8, "HI": 1.5, "NH": 1.4, "ME": 1.4,
    "MT": 1.1, "RI": 1.1, "DE": 1.0, "SD": 0.9, "ND": 0.8, "AK": 0.7,
    "VT": 0.6, "WY": 0.6,
}


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def fetch_senator_small_donor_pairs() -> list[tuple[str, float]]:
    """(state, smallDonorPercentage) for every current senator with a scorecard."""
    listing = _fetch_json(f"{API_BASE}/politicians?branch=senate")
    ids = [d["id"] for d in listing if d.get("hasScorecard")]

    pairs = []
    for pid in ids:
        detail = _fetch_json(f"{API_BASE}/politicians/{pid}")
        sc = detail.get("scorecard", {})
        state = sc.get("state")
        pct = (sc.get("funding") or {}).get("smallDonorPercentage")
        if state in STATE_POPULATION_M and pct is not None:
            pairs.append((state, float(pct)))
    return pairs


def fit_ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least squares: y = A + B*x. Closed-form, no numpy."""
    mx, my = statistics.mean(xs), statistics.mean(ys)
    b_num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b_den = sum((x - mx) ** 2 for x in xs)
    b = b_num / b_den
    a = my - b * mx
    return a, b


def main() -> None:
    pairs = fetch_senator_small_donor_pairs()
    print(f"{len(pairs)} senators with state + small-donor data")

    xs = [math.log(STATE_POPULATION_M[state]) for state, _ in pairs]
    ys = [pct for _, pct in pairs]

    a, b = fit_ols(xs, ys)
    print(f"\nFit: expected_pct = {a:.2f} + {b:.2f} * ln(population_millions)")

    residuals = [y - (a + b * x) for x, y in zip(xs, ys)]
    resid_stdev = statistics.pstdev(residuals)
    print(f"Residual stdev: {resid_stdev:.2f}")
    print(f"National mean small-donor %: {statistics.mean(ys):.2f}")

    # Saturation constants: full credit/deficit at ~1.5 residual-stdevs
    # past the baseline, the same "roughly one meaningful standard
    # deviation of real spread" reasoning used elsewhere in this file's
    # calibration constants (see MIN_STDEV's docstring in ground_truth.py
    # for the same style of justification).
    saturation = round(1.5 * resid_stdev, 1)
    print(f"\nSuggested SURPLUS_SATURATION_PT = DEFICIT_SATURATION_PT = {saturation}")

    min_expected = round(min(a + b * x for x in xs), 1)
    max_expected = round(max(a + b * x for x in xs), 1)
    print(f"Suggested MIN_EXPECTED_PCT = {max(0.0, min_expected - 2):.1f}")
    print(f"Suggested MAX_EXPECTED_PCT = {max_expected + 2:.1f}")

    print("\nPer-state expected baseline:")
    for state, pop in sorted(STATE_POPULATION_M.items(), key=lambda kv: kv[1]):
        expected = a + b * math.log(pop)
        print(f"  {state}: pop={pop:>5.1f}M  expected={expected:5.1f}%")


if __name__ == "__main__":
    main()
