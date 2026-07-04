"""Shadow-scoring harness: score all senators from existing DB data.

Rebuilds each senator's scoring payload from the live database and runs
the *currently installed* scoring code over it — without touching stored
scores. Use it to preview the population-level impact of an algorithm
change before deploying it:

    docker exec mp-backend-<slot> python3 scripts/rescore.py

or, to test uncommitted code, copy the tree into the container and put it
first on PYTHONPATH:

    docker cp backend/app <container>:/tmp/newcode/app
    docker exec -e PYTHONPATH=/tmp/newcode <container> python3 scripts/rescore.py

Outputs per-senator old→new comparisons, distribution statistics,
dimension correlations, party means, the FI-vs-fundraising-scale
correlation (watch for scale bias), and the ground-truth reference table.

Origin: the 2026-07 score audits used this approach to validate the v4
and v4.1 algorithm changes against all 100 senators before deploying.

Limitations:
- Promise alignments and House vote labels are recomputed only by real
  pipeline runs; this harness scores from stored data.
- totalFromPACs is corrected from cached FEC financials when available
  (mirroring normalize_finance v4); outsideSpendingFor uses the stored
  senator value unless cached outside-spending data exists.
"""

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys

# Fall back to the installed tree, but never shadow a PYTHONPATH override
# (the docstring's "test uncommitted code" recipe depends on this).
if "/app" not in sys.path:
    sys.path.append("/app")

from app.pipeline.analyze.score_calculator import calculate_scores  # noqa: E402
from app.pipeline.transform.candidate_names import is_candidate_self_donor  # noqa: E402

DB = "file:/data/civitas.db?mode=ro"

# Keep in sync with app/pipeline/analyze/ground_truth.py
# (v4.2 Constituent Alignment ranges, 2026-07-04)
GROUND_TRUTH = [
    ("Collins",   "iv", 70, 100), ("Murkowski", "iv", 70, 100),
    ("Sanders",   "iv", 35, 75),  ("Sanders",   "fi", 70, 100),
    ("Warren",    "iv", 35, 70),  ("Warren",    "fi", 70, 100),
    ("Cruz",      "iv", 30, 60),  ("Cruz",      "fi", 50, 95),
    ("McConnell", "iv", 30, 60),  ("McConnell", "fi", 30, 90),
    ("Paul",      "iv", 55, 95),  ("Paul",      "fi", 60, 100),
    ("Klobuchar", "iv", 40, 70),  ("Klobuchar", "fi", 35, 75),
    ("Ossoff",    "iv", 28, 50),  ("Thune",     "iv", 38, 62),
    ("Fetterman", "iv", 50, 85),
]

DIM_KEYS = ["fi", "pp", "iv", "fd", "le"]
OVERALL_WEIGHTS = [("fi", .25), ("pp", .20), ("iv", .20), ("fd", .15), ("le", .20)]


def corr(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den if den else 0.0


def load_fec_caches(cur):
    cur.execute("SELECT cache_key, data_json FROM api_cache WHERE cache_key LIKE 'candidate-search-%-S'")
    search = {}
    for r in cur.fetchall():
        d = json.loads(r["data_json"])
        cid = d.get("candidate_id") if isinstance(d, dict) else (d[0].get("candidate_id") if d else None)
        if cid:
            search[r["cache_key"]] = cid
    cur.execute("SELECT cache_key, data_json FROM api_cache WHERE cache_key LIKE 'candidate-financials-%'")
    fin = {r["cache_key"]: json.loads(r["data_json"]) for r in cur.fetchall()}
    return search, fin


def corrected_pac(search, fin, name, state):
    cid = search.get(f"candidate-search-{name}-{state}-S")
    if not cid:
        return None
    rows = fin.get(f"candidate-financials-{cid}")
    if not rows:
        return None
    rows = sorted(
        rows,
        key=lambda c: c.get("candidate_election_year") or c.get("cycle") or 0,
        reverse=True,
    )
    return sum(c.get("other_political_committee_contributions", 0) or 0 for c in rows[:2])


def build_payload(cur, s, search, fin):
    sid = s["id"]

    cur.execute(
        "SELECT name, total, type FROM donors WHERE senator_id=? ORDER BY total DESC",
        (sid,),
    )
    top_donors = []
    for r in cur.fetchall():
        d = dict(r)
        if d["type"] == "CandidateAffiliated":
            continue
        if d["type"] != "Self-Funded" and is_candidate_self_donor(d["name"], s["name"]):
            d["type"] = "Self-Funded"
        top_donors.append(d)

    cur.execute(
        "SELECT industry, total, percentage FROM industry_donations WHERE senator_id=? ORDER BY total DESC",
        (sid,),
    )
    industry = [
        {"industry": r["industry"], "name": r["industry"], "total": r["total"],
         "percentage": r["percentage"]}
        for r in cur.fetchall()
    ]

    pac = corrected_pac(search, fin, s["name"], s["state"])
    total_from_pacs = pac if pac and pac > 0 else (s["total_from_pacs"] or 0)

    cur.execute("SELECT * FROM key_votes WHERE senator_id=?", (sid,))
    key_votes = []
    for r in cur.fetchall():
        try:
            areas = json.loads(r["policy_areas"]) if r["policy_areas"] else []
        except Exception:
            areas = []
        key_votes.append({
            "billId": r["bill_id"], "vote": r["vote"],
            "policyArea": r["policy_area"] or "PROCEDURAL",
            "policyAreas": areas,
            "partyAlignmentWeight": r["party_alignment_weight"] or 0.0,
            "votedWithParty": None if r["voted_with_party"] is None else bool(r["voted_with_party"]),
            "stance": r["stance"] or "neutral",
        })

    cur.execute("SELECT * FROM lobbying_matches WHERE senator_id=?", (sid,))
    matches = [
        {"senatorVoteAligned": r["senator_vote_aligned"],
         "donationToSenator": r["donation_to_senator"] or 0,
         "isConsensusVote": "[Consensus]" in (r["description"] or "")}
        for r in cur.fetchall()
    ]

    cur.execute("SELECT alignment FROM campaign_promises WHERE senator_id=?", (sid,))
    promises = [{"alignment": r["alignment"]} for r in cur.fetchall()]

    cur.execute("SELECT * FROM sponsored_bills WHERE senator_id=?", (sid,))
    bills = [
        {"title": r["title"], "isLaw": bool(r["is_law"]),
         "latestAction": r["latest_action"], "billType": r["bill_type"],
         "congress": r["congress"]}
        for r in cur.fetchall()
    ]

    return {
        "id": sid, "party": s["party"], "state": s["state"],
        "funding": {
            "totalRaised": s["total_raised"] or 0,
            "totalFromPACs": min(total_from_pacs, s["total_raised"] or 0),
            "smallDonorPercentage": s["small_donor_percentage"] or 0,
            "topDonors": top_donors[:100],
            "industryBreakdown": industry,
            "outsideSpendingFor": 0,
        },
        "votingRecord": {"keyVotes": key_votes, "recentVotes": []},
        "lobbyingMatches": matches,
        "campaignPromises": promises,
        "sponsoredBills": bills,
        "leadershipScore": s["leadership_score"],
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    search, fin = load_fec_caches(cur)

    cur.execute("SELECT * FROM senators")
    senators = [dict(r) for r in cur.fetchall()]

    results = []
    for s in senators:
        new = calculate_scores(build_payload(cur, s, search, fin))
        results.append({
            "name": s["name"], "state": s["state"], "party": s["party"],
            "raised": s["total_raised"] or 0,
            "old": {
                "fi": s["score_funding_independence"], "pp": s["score_promise_persistence"],
                "iv": s["score_independent_voting"], "fd": s["score_funding_diversity"],
                "le": s["score_legislative_effectiveness"],
            },
            "new": {
                "fi": new["fundingIndependence"], "pp": new["promisePersistence"],
                "iv": new["independentVoting"], "fd": new["fundingDiversity"],
                "le": new["legislativeEffectiveness"],
            },
        })
    conn.close()

    print(f"\n{'Name':<26} {'ST':>2}  {'FI old>new':>11} {'IV old>new':>11} {'LE old>new':>11}")
    print("-" * 70)
    for r in sorted(results, key=lambda x: -(x["new"]["fi"] + x["new"]["iv"])):
        o, n = r["old"], r["new"]
        print(f"{r['name']:<26} {r['state']:>2}  "
              f"{o['fi'] or 0:>4.0f}>{n['fi']:>4.0f}  "
              f"{o['iv'] or 0:>4.0f}>{n['iv']:>4.0f}  "
              f"{o['le'] or 0:>4.0f}>{n['le']:>4.0f}")

    print("\nDISTRIBUTIONS (new | old)")
    for dim in DIM_KEYS:
        nv = [r["new"][dim] for r in results]
        ov = [r["old"][dim] or 0 for r in results]
        print(f"  {dim.upper()}: new mean={statistics.mean(nv):5.1f} stdev={statistics.stdev(nv):4.1f} "
              f"min={min(nv):3.0f} max={max(nv):3.0f}  |  "
              f"old mean={statistics.mean(ov):5.1f} stdev={statistics.stdev(ov):4.1f}")

    print("\nGROUND TRUTH (new scores)")
    fails = 0
    for frag, dim, lo, hi in GROUND_TRUTH:
        match = next((r for r in results if frag in r["name"]), None)
        if not match:
            print(f"  {frag}: NOT FOUND")
            continue
        v = match["new"][dim]
        ok = lo <= v <= hi
        fails += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL':<5} {frag:<12} {dim.upper()}={v:>3.0f}  "
              f"expected [{lo},{hi}]  (old={match['old'][dim] or 0:.0f})")
    print(f"\n{fails} ground-truth failures")

    print("\nPARTY MEANS")
    for party in ("D", "R"):
        sub = [r for r in results if r["party"] == party]
        if not sub:
            continue
        parts = "  ".join(
            f"{d.upper()}={statistics.mean([r['new'][d] for r in sub]):5.1f}" for d in DIM_KEYS
        )
        ov = [sum(r["new"][d] * w for d, w in OVERALL_WEIGHTS) for r in sub]
        print(f"  {party}: {parts}  OVERALL={statistics.mean(ov):5.1f} (n={len(sub)})")

    print("\nDIMENSION CORRELATIONS (|r|>0.40 flagged)")
    for i, d1 in enumerate(DIM_KEYS):
        for d2 in DIM_KEYS[i + 1:]:
            c = corr([r["new"][d1] for r in results], [r["new"][d2] for r in results])
            print(f"  {d1.upper()}x{d2.upper()}: {c:+.3f}{' !' if abs(c) > 0.4 else ''}")

    funded = [r for r in results if r["raised"] > 0]
    print(f"\nFI vs log10(totalRaised): r = "
          f"{corr([math.log10(r['raised']) for r in funded], [r['new']['fi'] for r in funded]):+.3f} "
          f"(scale-bias check — v3 was +0.68)")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
