"""Re-verify Funding Independence's PAC-dependency calibration target.

score_calculator.py's PAC component (_funding_independence_core) uses
`ratio_score = max(0, 1 - 2*pac_ratio) * 100`, calibrated so the median
senator's true PAC ratio (≈28%, per a prior audit) lands near 50. A 2026-07
citation audit found no academic paper actually supplies this number (see
that function's docstring) — it's this platform's own empirical finding,
which means it needs the same periodic re-verification every other
self-calibrated constant in this file gets, not a one-time guess.

Pulls live (totalFromPACs, totalRaised) pairs from the public API — same
reasoning fetch_district_pvi.py and fetch_state_small_donor_baseline.py
give for scraping over a local data source.

Run from the repo (network required):
    python3 backend/scripts/audit_pac_ratio.py
"""

import json
import statistics
import urllib.request

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (PAC-ratio audit; contact: mack.ryanm@gmail.com)"}


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def fetch_pac_ratios(branch: str) -> list[float]:
    listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
    ids = [d["id"] for d in listing if d.get("hasScorecard")]

    ratios = []
    for pid in ids:
        detail = _fetch_json(f"{API_BASE}/politicians/{pid}")
        funding = (detail.get("scorecard") or {}).get("funding") or {}
        total_raised = funding.get("totalRaised") or 0
        total_pacs = funding.get("totalFromPACs") or 0
        if total_raised > 0:
            ratios.append(total_pacs / total_raised)
    return ratios


def main() -> None:
    for branch in ("senate", "house"):
        ratios = fetch_pac_ratios(branch)
        if not ratios:
            print(f"{branch}: no data")
            continue
        median = statistics.median(ratios)
        mean = statistics.mean(ratios)
        p10 = statistics.quantiles(ratios, n=10)[0]
        p90 = statistics.quantiles(ratios, n=10)[8]
        print(
            f"{branch}: n={len(ratios)} median={median:.1%} mean={mean:.1%} "
            f"p10={p10:.1%} p90={p90:.1%}"
        )
        implied_multiplier = 0.5 / median if median > 0 else float("nan")
        print(
            f"  current formula assumes median≈28% (×2.0 multiplier); "
            f"live median is {median:.1%} -> multiplier for median=50 "
            f"would be ×{implied_multiplier:.2f}"
        )


if __name__ == "__main__":
    main()
