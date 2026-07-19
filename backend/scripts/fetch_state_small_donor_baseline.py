"""Fit the state-population baseline for Funding Independence's small-donor
component (see score_calculator.py's _state_small_donor_baseline).

One-off calibration tool, not part of the live pipeline — mirrors
fetch_district_pvi.py in spirit: writes its fitted output to a checked-in
JSON file (app/data/small_donor_baseline.json) that score_calculator.py's
_small_donor_baseline_fit() reads, rather than printing values for a
human to hand-type into source as Python literals (AGENTS.md
"Calibrated constants are generated data"). Rerun this whenever the
regression looks stale (a multi-year drift in fundraising patterns —
state population itself is static enough between censuses to only need
scripts/fetch_state_population.py rerun after 2030).

Pulls live (state, smallDonorPercentage) pairs from the public API rather
than the DB directly, so this can run from any machine with network
access — same reasoning fetch_district_pvi.py gives for scraping over a
local data source. State population comes from app/data/state_population.json
(scripts/fetch_state_population.py) — the same file score_calculator.py's
_state_population() reads, so this script's inputs can't silently drift
from what the live formula actually uses.

Fits smallDonorPercentage = A + B * ln(population_millions) by ordinary
least squares (closed-form, stdlib only — matches score_calibration.py's
"no numpy" convention), then derives residual-based saturation constants
for the surplus/deficit scoring curve.

Run from the repo (network required):
    python3 backend/scripts/fetch_state_small_donor_baseline.py
"""

import json
import math
import pathlib
import statistics
import urllib.request

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (funding-baseline calibration; contact: mack.ryanm@gmail.com)"}

STATE_POPULATION_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "state_population.json"
OUTPUT_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "small_donor_baseline.json"


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _load_state_population() -> dict[str, float]:
    """Same file (and same convention) score_calculator.py's
    _state_population() reads — see fetch_state_population.py."""
    return {k: float(v) for k, v in json.loads(STATE_POPULATION_PATH.read_text())["states"].items()}


def fetch_senator_small_donor_pairs(state_population: dict[str, float]) -> list[tuple[str, float]]:
    """(state, smallDonorPercentage) for every current senator with a scorecard."""
    listing = _fetch_json(f"{API_BASE}/politicians?branch=senate")
    ids = [d["id"] for d in listing if d.get("hasScorecard")]

    pairs = []
    for pid in ids:
        detail = _fetch_json(f"{API_BASE}/politicians/{pid}")
        sc = detail.get("scorecard", {})
        state = sc.get("state")
        pct = (sc.get("funding") or {}).get("smallDonorPercentage")
        if state in state_population and pct is not None:
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
    state_population = _load_state_population()
    pairs = fetch_senator_small_donor_pairs(state_population)
    print(f"{len(pairs)} senators with state + small-donor data")

    xs = [math.log(state_population[state]) for state, _ in pairs]
    ys = [pct for _, pct in pairs]

    a, b = fit_ols(xs, ys)
    print(f"Fit: expected_pct = {a:.2f} + {b:.2f} * ln(population_millions)")

    residuals = [y - (a + b * x) for x, y in zip(xs, ys)]
    resid_stdev = statistics.pstdev(residuals)
    print(f"Residual stdev: {resid_stdev:.2f}")
    national_mean = statistics.mean(ys)
    print(f"National mean small-donor %: {national_mean:.2f}")

    # Saturation constant: full credit/deficit at ~1.5 residual-stdevs
    # past the baseline, the same "roughly one meaningful standard
    # deviation of real spread" reasoning used elsewhere in this file's
    # calibration constants (see MIN_STDEV's docstring in ground_truth.py
    # for the same style of justification).
    saturation = round(1.5 * resid_stdev, 1)
    print(f"Saturation (full credit/deficit at this many points past baseline): {saturation}")

    # Bounds: the observed range of *fitted* baselines across all 50
    # states, padded 2 points each direction so a state right at the
    # sample's population extreme doesn't sit exactly on the clamp.
    min_expected = round(min(a + b * x for x in xs), 1)
    max_expected = round(max(a + b * x for x in xs), 1)
    bound_lo = round(max(0.0, min_expected - 2), 1)
    bound_hi = round(max_expected + 2, 1)
    print(f"Fitted baseline range across all states: {min_expected:.1f}% - {max_expected:.1f}%")
    print(f"Clamp bounds (padded 2 points): {bound_lo:.1f}% - {bound_hi:.1f}%")

    print("\nPer-state expected baseline:")
    for state, pop in sorted(state_population.items(), key=lambda kv: kv[1]):
        expected = a + b * math.log(pop)
        print(f"  {state}: pop={pop:>5.1f}M  expected={expected:5.1f}%")

    json.dump(
        {
            "_source": f"OLS regression of {len(pairs)} live senators' smallDonorPercentage "
                       "against ln(state population); regenerate with "
                       "backend/scripts/fetch_state_small_donor_baseline.py",
            "A": round(a, 2),
            "B": round(b, 2),
            "national_mean_pct": round(national_mean, 2),
            "min_expected_pct": bound_lo,
            "max_expected_pct": bound_hi,
            "saturation_pt": saturation,
        },
        open(OUTPUT_PATH, "w"),
        indent=1, sort_keys=True,
    )
    print(f"\nwrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
