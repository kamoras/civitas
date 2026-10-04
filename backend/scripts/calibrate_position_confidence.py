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
pairs, one for both chambers unless one per chamber predicts held-out
members (leave one member out) better by more than a standard error of
the difference (choose_structure: the one-standard-error rule), so the
data, not a choice, decide whether the chambers differ; a rerun decides
again. n0 is weakly determined, and so is the count at which a position
counts half (half_weight_votes, a reparametrisation of it, bounded above
at RELIABLE_VOTES / 2), reported with its interval. Positions Voteview
publishes with no count get their own
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
dependence is weak and predicts held-out members worse than no term
(research note section 14). So the weight is at most one curve per
chamber (one for both, as calibrated now), and nothing in it follows the
sitting Congress: a rerun only adds pairs. Era and the pair's direction (a member who arrived or one who left)
are tested the same way and reported, not used: an era's curve would
apply only as the latest era's, and a direction can't be known for a
sitting member's record.

prior_test is the evidence for the flank rule's use of the last
Congress's full record (party_line_record), on pairs centered as that
rule centers.

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
BOOTSTRAP = 1000
CENTER = 0.6  # the party-line test's log n0 = a + b * (share - CENTER)
A_GRID = np.arange(2.0, 6.0, 0.05)
B_GRID = np.arange(-6.0, 10.0, 0.25)
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


def deviations(rows: list[dict], weight=None) -> dict[str, tuple[float, float, bool]]:
    """icpsr -> (scaled votes, 0 when none or 0 reported; position from the
    party's center signed toward its flank; whether Voteview has a career
    DW-NOMINATE position for the member) for each major-party member of a
    state with a position, one row each (a member listed twice, after a
    party switch, is left out). The center is the median of the party's
    full records, or, with `weight` ((votes, career) -> reliability weight),
    the weighted mean of all its members, as the flank rule centers
    (party_line_record)."""
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
    if weight is None:
        center = {
            p: statistics.median([x for _, q, x, n, _ in members if q == p and n >= RELIABLE_VOTES] or [0.0])
            for p in (100.0, 200.0)
        }
    else:
        center = {}
        for p in (100.0, 200.0):
            ws = [(x, weight(n, career)) for _, q, x, n, career in members if q == p]
            total = sum(w for _, w in ws)
            center[p] = sum(x * w for x, w in ws) / total if total else 0.0
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


def pairs(cache: pathlib.Path | None = None, span: range | None = None, weight=None,
          shares: dict | None = None) -> tuple[list[tuple], dict]:
    """(chamber, icpsr, n, thin-or-earlier deviation, full deviation, kind,
    transition, party-line share, thin side is the later) with kind "full"
    (both sides full records: n is the earlier side's), "thin" (one side
    counted under RELIABLE_VOTES: n is that side's) or "uncounted" (one
    side with no count but a career position, the case the score weights
    by uncounted_weight), every pair keyed by the transition it spans (its
    earlier Congress) and that Congress's party-line share; and each
    chamber's party-line share by usable Congress (`shares`, when given, is
    reused). `weight` ({chamber: (votes, career) -> weight}) centers each
    Congress as the flank rule does (deviations)."""
    span = span or congresses()
    out, shares = [], dict(shares or {})
    for chamber in ("S", "H"):
        usable = [c for c in span if _usable(chamber, c, cache)]
        share = shares[chamber] if chamber in shares else {c: party_line_share(chamber, c, cache) for c in usable}
        shares[chamber] = share
        def seated(congress: int) -> set[str]:
            # Everyone in a Congress's export: a thin record is a run of
            # consecutive votes only for a member who arrived or left, so an
            # entrant must be absent the Congress before and a leaver the one
            # after (an absence mid-Congress, such as a campaign, isn't).
            try:
                return {_id(r["icpsr"]) for r in member_rows(chamber, congress, cache)}
            except OSError:
                return set()
        for earlier, later in zip(usable, usable[1:]):
            if later != earlier + 1:
                continue
            w = (weight or {}).get(chamber)
            prev = deviations(member_rows(chamber, earlier, cache), w)
            cur = deviations(member_rows(chamber, later, cache), w)
            before, after = seated(earlier - 1), seated(later + 1)
            for icpsr in prev.keys() & cur.keys():
                (na, xa, ca), (nb, xb, cb) = prev[icpsr], cur[icpsr]
                if na >= RELIABLE_VOTES and nb >= RELIABLE_VOTES:
                    out.append((chamber, icpsr, na, xa, xb, "full", earlier, share[earlier], False))
                elif nb >= RELIABLE_VOTES or na >= RELIABLE_VOTES:
                    later_thin = na >= RELIABLE_VOTES
                    if icpsr in (after if later_thin else before):
                        continue  # served on: the thin record isn't an arrival or a departure
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


def _thin(data: list[tuple], kind: str = "thin", drift: dict | None = None) -> tuple[np.ndarray, ...]:
    """(n, drift * thin, full) for pairs of `kind` in transitions with a
    drift, each with the drift in its own direction."""
    d = drifts(data) if drift is None else drift
    rows = [r for r in data if r[5] == kind and (r[0], r[6]) in d]
    if not rows:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    return (np.array([r[2] for r in rows]),
            np.array([d[(r[0], r[6])][1 if r[8] else 0] * r[3] for r in rows]),
            np.array([r[4] for r in rows]))


def _loss(n, dx, y, n0) -> float:
    return float(((y - relative_weight(n, n0) * dx) ** 2).sum())


def fit_n0(data: list[tuple], drift: dict | None = None) -> float:
    """The least-squares n0 on N0_GRID over the thin pairs (drift from
    `data`'s full pairs unless given)."""
    n, dx, y = _thin(data, drift=drift)
    return float(min(N0_GRID, key=lambda g: _loss(n, dx, y, g)))


CHAMBERS = (("senate", "S"), ("house", "H"))
# Candidate structures for n0: the group a pair's n0 is fitted within.
# "pooled" and "chamber" can be applied to a sitting member's record; era
# and direction are tested and reported only.
STRUCTURES = {
    "pooled": lambda r: "all",
    "chamber": lambda r: r[0],
    "era": lambda r: r[6] >= 110,
    "direction": lambda r: r[8],
}
USABLE = ("pooled", "chamber")


def heldout_errors(data: list[tuple], structure: str) -> dict[str, float]:
    """Each thin member's squared error, their pairs predicted by n0 fitted
    (in its group) and drift measured without that member."""
    group = STRUCTURES[structure]
    out: dict[str, float] = {}
    for m in sorted({r[1] for r in data if r[5] == "thin"}):
        err = 0.0
        train = [r for r in data if r[1] != m]
        d = drifts(train)
        fits: dict = {}
        for r in data:
            if r[1] != m or r[5] != "thin" or (r[0], r[6]) not in d:
                continue
            g = group(r)
            if g not in fits:
                fits[g] = fit_n0([x for x in train if group(x) == g], d)
            k = d[(r[0], r[6])][1 if r[8] else 0]
            err += (r[4] - float(relative_weight(r[2], fits[g])) * k * r[3]) ** 2
        out[m] = err
    return out


def heldout_error(data: list[tuple], structure: str) -> float:
    return sum(heldout_errors(data, structure).values())


def choose_structure(data: list[tuple]) -> tuple[str, dict]:
    """The simplest usable structure (USABLE is in order of simplicity) whose
    held-out error is within one standard error of the best's, the standard
    error of the member-by-member difference (the one-standard-error rule:
    Hastie, Tibshirani & Friedman 2009, section 7.10): a more complex curve
    has to predict better by more than the noise in the comparison."""
    errors = {name: heldout_errors(data, name) for name in USABLE}
    total = {name: sum(e.values()) for name, e in errors.items()}
    best = min(USABLE, key=lambda name: total[name])
    report = {}
    for name in USABLE:
        diff = np.array([errors[name][m] - errors[best][m] for m in errors[best]])
        report[name] = {"heldout_error": round(total[name], 4),
                        "above_best": round(float(diff.sum()), 4),
                        "standard_error": round(float(np.sqrt(len(diff)) * diff.std(ddof=1)), 4) if len(diff) > 1 else 0.0}
    chosen = next(name for name in USABLE if report[name]["above_best"] <= report[name]["standard_error"])
    return chosen, report


def fit_chambers(data: list[tuple], structure: str) -> dict[str, float]:
    """n0 for each chamber under `structure` ("pooled": the same for both)."""
    if structure == "pooled":
        n0 = fit_n0(data)
        return {name: n0 for name, _ in CHAMBERS}
    d = drifts(data)
    return {name: fit_n0([r for r in data if r[0] == letter], d) for name, letter in CHAMBERS}


def fit_party_line(data: list[tuple], structure: str, drift: dict | None = None) -> tuple[float, dict, float]:
    """The rejected alternative: log n0 = a[group] + b * (party-line share -
    CENTER), groups as in `structure`. (b, {group: a}, squared error)."""
    d = drifts(data) if drift is None else drift
    group = STRUCTURES[structure]
    parts = {}
    for g in sorted({group(r) for r in data}, key=str):
        rows = [r for r in data if group(r) == g]
        thin = [r for r in rows if r[5] == "thin" and (r[0], r[6]) in d]
        if thin:
            n, dx, y = _thin(rows, drift=d)
            parts[g] = (n, dx, y, np.array([r[7] for r in thin]))

    def at(b):
        # The intercepts are separate per group, so each is fitted alone.
        fits = {g: min((_loss(n, dx, y, np.exp(a + b * (u - CENTER))), a) for a in A_GRID)
                for g, (n, dx, y, u) in parts.items()}
        return sum(f[0] for f in fits.values()), {g: f[1] for g, f in fits.items()}
    best = None
    for b in B_GRID:
        loss, a = at(b)
        if best is None or loss < best[0]:
            best = (loss, b, a)
    loss, b, a = best
    return float(b), a, float(loss)


def party_line_test(data: list[tuple], structure: str) -> dict:
    """The party-line alternative against one n0 per group: its b, both
    fits' squared error over the thin pairs, and its held-out error
    (heldout_error's, for the same structure)."""
    d = drifts(data)
    group = STRUCTURES[structure]
    b, _, loss_with = fit_party_line(data, structure, d)
    loss_without = sum(
        _loss(*_thin([r for r in data if group(r) == g], drift=d), fit_n0([r for r in data if group(r) == g], d))
        for g in {group(r) for r in data})
    err = 0.0
    for m in sorted({r[1] for r in data if r[5] == "thin"}):
        train = [r for r in data if r[1] != m]
        dm = drifts(train)
        fb, fa, _ = fit_party_line(train, structure, dm)
        for r in data:
            if r[1] == m and r[5] == "thin" and (r[0], r[6]) in dm and group(r) in fa:
                n0 = float(np.exp(fa[group(r)] + fb * (r[7] - CENTER)))
                k = dm[(r[0], r[6])][1 if r[8] else 0]
                err += (r[4] - float(relative_weight(r[2], n0)) * k * r[3]) ** 2
    return {"b": round(b, 2), "loss_with": round(loss_with, 4), "loss_without": round(loss_without, 4),
            "heldout_error": round(err, 4)}


def uncounted_weight(data: list[tuple]) -> float:
    """Slope of full on drift * thin for positions published with no count."""
    _, dx, y = _thin(data, "uncounted")
    return float((dx * y).sum() / (dx * dx).sum()) if len(dx) else 0.0


PRIOR_BANDS = ((0, 25), (25, 50), (50, 100), (100, 150), (150, RELIABLE_VOTES))


def _same_side(r) -> bool:
    return (r[3] > 0) == (r[4] > 0)


def prior_crossover(data: list[tuple]) -> float | None:
    """An unstratified crossing for the flank rule's switch, reported only:
    the count at which a thin record would match the last Congress's full
    record if a thin record's noise and the drift between Congresses were
    independent. A last full record agrees with this Congress's at q, the
    full pairs' rate; a thin record is observed only against the other
    Congress's full record, a(n) = p q + (1 - p)(1 - q), so p = q where
    a(n) = q^2 + (1 - q)^2, solved on a logistic fit of agreement on log n.
    full_by_distance shows drift flips a side mostly near the party's
    center, as noise does, which switch_model allows for by strata. None
    when agreement doesn't rise with the count."""
    full = [r for r in data if r[5] == "full"]
    thin = [r for r in data if r[5] == "thin" and r[2] > 0]
    if not full or len(thin) < 3:
        return None
    q = sum(map(_same_side, full)) / len(full)
    rate = min(max(q * q + (1 - q) * (1 - q), 1e-6), 1 - 1e-6)
    x = np.column_stack([np.ones(len(thin)), np.log([r[2] for r in thin])])
    y = np.array([_same_side(r) for r in thin], float)
    beta = np.zeros(2)
    for _ in range(50):  # Newton's method on the logistic likelihood
        p = 1 / (1 + np.exp(-x @ beta))
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(hessian, x.T @ (y - p))
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    if beta[1] <= 0:
        return None
    return float(np.exp((np.log(rate / (1 - rate)) - beta[0]) / beta[1]))


DISTANCE_BANDS = ((0.0, 0.02), (0.02, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 10.0))
# Strata of distance from the party's center for the switch's model: the
# side flips mostly near the center, by drift and noise alike.
SWITCH_STRATA = (0.0, 0.05, 0.1)
SWITCH_COUNTS = np.arange(1, RELIABLE_VOTES)  # the counts a switch can fall among


def _stratum(x: float) -> int:
    return sum(abs(x) >= edge for edge in SWITCH_STRATA) - 1


def switch_model(data: list[tuple]):
    """How well each record places a member on their side of the party,
    by distance from the center of the last full record (the earlier one
    in a pair of full records, the full side of a thin pair; strata,
    SWITCH_STRATA): (last full record's rate in the full pairs' mix, a thin
    record's own-Congress rate at each of SWITCH_COUNTS in that mix), or
    None. A last full record agrees with the next Congress's at q_s, the
    full pairs' rate. A thin record is observed only against the other
    Congress's full record; with its noise and the drift independent within
    a stratum, its observed rate there is a_s(n) = p_s q_s + (1 - p_s)(1 -
    q_s), p_s its agreement with its own Congress's full record (clipped to
    [0, 1]). a_s(n) is a logistic in log n with an intercept per stratum."""
    full = [r for r in data if r[5] == "full"]
    thin = [r for r in data if r[5] == "thin" and r[2] > 0]
    k = len(SWITCH_STRATA)
    if not full or len(thin) < k + 1:
        return None
    # The last full record the rule reads: the earlier of a pair of full
    # records, the full side of a thin pair (the earlier, for a member who
    # left, the rule's case).
    mix = np.array([sum(_stratum(r[3]) == s for r in full) for s in range(k)], float) / len(full)
    q = np.array([np.mean([_same_side(r) for r in full if _stratum(r[3]) == s] or [0.5]) for s in range(k)])
    x = np.column_stack([np.array([[_stratum(r[4]) == s for s in range(k)] for r in thin], float),
                         np.log([r[2] for r in thin])])
    y = np.array([_same_side(r) for r in thin], float)
    beta = np.zeros(k + 1)
    for _ in range(100):  # Newton's method on the logistic likelihood, lightly ridged
        p = 1 / (1 + np.exp(-np.clip(x @ beta, -500, 500)))
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + 1e-6 * np.eye(k + 1)
        step = np.linalg.solve(hessian, x.T @ (y - p) - 1e-6 * beta)
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    present = (mix > 0) & np.array([any(_stratum(r[4]) == s for r in thin) for s in range(k)])
    if not present.any() or np.any(2 * q[present] - 1 <= 0):
        return None
    w = mix[present] / mix[present].sum()
    a = 1 / (1 + np.exp(-np.clip(beta[:k][None, present] + beta[-1] * np.log(SWITCH_COUNTS)[:, None], -500, 500)))
    own = np.clip((a - (1 - q[present])) / (2 * q[present] - 1), 0.0, 1.0)
    return float(q[present] @ w), own @ w


def misplaced(model, switch: float) -> float:
    """The share of sides misplaced over counts 1 to RELIABLE_VOTES - 1,
    each equally likely, reading the last full record below `switch` and
    the new record from it."""
    last, own = model
    return float(np.mean(np.where(SWITCH_COUNTS < switch, 1 - last, 1 - own)))


def best_switch(model) -> int:
    """The switch with the fewest misplaced sides; RELIABLE_VOTES is no
    switch short of a full record."""
    return min(range(1, RELIABLE_VOTES + 1), key=lambda s: misplaced(model, s))


def switch_test(data: list[tuple], seed: int = 0) -> dict | None:
    """Whether a switch short of a full record places members better than
    keeping the last full record until the new one is full: the best switch
    on `data`, the share of sides it saves there, and the share saved out of
    bag: a switch chosen on members resampled, judged on the members that
    resample left out, with its 5th, 50th and 95th percentiles and how often
    it saves any (over the draws whose left-out members support a model,
    draws_judged; None if none do); and the thin pairs behind it, all and
    over 100 votes."""
    model = switch_model(data)
    if model is None:
        return None
    switch = best_switch(model)
    rng = np.random.default_rng(seed)
    by_member: dict[str, list[tuple]] = {}
    for row in data:
        by_member.setdefault(row[1], []).append(row)
    ids = sorted(by_member)
    saved = []
    for _ in range(BOOTSTRAP):
        picked = rng.choice(ids, len(ids))
        drawn = switch_model([row for i in picked for row in by_member[i]])
        # Judged on the members the resample left out, never on those it chose from.
        held = switch_model([row for i in sorted(set(ids) - set(picked)) for row in by_member[i]])
        if held is None:
            continue
        chosen = RELIABLE_VOTES if drawn is None else best_switch(drawn)
        saved.append(misplaced(held, RELIABLE_VOTES) - misplaced(held, chosen))
    saved = np.array(saved)
    thin = [r for r in data if r[5] == "thin"]
    judged = {"saved_out_of_bag": None, "share_saving": None} if not len(saved) else {
        "saved_out_of_bag": [round(float(v), 4) for v in np.percentile(saved, (5, 50, 95))],
        "share_saving": round(float((saved > 0).mean()), 3)}
    return {"switch_votes": switch, "thin_pairs": len(thin), "thin_pairs_over_100": sum(r[2] > 100 for r in thin),
            "saved": round(misplaced(model, RELIABLE_VOTES) - misplaced(model, switch), 4),
            "draws_judged": len(saved), **judged}


def prior_test(data: list[tuple]) -> dict:
    """The flank rule's choice (party_line_record): read a member's side of
    their party from their last full record or from this Congress's thin
    one? `data` is centered as the rule centers (each party's weighted mean
    in that Congress). Over the pairs, how often each puts the member on
    the same side as their full record in the other Congress, and the mean
    squared gap: full records against the next Congress's full record,
    thin records by count band against their pair's full one.

    switch_test asks whether a switch short of a full record does better,
    on the pairs shaped like the rule's case ("rule_shape": a full record,
    then the first votes of the next Congress, members who left) and on
    all of them. A thin record is compared across a Congress here, the
    rule within one, so switch_model takes one Congress's drift out within
    strata of distance from the center (full_by_distance: drift flips a
    side mostly near it, as noise does). crossover_if_independent is the
    unstratified crossing, reported for comparison."""
    def summary(rows):
        return {"pairs": len(rows),
                "same_side": round(sum(map(_same_side, rows)) / len(rows), 3),
                "mean_squared_gap": round(sum((r[4] - r[3]) ** 2 for r in rows) / len(rows), 4)}
    full = [r for r in data if r[5] == "full"]
    out: dict = {"full": summary(full)}
    for lo, hi in PRIOR_BANDS:
        rows = [r for r in data if r[5] == "thin" and lo < r[2] <= hi]
        if rows:
            out[f"thin {lo}-{hi}"] = summary(rows)
    out["full_by_distance"] = {
        f"{lo}-{hi}": summary(rows)["same_side"]
        for lo, hi in DISTANCE_BANDS if (rows := [r for r in full if lo <= abs(r[3]) < hi])
    }
    crossover = prior_crossover(data)
    out["crossover_if_independent"] = None if crossover is None else round(crossover, 1)
    usable = [r for r in data if r[5] != "uncounted"]
    out["rule_shape_bands"] = {
        f"thin {lo}-{hi}": summary(rows) for lo, hi in PRIOR_BANDS
        if (rows := [r for r in usable if r[5] == "thin" and r[8] and lo < r[2] <= hi])}
    out["switch_test"] = {
        "rule_shape": switch_test([r for r in usable if r[5] == "full" or r[8]]),
        "all": switch_test(usable),
    }
    return out


def prior_until_votes(test: dict) -> float:
    """The count below which the flank rule reads the last full record: a
    full record (no switch short of one) unless, on the pairs shaped like
    the rule's case, a switch chosen on resampled members saves sides on the
    members left out in at least 95% of resamples (its 5th percentile above 0); then that
    switch."""
    shape = (test.get("switch_test") or {}).get("rule_shape")
    if shape and shape.get("saved_out_of_bag") and shape["saved_out_of_bag"][0] > 0:
        return float(shape["switch_votes"])
    return float(RELIABLE_VOTES)


def bootstrap(data: list[tuple], structure: str, seed: int = 0) -> dict:
    """5th and 95th percentiles of each chamber's n0 and half point and of
    the no-count weight, resampling members (in a fixed order, so a rerun
    on the same data reproduces them)."""
    rng = np.random.default_rng(seed)
    by_member: dict[str, list[tuple]] = {}
    for row in data:
        by_member.setdefault(row[1], []).append(row)
    ids = sorted(by_member)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [row for i in rng.choice(ids, len(ids)) for row in by_member[i]]
        draws.append((fit_chambers(sample, structure), uncounted_weight(sample)))

    def interval(values):
        return [round(float(np.percentile(values, q)), 3) for q in (5, 95)]
    out: dict = {name: {"n0": interval([c[name] for c, _ in draws]),
                        "half_weight_votes": interval([half_point(c[name]) for c, _ in draws])}
                 for name, _ in CHAMBERS}
    out["uncounted_weight"] = interval([u for _, u in draws])
    return out


def calibrate(cache: pathlib.Path | None = None) -> dict:
    span = congresses()
    data, shares = pairs(cache, span)
    d = drifts(data)
    used = [r for r in data if r[5] != "full" and (r[0], r[6]) in d]
    last = max(r[6] for r in data) + 1
    structure, comparison = choose_structure(data)
    heldout = {name: round(heldout_error(data, name), 4) for name in STRUCTURES}
    n0 = fit_chambers(data, structure)
    uncounted = uncounted_weight(data)
    left_out = [uncounted_weight([r for r in data if r[1] != m])
                for m in sorted({r[1] for r in used if r[5] == "uncounted"})]

    def rule_weight(chamber_n0):
        # The flank rule's weight (score_calculator.position_confidence).
        return lambda votes, career: (uncounted if career else 0.0) if votes <= 0 else float(
            relative_weight(votes, chamber_n0))
    rule_data, _ = pairs(cache, span, weight={letter: rule_weight(n0[name]) for name, letter in CHAMBERS},
                         shares=shares)
    return {
        "calibrated_through": last,
        "structure": structure,
        "chambers": {
            name: {"n0": n0[name], "half_weight_votes": round(half_point(n0[name]), 1),
                   "thin_pairs": sum(1 for r in used if r[0] == letter and r[5] == "thin")}
            for name, letter in CHAMBERS
        },
        "reference_votes": RELIABLE_VOTES,
        "uncounted_weight": round(uncounted_weight(data), 3),
        # Its range with each of its pairs' members left out in turn: few
        # pairs, so one member can move it a lot.
        "uncounted_weight_leave_one_out": [round(f(left_out), 3) for f in (min, max)] if left_out else None,
        "interval_90": bootstrap(data, structure),
        "heldout_error": heldout,
        "structure_test": comparison,
        "half_weight_votes_pooled": round(half_point(fit_n0(data)), 1),
        "half_weight_votes_by_chamber": {
            name: round(half_point(c), 1) for name, c in fit_chambers(data, "chamber").items()},
        "half_weight_votes_by_chamber_interval_90": {
            name: b["half_weight_votes"] for name, b in bootstrap(data, "chamber").items()
            if name != "uncounted_weight"},
        "half_weight_votes_by_era": {
            f"{e.start}-{e.stop - 1}": round(half_point(fit_n0([r for r in data if r[6] in e])), 1)
            for e in (range(FIRST_CONGRESS, 110), range(110, last + 1))
        },
        "party_line_test": party_line_test(data, structure),
        "prior_test": (test := prior_test(rule_data)),
        "prior_until_votes": prior_until_votes(test),
        "party_line_share_by_congress": {
            name: {str(c): round(u, 3) for c, u in shares[letter].items()}
            for name, letter in CHAMBERS
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
    calibrated = calibrate(args.cache)
    # With a cache, the dates the exports used were fetched.
    used = range(FIRST_CONGRESS - 1, calibrated["calibrated_through"] + 1)  # the 100th: who was seated before
    cached = [f.stat().st_mtime for c in used for ch in "SH" for kind in ("members", "votes")
              if (f := args.cache / f"{ch}{c}_{kind}.csv").exists()] if args.cache else []
    dates = sorted({datetime.date.fromtimestamp(t).isoformat() for t in cached}) or [datetime.date.today().isoformat()]
    retrieved = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
    data = {
        "_source": (
            f"Voteview (voteview.com, Lewis et al.) member and vote exports, Senate and House, Congresses "
            f"{FIRST_CONGRESS}-{calibrated['calibrated_through']} (later ones still thin by the calendar "
            f"are left out), exports retrieved {retrieved}; "
            "regenerate with backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Members of the 50 states with positions in adjacent Congresses, each read from the "
            "party's center toward its flank, keyed by the transition they span: full = "
            "drift[chamber, transition] * weight(n) * thin, weight(n) = min(1, w(n) / "
            f"w({RELIABLE_VOTES})), w(n) = n / (n + n0); drift from each transition's pairs of full "
            f"records (both sides {RELIABLE_VOTES} or more scaled votes) in the pair's direction, n0 "
            "the least-squares fit of the thin pairs on a grid to 5000, for every Congress, under "
            "one n0 for both chambers unless one per chamber has a heldout_error (the squared error "
            "of each thin member's pairs fitted without that member) smaller by more than the "
            "standard error of the difference (structure_test; era and direction are tested, not "
            "usable); half_weight_votes is where "
            "weight(n) = 0.5, never above reference_votes / 2 (as n0 grows the curve tends to n / "
            "reference_votes), so an interval reaching that limit is open above; "
            "uncounted_weight is the slope, for both chambers, for positions published with no "
            "count (or 0) but a career DW-NOMINATE position; party_line_test is the rejected "
            "alternative with log n0 linear in the Congress's party-line share; transitions with "
            "no full pairs and Congresses still thin by the calendar are left out; interval_90 is "
            "the 5th-95th percentile over members resampled; prior_test compares, for the flank rule, "
            "a last full record and a thin record as evidence of a member's side of their party, "
            "centered as the rule centers, and prior_until_votes is the count below which the rule "
            "reads the last full record (a full record unless a switch short of one, chosen on "
            "resampled members, saves sides on the members left out in 95% of resamples, on pairs "
            "shaped like the rule's case); "
            "structure_test is the one-standard-error rule's comparison; crossover_if_independent is "
            "the rejected unstratified crossing, reported only"
        ),
        **calibrated,
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: {data['structure']}, {data['chambers']}, intervals {data['interval_90']}, "
          f"held-out error {data['heldout_error']}, party-line test {data['party_line_test']}")


if __name__ == "__main__":
    main()
