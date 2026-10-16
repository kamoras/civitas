"""The map's own ranges against the count, and each seat's swing from its
usual vote, for a held election.

Both read the election's archive only (app/election_archive.py): the map
as its last campaign build left it, frozen on election day, and the
record's counts as the states reported them. Nothing is refitted and no
range is rebuilt: a seat is checked against the 80% and 95% ranges it was
published with, so the check can't learn from the result (no look-ahead).

A count enters only once its source itself lists it as official
(RaceResult.official, the flag "Civitas never calls a race" rests on).
Until then the seat is counted as waiting, never graded on a count that
can still move. Even then the count only ever "leads": this measures the
map, it calls no race.

Units are the map's own (build.UNITS): a margin is the Republican minus
the Democratic share of the two-party vote, in points (negative = D),
computed from the count exactly as the fit's training results were
(scripts/fit_forecast_model.py: the top two must be a Democrat and a
Republican). A usual vote is a lean, half a margin's scale, so it is
doubled to compare: "a place that leans D+8 has a margin of D+16 when the
country splits evenly". The swing is the margin minus that doubled lean.
"""

from __future__ import annotations

import csv
import io
import statistics

from scipy.stats import beta

from app.forecast.build import UNITS, bin_of, margin_label, range_label
from app.forecast.polls import POLL_ATTRIBUTION, POLL_LICENSE, POLL_LICENSE_URL

# What each seat's count is, for the check: "official" (the source lists it
# official), "counting" (a count, not yet official), "no_count" (none read:
# a state whose feed Civitas doesn't read, a ranked-choice state, or a
# contest the feed never matched), "not_two_party" (official, but its top two
# are not a Democrat and a Republican: the fit never saw such a race).
COUNT_STATES = ("official", "counting", "no_count", "not_two_party")
# The levels the map published, and the interval's own level.
LEVELS = (80, 95)
INTERVAL_LEVEL = 0.95
UNLABELLED, UNLABELLED_SHORT = "unlabelled", "basis not recorded"


def clopper_pearson(k: int, n: int, level: float = INTERVAL_LEVEL) -> list[float] | None:
    """The exact binomial interval for k of n (Clopper & Pearson 1934), as
    shares; None for no trials."""
    if n == 0:
        return None
    a = (1 - level) / 2
    lo = 0.0 if k == 0 else float(beta.ppf(a, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - a, k + 1, n - k))
    return [round(lo, 3), round(hi, 3)]


def two_party_margin(result: dict) -> float | None:
    """R minus D share of the two-party vote, in points, when the count's top
    two are one Democrat and one Republican; else None."""
    top = sorted(result.get("candidates") or [], key=lambda c: -(c.get("votes") or 0))[:2]
    sides = {c.get("party"): c.get("votes") or 0 for c in top}
    if len(top) < 2 or set(sides) != {"DEM", "REP"} or not sum(sides.values()):
        return None
    return 100 * (sides["REP"] - sides["DEM"]) / (sides["REP"] + sides["DEM"])


def _count_state(result: dict | None, margin: float | None) -> str:
    if result is None:
        return "no_count"
    if not result.get("official"):
        return "counting"
    return "official" if margin is not None else "not_two_party"


def _seat(chamber: str, s: dict, result: dict | None) -> dict:
    margin = two_party_margin(result) if result else None
    count = _count_state(result, margin)
    ranged = s["status"] == "range"
    official = count == "official"
    out = {
        "id": s["id"], "chamber": chamber, "state": s["state"], "stateName": s["stateName"],
        "district": s.get("district"), "special": s.get("special", False),
        "mapStatus": s["status"], "count": count,
        "dem": s.get("dem"), "rep": s.get("rep"),
        "lean": s.get("lean"), "leanLabel": s.get("leanLabel"),
        # A map built before ranges were labelled by basis records none.
        "rangeBasis": (s.get("rangeBasis") or UNLABELLED) if ranged else None,
        "rangeBasisShort": (s.get("rangeBasisShort") or UNLABELLED_SHORT) if ranged else None,
        "range80": s.get("range80") if ranged else None,
        "range95": s.get("range95") if ranged else None,
        "range80Label": s.get("range80Label") if ranged else None,
        "range95Label": range_label(*s["range95"]) if ranged else None,
        "sourceName": (result or {}).get("sourceName"), "sourceUrl": (result or {}).get("sourceUrl"),
        "fetchedAt": (result or {}).get("fetchedAt"),
        "margin": None, "marginLabel": None, "usualMargin": None, "usualMarginLabel": None,
        "swing": None, "swingLabel": None, "swingBin": None,
        "inside80": None, "inside95": None, "fromCentre": None,
    }
    if s.get("lean") is not None:
        usual = 2 * s["lean"]
        out.update(usualMargin=round(usual, 2), usualMarginLabel=margin_label(usual))
    if not official:
        return out
    out.update(margin=round(margin, 2), marginLabel=margin_label(margin))
    if out["usualMargin"] is not None:
        swing = margin - 2 * s["lean"]
        out.update(swing=round(swing, 2), swingLabel=margin_label(swing), swingBin=bin_of(swing))
    if ranged:
        out.update(inside80=s["range80"][0] <= margin <= s["range80"][1],
                   inside95=s["range95"][0] <= margin <= s["range95"][1],
                   fromCentre=None if s.get("mean") is None else round(margin - s["mean"], 2))
    return out


def _cell(seats: list[dict]) -> dict:
    graded = [s for s in seats if s["inside80"] is not None]
    n = len(graded)
    cell = {"graded": n}
    for level in LEVELS:
        held = sum(1 for s in graded if s[f"inside{level}"])
        cell[f"held{level}"] = held
        cell[f"share{level}"] = round(held / n, 3) if n else None
        cell[f"interval{level}"] = clopper_pearson(held, n)
    return cell


def _promised(calibration: dict) -> dict:
    """What the map said its ranges would do: the nominal levels, and the
    held-out coverage it published beside them (leave-one-cycle-out, and
    the forward check on the newest cycle it never saw)."""
    fw = calibration.get("forward")
    return {
        "nominal80": 0.8, "nominal95": 0.95,
        "heldOut": {"cycles": calibration.get("cycles"), "races": calibration.get("races"),
                    "coverage80": calibration.get("coverage80"), "coverage95": calibration.get("coverage95"),
                    "coverage80Fundamentals": calibration.get("coverage80Fundamentals")},
        "forward": None if not fw else {"cycle": fw.get("cycle"), "races": fw.get("races"),
                                        "coverage80": fw.get("coverage80"), "coverage95": fw.get("coverage95")},
    }


def _swing_summary(seats: list[dict]) -> dict:
    swings = [s["swing"] for s in seats if s["swing"] is not None]
    if not swings:
        return {"seats": 0, "mean": None, "meanLabel": None, "median": None, "medianLabel": None}
    mean, median = statistics.fmean(swings), statistics.median(swings)
    return {"seats": len(swings), "mean": round(mean, 2), "meanLabel": margin_label(mean),
            "median": round(median, 2), "medianLabel": margin_label(median)}


def grade(map_data: dict, record: dict) -> dict:
    """The check and the swing map for one held election, per chamber."""
    results = {r["id"]: r["result"] for r in record.get("races", []) if r.get("result")}
    chambers = {}
    for chamber, c in map_data.get("chambers", {}).items():
        seats = [_seat(chamber, s, results.get(s["id"])) for s in c.get("seats", [])]
        ranged = [s for s in seats if s["mapStatus"] == "range"]
        bases = sorted({s["rangeBasis"] for s in ranged})
        chambers[chamber] = {
            # "N of M races certified": the seats the map gave a range,
            # and how many of them have a count its source lists official.
            "ranged": len(ranged),
            "certified": sum(1 for s in ranged if s["count"] in ("official", "not_two_party")),
            "counts": {k: sum(1 for s in ranged if s["count"] == k) for k in COUNT_STATES},
            "all": _cell(ranged),
            "byBasis": [dict(_cell([s for s in ranged if s["rangeBasis"] == b]), basis=b,
                             label=next(s["rangeBasisShort"] for s in ranged if s["rangeBasis"] == b),
                             ranged=sum(1 for s in ranged if s["rangeBasis"] == b))
                        for b in bases],
            "promised": _promised(c.get("calibration") or {}),
            # Furthest outside first: how far the margin fell past the
            # 80% range's nearer edge, in margin points.
            "misses": sorted((dict(s, outsideBy=round(max(s["range80"][0] - s["margin"],
                                                          s["margin"] - s["range80"][1]), 2))
                              for s in ranged if s["inside80"] is False),
                             key=lambda s: -s["outsideBy"]),
            "swing": _swing_summary(seats),
            "seats": seats,
        }
    return {
        "cycle": map_data.get("cycle") or record.get("cycle"),
        "electionDay": record.get("electionDay"),
        "mapAsOf": map_data.get("asOf"),
        "units": UNITS,
        "bins": map_data.get("bins"),
        "intervalLevel": INTERVAL_LEVEL,
        "chambers": chambers,
    }


def summary(graded: dict) -> dict:
    """Both chambers in a line, for the elections hub's card."""
    cs = graded["chambers"].values()
    out = {"ranged": sum(c["ranged"] for c in cs), "certified": sum(c["certified"] for c in cs),
           "official": sum(c["counts"]["official"] for c in cs),
           "graded": sum(c["all"]["graded"] for c in cs)}
    for level in LEVELS:
        out[f"held{level}"] = sum(c["all"][f"held{level}"] for c in cs)
    out["swingSeats"] = sum(c["swing"]["seats"] for c in cs)
    return out


CSV_COLUMNS = (
    "chamber", "seat", "state", "district", "special", "count_status", "democrat", "republican",
    "usual_vote_lean", "usual_vote_as_margin", "margin", "swing", "range_basis", "range80_low", "range80_high",
    "range95_low", "range95_high", "inside80", "inside95", "margin_minus_range_centre", "count_source",
    "count_source_url", "count_read_at",
)


def csv_text(graded: dict) -> str:
    out = io.StringIO()
    out.write(f"# Civitas {graded['cycle']}: each seat's official count against the pre-election map as it stood "
              f"on {graded['mapAsOf']}. A measurement, not a call: a count is used only once its source lists it "
              "official.\n")
    out.write("# margin: Republican minus Democratic share of the two-party vote, in points (negative = D). "
              "usual_vote_lean is half that scale; usual_vote_as_margin is twice it, the margin the seat gives "
              "when the country splits evenly. swing = margin - usual_vote_as_margin (negative = toward D).\n")
    out.write("# Counts: each state's own results system, as the archive copied it. Usual vote: Civitas's "
              "computed lean from official returns (app/data/state_pvi.json, app/data/district_lean.json).\n")
    out.write(f"# Ranges: built on polls from {POLL_ATTRIBUTION}, {POLL_LICENSE} ({POLL_LICENSE_URL}); the range "
              f"columns are shared under the same licence, with attribution.\n")
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)

    def blank(v):
        return "" if v is None else v

    for chamber, c in graded["chambers"].items():
        for s in c["seats"]:
            r80, r95 = s["range80"] or (None, None), s["range95"] or (None, None)
            w.writerow([blank(v) for v in (
                chamber, s["id"], s["state"], s["district"], s["special"], s["count"], s["dem"], s["rep"],
                s["lean"], s["usualMargin"], s["margin"], s["swing"], s["rangeBasis"], *r80, *r95,
                s["inside80"], s["inside95"], s["fromCentre"], s["sourceName"], s["sourceUrl"], s["fetchedAt"],
            )])
    return out.getvalue()
