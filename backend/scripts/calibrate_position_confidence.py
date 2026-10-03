"""Calibrate how much a congress-specific roll-call position can be trusted.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_reliability(): the weight Constituent Alignment's
position-congruence component gives a member whose Nokken-Poole position
rests on n scaled roll calls (v6.27),

    weight(n) = min(1, w(n) / w(RELIABLE_VOTES)),   w(n) = n / (n + n0),

so a full record (RELIABLE_VOTES or more) counts in full and a thin one
counts by how much less it says.

Measured on Voteview's own positions, with no model of how Voteview
estimates them. A member who served part of one Congress and all of the
next (or all of one and part of the next) has a thin position and a full
one for adjacent Congresses. Read each against their party's center that
Congress, signed toward the party's flank, every pair keyed by the
transition it spans (its earlier Congress):

    full = drift[chamber, transition] * weight(n) * thin + error

Drift is how far positions carry from one Congress to the next, and it
varies by era (a full record's slope on the next Congress's runs from about
0.66 to 1.00), so each chamber and transition gets its own, set by that
transition's pairs of full records alone, which show no gradient in their
count (full records count 1). n0 is then the least-squares fit of the thin
pairs. The curve's shape is weakly identified; the count at which a
position counts half (half_weight_votes) is the stable summary, reported
with its interval. Positions Voteview publishes with no count get their own
measured weight, on the same drift: those with a career DW-NOMINATE
position, as the score applies it (a member with neither a count nor a
career, such as one just sworn in, reads as no votes).

Left out: Voteview's 0, 0 placeholders (no position); members from outside
the 50 states (the House's delegates, whose records are thin because they
vote only in the Committee of the Whole, not because they served part of a
Congress), the states being those of the pipeline's House district table;
transitions with no full pairs; and a Congress still thin by the calendar
(its median member under RELIABLE_VOTES, as early in a sitting Congress),
whose short records reflect the date, not partial service.

A party-line term was tested and is reported, not used: with one drift for
every Congress, n0 appeared to rise with the share of roll calls on which
the parties' majorities split, but with drift measured per transition the
dependence vanishes (research note section 14). So the weight is one curve,
and nothing in it follows the sitting Congress: a rerun only adds pairs.

Run from the repo (network required; the vote files are large, so pass a
cache directory to keep them):
    python3 backend/scripts/calibrate_position_confidence.py [--cache DIR] [--out FILE]
"""

import argparse
import csv
import datetime
import io
import json
import pathlib
import statistics
import sys
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402

MEMBERS_URL = "https://voteview.com/static/data/out/members/{chamber}{congress}_members.csv"
VOTES_URL = "https://voteview.com/static/data/out/votes/{chamber}{congress}_votes.csv"
FIRST_CONGRESS = 101  # the first with Nokken-Poole positions and counts throughout
RELIABLE_VOTES = 200  # a full record; the pairs measure the weight below it
N0_GRID = np.concatenate([np.arange(1.0, 400.0, 1.0), np.arange(400.0, 5001.0, 25.0)])
CENTER = 0.6  # the party-line test's log n0 = a + b * (share - CENTER)
A_GRID = np.arange(2.0, 6.0, 0.05)
B_GRID = np.arange(-6.0, 10.0, 0.25)
BOOTSTRAP = 300
YEA, NAY = (1, 2, 3), (4, 5, 6)
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "position_confidence.json"


def _csv(url: str, name: str, cache: pathlib.Path | None) -> list[dict]:
    if cache is not None and (cache / name).exists():
        text = (cache / name).read_text()
    else:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})) as resp:
            text = resp.read().decode()
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_text(text)
    return list(csv.DictReader(io.StringIO(text)))


def member_rows(chamber: str, congress: int, cache: pathlib.Path | None = None) -> list[dict]:
    rows = _csv(MEMBERS_URL.format(chamber=chamber, congress=congress), f"{chamber}{congress}_members.csv", cache)
    return [r for r in rows if r.get("chamber") != "President"]


def party_line_share(chamber: str, congress: int, cache: pathlib.Path | None = None) -> float:
    """Share of a Congress's roll calls on which the two parties' majorities
    voted opposite ways (Yea against Nay), over major-party members' yea and
    nay votes."""
    party = {}
    for r in member_rows(chamber, congress, cache):
        code = _float(r.get("party_code"))
        if code in (100.0, 200.0):
            party.setdefault(_id(r["icpsr"]), code)
    tally: dict[str, dict[float, list[int]]] = {}
    for r in _csv(VOTES_URL.format(chamber=chamber, congress=congress), f"{chamber}{congress}_votes.csv", cache):
        p, code = party.get(_id(r["icpsr"])), int(_float(r.get("cast_code")) or 0)
        if p is None or code not in YEA + NAY:
            continue
        t = tally.setdefault(r["rollnumber"], {100.0: [0, 0], 200.0: [0, 0]})[p]
        t[0 if code in YEA else 1] += 1
    opposed = [(d[0] > d[1]) != (r[0] > r[1]) for d, r in (v.values() for v in tally.values())
               if sum(d) and sum(r)]
    return sum(opposed) / len(opposed)


def _float(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN is no value


def _id(value) -> str:
    """An ICPSR id as one spelling: several exports write "14009.0"."""
    x = _float(value)
    return str(int(x)) if x is not None else str(value)


def is_placeholder(row: dict) -> bool:
    """Voteview writes 0, 0 for a member it could not scale: no estimate."""
    return _float(row.get("nokken_poole_dim1")) == 0 and _float(row.get("nokken_poole_dim2")) == 0


def _states() -> set[str]:
    """The 50 states: those of the pipeline's House district table (the
    state lean table also lists DC, whose delegate doesn't vote)."""
    from app.pipeline.analyze.score_calculator import _district_pvi

    return {key.split("-")[0] for key in _district_pvi()}


def deviations(rows: list[dict]) -> dict[str, tuple[float, float, bool]]:
    """icpsr -> (scaled votes, 0 when none or 0 reported; position from the
    party's center signed toward its flank; whether Voteview has a career
    DW-NOMINATE position for the member) for each major-party member of a
    state with a position, one row each (a member listed twice, after a
    party switch, is left out). The center is the median of the party's
    full records."""
    states = _states()
    seen: dict[str, list[dict]] = {}
    for r in rows:
        seen.setdefault(_id(r["icpsr"]), []).append(r)
    members = []
    for icpsr, rs in seen.items():
        r = rs[0]
        party = _float(r.get("party_code"))
        x = _float(r.get("nokken_poole_dim1"))
        if len(rs) != 1 or party not in (100.0, 200.0) or x is None or is_placeholder(r):
            continue
        if (r.get("state_abbrev") or "").strip().upper() not in states:
            continue
        career = _float(r.get("nominate_dim1")) is not None
        members.append((icpsr, party, x, _float(r.get("nominate_number_of_votes")) or 0.0, career))
    center = {
        p: statistics.median([x for _, q, x, n, _ in members if q == p and n >= RELIABLE_VOTES] or [0.0])
        for p in (100.0, 200.0)
    }
    return {i: (n, (x - center[p]) * (-1.0 if p == 100.0 else 1.0), career) for i, p, x, n, career in members}


def congresses() -> range:
    """FIRST_CONGRESS through the sitting one (settings.CURRENT_CONGRESS,
    which follows the clock), so a rerun in any Congress includes it."""
    from app.config import settings

    return range(FIRST_CONGRESS, settings.CURRENT_CONGRESS + 1)


def _usable(chamber: str, congress: int, cache: pathlib.Path | None) -> bool:
    """A Congress whose export exists and isn't thin by the calendar."""
    try:
        rows = member_rows(chamber, congress, cache)
    except OSError:
        return False
    counts = [_float(r.get("nominate_number_of_votes")) or 0.0 for r in rows]
    return bool(counts) and statistics.median(counts) >= RELIABLE_VOTES


def pairs(cache: pathlib.Path | None = None, span: range | None = None) -> tuple[list[tuple], dict]:
    """(chamber, icpsr, n, thin-or-earlier deviation, full deviation, kind,
    transition, party-line share, thin side is the later) with kind "full"
    (both sides full records: n is the earlier side's), "thin" (one side
    counted under RELIABLE_VOTES: n is that side's) or "uncounted" (one
    side with no count but a career position, the case the score weights
    by uncounted_weight), every pair keyed by the transition it spans (its
    earlier Congress) and that Congress's party-line share; and each
    chamber's party-line share by usable Congress."""
    span = span or congresses()
    out, shares = [], {}
    for chamber in ("S", "H"):
        usable = [c for c in span if _usable(chamber, c, cache)]
        share = shares[chamber] = {c: party_line_share(chamber, c, cache) for c in usable}
        for earlier, later in zip(usable, usable[1:]):
            if later != earlier + 1:
                continue
            prev = deviations(member_rows(chamber, earlier, cache))
            cur = deviations(member_rows(chamber, later, cache))
            for icpsr in prev.keys() & cur.keys():
                (na, xa, ca), (nb, xb, cb) = prev[icpsr], cur[icpsr]
                if na >= RELIABLE_VOTES and nb >= RELIABLE_VOTES:
                    out.append((chamber, icpsr, na, xa, xb, "full", earlier, share[earlier], False))
                elif nb >= RELIABLE_VOTES or na >= RELIABLE_VOTES:
                    later_thin = na >= RELIABLE_VOTES
                    n, thin, full, career = (nb, xb, xa, cb) if later_thin else (na, xa, xb, ca)
                    if n > 0:
                        kind = "thin"
                    elif career:
                        kind = "uncounted"  # the score's no-count case: a career position, no count
                    else:
                        continue  # no count and no career position: the score reads it as no votes
                    out.append((chamber, icpsr, n, thin, full, kind, earlier, share[earlier], later_thin))
    return out, shares


def relative_weight(n, n0):
    """min(1, w(n) / w(RELIABLE_VOTES)), w(n) = n / (n + n0): the score's
    weight (score_calculator.position_confidence)."""
    ref = RELIABLE_VOTES / (RELIABLE_VOTES + n0)
    return np.minimum(np.asarray(n, float) / (np.asarray(n, float) + n0) / ref, 1.0)


def half_point(n0: float) -> float:
    """The vote count at which relative_weight is one half."""
    r = RELIABLE_VOTES / (RELIABLE_VOTES + n0)
    return 0.5 * r * n0 / (1 - 0.5 * r)


def drifts(data: list[tuple]) -> dict[tuple, tuple[float, float]]:
    """Each (chamber, transition)'s drift from its full pairs, both ways:
    the slope of the later position on the earlier (for a member whose
    thin record is the earlier) and of the earlier on the later (a member
    whose thin record is the later: a departing member)."""
    acc: dict[tuple, list[float]] = {}
    for r in data:
        if r[5] == "full":
            a = acc.setdefault((r[0], r[6]), [0.0, 0.0, 0.0])
            a[0] += r[3] * r[4]
            a[1] += r[3] * r[3]
            a[2] += r[4] * r[4]
    return {k: (xy / xx, xy / yy) for k, (xy, xx, yy) in acc.items() if xx and yy}


def _thin(data: list[tuple], kind: str = "thin") -> tuple[np.ndarray, ...]:
    """(n, drift * thin, full) for pairs of `kind` in transitions with a
    drift, each with the drift in its own direction."""
    d = drifts(data)
    rows = [r for r in data if r[5] == kind and (r[0], r[6]) in d]
    if not rows:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    return (np.array([r[2] for r in rows]),
            np.array([d[(r[0], r[6])][1 if r[8] else 0] * r[3] for r in rows]),
            np.array([r[4] for r in rows]))


def _loss(n, dx, y, n0) -> float:
    return float(((y - relative_weight(n, n0) * dx) ** 2).sum())


def fit_n0(data: list[tuple]) -> float:
    """The least-squares n0 on N0_GRID over the thin pairs."""
    n, dx, y = _thin(data)
    return float(min(N0_GRID, key=lambda g: _loss(n, dx, y, g)))


def party_line_test(data: list[tuple]) -> dict:
    """The rejected alternative: log n0 = a + b * (party-line share - CENTER),
    on the same drift. Its b, and both fits' squared error over the thin pairs."""
    d = drifts(data)
    rows = [r for r in data if r[5] == "thin" and (r[0], r[6]) in d]
    n, dx, y = _thin(data)
    u = np.array([r[7] for r in rows])
    best = min((_loss(n, dx, y, np.exp(a + b * (u - CENTER))), a, b) for b in B_GRID for a in A_GRID)
    return {"b": round(float(best[2]), 2), "loss_with": round(best[0], 4),
            "loss_without": round(_loss(n, dx, y, fit_n0(data)), 4)}


def uncounted_weight(data: list[tuple]) -> float:
    """Slope of full on drift * thin for positions published with no count."""
    _, dx, y = _thin(data, "uncounted")
    return float((dx * y).sum() / (dx * dx).sum()) if len(dx) else 0.0


def bootstrap(data: list[tuple], seed: int = 0) -> dict[str, list[float]]:
    """5th and 95th percentiles of n0, the half point and the no-count
    weight, resampling members."""
    rng = np.random.default_rng(seed)
    by_member: dict[str, list[tuple]] = {}
    for row in data:
        by_member.setdefault(row[1], []).append(row)
    ids = list(by_member)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [row for i in rng.choice(ids, len(ids)) for row in by_member[i]]
        n0 = fit_n0(sample)
        draws.append({"n0": n0, "half_weight_votes": half_point(n0), "uncounted_weight": uncounted_weight(sample)})
    return {k: [round(float(np.percentile([d[k] for d in draws], q)), 3) for q in (5, 95)] for k in draws[0]}


def calibrate(cache: pathlib.Path | None = None) -> dict:
    span = congresses()
    data, shares = pairs(cache, span)
    n0 = fit_n0(data)
    d = drifts(data)
    used = [r for r in data if r[5] != "full" and (r[0], r[6]) in d]
    by_chamber = {name: [r for r in data if r[0] == letter] for name, letter in (("senate", "S"), ("house", "H"))}
    last = max(r[6] for r in data) + 1
    return {
        "calibrated_through": last,
        "n0": n0,
        "reference_votes": RELIABLE_VOTES,
        "half_weight_votes": round(half_point(n0), 1),
        "uncounted_weight": round(uncounted_weight(data), 3),
        "interval_90": bootstrap(data),
        "by_chamber": {
            name: {"half_weight_votes": round(half_point(fit_n0(rows)), 1),
                   "thin_pairs": sum(1 for r in rows if r[5] == "thin" and (r[0], r[6]) in d)}
            for name, rows in by_chamber.items()
        },
        "half_weight_votes_by_era": {
            f"{e.start}-{e.stop - 1}": round(half_point(fit_n0([r for r in data if r[6] in e])), 1)
            for e in (range(FIRST_CONGRESS, 110), range(110, last + 1))
        },
        "party_line_test": party_line_test(data),
        "party_line_share_by_congress": {
            name: {str(c): round(u, 3) for c, u in shares[letter].items()}
            for name, letter in (("senate", "S"), ("house", "H"))
        },
        "pairs": {
            "full": sum(1 for r in data if r[5] == "full"),
            "thin": sum(1 for r in used if r[5] == "thin"),
            "uncounted": sum(1 for r in used if r[5] == "uncounted"),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args()
    data = {
        "_source": (
            f"Voteview (voteview.com, Lewis et al.) member and vote exports, Senate and House, Congresses "
            f"{congresses().start}-{congresses().stop - 1}, retrieved {datetime.date.today().isoformat()}; "
            "regenerate with backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Members of the 50 states with positions in adjacent Congresses, each read from the "
            "party's center toward its flank, keyed by the transition they span: full = "
            "drift[chamber, transition] * weight(n) * thin, weight(n) = min(1, w(n) / "
            f"w({RELIABLE_VOTES})), w(n) = n / (n + n0); drift from each transition's pairs of full "
            f"records (both sides {RELIABLE_VOTES} or more scaled votes) in the pair's direction, n0 "
            "the least-squares fit of the thin pairs on a grid to 5000, one for both chambers and "
            "every Congress; half_weight_votes is where weight(n) = 0.5, never above reference_votes / 2 (as n0 grows "
            "the curve tends to n / reference_votes), so an interval reaching that limit is open above; "
            "uncounted_weight is the "
            "slope for positions published with no count (or 0) but a career DW-NOMINATE position; "
            "party_line_test is the rejected alternative with log n0 linear in the Congress's "
            "party-line share; transitions with no full pairs and Congresses still thin by the "
            "calendar are left out; interval_90 is the 5th-95th percentile over members resampled"
        ),
        **calibrate(args.cache),
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: n0 {data['n0']}, half weight at {data['half_weight_votes']} votes "
          f"{data['interval_90']['half_weight_votes']}, no count {data['uncounted_weight']}, by chamber "
          f"{data['by_chamber']}, party-line test {data['party_line_test']}")


if __name__ == "__main__":
    main()
