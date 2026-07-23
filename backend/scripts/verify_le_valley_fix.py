"""Verify the 2026-07 Legislative Effectiveness "inaction beats trying and
failing" fix: bucket every live senator/representative by substantive bill
count and confirm the confirmed-zero-bills bucket no longer scores above
the 1-2-bill bucket.

Formula-consistent old-vs-new comparison, not a live-vs-stale-API-field
diff (see score_calculator.py's Funding Independence fix for why that
distinction matters — a prior comparison against a possibly-stale API
field produced a false "systematic bias" alarm). The OLD formula is
reconstructed here verbatim from the pre-fix source rather than checked
out via git, so both sides run against the exact same input data (pulled
from the public API, same reasoning fetch_district_pvi.py gives for
scraping over a local data source) in the same process.

Run from the repo (network required):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
        run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/verify_le_valley_fix.py
"""

import json
import statistics
import urllib.request

from app.pipeline.analyze.score_calculator import (
    SUBSTANTIVE_BILL_TYPES,
    _ADVANCEMENT_ACTION_KEYWORDS,
    _advancement_baseline,
    _legislative_effectiveness_core,
)

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (LE-valley-fix verification; contact: mack.ryanm@gmail.com)"}


def _old_legislative_effectiveness_core(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
    party: str | None = None,
    years_in_office: float | None = None,
) -> dict:
    """Verbatim pre-fix formula (score_calculator.py before the 2026-07
    valley fix) — no confirmed-zero distinction, no volume shrinkage."""
    if not sponsored_bills:
        if leadership_score and leadership_score > 0:
            lp = min(leadership_score, 1.0) * 100
            return {"score": max(0, min(100, round(50 * 0.40 + lp * 0.30 + 50 * 0.30)))}
        return {"score": 50}

    n_bills = len(sponsored_bills)
    substantive = [
        b for b in sponsored_bills
        if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES
    ]
    became_law = 0
    advanced = 0
    for bill in substantive:
        if bill.get("isLaw"):
            became_law += 1
        else:
            action = (bill.get("latestAction") or "").lower()
            if any(kw in action for kw in _ADVANCEMENT_ACTION_KEYWORDS):
                advanced += 1

    n_sub = len(substantive)
    if n_sub > 0:
        success_rate = (became_law + advanced) / n_sub
        expected = sum(
            _advancement_baseline((b.get("billType") or "").lower(), b.get("congress"), party)
            for b in substantive
        ) / n_sub
        advancement_raw = min(success_rate / (2.0 * expected), 1.0) * 100
        advancement_conf = min(n_sub / 10, 1.0)
        advancement_score = advancement_raw * advancement_conf + 50 * (1 - advancement_conf)
    else:
        advancement_score = 50.0

    if leadership_score is not None and leadership_score > 0:
        leadership_raw = min(leadership_score, 1.0) * 100
    else:
        leadership_raw = 50.0
    leadership_conf = min((years_in_office or 0) / 6.0, 1.0)
    leadership_pct = leadership_raw * leadership_conf + 50 * (1 - leadership_conf)

    HOUSE_TYPES = {"hr", "hjres", "hres", "hconres"}
    house_n = sum(1 for b in sponsored_bills if (b.get("billType") or "").lower() in HOUSE_TYPES)
    is_house_member = house_n > (n_bills - house_n)
    VOLUME_CEILING = 40.0 if is_house_member else 95.0

    if n_sub > 0:
        congresses = {b.get("congress") for b in substantive if b.get("congress")}
        per_congress = n_sub / max(len(congresses), 1)
        volume_raw = min(per_congress / VOLUME_CEILING, 1.0) * 100
    else:
        volume_raw = 50.0

    score = max(0, min(100, round(
        advancement_score * 0.40 + leadership_pct * 0.30 + volume_raw * 0.30
    )))
    return {"score": score}


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _bucket(n_sub: int) -> str:
    if n_sub == 0:
        return "0"
    if n_sub <= 2:
        return "1-2"
    if n_sub <= 9:
        return "3-9"
    return "10+"


def main() -> None:
    buckets: dict[str, list[tuple[float, float]]] = {"0": [], "1-2": [], "3-9": [], "10+": []}
    all_new_scores: list[float] = []

    for branch in ("senate", "house"):
        listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
        ids = [d["id"] for d in listing if d.get("hasScorecard")]
        print(f"{branch}: {len(ids)} entities with scorecards")
        for pid in ids:
            detail = _fetch_json(f"{API_BASE}/politicians/{pid}")
            sc = detail.get("scorecard") or {}
            bills = sc.get("sponsoredBills") or []
            leadership_score = sc.get("leadershipScore")
            party = sc.get("party")
            years_in_office = (detail.get("identity") or {}).get("yearsInOffice")

            n_sub = sum(
                1 for b in bills
                if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES
            )
            old = _old_legislative_effectiveness_core(bills, leadership_score, party, years_in_office)["score"]
            new = _legislative_effectiveness_core(bills, leadership_score, party, years_in_office)["score"]
            buckets[_bucket(n_sub)].append((old, new))
            all_new_scores.append(new)

    print(f"\n{'bucket':8} {'n':>5} {'old mean':>10} {'new mean':>10} {'delta':>8}")
    means = {}
    for key in ("0", "1-2", "3-9", "10+"):
        pairs = buckets[key]
        if not pairs:
            print(f"{key:8} {'(empty)':>5}")
            continue
        old_mean = statistics.mean(p[0] for p in pairs)
        new_mean = statistics.mean(p[1] for p in pairs)
        means[key] = new_mean
        print(f"{key:8} {len(pairs):>5} {old_mean:>10.1f} {new_mean:>10.1f} {new_mean - old_mean:>+8.1f}")

    print()
    if "0" in means and "1-2" in means:
        if means["0"] <= means["1-2"]:
            print(f"PASS: 0-bucket mean ({means['0']:.1f}) <= 1-2-bucket mean ({means['1-2']:.1f})")
        else:
            print(f"FAIL: 0-bucket mean ({means['0']:.1f}) > 1-2-bucket mean ({means['1-2']:.1f})")

    stdev = statistics.pstdev(all_new_scores)
    # Historical LE stdev floor this fix was originally verified against
    # (the retired MIN_STDEV table — see ground_truth.py's git history);
    # kept so this point-in-time harness reproduces its original verdict.
    floor = 8.0
    verdict = "PASS" if stdev >= floor else "FAIL"
    print(f"{verdict}: population stdev {stdev:.2f} (floor {floor})")


if __name__ == "__main__":
    main()
