"""Regenerate app/data/confidence_grades.json: how many party-line votes a
member's Constituent Alignment rests on before its data grade is Partial or
Full (score_calculator.calculate_confidence).

The grade says how far a member's break rate can be trusted as a measure of
the member rather than of which votes happened to be cast. That is split-half
reliability (Spearman 1910; Brown 1910): split a member's party-line votes in
two at random, measure the break rate on each half, and correlate the halves
across the chamber's members. Done at each record length n (two disjoint
draws of n votes per member with at least 2n, averaged over DRAWS draws), it
gives the reliability of a break rate measured on n votes. The grade's cut
points are the conventional bars (Nunnally & Bernstein 1994, Psychometric
Theory, 3rd ed., pp. 264-265): 0.70, modest reliability, for Partial, and
0.80 for Full. Each chamber gets the smallest n whose reliability reaches each
bar, since the two chambers' members break at different rates.

A party-line vote is labelled as the scores label it (compute_party_split,
housekeeping questions aside), and each measure counts once
(party_line_record.measure_key), as party_break_rate counts them.

app/data_refresh.py reruns it monthly against the serving database; the
checked-in file is the fallback. Run inside the backend container:
    docker compose run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_confidence_grades.py
"""

import argparse
import datetime
import json
import pathlib
import random
import statistics
from collections import defaultdict

from app.database import SessionLocal
from app.models import RollCall, RollCallPosition
from app.pipeline.analyze.party_line_record import measure_key
from app.pipeline.transform.normalize_votes import compute_party_split, is_housekeeping

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "confidence_grades.json"
# Nunnally & Bernstein (1994): modest reliability, and the bar for basic research.
PARTIAL_RELIABILITY = 0.70
FULL_RELIABILITY = 0.80
# Random splits averaged at each record length, from a fixed seed so a rerun
# on the same data writes the same file.
DRAWS = 50
SEED = 1
# Fewest members with 2n votes for a reliability at n to be measured (a
# correlation over fewer members is mostly noise).
MIN_MEMBERS = 20
YEA, NAY = {"yea", "aye", "yes"}, {"nay", "no"}


def break_records(roll_calls: list[dict]) -> dict[str, list[int]]:
    """{member: [1 for each party-line measure they broke on, 0 for each
    they voted with their party on]} for one chamber's roll calls, each a
    dict with question, bill_id, chamber and positions [(member, party,
    position)]. A measure's first roll call (in the order given) counts."""
    seen: dict[str, set] = defaultdict(set)
    out: dict[str, list[int]] = defaultdict(list)
    for rc in roll_calls:
        if is_housekeeping(rc["question"], rc["chamber"]):
            continue
        label = compute_party_split({"members": [{"party": p, "voteCast": v} for _, p, v in rc["positions"]]})
        if label not in ("R", "D"):
            continue
        unit = measure_key(rc["question"], rc["bill_id"]) or f"rc-{rc['id']}"
        for member, party, vote in rc["positions"]:
            v = (vote or "").strip().lower()
            if party not in ("R", "D") or (v not in YEA and v not in NAY) or unit in seen[member]:
                continue
            seen[member].add(unit)
            out[member].append(int((v in YEA) != (party == label)))
    return dict(out)


def reliability_curve(records: dict[str, list[int]], draws: int = DRAWS, seed: int = SEED,
                      stop_at: float = FULL_RELIABILITY) -> dict[int, float]:
    """{n: mean split-half correlation of break rates on n votes}, from n = 1
    up to the first n that reaches `stop_at` (or fewer than MIN_MEMBERS
    members have 2n votes)."""
    rng = random.Random(seed)
    curve: dict[int, float] = {}
    n = 1
    while True:
        members = [v for v in records.values() if len(v) >= 2 * n]
        if len(members) < MIN_MEMBERS:
            return curve
        rs = []
        for _ in range(draws):
            a, b = [], []
            for votes in members:
                s = rng.sample(votes, 2 * n)
                a.append(sum(s[:n]) / n)
                b.append(sum(s[n:]) / n)
            if statistics.pstdev(a) and statistics.pstdev(b):
                rs.append(statistics.correlation(a, b))
        curve[n] = round(statistics.mean(rs), 4) if rs else 0.0
        if curve[n] >= stop_at:
            return curve
        n += 1


def first_reaching(curve: dict[int, float], bar: float) -> int | None:
    return next((n for n, r in sorted(curve.items()) if r >= bar), None)


def _roll_calls(db, chamber: str, congress: int) -> list[dict]:
    rows = (db.query(RollCall).filter(RollCall.chamber == chamber, RollCall.congress == congress)
            .order_by(RollCall.date, RollCall.session, RollCall.number).all())
    positions = defaultdict(list)
    ids = [r.id for r in rows]
    for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_(ids)).all():
        member = p.member_id or f"{p.last_name}|{p.state}"
        positions[p.roll_call_id].append((member, p.party, p.position))
    return [{"id": r.id, "chamber": chamber, "question": r.question, "bill_id": r.bill_id,
             "positions": positions[r.id]} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--congress", type=int, default=None, help="default: the newest stored")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        congress = args.congress or db.query(RollCall.congress).order_by(RollCall.congress.desc()).limit(1).scalar()
        chambers = {}
        for chamber in ("senate", "house"):
            records = break_records(_roll_calls(db, chamber, congress))
            curve = reliability_curve(records)
            partial, full = first_reaching(curve, PARTIAL_RELIABILITY), first_reaching(curve, FULL_RELIABILITY)
            if partial is None or full is None:
                raise SystemExit(f"{chamber}: reliability never reached {FULL_RELIABILITY} on the {congress}th "
                                 f"Congress's records ({len(records)} members); keeping the file in use")
            chambers[chamber] = {"partial": partial, "full": full, "members": len(records),
                                 "curve": {str(n): r for n, r in curve.items()}}
            print(chamber, partial, full, flush=True)
    finally:
        db.close()
    args.out.write_text(json.dumps({
        "_as_of": datetime.date.today().isoformat(),
        "_source": (f"backend/scripts/calibrate_confidence_grades.py over the {congress}th Congress's stored roll "
                    f"calls and positions: split-half reliability of members' party-line break rates by record "
                    f"length, {DRAWS} random splits per length (seed {SEED})."),
        "_rule": (f"Partial from the smallest record whose split-half reliability reaches {PARTIAL_RELIABILITY}, Full "
                  f"from {FULL_RELIABILITY} (Nunnally & Bernstein 1994)."),
        "congress": congress,
        "reliability": {"partial": PARTIAL_RELIABILITY, "full": FULL_RELIABILITY},
        "chambers": chambers,
    }, indent=1) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
