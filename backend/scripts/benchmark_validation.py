"""External benchmark validation: Civitas scores vs independent records.

Checks the stored scores against measures computed from data Civitas does not
score from, using the same constructs the scores claim to measure (v6.16):

  Constituent Alignment (both chambers, Voteview):
    1. Seat-relative break residual — how far each member's break rate on
       Voteview party-unity votes (majority of one party against the other)
       sits from the rate same-party members show at the same seat lean, in
       standard deviations per vote, with the expectation measured from
       Voteview's own votes by the same compute_constituent_reference the
       pipeline uses, put through the score's own vote shape
       (score_calculator.seat_relative_vote_score — since v6.16 it peaks at
       the expectation) at that reference's own scale. The scale is
       Voteview's SATURATION_QUANTILE, not the pipeline's: the two vote sets
       differ, and the same quantile rule is the like-for-like comparison.
       CA should correlate positively (it is the 70% vote component's
       construct on an independent vote record).
    2. Constituent Alignment recomputed from Voteview — that vote shape and
       the position-congruence score of each member's Nokken-Poole
       seat-relative extremity (the congress-specific position minus the
       per-party fit on seat lean, build_chamber_ideal_points as the
       pipeline, through score_calculator.position_congruence_score), at the
       scorer's own weights. CA should correlate positively. It replaced a
       check that CA falls as extremity rises (v6.16): with the vote part
       peaked at the seat's expectation, the heaviest breakers — often the
       most center-ward members — score low overall, so total CA no longer
       moves one way with position.
  Legislative Effectiveness (optional, --les-csv):
    3. The Center for Effective Lawmaking's Legislative Effectiveness Score
       (thelawmakers.org) — the benchmark our LE adapts. Supply their file as
       CSV; --les-id-col / --les-col name its bioguide/ICPSR and score
       columns (their layout has not been verified from here).

Run after algorithm changes, inside the backend container:

    docker exec "$(docker ps -q -f name=civitas_backend)" python3 scripts/benchmark_validation.py --chamber both

Baselines: the v4.1/v4.2 figures this script used to print compared the
pre-v6.13 design (raw break rate; DW-NOMINATE). v6.16 has no baseline yet —
record the first production run's correlations here, then investigate any
later run where a correlation drops by more than ~0.15 or changes sign.
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
CHAMBERS = {"senate": ("S", "Senate", "senators"), "house": ("H", "House", "representatives")}


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
    """Per-icpsr [against, total] on party-unity votes (majority vs majority)."""
    votes = defaultdict(dict)
    for r in vote_rows:
        votes[r["rollnumber"]][r["icpsr"]] = r["cast_code"]

    breaks = defaultdict(lambda: [0, 0])
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


def seat_relative_vote_shape(rows: list[dict]) -> dict[str, float]:
    """bioguide -> the seat-relative vote score recomputed on these rows:
    break rate minus the same-party expectation at that seat lean (measured
    from these rows by the pipeline's own compute_constituent_reference),
    through score_calculator.seat_relative_vote_score at that reference's
    scale (party-unity votes are unweighted, so each
    member's evidence is their vote count). rows: {bioguide, party, state, district,
    break_rate, n_votes}."""
    from app.pipeline.analyze.score_calculator import (
        _signed_state_alignment,
        compute_constituent_reference,
        seat_break_residual,
        seat_relative_vote_score,
    )

    # An Independent is scored as the party they caucus with, as the
    # pipeline does; party_unity_breaks already counts their breaks against
    # the Democratic majority, so they caucus D here.
    def caucus(party: str) -> str:
        return "D" if party == "I" else party

    inputs = []
    for r in rows:
        p = caucus(r["party"])
        if p in ("D", "R"):  # rows are already full-confidence (run_chamber)
            alignment = _signed_state_alignment(r["state"], r["party"], effective_party=p, district=r.get("district"))
            inputs.append((p, alignment, r["break_rate"], r["n_votes"]))
    ref = compute_constituent_reference(inputs)
    if ref is None:
        return {}
    out = {}
    for r in rows:
        p = caucus(r["party"])
        if p not in ("D", "R"):
            continue
        # The score's own expectation lookup, handed this reference for
        # whichever chamber the row belongs to.
        dev = seat_break_residual(
            r["break_rate"], r["n_votes"], r["state"], r["party"], effective_party=p, district=r.get("district"),
            reference={"senate": ref, "house": ref},
        )
        if dev is not None:
            out[r["bioguide"]] = seat_relative_vote_score(*dev, r["n_votes"])
    return out

def position_congruence(member_rows: list[dict], chamber: str) -> dict[str, float]:
    """bioguide -> the position-congruence score of the member's Nokken-Poole
    (or DW-NOMINATE) position minus the per-party fit on seat lean, signed
    toward the party flank — the pipeline's own build_chamber_ideal_points
    and score_calculator.position_congruence_score at that fit's saturation."""
    from app.pipeline.analyze.score_calculator import (
        _district_pvi,
        _seat_pvi,
        _state_pvi,
        position_congruence_score,
    )
    from app.pipeline.fetch.voteview import PARTY_CODES, build_chamber_ideal_points

    data, _ = build_chamber_ideal_points(member_rows, chamber, _state_pvi(), _district_pvi())
    saturation = data.get("extremity_p90")
    if not saturation:
        return {}
    out = {}
    for row in member_rows:
        bio = (row.get("bioguide_id") or "").strip()
        party = PARTY_CODES.get(int(row.get("party_code") or 0))
        fit = data["fit"].get(party or "")
        if bio not in data["members"] or not fit:
            continue
        district = None
        if chamber == "house":
            try:
                district = int(float(row.get("district_code") or 0)) or None
            except ValueError:
                district = None
        expected = fit["a"] + fit["b"] * _seat_pvi(row.get("state_abbrev", ""), district)
        residual = data["members"][bio] - expected
        out[bio] = position_congruence_score(-residual if party == "D" else residual, float(saturation))
    return out


def load_les(path: str, id_col: str, score_col: str) -> dict[str, float]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    cols = {c.lower(): c for c in rows[0]}
    idc, sc = cols.get(id_col.lower()), cols.get(score_col.lower())
    if idc is None or sc is None:
        raise SystemExit(f"--les-csv has no {id_col!r}/{score_col!r} columns; it has {list(rows[0])}")
    out = {}
    for r in rows:
        try:
            out[str(r[idc]).strip()] = float(r[sc])
        except (TypeError, ValueError):
            continue
    return out


def run_chamber(chamber: str, congress: int, les: dict[str, float] | None, les_key: str) -> list[str]:
    from app.pipeline.analyze.score_calculator import (
        CONSTITUENT_FULL_CONFIDENCE_VOTES,
        POSITION_CONGRUENCE_WEIGHT,
    )

    letter, voteview_chamber, table = CHAMBERS[chamber]
    print(f"\n=== {voteview_chamber} {congress} ===")
    member_rows = [r for r in fetch_csv(f"{VOTEVIEW}/members/{letter}{congress}_members.csv")
                   if r["chamber"] == voteview_chamber]
    vote_rows = fetch_csv(f"{VOTEVIEW}/votes/{letter}{congress}_votes.csv")
    members = {
        r["icpsr"]: {"bioguide": r["bioguide_id"], "icpsr": r["icpsr"], "state": r["state_abbrev"],
                     "district": (int(float(r["district_code"] or 0)) or None) if chamber == "house" else None,
                     "party": {"100": "D", "200": "R", "328": "I"}.get(r["party_code"], "?")}
        for r in member_rows
    }
    breaks, n_unity = party_unity_breaks(members, vote_rows)
    print(f"party-unity roll calls: {n_unity}")
    rows = [
        {**m, "break_rate": breaks[icpsr][0] / breaks[icpsr][1], "n_votes": breaks[icpsr][1]}
        for icpsr, m in members.items()
        if breaks.get(icpsr) and breaks[icpsr][1] >= CONSTITUENT_FULL_CONFIDENCE_VOTES
    ]
    vote_shape = seat_relative_vote_shape(rows)
    congruence = position_congruence(member_rows, chamber)
    # The score's own combination: position at its weight where an ideal
    # point exists, the vote shape alone where one doesn't.
    recomputed = {
        b: v * (1 - POSITION_CONGRUENCE_WEIGHT) + congruence[b] * POSITION_CONGRUENCE_WEIGHT
        if b in congruence else v
        for b, v in vote_shape.items()
    }

    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row
    ours = {
        r["bioguide_id"]: dict(r) for r in conn.execute(
            f"SELECT name, bioguide_id, score_constituent_alignment ca, "
            f"score_legislative_effectiveness le FROM {table} WHERE is_current = 1"
        ) if r["bioguide_id"]
    }
    conn.close()

    problems: list[str] = []

    def report(label, bench: dict[str, float], field: str, expect_sign: int):
        pairs = [(bench[b], ours[b][field]) for b in bench if b in ours and ours[b][field] is not None]
        if len(pairs) < (40 if chamber == "senate" else 150):
            problems.append(f"{chamber}: only {len(pairs)} matches for {label} — join problem?")
            print(f"{label}: too few matches ({len(pairs)})")
            return
        r = corr([p[0] for p in pairs], [p[1] for p in pairs])
        ok = r * expect_sign > 0
        print(f"{label}: r = {r:+.3f} (n={len(pairs)}, expected {'+' if expect_sign > 0 else '-'}){'' if ok else '  <-- WRONG SIGN'}")
        if not ok:
            problems.append(f"{chamber}: {label} r={r:+.3f}, expected the opposite sign")

    report("CA vs seat-relative vote shape (Voteview)", vote_shape, "ca", +1)
    report("CA vs Constituent Alignment recomputed from Voteview", recomputed, "ca", +1)
    if les is not None:
        key_of = {m["bioguide"]: m["icpsr"] for m in members.values()}
        les_by_bio = {b: les[key_of[b] if les_key == "icpsr" else b] for b in key_of
                      if (key_of[b] if les_key == "icpsr" else b) in les}
        report("LE vs CEL Legislative Effectiveness Score", les_by_bio, "le", +1)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--congress", type=int, default=None, help="default: settings.CURRENT_CONGRESS")
    ap.add_argument("--chamber", choices=["senate", "house", "both"], default="both")
    ap.add_argument("--les-csv", help="CEL LES file exported as CSV")
    ap.add_argument("--les-id-col", default="icpsr", help="member id column (icpsr or bioguide)")
    ap.add_argument("--les-col", default="les", help="score column")
    args = ap.parse_args()

    from app.config import settings
    congress = args.congress or settings.CURRENT_CONGRESS
    les = load_les(args.les_csv, args.les_id_col, args.les_col) if args.les_csv else None
    les_key = "icpsr" if args.les_id_col.lower() == "icpsr" else "bioguide"
    problems = []
    for chamber in (["senate", "house"] if args.chamber == "both" else [args.chamber]):
        problems += run_chamber(chamber, congress, les, les_key)
    if problems:
        print("\nPROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    print("\nOK: every benchmark correlates in the expected direction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
