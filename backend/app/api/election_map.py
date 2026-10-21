"""The general-election map: each seat's structural lean and, where
polls exist, the poll-informed range of its vote margin.

Ranges, not results, and never a race call: the file holds no chance of
anyone winning. Served from /data/election_map.json, which the pipeline
process writes (app.forecast.build). From election day on the map yields
to the results map: both routes answer 404 once active_election() leaves
the campaign phase. Its final pre-election state is copied into the
election's archive on election day (app/election_archive.py,
GET /api/elections/archive/{cycle}/map).

The poll figures and everything built on them are derived from Wikipedia
(Wikipedia contributors) under CC BY-SA 4.0, and every response says so.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.election_phase import CAMPAIGN, active_election
from app.file_cache import new_reload_lock, reload_if_moved
from app.forecast.build import LEANS_MAX, map_path
from app.readout import mark, seat_name
from app.forecast.polls import POLL_ATTRIBUTION, POLL_LICENSE, POLL_LICENSE_URL
from app.ordinals import ordinal
from app.time_utils import utcnow

router = APIRouter(prefix="/elections")

_lock = new_reload_lock()
_cache: dict | None = None
_stamp = None

# The refresh runs every three hours; a map older than three missed runs
# is marked stale on the page (the build alerts on each failure).
STALE_AFTER = timedelta(hours=9)
HIDDEN = ("The election map is shown during the campaign only; from election day on, the results map replaces it, "
          "and the map as it stood before the election is kept in that election's archive.")
NOT_BUILT = "The election map has not been built yet."


def _load() -> dict | None:
    global _cache, _stamp
    path = map_path()

    def read():
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    with _lock:
        _cache, _stamp = reload_if_moved([path], _cache, _stamp, read)
        return _cache


def current_map(db: Session | None = None) -> dict:
    """The map's data while it may be shown; HTTPException otherwise. A
    caller with a session passes it: without one, active_election opens its
    own in the results window."""
    election = active_election(db)
    if election.phase != CAMPAIGN:
        raise HTTPException(status_code=404, detail=HIDDEN)
    data = _load()
    if not data:
        raise HTTPException(status_code=503, detail=NOT_BUILT)
    if data.get("electionDay") != election.election_day.isoformat():
        raise HTTPException(status_code=404, detail=HIDDEN)
    as_of = datetime.fromisoformat(data["asOf"].replace("Z", "+00:00")).replace(tzinfo=None)
    # The cycle the page names in its titles, from the election itself (a
    # file built before the field existed has the same election day).
    return dict(data, cycle=election.cycle, stale=utcnow() - as_of > STALE_AFTER)


VIEWS = ("usual", "polls", "diff")
_CLOSE_USUAL = {"close", "D_leans", "R_leans"}


def _seat_name(s: dict) -> str:
    return seat_name(s["stateName"], s.get("district"), s["special"])


def _seat_words(s: dict, view: str) -> str:
    """One seat in the map's view, as its readout says it: the usual vote,
    the poll range and whether the polls point away from it, in the words
    the build wrote. Ranges, never a call."""
    usual = f"usual vote {s['leanLabel']} lean" if s.get("leanLabel") else "no usual-vote figure"
    if s["status"] != "range":
        return f"{usual}. {s.get('reason') or ''}".strip()
    if view == "usual" or not s.get("range80Label"):
        return usual
    polls = (f"{s.get('rangeBasisLabel') or 'Margin range'}: {s['range80Label']}"
             f"{', too close to tell' if s.get('close') else ''}")
    d = s.get("difference") or {"strength": 0, "seatPolls": True}
    diff = (f", polls point toward {d['direction']}" if d["strength"] > 0
            else "" if d["seatPolls"] else ", no seat poll") if view == "diff" else ""
    return f"{polls}; {usual}{diff}"


def leans_max(data: dict) -> float:
    """The map's "leans" bound, from the file; an archived map written
    before the file carried it uses the build's."""
    return (data.get("bins") or {}).get("leansMax", LEANS_MAX)


def state_marks(chamber: str, seats: list[dict], leans_max: float) -> dict[str, dict[str, dict]]:
    """Each state's readout on the tile map, per view: a Senate state names
    its race (or both, where it has two); a House state its seat count and
    how many are close or where polls disagree. Counted here, so the page
    counts nothing (ElectionMap)."""
    by_state: dict[str, list[dict]] = {}
    for s in seats:
        by_state.setdefault(s["state"], []).append(s)
    out: dict[str, dict[str, dict]] = {v: {} for v in VIEWS}
    for st, group in by_state.items():
        name = group[0]["stateName"]
        for view in VIEWS:
            if chamber == "house":
                n = len(group)
                if view == "usual":
                    k = sum(s.get("usualBin") in _CLOSE_USUAL for s in group)
                    context = f"{k} within {leans_max:g} points of even in the usual vote"
                elif view == "polls":
                    k = sum(s["status"] == "range" and s.get("pollsBin") == "close" for s in group)
                    context = f"{k} too close to tell"
                else:
                    k = sum(s["status"] == "range" and (s.get("difference") or {}).get("strength", 0) > 0
                            for s in group)
                    context = f"{k} where polls disagree with the usual vote"
                out[view][st] = mark(st, name, f"{n} {'seat' if n == 1 else 'seats'}", context=context)
            elif len(group) == 1:
                out[view][st] = mark(st, _seat_name(group[0]), _seat_words(group[0], view))
            else:
                out[view][st] = mark(st, name, f"{len(group)} races: " + "; ".join(
                    f"{_seat_name(s)}, {_seat_words(s, view)}" for s in group))
    return out


@router.get("/map")
def election_map(chamber: str | None = Query(None, pattern="^(senate|house)$",
                                              description="Only this chamber's seats (the page loads one at a time)")):
    """Every Senate and House seat on the active election's ballot: its usual vote (structural lean) and, where
    Democrats and Republicans both have a nominee, the poll-informed 80%
    range of the margin, with the polls behind it and the method's
    held-out calibration. `chamber` returns one chamber's, with
    `chambersAvailable` naming both."""
    data = current_map()
    chambers = {
        name: dict(c, seats=[dict(s, polls={k: v for k, v in s["polls"].items() if k != "all"}) if "polls" in s else s
                             for s in c["seats"]],
                   stateMarks=state_marks(name, c["seats"], leans_max(data)))
        for name, c in data["chambers"].items() if chamber in (None, name)
    }
    data = dict(data, chambersAvailable=list(data["chambers"]))
    return cached_json(dict(data, chambers=chambers), max_age=CACHE_TTL_LIST_S)


def seat_summary(chamber: str, s: dict) -> dict:
    """One seat in a line, for the pages that link to the map (a state's
    ballot, a member's profile): every figure and label the map's build
    wrote, none re-derived."""
    ranged = s["status"] == "range"
    return {
        "id": s["id"], "chamber": chamber, "state": s["state"], "district": s.get("district"),
        "special": s["special"], "leanLabel": s["leanLabel"], "status": s["status"],
        "range80Label": s["range80Label"] if ranged else None,
        "close": s["close"] if ranged else None,
        "reason": None if ranged else s.get("reason"),
    }


@router.get("/map/seats")
def election_map_seats(
    state: str | None = Query(None, pattern="^[A-Z]{2}$", description="A state's seats, by postal code"),
    seat: str | None = Query(None, max_length=40, description="One seat, by its race id"),
):
    """A state's seats, or one seat, from the map in a line each (usual vote
    and the 80% poll range), for the pages that link to the map. 404 out of
    the campaign, as the map is."""
    if not (state or seat):
        raise HTTPException(status_code=422, detail="state or seat is required")
    data = current_map()
    seats = [seat_summary(chamber, s) for chamber, c in data["chambers"].items() for s in c["seats"]
             if (state is None or s["state"] == state) and (seat is None or s["id"] == seat)]
    return cached_json({
        "cycle": data["cycle"], "asOf": data["asOf"], "stale": data["stale"], "seats": seats,
        "pollSource": {"attribution": POLL_ATTRIBUTION, "license": POLL_LICENSE, "url": POLL_LICENSE_URL},
    }, max_age=CACHE_TTL_LIST_S)


CSV_COLUMNS = (
    "chamber", "seat", "state", "district", "special", "democrat", "republican", "incumbent_party",
    "usual_vote_lean", "usual_vote_label", "status", "range_basis", "structural_margin", "range80_low", "range80_high",
    "range95_low", "range95_high", "poll_average", "polls_used", "polls_listed", "latest_poll", "poll_source",
    "differs_from_usual", "difference_direction",
)


def csv_text(data: dict) -> str:
    out = io.StringIO()
    out.write(f"# Civitas {data['cycle']} general-election map, as of {data['asOf']}. Ranges, not results. "
              "Not a race call.\n")
    out.write("# Margins are Republican minus Democratic share of the two-party vote, in points (negative = D). "
              "The usual-vote lean is the seat's Democratic two-party share minus the nation's (negated, so "
              "positive = R): a different unit, half a margin's scale.\n")
    out.write("# range_basis: polls (the seat's polls are in the range), no_poll (none in the poll window: usual "
              "vote, incumbency and national swing alone), before_window (no poll enters any range yet).\n")
    out.write("# Usual vote: Civitas's computed lean from official returns (Senate: app/data/state_pvi.json; "
              f"House: app/data/district_lean.json, {ordinal(data['sources']['lean']['house']['congress'])} "
              "Congress lines).\n")
    out.write(f"# Polls: {POLL_ATTRIBUTION}, {POLL_LICENSE} ({POLL_LICENSE_URL}). The poll-derived columns are "
              f"shared under the same licence; reuse them with attribution and under {POLL_LICENSE}.\n")
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for chamber, c in data["chambers"].items():
        for s in c["seats"]:
            _row(w, chamber, s)
    return out.getvalue()


def _row(w, chamber: str, s: dict) -> None:
    ranged = s["status"] == "range"
    w.writerow([
        chamber, s["id"], s["state"], "" if s.get("district") is None else s["district"], s["special"],
        s.get("dem") or "", s.get("rep") or "", s.get("incumbentParty") or "",
        s["lean"], s["leanLabel"], s["status"], s.get("rangeBasis", "") if ranged else "",
        s.get("prior", "") if ranged else "",
        *(s["range80"] if ranged else ("", "")), *(s["range95"] if ranged else ("", "")),
        s.get("pollAverage") if ranged and s.get("pollAverage") is not None else "",
        s["polls"]["used"] if ranged else "", s.get("polls", {}).get("count", ""),
        (s["polls"].get("latest") or "") if ranged else "",
        (s.get("pollSource") or {}).get("state", ""),
        s["difference"]["strength"] if ranged else "",
        (s["difference"]["direction"] or "") if ranged else "",
    ])


@router.get("/map.csv")
def election_map_csv():
    """The map as a table, one row per seat, with the licence notice."""
    data = current_map()
    return Response(
        content=csv_text(data), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": f"public, max-age={CACHE_TTL_LIST_S}, stale-while-revalidate={CACHE_TTL_LIST_S}",
                 "Content-Disposition": f'attachment; filename="civitas-{data["cycle"]}-election-map.csv"'},
    )
