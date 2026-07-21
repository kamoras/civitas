"""Calibrate Legislative Effectiveness's V&W-based component constants:
_LES_POPULATION_AVG_SENATE, _LES_POPULATION_AVG_HOUSE,
_LES_AVG_BASELINE_SENATE, _LES_AVG_BASELINE_HOUSE, _LES_CREDIT_SATURATION
(score_calculator.py).

V&W's real LES normalizes to the chamber-term population mean (average
member = 1.0). This platform's sponsored-bill data is career-cumulative
(many congresses, not one fixed term), so the population average is a
periodically-recalibrated constant instead of a live computation — same
convention as every other self-calibrated constant in this file (e.g.
the FI small-donor state baseline). This script measures that average
directly from live production data using the SAME _les_cumulative_credit/
_les_bill_stage/_advancement_baseline functions the real formula uses, so
calibration and scoring can never silently drift apart.

Run inside the backend container (imports the real scoring module):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
        run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_les_credit_scale.py

After running, paste the reported constants into score_calculator.py
with a comment citing this script and the date.
"""

import json
import statistics
import urllib.request

from app.pipeline.analyze.score_calculator import (
    SUBSTANTIVE_BILL_TYPES,
    _LES_HOUSE_TYPES,
    _advancement_baseline,
    _les_cumulative_credit,
)

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (LES calibration; contact: mack.ryanm@gmail.com)"}


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _per_congress_credit(bills: list[dict]) -> float | None:
    n_sub = sum(1 for b in bills if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES)
    if n_sub == 0:
        return None
    congresses = {b.get("congress") for b in bills if b.get("congress")}
    n_congresses = max(len(congresses), 1)
    return sum(_les_cumulative_credit(b) for b in bills) / n_congresses


def _member_baseline(bills: list[dict], party: str | None) -> float | None:
    if not bills:
        return None
    return sum(
        _advancement_baseline((b.get("billType") or "").lower(), b.get("congress"), party)
        for b in bills
    ) / len(bills)


def main() -> None:
    per_congress_by_chamber: dict[str, list[float]] = {"senate": [], "house": []}
    # Chamber-specific as of 2026-07-21: a single pooled baseline compared
    # every member against a cross-chamber average, systematically
    # inflating House members' expected credit and deflating the Senate's
    # since the two chambers' real _advancement_baseline rates genuinely
    # differ (House mean ~0.044 vs Senate ~0.031) — see the constants'
    # comment in score_calculator.py for the live-population impact this
    # had before the fix (61% of House below neutral vs 38% of Senate).
    member_baselines_by_chamber: dict[str, list[float]] = {"senate": [], "house": []}

    for branch in ("senate", "house"):
        listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
        ids = [d["id"] for d in listing if d.get("hasScorecard")]
        for pid in ids:
            detail = _fetch_json(f"{API_BASE}/politicians/{pid}")
            sc = detail.get("scorecard") or {}
            bills = sc.get("sponsoredBills") or []
            party = sc.get("party")

            per_congress = _per_congress_credit(bills)
            if per_congress is not None:
                # Chamber inference: same logic _les_component_score uses.
                house_n = sum(1 for b in bills if (b.get("billType") or "").lower() in _LES_HOUSE_TYPES)
                is_house = house_n > (len(bills) - house_n)
                per_congress_by_chamber["house" if is_house else "senate"].append(per_congress)

            mb = _member_baseline(bills, party)
            if mb is not None:
                member_baselines_by_chamber[branch].append(mb)

    for chamber in ("senate", "house"):
        values = per_congress_by_chamber[chamber]
        if not values:
            print(f"{chamber}: no data")
            continue
        mean = statistics.mean(values)
        median = statistics.median(values)
        stdev = statistics.pstdev(values)
        p90 = statistics.quantiles(values, n=10)[8] if len(values) >= 10 else max(values)
        print(
            f"{chamber}: n={len(values)} mean={mean:.2f} median={median:.2f} "
            f"stdev={stdev:.2f} p90={p90:.2f}"
        )

    avg_baseline_by_chamber = {
        chamber: statistics.mean(values) if values else 0.0
        for chamber, values in member_baselines_by_chamber.items()
    }
    for chamber, avg in avg_baseline_by_chamber.items():
        print(f"\n{chamber}-average _advancement_baseline: {avg:.4f}")

    print("\nSuggested constants:")
    for chamber in ("senate", "house"):
        values = per_congress_by_chamber[chamber]
        if values:
            print(f"  _LES_POPULATION_AVG_{chamber.upper()} = {statistics.mean(values):.2f}")
    for chamber in ("senate", "house"):
        print(f"  _LES_AVG_BASELINE_{chamber.upper()} = {avg_baseline_by_chamber[chamber]:.4f}")
    # Saturation: same "~1.5x the residual spread" reasoning used
    # elsewhere in this file's calibration constants (e.g. the FI
    # small-donor baseline script) — full credit/deficit at roughly one
    # meaningful standard deviation of real per-congress spread past the
    # population average, chamber-specific stdevs averaged since the
    # saturation constant is currently shared across chambers.
    stdevs = [statistics.pstdev(v) for v in per_congress_by_chamber.values() if v]
    if stdevs:
        suggested_saturation = round(1.5 * statistics.mean(stdevs), 2)
        print(f"  _LES_CREDIT_SATURATION = {suggested_saturation}")


if __name__ == "__main__":
    main()
