"""Calibrate how much a congress-specific roll-call position can be trusted.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_reliability(chamber): n0 in the weight n / (n + n0) that
Constituent Alignment's position-congruence component gives a member whose
Nokken-Poole position rests on n scaled roll calls (v6.27).

Measured on Voteview's own positions, with no model of how Voteview
estimates them. A member who served part of one Congress and all of the
next (or all of one and part of the next) has a thin position and a full
one for adjacent Congresses. Read each against their party's center that
Congress, signed toward the party's flank:

    full = drift * n / (n + n0) * thin + error

Drift is how far anyone's position carries from one Congress to the next.
Pairs of full records (both sides at least RELIABLE_VOTES) measure drift
times the weight of a full record, and pairs with a thin side add the
weight of thin ones, so one least-squares fit identifies both.

n0 is not one number across eras. The more a Congress votes on party lines,
the less each vote says about where a member sits within their party, so
log n0 is fitted as a line in the thin Congress's party-line share (the
share of roll calls on which the two parties' majorities voted opposite
ways), and each chamber's n0 is read at the sitting Congress's share,
clamped to the range the thin pairs cover (their middle 90%) rather than
extrapolated. A position
published with no count is the thinnest record there is; the pairs give it
no measurable weight in recent Congresses (UNCOUNTED_FROM onward), so the
score reads it as 0 votes. Voteview's 0, 0 placeholders are no position and
are left out. Every estimate comes with a bootstrap interval over members.

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
CONGRESSES = range(101, 120)  # pairs (c, c + 1); the sitting Congress's records are the newest side
RELIABLE_VOTES = 200  # a full record; the pairs measure the weight below it
CENTER = 0.6  # log n0 = a + b * (party-line share - CENTER): centers the fit, changes nothing else
A_GRID = np.arange(2.0, 6.0, 0.02)
B_GRID = np.arange(-6.0, 12.0, 0.1)
UNCOUNTED_FROM = 110  # the recent Congresses the no-count weight is reported on
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


def deviations(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """icpsr -> (scaled votes, 0 when none reported; position from the
    party's center signed toward its flank) for each major-party member
    with a position, one row each (a member listed twice, after a party
    switch, is left out). The center is the median of the party's full
    records."""
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
        members.append((icpsr, party, x, _float(r.get("nominate_number_of_votes")) or 0.0))
    center = {
        p: statistics.median([x for _, q, x, n in members if q == p and n >= RELIABLE_VOTES] or [0.0])
        for p in (100.0, 200.0)
    }
    return {i: (n, (x - center[p]) * (-1.0 if p == 100.0 else 1.0)) for i, p, x, n in members}


def pairs(cache: pathlib.Path | None = None) -> list[tuple]:
    """(chamber, icpsr, n, thin-or-earlier deviation, full deviation, kind,
    congress, party-line share) with kind "full" (both sides full records:
    n and the Congress are the earlier side's), "thin" (one side counted
    under RELIABLE_VOTES: n and the Congress are that side's) or
    "uncounted" (one side with no count)."""
    out = []
    for chamber in ("S", "H"):
        share = {c: party_line_share(chamber, c, cache) for c in CONGRESSES}
        prev = deviations(member_rows(chamber, CONGRESSES.start, cache))
        for congress in CONGRESSES[1:]:
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
    return out


def fit(data: list[tuple]) -> dict:
    """Least squares of full = drift * n / (n + n0) * thin over counted
    pairs, with log n0 = a + b * (party-line share - CENTER) (a and b on
    their grids, drift closed-form for each); then, on that drift, the
    weight of positions with no count in Congresses UNCOUNTED_FROM on."""
    counted = [(n, x, y, u) for _, _, n, x, y, kind, _, u in data if kind != "uncounted"]
    n, x, y, u = (np.array(v) for v in zip(*counted))
    best = None
    for b in B_GRID:
        for a in A_GRID:
            z = n / (n + np.exp(a + b * (u - CENTER))) * x
            drift = float((z * y).sum() / (z * z).sum())
            loss = float(((y - drift * z) ** 2).sum())
            if best is None or loss < best[0]:
                best = (loss, float(a), float(b), drift)
    _, a, b, drift = best
    unc = [(x, y) for _, _, _, x, y, kind, c, _ in data if kind == "uncounted" and c >= UNCOUNTED_FROM]
    uncounted = (sum(x * y for x, y in unc) / sum(x * x for x, _ in unc) / drift) if unc else 0.0
    return {"a": a, "b": b, "drift": drift, "uncounted_weight": uncounted}


def half_weight(model: dict, share: float, lo: float, hi: float) -> float:
    """n0 at a party-line share, clamped to [lo, hi], the shares the pairs cover."""
    return float(np.exp(model["a"] + model["b"] * (min(max(share, lo), hi) - CENTER)))


def bootstrap(data: list[tuple], shares: dict[str, float], lo: float, hi: float, seed: int = 0) -> dict:
    """5th and 95th percentiles, resampling members: the model's slope, the
    drift, the recent no-count weight and each chamber's n0."""
    rng = np.random.default_rng(seed)
    by_member: dict[str, list[tuple]] = {}
    for row in data:
        by_member.setdefault(row[1], []).append(row)
    ids = list(by_member)
    draws = []
    for _ in range(BOOTSTRAP):
        m = fit([row for i in rng.choice(ids, len(ids)) for row in by_member[i]])
        draws.append({"b": m["b"], "drift": m["drift"], "uncounted_weight": m["uncounted_weight"],
                      **{c: half_weight(m, u, lo, hi) for c, u in shares.items()}})
    return {k: [round(float(np.percentile([d[k] for d in draws], q)), 3) for q in (5, 95)] for k in draws[0]}


def calibrate(cache: pathlib.Path | None = None) -> dict:
    data = pairs(cache)
    model = fit(data)
    # The shares the thin pairs cover: their middle 90%, so a few pairs
    # at an extreme Congress don't license reading the line out there.
    covered = [u for *_, kind, _, u in data if kind == "thin"]
    lo, hi = (float(np.percentile(covered, q)) for q in (5, 95))
    sitting = CONGRESSES.stop - 1
    shares = {name: party_line_share(letter, sitting, cache) for name, letter in (("senate", "S"), ("house", "H"))}
    interval = bootstrap(data, shares, lo, hi)
    return {
        "congress": sitting,
        "chambers": {
            name: {
                "party_line_share": round(u, 3),
                "read_at": round(min(max(u, lo), hi), 3),
                "half_weight_votes": round(half_weight(model, u, lo, hi), 1),
                "interval_90": interval[name],
            }
            for name, u in shares.items()
        },
        "model": {
            "log_n0": f"{model['a']:.2f} + {model['b']:.2f} * (party-line share - {CENTER})",
            "a": round(model["a"], 3), "b": round(model["b"], 3), "b_interval_90": interval["b"],
            "drift": round(model["drift"], 4), "drift_interval_90": interval["drift"],
            "shares_covered": [round(lo, 3), round(hi, 3)],
        },
        "uncounted_weight_recent": round(model["uncounted_weight"], 3),
        "uncounted_weight_interval_90": interval["uncounted_weight"],
        "pairs": {kind: sum(1 for row in data if row[5] == kind) for kind in ("full", "thin", "uncounted")},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args()
    data = {
        "_source": (
            f"Voteview (voteview.com, Lewis et al.) member exports, Senate and House, Congresses "
            f"{CONGRESSES.start}-{CONGRESSES.stop - 1}, retrieved {datetime.date.today().isoformat()}; "
            "regenerate with backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Members with positions in adjacent Congresses, each read from the party's center toward "
            "its flank: least squares of full = drift * n / (n + n0) * thin over pairs with a thin "
            f"side (under {RELIABLE_VOTES} scaled votes) and pairs of full records, both chambers, with "
            "log n0 linear in the thin Congress's party-line share; each chamber's half_weight_votes is "
            "n0 at the sitting Congress's share, clamped to the middle 90% of the thin pairs' shares; "
            "uncounted_weight_recent is the weight of positions with no count since the "
            f"{UNCOUNTED_FROM}th Congress, which the score reads as 0 votes; intervals are 5th-95th "
            "percentiles over members resampled"
        ),
        **calibrate(args.cache),
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: " + "; ".join(
        f"{c} n0 {v['half_weight_votes']} {v['interval_90']} at share {v['party_line_share']}"
        for c, v in data["chambers"].items()) + f"; model {data['model']['log_n0']}")


if __name__ == "__main__":
    main()
