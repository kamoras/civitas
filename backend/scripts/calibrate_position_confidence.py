"""Calibrate how much a congress-specific roll-call position can be trusted.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_reliability(): n0 in the weight n / (n + n0) that Constituent
Alignment's position-congruence component gives a member whose Nokken-Poole
position rests on n scaled roll calls (v6.27).

Measured on Voteview's own positions, with no model of how Voteview
estimates them. A member who served part of one Congress and all of the
next (or all of one and part of the next) has a thin position and a full
one for adjacent Congresses. Read each against their party's center that
Congress, signed toward the party's flank:

    full = drift[chamber, Congress] * n / (n + n0) * thin + error

Drift is how far positions carry from one Congress to the next, and it
varies by era (a full record's slope on the next Congress's runs from about
0.66 to 1.00), so each chamber and Congress gets its own, measured by that
Congress's pairs of full records (both sides at least RELIABLE_VOTES). n0
is then identified by how much more a thin record is attenuated than a
full one in the same Congress. Because the drift absorbs a full record's own
attenuation, what the pairs measure is a thin record's weight relative to a
full one: the score divides n / (n + n0) by its value at a typical full
record (full_record_votes) and caps at 1, so a full record counts in full.
Positions Voteview publishes with no count get their own measured weight,
on the same per-Congress drift.

Two things are left out: Voteview's 0, 0 placeholders (no position), and
members from outside the 50 states (the House's delegates, whose records
are thin because they vote only in the Committee of the Whole, not because
they served part of a Congress); the 50 states are read from the
pipeline's own state lean table.

A party-line term was tested and is reported, not used: with one drift for
every Congress, n0 appeared to rise with the share of roll calls on which
the parties' majorities split, but with drift measured per Congress the
dependence vanishes (research note section 14). So n0 is one number, and
nothing in it follows the sitting Congress: a rerun only adds new pairs.

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
N0_GRID = np.arange(1.0, 300.0, 1.0)
CENTER = 0.6  # the party-line test's log n0 = a + b * (share - CENTER)
A_GRID = np.arange(2.0, 6.0, 0.05)
B_GRID = np.arange(-6.0, 10.0, 0.25)
BOOTSTRAP = 200
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
    """The 50 states, from the pipeline's own state lean table."""
    from app.pipeline.analyze.score_calculator import _state_pvi

    return set(_state_pvi())


def deviations(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """icpsr -> (scaled votes, 0 when none reported; position from the
    party's center signed toward its flank) for each major-party member
    with a position, one row each (a member listed twice, after a party
    switch, is left out). The center is the median of the party's full
    records."""
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
        members.append((icpsr, party, x, _float(r.get("nominate_number_of_votes")) or 0.0))
    center = {
        p: statistics.median([x for _, q, x, n in members if q == p and n >= RELIABLE_VOTES] or [0.0])
        for p in (100.0, 200.0)
    }
    return {i: (n, (x - center[p]) * (-1.0 if p == 100.0 else 1.0)) for i, p, x, n in members}


def congresses() -> range:
    """FIRST_CONGRESS through the sitting one (settings.CURRENT_CONGRESS,
    which follows the clock), so a rerun in any Congress includes it."""
    from app.config import settings

    return range(FIRST_CONGRESS, settings.CURRENT_CONGRESS + 1)


def pairs(cache: pathlib.Path | None = None, span: range | None = None) -> tuple[list[tuple], dict]:
    """(chamber, icpsr, n, thin-or-earlier deviation, full deviation, kind,
    congress, party-line share) with kind "full" (both sides full records:
    n and the Congress are the earlier side's), "thin" (one side counted
    under RELIABLE_VOTES: n and the Congress are that side's) or
    "uncounted" (one side with no count); and each chamber's party-line
    share by Congress."""
    span = span or congresses()
    out, shares = [], {}
    for chamber in ("S", "H"):
        share = shares[chamber] = {c: party_line_share(chamber, c, cache) for c in span}
        prev = deviations(member_rows(chamber, span.start, cache))
        for congress in span[1:]:
            cur = deviations(member_rows(chamber, congress, cache))
            for icpsr in prev.keys() & cur.keys():
                (na, xa), (nb, xb) = prev[icpsr], cur[icpsr]
                if na >= RELIABLE_VOTES and nb >= RELIABLE_VOTES:
                    out.append((chamber, icpsr, na, xa, xb, "full", congress - 1, share[congress - 1]))
                elif nb >= RELIABLE_VOTES or na >= RELIABLE_VOTES:
                    if nb >= RELIABLE_VOTES:
                        n, thin, full, c = na, xa, xb, congress - 1
                    else:
                        n, thin, full, c = nb, xb, xa, congress
                    out.append((chamber, icpsr, n, thin, full, "thin" if n > 0 else "uncounted", c, share[c]))
            prev = cur
    return out, shares


def _loss(rows: list[tuple], n0) -> float:
    """Squared error of full = drift * n / (n + n0) * thin, with the drift of
    each chamber and Congress closed-form over its pairs. n0 is a number, or
    one per row."""
    n = np.array([r[2] for r in rows])
    z = n / (n + n0) * np.array([r[3] for r in rows])
    y = np.array([r[4] for r in rows])
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r[0], r[6]), []).append(i)
    total = 0.0
    for idx in groups.values():
        zz, yy = z[idx], y[idx]
        drift = (zz * yy).sum() / (zz * zz).sum()
        total += float(((yy - drift * zz) ** 2).sum())
    return total


def _counted(data: list[tuple]) -> list[tuple]:
    return [r for r in data if r[5] != "uncounted"]


def fit_n0(data: list[tuple]) -> float:
    """The least-squares n0 on N0_GRID, drift per chamber and Congress."""
    rows = _counted(data)
    return float(min(N0_GRID, key=lambda g: _loss(rows, g)))


def party_line_test(data: list[tuple]) -> dict:
    """The rejected alternative: log n0 = a + b * (party-line share - CENTER),
    with the same per-Congress drift. Its b, and both fits' squared error."""
    rows = _counted(data)
    u = np.array([r[7] for r in rows])
    best = min(((_loss(rows, np.exp(a + b * (u - CENTER))), a, b) for b in B_GRID for a in A_GRID))
    return {"b": round(float(best[2]), 2), "loss_with": round(best[0], 4),
            "loss_without": round(_loss(rows, fit_n0(data)), 4)}


def uncounted_weight(data: list[tuple]) -> float:
    """Slope of full on thin for positions published with no count, over
    the drift of their chamber and Congress (from the full pairs)."""
    full = [r for r in data if r[5] == "full"]
    drift = {}
    for key in {(r[0], r[6]) for r in full}:
        xs = np.array([(r[3], r[4]) for r in full if (r[0], r[6]) == key])
        drift[key] = (xs[:, 0] * xs[:, 1]).sum() / (xs[:, 0] ** 2).sum()
    unc = [r for r in data if r[5] == "uncounted" and (r[0], r[6]) in drift]
    num = sum(r[3] * r[4] for r in unc)
    den = sum(drift[(r[0], r[6])] * r[3] ** 2 for r in unc)
    return float(num / den) if den else 0.0


def bootstrap(data: list[tuple], seed: int = 0) -> dict[str, list[float]]:
    """5th and 95th percentiles of n0 and the no-count weight, resampling
    members."""
    rng = np.random.default_rng(seed)
    by_member: dict[str, list[tuple]] = {}
    for row in data:
        by_member.setdefault(row[1], []).append(row)
    ids = list(by_member)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [row for i in rng.choice(ids, len(ids)) for row in by_member[i]]
        draws.append({"half_weight_votes": fit_n0(sample), "uncounted_weight": uncounted_weight(sample)})
    return {k: [round(float(np.percentile([d[k] for d in draws], q)), 3) for q in (5, 95)] for k in draws[0]}


def calibrate(cache: pathlib.Path | None = None) -> dict:
    span = congresses()
    data, shares = pairs(cache, span)
    by_chamber = {name: [r for r in data if r[0] == letter] for name, letter in (("senate", "S"), ("house", "H"))}
    return {
        "calibrated_through": span.stop - 1,
        "half_weight_votes": fit_n0(data),
        "interval_90": bootstrap(data),
        "by_chamber": {
            name: {"half_weight_votes": fit_n0(rows), "thin_pairs": sum(1 for r in rows if r[5] == "thin")}
            for name, rows in by_chamber.items()
        },
        "one_n0_by_era": {
            f"{e.start}-{e.stop - 1}": fit_n0([r for r in data if r[6] in e])
            for e in (range(FIRST_CONGRESS, 110), range(110, span.stop))
        },
        "party_line_test": party_line_test(data),
        "party_line_share_by_congress": {
            name: {str(c): round(u, 3) for c, u in shares[letter].items()}
            for name, letter in (("senate", "S"), ("house", "H"))
        },
        # The drift absorbs a full record's own attenuation, so the pairs
        # identify a thin record's weight relative to a full one: the score
        # divides by the weight of this typical full record (the median count
        # of the full pairs' earlier side) and caps at 1.
        "full_record_votes": int(np.median([r[2] for r in data if r[5] == "full"])),
        "uncounted_weight": round(uncounted_weight(data), 3),
        "pairs": {kind: sum(1 for row in data if row[5] == kind) for kind in ("full", "thin", "uncounted")},
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
            "party's center toward its flank: least squares of full = drift[chamber, Congress] * "
            f"n / (n + n0) * thin over pairs with a thin side (under {RELIABLE_VOTES} scaled votes) "
            "and pairs of full records, one n0 for both chambers and every Congress; the score weighs "
            "a position by min(1, w(n) / w(full_record_votes)), w(n) = n / (n + n0), since the drift "
            "absorbs a full record's own attenuation; party_line_test is the rejected alternative "
            "with log n0 linear in the Congress's party-line share; uncounted_weight is the same slope "
            "for positions published with no count, on the full pairs' drift; interval_90 is the "
            "5th-95th percentile over members resampled"
        ),
        **calibrate(args.cache),
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: half_weight_votes {data['half_weight_votes']} "
          f"{data['interval_90']['half_weight_votes']}, by chamber {data['by_chamber']}, "
          f"party-line test {data['party_line_test']}")


if __name__ == "__main__":
    main()
