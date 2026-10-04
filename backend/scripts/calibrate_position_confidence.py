"""Calibrate how much a congress-specific roll-call position can be trusted.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_reliability(): the weight Constituent Alignment's
position-congruence component gives a member whose Nokken-Poole position
rests on n scaled roll calls (v6.27),

    weight(n) = min(1, w(n) / w(RELIABLE_VOTES)),   w(n) = n / (n + n0),

so a full record (RELIABLE_VOTES or more) counts in full and a thin one
counts by how much less it says.

Measured on Voteview's own positions, with no model of how Voteview
estimates them. A member with a thin record in one Congress and a full one
in the next, or the reverse, has a thin position and a full one for
adjacent Congresses: one who arrived or left mid-Congress, or one absent
for much of it (illness, a campaign), as the score's thin records are.
Read each against their party's center that Congress, signed toward the
party's flank, every pair keyed by the transition it spans (its earlier
Congress):

    full = drift[chamber, transition] * weight(n) * thin + error

Drift is how far positions carry from one Congress to the next, and it
varies by era (a full record's slope on the next Congress's runs from about
0.66 to 1.01, drift_range), so each chamber and transition gets its own, set
by that transition's pairs of full records alone, which show no gradient in
their count (full records count 1). n0 is then the least-squares fit of the
thin pairs. The structure shipped is chosen forward in time (forward_test):
each transition's thin pairs predicted from a fit on the earlier transitions
only, as the weight is always applied to a Congress the calibration hasn't
seen; one curve for both chambers unless one per chamber, or the latest
era's (split at ERA_SPLIT, a convention), predicts the next Congress better
by more than a standard error of the paired difference. That rule was
adopted in review, after a forward check had been run once (research note
section 14). The leave-one-member-out comparisons (choose_structure's
one-standard-error rule over all members; era_test on the latest era's;
era_split_test at every split; trend_test) are reported beside it; a rerun
decides again. n0 is weakly determined, and so is the count at which a
position counts half (half_weight_votes, a reparametrisation of it, bounded
above at RELIABLE_VOTES / 2), reported with its interval. Positions Voteview
publishes with no count get their own measured weight, on the same drift:
those with a career DW-NOMINATE position, as the score applies it (a member
with neither a count nor a career, such as one just sworn in, reads as no
votes).

Left out: Voteview's 0, 0 placeholders (no position); members from outside
the 50 states (the House's delegates, whose records are thin because they
vote only in the Committee of the Whole, not because they served part of a
Congress), the states being those of the pipeline's House district table;
transitions with no full pairs; and a Congress still thin by the calendar
(its median member under RELIABLE_VOTES, as early in a sitting Congress),
whose short records reflect the date, not partial service.

A party-line term was tested and is reported, not used: with one drift for
every Congress, n0 appeared to rise with the share of roll calls on which
the parties' majorities split, but with drift measured per transition its
slope is unstable (its sign has flipped between reruns) and it predicts
held-out members no better than no term (research note section 14). Nothing
in the weight follows the sitting Congress (the era split, if adopted, is a
fixed Congress), so a rerun only adds pairs. The pair's direction (which
side of the pair is thin: the earlier, mostly members who arrived, or the
later, mostly members who left or were absent at the end) and attendance
(attended arrivals and departures against the rest) are tested the same way
and reported, not used: neither can be known for a sitting member's record.
Members who switched parties during a Congress are left out of the fit
(deviations()); switcher_test reports which of their records best predicts
the next Congress's.

prior_test is the evidence for the flank rule's use of the last
Congress's full record (party_line_record), on pairs centered as that
rule centers; its switch test uses the pairs shaped like the rule's case,
a full record then a member's first, attended votes of the next Congress
(pairs()'s tenth field, `run`).

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
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402

MEMBERS_URL = "https://voteview.com/static/data/out/members/{chamber}{congress}_members.csv"
VOTES_URL = "https://voteview.com/static/data/out/votes/{chamber}{congress}_votes.csv"
FIRST_CONGRESS = 101  # the first with Nokken-Poole positions and counts throughout
FETCH_TIMEOUT_S = 120
RELIABLE_VOTES = 200  # a full record; the pairs measure the weight below it
N0_GRID = np.concatenate([np.arange(1.0, 400.0, 1.0), np.arange(400.0, 5001.0, 25.0)])
BOOTSTRAP = 1000
# The forward test predicts a transition only from at least this many
# earlier transitions with thin pairs (a convention).
MIN_TRAIN_TRANSITIONS = 3
CENTER = 0.6  # the party-line test's log n0 = a + b * (share - CENTER)
A_GRID = np.arange(-1.0, 8.6, 0.05)
B_GRID = np.arange(-25.0, 25.25, 0.25)
YEA, NAY = (1, 2, 3), (4, 5, 6)
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "position_confidence.json"


def _csv(url: str, name: str, cache: pathlib.Path | None) -> list[dict]:
    if cache is not None and (cache / name).exists():
        text = (cache / name).read_text()
    else:
        request = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:
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


def attendance(chamber: str, congress: int, cache: pathlib.Path | None = None) -> dict[str, float]:
    """icpsr -> the share of the roll calls in a member's span (their first
    to their last roll call with a row) on which they didn't vote: a cast
    code of 7-9, or no row at all (Voteview writes none for a Speaker who
    doesn't vote)."""
    span: dict[str, list[int]] = {}
    for r in _csv(VOTES_URL.format(chamber=chamber, congress=congress), f"{chamber}{congress}_votes.csv", cache):
        number = int(_float(r.get("rollnumber")) or 0)
        a = span.setdefault(_id(r["icpsr"]), [number, number, 0])
        a[0], a[1] = min(a[0], number), max(a[1], number)
        a[2] += 1 <= int(_float(r.get("cast_code")) or 0) <= 6
    return {i: 1 - voted / (last - first + 1) for i, (first, last, voted) in span.items()}


def roll_spans(chamber: str, congress: int, cache: pathlib.Path | None = None) -> dict[str, tuple[int, int]]:
    """icpsr -> (first, last) roll call with a row."""
    span: dict[str, list[int]] = {}
    for r in _csv(VOTES_URL.format(chamber=chamber, congress=congress), f"{chamber}{congress}_votes.csv", cache):
        number = int(_float(r.get("rollnumber")) or 0)
        a = span.setdefault(_id(r["icpsr"]), [number, number])
        a[0], a[1] = min(a[0], number), max(a[1], number)
    return {i: (a, b) for i, (a, b) in span.items()}


def switcher_test(cache: pathlib.Path | None = None, span: range | None = None) -> dict:
    """Which of a mid-Congress party switcher's records to read (the
    pipeline's choice, voteview.build_chamber_ideal_points): for each member
    of a state Voteview lists under two or more ICPSR ids in one Congress,
    with a full record in the next, the squared gap between that full
    position and each candidate: the latest record (the id whose first roll
    call comes last), the longer one (more scaled votes), and the
    vote-weighted mean. The mean over members, how many (and how many
    people), the paired differences of the latest from the other two with
    standard errors over people, and, where the latest isn't also the
    longer, in how many it is the closer."""
    span = span or congresses()
    states = _states()
    gaps: dict[str, list[float]] = {"latest": [], "longer": [], "weighted": []}
    people: list[str] = []
    for chamber in ("S", "H"):
        for c in span:
            try:
                rows, after = member_rows(chamber, c, cache), member_rows(chamber, c + 1, cache)
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    continue  # not published (the Congress after the sitting one)
                raise  # an outage stops the run rather than dropping a Congress
            ids: dict[str, list[dict]] = {}
            for r in rows:
                bio = (r.get("bioguide_id") or "").strip()
                if bio and (r.get("state_abbrev") or "").strip().upper() in states:
                    ids.setdefault(bio, []).append(r)
            ids = {b: rs for b, rs in ids.items() if len({_id(r["icpsr"]) for r in rs}) > 1}
            if not ids:
                continue
            spans = roll_spans(chamber, c, cache)
            for bio, rs in ids.items():
                nxt = [r for r in after if (r.get("bioguide_id") or "").strip() == bio]
                full = [r for r in nxt if (_float(r.get("nominate_number_of_votes")) or 0) >= RELIABLE_VOTES
                        and _float(r.get("nokken_poole_dim1")) is not None and not is_placeholder(r)]
                cands = [(r, x, _float(r.get("nominate_number_of_votes")) or 0.0) for r in rs
                         if (x := _float(r.get("nokken_poole_dim1"))) is not None and not is_placeholder(r)]
                if len(full) != 1 or len(cands) < 2:
                    continue
                target = _float(full[0]["nokken_poole_dim1"])
                # As the pipeline (voteview.switcher_latest): an id with no
                # roll call yet is the newest.
                latest = max(cands, key=lambda c: spans.get(_id(c[0]["icpsr"]), (float("inf"),))[0])[1]
                longer = max(cands, key=lambda c: c[2])[1]
                total = sum(n for _, _, n in cands)
                weighted = sum(x * n for _, x, n in cands) / total if total else longer
                for name, x in (("latest", latest), ("longer", longer), ("weighted", weighted)):
                    gaps[name].append((x - target) ** 2)
                people.append(bio)
    def paired(other):
        # Latest minus `other`, averaged within each person first (a member
        # who switched twice counts once), and its standard error over people.
        by: dict[str, list[float]] = {}
        for bio, a, b in zip(people, gaps["latest"], gaps[other]):
            by.setdefault(bio, []).append(a - b)
        d = np.array([np.mean(v) for v in by.values()])
        return {"mean": round(float(d.mean()), 4) if len(d) else None,
                "standard_error": round(float(d.std(ddof=1) / np.sqrt(len(d))), 4) if len(d) > 1 else None}
    differ = [(a, b) for a, b in zip(gaps["latest"], gaps["longer"]) if a != b]
    return {"members": len(gaps["latest"]), "people": len(set(people)),
            **{f"mean_squared_gap_{k}": round(float(np.mean(v)), 4) if v else None for k, v in gaps.items()},
            "latest_minus_longer": paired("longer"), "latest_minus_weighted": paired("weighted"),
            # Where the latest record is not also the longer one: how many,
            # and in how many the latest is the closer.
            "latest_not_longer": len(differ), "latest_closer": sum(a < b for a, b in differ)}


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
    state with a position, one row each. A member who switched parties
    during the Congress is left out: Voteview lists them under a new ICPSR
    id with the same bioguide id, so each id's record covers only part of
    the Congress, and the old one would read as a member who left, the new
    one as a member who arrived, with a change of party booked as a thin
    record's noise. The center is the median of the party's
    full records, or, with `weight` ((votes, career) -> reliability weight),
    the weighted mean of all its members, as the flank rule centers
    (party_line_record)."""
    states = _states()
    seen: dict[str, list[dict]] = {}
    ids_of: dict[str, set[str]] = {}
    for r in rows:
        seen.setdefault(_id(r["icpsr"]), []).append(r)
        if bioguide := (r.get("bioguide_id") or "").strip():
            ids_of.setdefault(bioguide, set()).add(_id(r["icpsr"]))
    switched = {i for ids in ids_of.values() if len(ids) > 1 for i in ids}
    members = []
    for icpsr, rs in seen.items():
        r = rs[0]
        party = _float(r.get("party_code"))
        x = _float(r.get("nokken_poole_dim1"))
        if len(rs) != 1 or icpsr in switched or party not in (100.0, 200.0) or x is None or is_placeholder(r):
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
    """A Congress whose export exists and isn't thin by the calendar. Only
    an export that isn't published (404) is unusable; any other failure
    stops the run rather than dropping a Congress from the calibration."""
    try:
        rows = member_rows(chamber, congress, cache)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise
    counts = [_float(r.get("nominate_number_of_votes")) or 0.0 for r in rows]
    return bool(counts) and statistics.median(counts) >= RELIABLE_VOTES


def pairs(cache: pathlib.Path | None = None, span: range | None = None, weight=None,
          shares: dict | None = None) -> tuple[list[tuple], dict]:
    """(chamber, icpsr, n, thin-or-earlier deviation, full deviation, kind,
    transition, party-line share, thin side is the later, run, moved) with
    kind "full"
    (both sides full records: n is the earlier side's), "thin" (one side
    counted under RELIABLE_VOTES: n is that side's) or "uncounted" (one
    side with no count but a career position, the case the score weights
    by uncounted_weight), every pair keyed by the transition it spans (its
    earlier Congress), that Congress's party-line share, whether the thin
    side is the later, whether the thin record is a run of attended
    consecutive votes from a member who arrived or left (missing no more
    of the roll calls in their span than nine in ten of that Congress's
    full records do: the flank rule's case), and whether the member arrived
    or left at all (absent the Congress before it, or after it; the newest
    Congress's leavers can't be checked until the next export); and each
    chamber's party-line share by usable Congress (`shares`, when given, is
    reused). `weight` ({chamber: (votes, career) -> weight}) centers each
    Congress as the flank rule does (deviations)."""
    span = span or congresses()
    out, shares = [], dict(shares or {})
    for chamber in ("S", "H"):
        usable = [c for c in span if _usable(chamber, c, cache)]
        share = shares[chamber] if chamber in shares else {c: party_line_share(chamber, c, cache) for c in usable}
        shares[chamber] = share
        def seated(congress: int) -> set[str] | None:
            # Everyone in a Congress's export, or None when it isn't published
            # (404). Any other failure stops the run rather than switching the
            # check off.
            try:
                return {_id(r["icpsr"]) for r in member_rows(chamber, congress, cache)}
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    return None
                raise
        for earlier, later in zip(usable, usable[1:]):
            if later != earlier + 1:
                continue
            w = (weight or {}).get(chamber)
            prev = deviations(member_rows(chamber, earlier, cache), w)
            cur = deviations(member_rows(chamber, later, cache), w)
            before, after = seated(earlier - 1), seated(later + 1)
            absent = {earlier: attendance(chamber, earlier, cache), later: attendance(chamber, later, cache)}
            # Nine in ten full records miss no more than this share of their span.
            usual = {c: float(np.percentile([absent[c].get(i, 0.0) for i, (n, _, _) in dev.items()
                                             if n >= RELIABLE_VOTES] or [0.0], 90))
                     for c, dev in ((earlier, prev), (later, cur))}
            for icpsr in prev.keys() & cur.keys():
                (na, xa, ca), (nb, xb, cb) = prev[icpsr], cur[icpsr]
                if na >= RELIABLE_VOTES and nb >= RELIABLE_VOTES:
                    out.append((chamber, icpsr, na, xa, xb, "full", earlier, share[earlier], False, False, False))
                elif nb >= RELIABLE_VOTES or na >= RELIABLE_VOTES:
                    later_thin = na >= RELIABLE_VOTES
                    n, thin, full, career = (nb, xb, xa, cb) if later_thin else (na, xa, xb, ca)
                    if n > 0:
                        kind = "thin"
                    elif career:
                        kind = "uncounted"  # the score's no-count case: a career position, no count
                    else:
                        continue  # no count and no career position: the score reads it as no votes
                    # The flank rule's case: arrived or left (the newest
                    # Congress's leavers can't be checked until the next
                    # export), and attended through their span.
                    other, side = (after, later) if later_thin else (before, earlier)
                    moved = other is not None and icpsr not in other
                    run = moved and absent[side].get(icpsr, 0.0) <= usual[side]
                    out.append((chamber, icpsr, n, thin, full, kind, earlier, share[earlier], later_thin, run, moved))
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
# The era structure's split: the first Congress of the later era. A
# convention (roughly the middle of 101-119 when it was set, fixed before
# the comparisons that use it); it stays at 110 on reruns, never
# re-centred, so the latest era only grows.
ERA_SPLIT = 110
# Candidate structures for n0: the group a pair's n0 is fitted within.
# "pooled" and "chamber" can be applied to a sitting member's record and are
# compared on every thin pair (choose_structure, reported). "era" can be
# applied only as its latest era's curve (every Congress scored from now on
# falls in it). The shipped structure is forward_test's choice among
# pooled, chamber and era; era_test and era_split_test report the era
# comparison left one member out. Direction and attendance can't be known
# for a sitting member's record, so they are tested and reported only.
STRUCTURES = {
    "pooled": lambda r: "all",
    "chamber": lambda r: r[0],
    "era": lambda r: r[6] >= ERA_SPLIT,
    "direction": lambda r: r[8],
    # Attended arrivals and departures (pairs()'s tenth field) apart from the
    # rest: whether absences make a thin record say less.
    "attendance": lambda r: r[9] if len(r) > 9 else False,
}
USABLE = ("pooled", "chamber")


def heldout_errors(data: list[tuple], structure: str, only=None) -> dict[str, float]:
    """Each thin member's squared error, their pairs predicted by n0 fitted
    (in its group) and drift measured without that member; with `only` (a
    pair predicate), over those of their pairs alone, for the members who
    have any."""
    group = STRUCTURES[structure]
    out: dict[str, float] = {}
    for m in sorted({r[1] for r in data if r[5] == "thin" and (only is None or only(r))}):
        err = 0.0
        train = [r for r in data if r[1] != m]
        d = drifts(train)
        fits: dict = {}
        for r in data:
            if r[1] != m or r[5] != "thin" or (r[0], r[6]) not in d or (only is not None and not only(r)):
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


def _paired(a: dict[str, float], b: dict[str, float]) -> dict | None:
    """sum(a - b) over the members of b and the standard error of that
    member-by-member difference; None with fewer than two members."""
    diff = np.array([a[m] - b[m] for m in b])
    if len(diff) < 2:
        return None
    return {"above": round(float(diff.sum()), 4),
            "standard_error": round(float(np.sqrt(len(diff)) * diff.std(ddof=1)), 4)}


def forward_errors(
    data: list[tuple], split: int = ERA_SPLIT, names: tuple = ("pooled", "chamber", "era", "trend"),
    window: int | None = None, predict_from: int | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[int, float]]]:
    """structure -> each thin member's squared error predicting each
    transition's thin pairs from a fit (n0) on the earlier transitions
    only, rolling forward one transition at a time: how well a curve
    measured on the past predicts the next Congress, the use the weight is
    put to; and the same summed by transition. The drift of the predicted
    transition comes from its own full pairs (drift is not part of the
    curve, and every structure shares it). Structures (`names`): pooled,
    chamber, era (the curve since `split`, pooled until that era has thin
    pairs), trend (log n0 linear in decades since ERA_SPLIT, on the A and
    B grids, whose ends it often reaches) and window (the last `window`
    transitions with thin pairs). With `predict_from`, only transitions
    from that one on are predicted (fits still use every earlier one)."""
    letters = dict((letter, name) for name, letter in CHAMBERS)
    thin_transitions = sorted({r[6] for r in data if r[5] == "thin"})
    out: dict[str, dict[str, float]] = {k: {} for k in names}
    by_t: dict[str, dict[int, float]] = {k: {} for k in names}
    for t in thin_transitions:
        train = [r for r in data if r[6] < t]
        earlier = sorted({r[6] for r in train if r[5] == "thin"})
        if len(earlier) < MIN_TRAIN_TRANSITIONS or (predict_from is not None and t < predict_from):
            continue
        test = [r for r in data if r[6] == t]
        drift = drifts(test)
        fits = {}
        if "pooled" in names or "era" in names:
            fits["pooled"] = fit_chambers(train, "pooled")
        if "chamber" in names:
            fits["chamber"] = fit_chambers(train, "chamber")
        if "era" in names:
            late = any(r[6] >= split and r[5] == "thin" for r in train)
            fits["era"] = fit_chambers(train, "era", split) if late else fits["pooled"]
        if "window" in names and window:
            since = earlier[-window] if len(earlier) >= window else earlier[0]
            fits["window"] = fit_chambers([r for r in train if r[6] >= since], "pooled")
        if "trend" in names:
            b, a, _ = fit_party_line(train, "pooled", drifts(train), _decades)
            a0 = next(iter(a.values()))
        for r in test:
            if r[5] != "thin" or (r[0], r[6]) not in drift:
                continue
            k = drift[(r[0], r[6])][1 if r[8] else 0]
            n0s = {name: f[letters[r[0]]] for name, f in fits.items() if name in names}
            if "trend" in names:
                n0s["trend"] = min(float(np.exp(a0 + b * _decades(r))), 1e6)
            for name, n0 in n0s.items():
                err = (r[4] - float(relative_weight(r[2], n0)) * k * r[3]) ** 2
                out[name][r[1]] = out[name].get(r[1], 0.0) + err
                by_t[name][t] = by_t[name].get(t, 0.0) + err
    return out, by_t


# The forward window structure's width, reported only (a convention).
FORWARD_WINDOW = 6


def forward_test(data: list[tuple]) -> dict:
    """The structure the calibration ships: one curve unless a structure
    the score can apply (per chamber, or the latest era's) predicts the
    next Congress better, forward_errors' total, by more than the
    standard error of its member-by-member difference from one curve (a
    convention; the better of the two if both do). The trend is reported,
    not chosen: it was added after the other tests (a stated choice), and
    over every predicted transition it doesn't clear the same bar. Each
    structure's total and paired difference from one curve ("above":
    negative, better).
    With a choice other than one curve, how much it rests on: its gain by
    transition; the comparison with the one, two and three members it
    helps most left out of it (fits unchanged) and left out of the data
    (refitted); the era comparison at every split; and a window of the
    last FORWARD_WINDOW transitions, recency without a split, against one
    curve and against the era curve (window_against_era: whether recency
    and a split can be told apart). last_three is the chosen structure's
    comparison on the last three predicted transitions alone."""
    errors, by_t = forward_errors(data)
    report = {name: {"error": round(sum(e.values()), 4), **(_paired(e, errors["pooled"]) or {})}
              for name, e in errors.items() if name != "pooled"}
    report["pooled"] = {"error": round(sum(errors["pooled"].values()), 4)}
    passing = [n for n in ("chamber", "era") if report[n].get("above") is not None
               and report[n]["above"] < -report[n]["standard_error"]]
    chosen = min(passing, key=lambda n: report[n]["error"]) if passing else "pooled"
    if chosen != "pooled":
        r = report[chosen]
        r["gain_by_transition"] = {str(t): round(v - by_t["pooled"][t], 4) for t, v in sorted(by_t[chosen].items())}
        gain = {m: errors[chosen][m] - errors["pooled"][m] for m in errors["pooled"]}
        order = sorted(gain, key=gain.get)
        r["without_most_helped_left_out_of_comparison"] = [
            _paired({m: errors[chosen][m] for m in order[k:]}, {m: errors["pooled"][m] for m in order[k:]})
            for k in (1, 2, 3)]
        refit = []
        for k in (1, 2, 3):
            rest = [row for row in data if row[1] not in order[:k]]
            e, _ = forward_errors(rest, names=("pooled", chosen))
            refit.append(_paired(e[chosen], e["pooled"]))
        r["without_most_helped_refitted"] = refit
        # The share of the gain each of the three members it helps most supplies.
        r["most_helped_share"] = [round(gain[m] / sum(gain.values()), 3) for m in order[:3]]
        e, _ = forward_errors(data, names=("pooled", chosen), predict_from=sorted(by_t["pooled"])[-3:][0])
        r["last_three"] = _paired(e[chosen], e["pooled"])
    thin = sorted({row[6] for row in data if row[5] == "thin"})
    sweep = {}
    # Every split the forward test can tell from one curve: the era curve
    # differs only once an earlier transition at or after the split has
    # thin pairs, so never at the last.
    for cut in thin[1:-1]:
        e, _ = forward_errors(data, split=cut, names=("pooled", "era"))
        t = _paired(e["era"], e["pooled"])
        if t is not None:
            sweep[str(cut)] = {**t, "adopted": t["above"] < -t["standard_error"]}
    e, _ = forward_errors(data, names=("pooled", "window"), window=FORWARD_WINDOW)
    # What the one curve was fitted on when each transition was predicted:
    # the share of its thin pairs from before ERA_SPLIT.
    fitted = [row for row in data if row[5] == "thin" and (row[0], row[6]) in drifts(data)]
    before = {str(t): round(sum(1 for row in fitted if row[6] < min(t, ERA_SPLIT))
                            / max(1, sum(1 for row in fitted if row[6] < t)), 3)
              for t in sorted(by_t["pooled"])}
    # The trend, by transition: its gain over one curve, and whether its
    # forward fit reached an end of the A or B grid; and, from the first
    # transition from which every fit is inside both grids, the trend
    # against one curve and against the era curve.
    at_end = {}
    for t in sorted(by_t["trend"]):
        b, a, _ = fit_party_line([row for row in data if row[6] < t], "pooled",
                                 drifts([row for row in data if row[6] < t]), _decades)
        if not a:
            continue  # nothing to fit on: no grid to reach
        a0 = next(iter(a.values()))
        at_end[t] = bool(a0 in (A_GRID[0], A_GRID[-1]) or b in (B_GRID[0], B_GRID[-1]))
    inside = [t for t in sorted(at_end) if not any(at_end[u] for u in at_end if u >= t)]
    trend = report["trend"]
    trend["gain_by_transition"] = {str(t): round(v - by_t["pooled"][t], 4) for t, v in sorted(by_t["trend"].items())}
    trend["fit_at_grid_end"] = {str(t): v for t, v in at_end.items()}
    if inside:
        late, _ = forward_errors(data, names=("pooled", "era", "trend"), predict_from=inside[0])
        trend["inside_grids"] = {"from": inside[0], "against_one_curve": _paired(late["trend"], late["pooled"]),
                                 "against_era": _paired(late["trend"], late["era"])}
    return {"chosen": chosen, "members": len(errors["pooled"]), **report,
            "training_before_split": before,
            "era_at_every_split": sweep, "window": _paired(e["window"], e["pooled"]),
            "window_against_era": _paired(e["window"], errors["era"])}


def era_test(data: list[tuple], structure: str, split: int = ERA_SPLIT) -> dict | None:
    """The era structure as it would be applied: the latest era's curve
    (eras split at `split`) against `structure`, both judged on the latest
    era's thin pairs alone, held out member by member. "above" is the era
    curve's error minus the other's (negative: better); "adopted" says
    whether it would pass the one-standard-error bar on this comparison
    (reported: the shipped structure is forward_test's). None if fewer
    than two members have thin pairs in the latest era."""
    def late(r):
        return r[6] >= split
    STRUCTURES["_split"] = late
    try:
        era = heldout_errors(data, "_split", only=late)
    finally:
        del STRUCTURES["_split"]
    out = _paired(era, heldout_errors(data, structure, only=late))
    if out is None:
        return None
    half = round(half_point(fit_n0([r for r in data if late(r)], drifts(data))), 1)
    return {**out, "members": len(era), "adopted": out["above"] < -out["standard_error"],
            "half_weight_votes": half}


def era_split_test(data: list[tuple], structure: str) -> dict:
    """era_test at every split with thin pairs on both sides, a check that
    the result doesn't hang on ERA_SPLIT, and at how many splits the latest
    era's curve would be adopted; where it would, that curve's n0, its
    range with each of the era's thin members left out in turn, and the
    test rerun without the member whose absence moves n0 most."""
    thin = sorted({r[6] for r in data if r[5] == "thin"})
    out = {str(cut): t for cut in thin[1:] if (t := era_test(data, structure, cut)) is not None}
    d = drifts(data)
    for cut, t in out.items():
        if t["adopted"]:
            # How much the adopted curve rests on single members: its n0 with
            # each of the latest era's thin members left out in turn.
            late = [r for r in data if r[6] >= int(cut)]
            fits = {m: fit_n0([r for r in late if r[1] != m], d)
                    for m in sorted({r[1] for r in late if r[5] == "thin"})}
            t["n0"] = n0 = fit_n0(late, d)
            t["n0_leaving_one_member_out"] = [min(fits.values()), max(fits.values())]
            # The test again without the member whose absence moves n0 most
            # (on a log scale); none when no member moves it.
            most = max(fits, key=lambda m: abs(np.log(fits[m]) - np.log(n0)))
            again = (era_test([r for r in data if r[1] != most], structure, int(cut))
                     if fits[most] != n0 else None)
            t["without_most_influential_member"] = again and {
                k: again[k] for k in ("above", "standard_error", "adopted", "half_weight_votes")}
    return {"splits": out, "adopted_at": sum(t["adopted"] for t in out.values()), "of": len(out)}


def fit_chambers(data: list[tuple], structure: str, split: int = ERA_SPLIT) -> dict[str, float]:
    """n0 for each chamber under `structure` ("pooled": the same for both;
    "era": the same for both, the latest era's (from `split`), which every
    Congress scored from now on falls in)."""
    if structure == "era":
        n0 = fit_n0([r for r in data if r[6] >= split], drifts(data))
        return {name: n0 for name, _ in CHAMBERS}
    if structure == "pooled":
        n0 = fit_n0(data)
        return {name: n0 for name, _ in CHAMBERS}
    d = drifts(data)
    return {name: fit_n0([r for r in data if r[0] == letter], d) for name, letter in CHAMBERS}


def _party_line(r) -> float:
    return r[7] - CENTER


def _decades(r) -> float:
    # Decades (five Congresses) from ERA_SPLIT: the time-trend test's term.
    return (r[6] - ERA_SPLIT) / 5


def fit_party_line(data: list[tuple], structure: str, drift: dict | None = None,
                   term=_party_line) -> tuple[float, dict, float]:
    """The rejected alternative: log n0 = a[group] + b * term(pair), the
    term the Congress's party-line share less CENTER unless given (the
    time-trend test passes _decades), groups as in `structure`.
    (b, {group: a}, squared error)."""
    d = drifts(data) if drift is None else drift
    group = STRUCTURES[structure]
    parts = {}
    for g in sorted({group(r) for r in data}, key=str):
        rows = [r for r in data if group(r) == g]
        thin = [r for r in rows if r[5] == "thin" and (r[0], r[6]) in d]
        if thin:
            n, dx, y = _thin(rows, drift=d)
            parts[g] = (n, dx, y, np.array([term(r) for r in thin]))

    def at(b):
        # The intercepts are separate per group, so each is fitted alone.
        fits = {g: min((_loss(n, dx, y, np.exp(a + b * u)), a) for a in A_GRID)
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
    fits' squared error over the thin pairs, its held-out error
    (heldout_error's, for the same structure), and the member-by-member
    difference from the structure's own held-out errors with its standard
    error ("above": positive, worse)."""
    d = drifts(data)
    group = STRUCTURES[structure]
    b, _, loss_with = fit_party_line(data, structure, d)
    loss_without = sum(
        _loss(*_thin([r for r in data if group(r) == g], drift=d), fit_n0([r for r in data if group(r) == g], d))
        for g in {group(r) for r in data})
    errors = _term_errors(data, structure, _party_line)
    return {"b": round(b, 2), "loss_with": round(loss_with, 4), "loss_without": round(loss_without, 4),
            "heldout_error": round(sum(errors.values()), 4),
            **(_paired(errors, heldout_errors(data, structure)) or {})}


def _term_errors(data: list[tuple], structure: str, term, only=None) -> dict[str, float]:
    """heldout_errors for log n0 = a[group] + b * term(pair): each thin
    member's pairs (those `only` keeps, if given) predicted by a and b
    fitted, and drift measured, without that member."""
    group = STRUCTURES[structure]
    errors: dict[str, float] = {}
    for m in sorted({r[1] for r in data if r[5] == "thin" and (only is None or only(r))}):
        train = [r for r in data if r[1] != m]
        dm = drifts(train)
        fb, fa, _ = fit_party_line(train, structure, dm, term)
        err = 0.0
        for r in data:
            if (r[1] == m and r[5] == "thin" and (r[0], r[6]) in dm and group(r) in fa
                    and (only is None or only(r))):
                n0 = float(np.exp(fa[group(r)] + fb * term(r)))
                k = dm[(r[0], r[6])][1 if r[8] else 0]
                err += (r[4] - float(relative_weight(r[2], n0)) * k * r[3]) ** 2
        errors[m] = err
    return errors


def trend_test(data: list[tuple], against: str) -> dict:
    """A time trend in n0, log n0 = a + b * decades since ERA_SPLIT: the
    split-free form of the era question, added after the era tests (so
    reported, not adopted, by this calibration). Its b, the half point it
    implies at the latest Congress with thin pairs, and, against the
    structure `against`, its paired difference over every thin member
    ("all") and over the latest era's ("latest", as era_test judges), the
    latter also without the member whose own difference favours the trend
    most."""
    # One line for every pair (pooled): the split-free form.
    d = drifts(data)
    b, a, _ = fit_party_line(data, "pooled", d, _decades)
    last = max(r[6] for r in data if r[5] == "thin")
    n0_last = float(np.exp(next(iter(a.values())) + b * (last - ERA_SPLIT) / 5)) if a else None
    late = STRUCTURES["era"]
    trend_late, base_late = (_term_errors(data, "pooled", _decades, only=late),
                             heldout_errors(data, against, only=late))
    most = min(trend_late, key=lambda m: trend_late[m] - base_late[m]) if trend_late else None
    rest = [r for r in data if r[1] != most]
    return {"b": round(b, 2), "latest_congress": last,
            "half_weight_votes_latest": round(half_point(n0_last), 1) if n0_last else None,
            "all": _paired(_term_errors(data, "pooled", _decades), heldout_errors(data, against)),
            "latest": _paired(trend_late, base_late),
            "latest_without_most_influential_member": _paired(
                _term_errors(rest, "pooled", _decades, only=late), heldout_errors(rest, against, only=late))}


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
    then the first, attended votes of the next Congress, from a member who
    left during it) and on
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
        if (rows := [r for r in usable if r[5] == "thin" and r[8] and r[9] and lo < r[2] <= hi])}
    out["switch_test"] = {
        "rule_shape": switch_test([r for r in usable if r[5] == "full" or (r[8] and r[9])]),
        # Every member who left, attended or not: how much the attendance
        # convention matters.
        "leavers": switch_test([r for r in usable if r[5] == "full" or (r[8] and len(r) > 10 and r[10])]),
        "all": switch_test(usable),
    }
    return out


def prior_until_votes(test: dict) -> float:
    """The count below which the flank rule reads the last full record: a
    full record (no switch short of one) unless, on the pairs shaped like
    the rule's case, a switch chosen on resampled members saves sides on the
    members left out in at least 95% of resamples (its 5th percentile above
    0); then that switch."""
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


# Vote bands of full records for full_slope_by_votes (presentation only).
FULL_BANDS = (200, 400, 600, 800, 1100, 10_000)


def full_slope_by_votes(data: list[tuple], drift: dict) -> dict[str, float]:
    """Slope of the later full position on drift * the earlier one, full
    pairs binned by the earlier record's votes (FULL_BANDS)."""
    out = {}
    for lo, hi in zip(FULL_BANDS, FULL_BANDS[1:]):
        rows = [r for r in data if r[5] == "full" and lo <= r[2] < hi and (r[0], r[6]) in drift]
        dx = np.array([drift[(r[0], r[6])][0] * r[3] for r in rows])
        y = np.array([r[4] for r in rows])
        if len(rows) and (dx ** 2).sum():
            out[f"{lo}-{hi}"] = round(float((dx * y).sum() / (dx ** 2).sum()), 3)
    return out


def thin_offset(data: list[tuple]) -> dict:
    """Whether a thin position is biased, not just noisy: the mean of
    thin - full / drift (the full position carried back by the pair's
    drift) over thin pairs, with its standard error, for all of them and
    for those under 50 votes and from 50 up; and the same over full pairs
    (full_baseline), which isn't exactly 0 with no bias (the drift is a
    slope through the origin), the reference the thin offset is read
    against. It tests a constant shift only: a proportional one looks
    like noise in these pairs, which the weight absorbs."""
    d = drifts(data)
    rows = [r for r in data if r[5] == "thin" and (r[0], r[6]) in d]

    def summary(sel):
        x = np.array([r[3] - r[4] / d[(r[0], r[6])][1 if r[8] else 0] for r in sel])
        if len(x) < 2:
            return None
        se = float(x.std(ddof=1) / np.sqrt(len(x)))
        return {"pairs": len(x), "mean": round(float(x.mean()), 4), "standard_error": round(se, 4),
                "upper_95": round(float(x.mean()) + 1.96 * se, 4)}
    full = [r for r in data if r[5] == "full" and (r[0], r[6]) in d]
    baseline = np.array([r[3] - r[4] / d[(r[0], r[6])][0] for r in full])
    return {"all": summary(rows), "under_50": summary([r for r in rows if r[2] < 50]),
            "from_50": summary([r for r in rows if r[2] >= 50]),
            "full_baseline": {"pairs": len(baseline), "mean": round(float(baseline.mean()), 4),
                              "standard_error": round(float(baseline.std(ddof=1) / np.sqrt(len(baseline))), 4)}
            if len(baseline) > 1 else None}


def calibrate(cache: pathlib.Path | None = None) -> dict:
    span = congresses()
    data, shares = pairs(cache, span)
    d = drifts(data)
    used = [r for r in data if r[5] != "full" and (r[0], r[6]) in d]
    last = max(r[6] for r in data) + 1
    base, comparison = choose_structure(data)
    # The structures that can't be applied, against the chosen one on every
    # thin pair: reported, never chosen.
    chosen_errors = heldout_errors(data, base)
    comparison["reported"] = {name: _paired(heldout_errors(data, name), chosen_errors)
                              for name in STRUCTURES if name not in USABLE}
    eras = era_test(data, base)
    if eras:
        # The other structure the score could apply, judged the same way.
        other = "pooled" if base == "chamber" else "chamber"
        eras[f"{other}_on_latest"] = _paired(heldout_errors(data, other, only=STRUCTURES["era"]),
                                             heldout_errors(data, base, only=STRUCTURES["era"]))
    forward = forward_test(data)
    structure = forward["chosen"]
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
        # One curve's interval, beside the shipped structure's: interval_90
        # holds the structure fixed, so it leaves out the choice of it.
        "half_weight_votes_pooled_interval_90": bootstrap(data, "pooled")["house"]["half_weight_votes"],
        "half_weight_votes_by_chamber": {
            name: round(half_point(c), 1) for name, c in fit_chambers(data, "chamber").items()},
        "half_weight_votes_by_chamber_interval_90": {
            name: b["half_weight_votes"] for name, b in bootstrap(data, "chamber").items()
            if name != "uncounted_weight"},
        "era_split": ERA_SPLIT,
        "forward_test": forward,
        "era_test": eras,
        "switcher_test": switcher_test(cache, span),
        "era_split_test": era_split_test(data, base),
        "half_weight_votes_by_era": {
            f"{e.start}-{e.stop - 1}": round(half_point(fit_n0([r for r in data if r[6] in e])), 1)
            for e in (range(FIRST_CONGRESS, ERA_SPLIT), range(ERA_SPLIT, last + 1))
        },
        "thin_offset": thin_offset(data),
        "thin_pairs_by_era": {
            f"{e.start}-{e.stop - 1}": {
                "thin": sum(1 for r in used if r[5] == "thin" and r[6] in e),
                "under_50_votes": sum(1 for r in used if r[5] == "thin" and r[6] in e and r[2] < 50)}
            for e in (range(FIRST_CONGRESS, ERA_SPLIT), range(ERA_SPLIT, last + 1))
        },
        # Each chamber-transition's drift, both ways (drifts), and the full
        # records' slope on the drift by their vote count: a record counts
        # in full from RELIABLE_VOTES on, so the slope should not rise above it.
        "drift_range": {way: [round(min(v[i] for v in d.values()), 3), round(max(v[i] for v in d.values()), 3)]
                        for i, way in enumerate(("earlier_thin", "later_thin"))},
        "full_slope_by_votes": full_slope_by_votes(data, d),
        "party_line_test": party_line_test(data, structure),
        "trend_test": trend_test(data, structure),
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
    # Every export read: the paired Congresses, and the ones either side of
    # them for who was seated before and after.
    used = range(FIRST_CONGRESS - 1, calibrated["calibrated_through"] + 2)
    cached = [f.stat().st_mtime for c in used for ch in "SH" for kind in ("members", "votes")
              if (f := args.cache / f"{ch}{c}_{kind}.csv").exists()] if args.cache else []
    dates = sorted({datetime.date.fromtimestamp(t).isoformat() for t in cached}) or [datetime.date.today().isoformat()]
    retrieved = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
    data = {
        "_source": (
            f"Voteview (voteview.com, Lewis et al.) member and vote exports, Senate and House: pairs from "
            f"Congresses {FIRST_CONGRESS}-{calibrated['calibrated_through']} (later ones still thin by the "
            f"calendar are left out), with the exports either side read for who was seated; exports "
            f"retrieved {retrieved}; "
            "regenerate with backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Members of the 50 states with positions in adjacent Congresses (a member who switched "
            "parties during a Congress is left out), each read from the "
            "party's center toward its flank, keyed by the transition they span: full = "
            "drift[chamber, transition] * weight(n) * thin, weight(n) = min(1, w(n) / "
            f"w({RELIABLE_VOTES})), w(n) = n / (n + n0); drift from each transition's pairs of full "
            f"records (both sides {RELIABLE_VOTES} or more scaled votes) in the pair's direction, n0 "
            f"the least-squares fit of the thin pairs on a grid to {N0_GRID[-1]:.0f} (reference_votes is the "
            "full-record count, the w(...) in the denominator); transitions with no full pairs "
            "and Congresses still thin by the calendar are left out. "
            "structure is the one forward_test chooses (each transition predicted from fits on the "
            "earlier ones; one curve unless one per chamber or the latest era's, split at era_split "
            "by convention, predicts the next Congress better by more than the paired standard error, "
            "a convention); forward_test reports chosen, members (the thin members predicted), each "
            "structure's error and paired difference from one "
            "curve (trend included, reported only), the chosen one's gain by transition, its comparison "
            "without the members it helps most (left out of it, and refitted) and the share of the gain "
            "each supplies (most_helped_share), last_three (the chosen "
            "structure against one curve on the last three predicted transitions), the era comparison "
            "at every split it can test (era_at_every_split, each with adopted: whether the rule would adopt "
            "it), a window of the last FORWARD_WINDOW "
            f"({FORWARD_WINDOW}, a convention) transitions with no split (window) and the window "
            "against the era curve (window_against_era), and the share of the thin pairs one curve was "
            "fitted on from before the split, by predicted transition (training_before_split); trend "
            "also has its gain by transition, whether each forward fit reached an end of a search grid "
            "(fit_at_grid_end), and inside_grids, from the first transition from which every fit is "
            "inside the grids, the trend against one curve and against the era curve. "
            "Leave-one-member-out comparisons, reported: heldout_error is the squared error of each "
            "thin member's pairs fitted without that member, by structure; structure_test is one "
            "curve against one per chamber (each with heldout_error, above_best, its paired "
            "difference from the better, and its standard_error), with reported giving direction, attendance (attended "
            "arrivals and departures against the rest) and era (each era's curve on its own members) "
            "against its choice, paired; era_test judges the latest era's curve against that choice "
            "on the latest era's thin pairs alone (members; adopted, whether it would be), with its "
            "half_weight_votes and the other usable "
            "structure judged the same way (chamber_on_latest while the leave-one-out comparison, structure_test, keeps one curve); "
            "era_split_test repeats it at every split (splits; adopted_at the number of splits that adopt, of the "
            "number tested; at an adopting split, with its n0 leaving one "
            "member out in turn, and the test rerun without the member whose absence moves n0 most, "
            "none if no member moves it); trend_test is the split-free form, log n0 linear in decades "
            "since the split, added after the era tests and reported, not adopted (b its slope per "
            "decade, half_weight_votes_latest its half point at latest_congress, and all, latest and "
            "latest_without_most_influential_member its paired comparisons); party_line_test is "
            "the rejected term (log n0 linear in the Congress's party-line share) added to the shipped "
            "structure's grouping, its above and standard_error the paired difference from that "
            "grouping (under era, each era's own curve), b the term's slope, loss_with and loss_without "
            "the in-sample losses and heldout_error the held-out error with it. "
            "chambers' thin_pairs count every thin pair; half_weight_votes is where weight(n) = 0.5, "
            "never above reference_votes / 2 (as n0 grows the curve tends to n / reference_votes), and "
            f"at most about {half_point(N0_GRID[-1]):.0f} on the n0 grid (to {N0_GRID[-1]:.0f}), so an interval "
            "reaching the grid's end is "
            "open above; half_weight_votes_pooled, _by_chamber and "
            "_by_era are the half points one curve, a curve per chamber and a curve per era would give; "
            "interval_90 is the 5th-95th percentile over members resampled with the structure held "
            "fixed (so it leaves out the choice of structure; half_weight_votes_pooled_interval_90 and "
            "half_weight_votes_by_chamber_interval_90 are the other structures', beside it); "
            "uncounted_weight is the slope, for both chambers, for positions published with no count "
            "(or 0) but a career DW-NOMINATE position, and uncounted_weight_leave_one_out its range "
            "with each of its members left out in turn; thin_offset is the mean of thin - full / drift "
            "over thin pairs (all, under 50 votes, from 50 up) with its standard error and upper_95, the "
            "mean plus 1.96 standard errors, and full_baseline "
            "the same over full pairs (not exactly 0 without bias): a test for a constant shift, not a "
            "proportional one; thin_pairs_by_era counts each era's thin "
            "pairs and those under 50 votes; drift_range is the lowest and highest chamber-transition "
            "drift in each direction (earlier_thin, later_thin); full_slope_by_votes is the full records' slope on the drift by their vote count; "
            "party_line_share_by_congress is each Congress's share of party-line roll calls; pairs "
            "counts the pairs used and calibrated_through the last Congress paired. "
            "switcher_test compares, for a member who switched parties during a Congress, the latest "
            "record, the longer one and their vote-weighted mean against the next Congress's full "
            "record, paired differences over people (the pipeline reads the latest, a stated choice "
            "the evidence can't settle): members and people counted, each record's mean squared gap, "
            "latest_minus_longer and latest_minus_weighted the paired means, latest_not_longer the "
            "member-Congresses whose latest record is not their longer one and latest_closer those of them it "
            "places nearer. "
            "prior_test compares, for the flank rule, a last full record and a thin record as "
            "evidence of a member's side of their party, centered as the rule centers, by band of the "
            "thin record's votes (each band, and full for full-full pairs, gives pairs, same_side: the "
            "share on the same side of their party in both records, and mean_squared_gap), with "
            "full_by_distance (how often a full record keeps its side, by "
            "distance from the party's center) and rule_shape_bands (the bands on pairs shaped like "
            "the rule's case); prior_until_votes is the count below which the rule reads the last "
            "full record (a full record unless a switch short of one, chosen on resampled members, "
            "saves sides on the members left out in 95% of resamples, on pairs shaped like the rule's "
            "case: a full record, then the thin record of a member who left during the next Congress, "
            "absent the Congress after, missing no more of the roll calls in their span than nine in "
            "ten of that Congress's full records, a convention); switch_test's rule_shape is that "
            "test (null when too few such pairs support its model), and it also reports every member "
            "who left (leavers) and every thin pair (all), each with switch_votes (the best switch), "
            "saved (the share of sides it saves in sample), saved_out_of_bag (its 5th, 50th and 95th "
            "percentiles on members left out), draws_judged, share_saving (how often it saves any) and "
            "the thin pairs behind it, all and over 100 votes; crossover_if_independent is the rejected "
            "unstratified crossing, reported only"
        ),
        **calibrated,
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out}: {data['structure']}, {data['chambers']}, intervals {data['interval_90']}, "
          f"held-out error {data['heldout_error']}, party-line test {data['party_line_test']}")


if __name__ == "__main__":
    main()
