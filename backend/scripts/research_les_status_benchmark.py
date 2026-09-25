"""Does Legislative Effectiveness compare a sponsor with members of the same
majority/minority status? (v6.13; docs/methodology/member-score/v6.13.md)

Simulates a House whose sponsors are identical except for status: each bill
advances past introduction at the majority's or the minority's rate (the
2026-07 corpus rates, 6.4% / 2.4%). Every simulated member therefore performs
exactly at their status's expectation, and a correct benchmark scores the
typical member of either status at 50.

Runs this repository's own compute_les_reference / _les_component_score twice:
with the same-status median benchmark, and with it removed so the older
advancement-rate tilt (chamber median x member baseline / chamber-average
baseline) sets the bar.

    cd backend && PYTHONPATH=. python scripts/research_les_status_benchmark.py
"""

import random
import statistics

from app.pipeline.analyze.score_calculator import _les_component_score, compute_les_reference

RATES = {"R": 0.064, "D": 0.024}  # majority R, minority D
SEATS = {"R": 220, "D": 213}


def simulate(seed: int = 1):
    rng = random.Random(seed)
    members = []
    for party, n in SEATS.items():
        for _ in range(n):
            bills = [
                {"billType": "hr", "congress": 119,
                 "stage": "INTRODUCED" if rng.random() > RATES[party]
                 else rng.choice(["IN_COMMITTEE", "PASSED_CHAMBER", "ENACTED"])}
                for _ in range(rng.randint(8, 25))
            ]
            members.append((bills, party))
    return members


def party_scores(members, ref):
    out = {"R": [], "D": []}
    for bills, party in members:
        out[party].append(_les_component_score(bills, party, 4.0, {"house": ref})[0])
    return {p: (round(statistics.median(v), 1), round(statistics.mean(v), 1)) for p, v in out.items()}


def main():
    members = simulate()
    ref = compute_les_reference(members, 119, "R")
    tilt_only = {k: v for k, v in ref.items() if k != "status_median"}
    print(f"measured advancement rates: {ref['advancement_rates']}")
    print(f"status medians: {ref.get('status_median')}")
    for label, r in (("advancement-rate tilt (before)", tilt_only), ("same-status median (v6.13)", ref)):
        s = party_scores(members, r)
        print(f"{label:32} majority median/mean {s['R']}   minority median/mean {s['D']}")


if __name__ == "__main__":
    main()
