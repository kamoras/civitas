"""External benchmark validation: Civitas scores vs Voteview.

Downloads Voteview's Senate data for the current congress and checks our
Independent Voting scores against the academic gold standard:

  1. Party-unity break rate — per-senator rate of voting against their
     own party's majority on party-unity votes (D majority vs R majority),
     computed from Voteview's complete roll-call record. Our IV should
     correlate strongly and positively.
  2. |DW-NOMINATE dim1| (ideological extremity) — should correlate
     negatively with IV (extremists break less).

Run quarterly (or after algorithm changes) inside the backend container:

    docker exec mp-backend-<slot> python3 scripts/benchmark_validation.py

Baseline (2026-07-02, algorithm v4.1, Senate 119, 97 matched senators):
    IV vs party-unity break rate:  r = +0.70
    IV vs |DW-NOMINATE| extremity: r = -0.48

v4.2 note (2026-07-04): the dimension is now Constituent Alignment —
scored against a seat-specific expected break rate (Cook PVI), not raw
defection — so a LOWER raw-rate correlation is by design:
    CA vs party-unity break rate (shadow-scored):        r = +0.45
    CA vs seat-relative surplus (rate − expected):       r = +0.60
Investigate if the raw-rate correlation falls below ~+0.30 (sign/ordering
regressions) or the surplus correlation falls below ~+0.50.

Future work: correlate Legislative Effectiveness against Volden &
Wiseman's LES (thelawmakers.org) once a stable download URL is wired in.
"""

import argparse
import csv
import io
import math
import sqlite3
import sys
import urllib.request
from collections import defaultdict

DB = "file:/data/civitas.db?mode=ro"
VOTEVIEW = "https://voteview.com/static/data/out"

YEA = {"1", "2", "3"}
NAY = {"4", "5", "6"}


def fetch_csv(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return list(csv.DictReader(io.TextIOWrapper(r, encoding="utf-8")))


def corr(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den if den else 0.0


def party_unity_breaks(members, vote_rows, min_party_votes=10):
    """Per-icpsr break counts on party-unity votes (majority vs majority)."""
    votes = defaultdict(dict)
    for r in vote_rows:
        votes[r["rollnumber"]][r["icpsr"]] = r["cast_code"]

    breaks = defaultdict(lambda: [0, 0])  # icpsr -> [against, total]
    n_unity = 0
    for _, member_votes in votes.items():
        tallies = {"D": [0, 0], "R": [0, 0]}
        for icpsr, cast in member_votes.items():
            m = members.get(icpsr)
            if not m:
                continue
            party = m["party"] if m["party"] in ("D", "R") else "D"  # I caucus D
            if cast in YEA:
                tallies[party][0] += 1
            elif cast in NAY:
                tallies[party][1] += 1
        if min(sum(tallies["D"]), sum(tallies["R"])) < min_party_votes:
            continue
        d_yea = tallies["D"][0] > tallies["D"][1]
        r_yea = tallies["R"][0] > tallies["R"][1]
        if d_yea == r_yea:
            continue
        n_unity += 1
        for icpsr, cast in member_votes.items():
            m = members.get(icpsr)
            if not m:
                continue
            party = m["party"] if m["party"] in ("D", "R") else "D"
            majority_yea = d_yea if party == "D" else r_yea
            if cast in YEA:
                agreed = majority_yea
            elif cast in NAY:
                agreed = not majority_yea
            else:
                continue
            breaks[icpsr][1] += 1
            if not agreed:
                breaks[icpsr][0] += 1
    return breaks, n_unity


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--congress", type=int, default=119)
    args = ap.parse_args()

    c = args.congress
    print(f"Fetching Voteview Senate {c} data...")
    member_rows = fetch_csv(f"{VOTEVIEW}/members/S{c}_members.csv")
    vote_rows = fetch_csv(f"{VOTEVIEW}/votes/S{c}_votes.csv")

    members = {}
    for r in member_rows:
        if r["chamber"] != "Senate":
            continue
        members[r["icpsr"]] = {
            "name": r["bioname"],
            "bioguide": r["bioguide_id"],
            "party": {"100": "D", "200": "R", "328": "I"}.get(r["party_code"], "?"),
            "nom1": float(r["nominate_dim1"]) if r["nominate_dim1"] else None,
        }

    breaks, n_unity = party_unity_breaks(members, vote_rows)
    print(f"Party-unity votes: {n_unity}")

    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name, bioguide_id, score_independent_voting iv FROM senators")
    ours = {r["bioguide_id"]: dict(r) for r in cur.fetchall() if r["bioguide_id"]}
    conn.close()

    pairs = []
    for icpsr, m in members.items():
        b = breaks.get(icpsr)
        if not b or b[1] < 20:
            continue
        mine = ours.get(m["bioguide"])
        if not mine or mine["iv"] is None:
            continue
        pairs.append({
            "name": mine["name"],
            "break_rate": b[0] / b[1],
            "iv": mine["iv"],
            "extremity": abs(m["nom1"]) if m["nom1"] is not None else None,
        })

    print(f"Matched senators: {len(pairs)}")
    if len(pairs) < 50:
        print("ERROR: too few matches — bioguide join problem?")
        return 1

    r_unity = corr([p["break_rate"] for p in pairs], [p["iv"] for p in pairs])
    ext_pairs = [p for p in pairs if p["extremity"] is not None]
    r_ext = corr([p["extremity"] for p in ext_pairs], [p["iv"] for p in ext_pairs])

    print(f"\nIV vs Voteview party-unity break rate: r = {r_unity:+.3f}  (baseline +0.70)")
    print(f"IV vs |DW-NOMINATE dim1| extremity:    r = {r_ext:+.3f}  (baseline -0.48)")

    # Largest disagreements are the most informative cases to inspect.
    ranked = sorted(pairs, key=lambda p: p["break_rate"])
    n = len(ranked)
    print("\nLargest IV-vs-benchmark disagreements:")
    scored = sorted(
        pairs,
        key=lambda p: abs(
            (sorted(pairs, key=lambda q: q["break_rate"]).index(p) / n)
            - (sorted(pairs, key=lambda q: q["iv"]).index(p) / n)
        ),
        reverse=True,
    )
    for p in scored[:5]:
        print(f"  {p['name']:<26} break_rate={p['break_rate']*100:5.1f}%  IV={p['iv']:.0f}")

    if r_unity < 0.5:
        print("\nWARNING: party-unity correlation below 0.5 — IV pipeline may have regressed")
        return 1
    print("\nOK: IV tracks the external benchmark")
    return 0


if __name__ == "__main__":
    sys.exit(main())
