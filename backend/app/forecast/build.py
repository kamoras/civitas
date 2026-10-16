"""Build the general-election map's data: every Senate seat and every
House seat on the ballot, with its structural lean and, where Democrats
and Republicans both have a nominee, the poll-informed range of the vote
margin. Each chamber has its own fitted parameters and its own held-out
checks (app/data/forecast_model.json, forecast_house_model.json).

Runs in the pipeline process (scheduler._election_map_refresh) and
writes /data/election_map.json; the API serves that file
(api/elections.py, GET /api/elections/map). It reads the database
(races and confirmed candidates) and writes nothing to it.

A range is never a call: the map and the API show where the margin is
likely to fall, with how often such ranges held the result on cycles the
method never saw. Chances are not part of the output.

Hidden from election day on: the build stops once active_election()
leaves the campaign phase, and the API refuses the file from then on,
so this map yields to the results map.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app import data_refresh, district_lean
from app.atomic_write import runtime_data_path, write_text_atomic
from app.election_calendar import last_certified_general
from app.election_phase import CAMPAIGN, active_election
from app.forecast import model
from app.forecast import polls as wiki
from app.forecast import training_polls
from app.models import Race
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.ordinals import ordinal
from app.pipeline.fetch.district_pvi import congress_for_election
from app.state_names import STATE_NAMES
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[1] / "data"
# The fits and their training rows: refreshed by app/data_refresh.py into
# the data volume, read through data_refresh.read (the checked-in copy
# until the first refit).
MODEL_FILES = {"senate": "forecast_model.json", "house": "forecast_house_model.json"}
TRAINING_FILES = {"senate": "forecast_training.json", "house": "forecast_house_training.json"}
OFFICES = {"senate": "S", "house": "H"}
STATE_PVI_PATH = DATA / "state_pvi.json"
MAP_FILE = "election_map.json"
# Display bins of the map, in points (the legend and the tiles read the
# bin the API names, so these edges live here alone): under LEANS_MAX a
# seat "leans", under USUALLY_MAX it "usually" goes that way, beyond it
# "strongly". A display grouping, not a fitted value.
LEANS_MAX = 5.0
USUALLY_MAX = 15.0
SCHEMA_VERSION = 2
ALERT_CONDITION = "election-map-refresh"
HOUSE_LINES_CONDITION = "election-map-house-lines"
TRAINING_CONDITION = "election-map-training-stale"
# The newest polls a seat's detail lists (each with its source); every poll
# in the average still counts, and "used" says how many there are.
POLLS_SHOWN = 6


def map_path() -> str:
    return runtime_data_path(MAP_FILE)


def house_lines(cycle: int) -> int:
    """The Congress whose district lines a cycle's House election is fought
    on: the one it seats, the same function the race page reads
    (api/elections._usual_vote), so the map and the race page can't put a
    seat on different lines."""
    return congress_for_election(cycle)


def house_article_prefix(cycle: int) -> str:
    return f"{cycle} United States House of Representatives election"


def generic_article(cycle: int) -> str:
    """The cycle's national House article, whose aggregate table holds the
    generic-ballot averages."""
    return f"{house_article_prefix(cycle)}s"


def article_title(race: Race) -> str:
    return (f"{race.cycle_year} United States Senate {'special ' if race.is_special else ''}"
            f"election in {STATE_NAMES[race.state]}")


def house_articles(race: Race, titles: list[str]) -> list[str]:
    """The articles that may hold a House seat's polls: an at-large seat's
    own article ("...election in Alaska"), or the state's article(s)
    ("...elections in Ohio", California's two halves), from the list of
    titles Wikipedia holds under house_article_prefix(cycle)."""
    name = STATE_NAMES[race.state]
    prefix = house_article_prefix(race.cycle_year)
    if race.district == 0:
        return [f"{prefix} in {name}"]
    stem = f"{prefix}s in {name}"
    return [t for t in titles if t == stem or t.startswith(stem + " (")] or [stem]


def _lean_table() -> tuple[dict[str, float], dict]:
    """{state: unrounded lean} from state_pvi.json's own stored counts —
    the same numbers and formula as the state PVIs the site shows."""
    spvi = json.loads(STATE_PVI_PATH.read_text())
    inputs = spvi["inputs"]
    older, newer = sorted(inputs)[-2:]
    nation = {y: {k: sum(v[k] for v in inputs[y].values()) for k in ("D", "R")} for y in (older, newer)}
    leans = {st: model.two_party_lean(inputs[older][st], inputs[newer][st], nation[older], nation[newer])
             for st in inputs[newer]}
    return leans, {"source": spvi["_source"], "window": spvi["_window"], "file": "app/data/state_pvi.json"}


def _house_leans(cycle: int) -> tuple[dict[str, float], dict]:
    """The computed lean on the lines the cycle is fought on. Empty for a
    Congress district_lean.json has no table for (computed_lean never hands
    back another Congress's lines): every seat is then "no_lean", as the
    race page shows no usual vote, and refresh() raises an ops alert."""
    congress = house_lines(cycle)
    table = district_lean.computed_lean(congress)
    meta = district_lean.meta() or {}
    return table, {"source": meta.get("source"), "window": meta.get("window"), "file": "app/data/district_lean.json",
                   "congress": congress}


lean_label = margin_label = district_lean.lean_label


def bin_of(value: float) -> str:
    """'close', or '<side>_<leans|usually|strongly>', decided on the value as
    it is written (rounded to a whole point), so a label and its colour can
    never disagree: "R+5" is never drawn in the "R <5" colour, nor "EVEN" in
    a party's."""
    r = round(value)
    if r == 0:
        return "close"
    side = "R" if r > 0 else "D"
    return f"{side}_{'leans' if abs(r) < LEANS_MAX else 'usually' if abs(r) < USUALLY_MAX else 'strongly'}"


def range_close(low: float, high: float) -> bool:
    """Whether a range, as written, reaches even: "D+3 to EVEN" does."""
    return round(low) <= 0 <= round(high)


def range_bin(low: float, high: float) -> str:
    """A range is binned by its edge nearest even, so a wide range reads
    paler and one that crosses even is 'close' (value-suppressing
    uncertainty; Correll, Moritz & Heer 2018)."""
    if range_close(low, high):
        return "close"
    return bin_of(low if round(low) > 0 else high)


def range_label(low: float, high: float) -> str:
    """"D+8 to R+2", the Democratic side first."""
    return f"{margin_label(low)} to {margin_label(high)}"


def _nominee(cands: list, party: str):
    """The party's confirmed nominee: one confirmed candidate, or among
    several (a top-four primary sends more than one), the incumbent, else
    the one with the most contributions."""
    mine = [c for c in cands if c.party == party and c.confirmed_general]
    if not mine:
        return None
    return sorted(mine, key=lambda c: (c.incumbent_challenge != "I", -(c.contributions or 0)))[0]


def _display_name(c) -> str:
    return c.ballot_name or c.name


def _national(rev: dict | None, as_of: date, cycle: int) -> dict:
    if rev is None:
        raise wiki.GateFailure("Wikipedia could not be reached")
    rows = wiki.generic_ballot(rev["content"], as_of, cycle)
    margin = sum(r["rep"] - r["dem"] for r in rows) / len(rows)
    return {"margin": round(margin, 2), "label": margin_label(margin), "averages": rows,
            "updated": max(r["updated"] for r in rows), "article": rev["title"], "revid": rev["revid"],
            "timestamp": rev["timestamp"], "url": wiki.revision_url(rev["title"], rev["revid"])}


class _Unreachable(Exception):
    """Wikipedia didn't answer (fetch_current returned None, not {})."""


def _previous(election_day: date) -> dict:
    """The last map written for this election; {} for another election's,
    so a new cycle's first build never keeps the last cycle's national
    figure or polls."""
    try:
        data = json.loads(Path(map_path()).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("electionDay") == election_day.isoformat() else {}


def _calibration(evaluation: dict) -> dict:
    fw = evaluation.get("forward")
    return {
        "cycles": [evaluation["cycles"][0], evaluation["cycles"][-1]],
        "races": evaluation["races"],
        "coverage80": evaluation["coverage80"],
        "coverage95": evaluation["coverage95"],
        "coverage80Fundamentals": evaluation.get("coverage80Fundamentals"),
        "meanAbsError": evaluation["meanAbsError"],
        "table": evaluation["calibration"],
        "seatTotals": evaluation["seatTotals"],
        # Cycles whose Democratic seat count fell outside the central 90%
        # band that independent races would give (1.645 standard
        # deviations): the measured reason the map adds up no total.
        "seatTotalsOutside90": sum(
            1 for t in evaluation["seatTotals"]
            if abs(t["actualDem"] - t["expectedDem"]) > 1.6448536269514722 * t["independentSd"]),
        "brier": {"combined": evaluation["polled"]["combined"]["brier"],
                  "fundamentals": evaluation["polled"]["fundamentals"]["brier"],
                  "pollsOnly": evaluation["polled"]["pollsOnly"]["brier"],
                  "polledRaces": evaluation["polledRaces"]},
        # The newest cycle predicted by a fit on the cycles before it only
        # (no look-ahead), beside the leave-one-cycle-out figures above.
        "forward": None if not fw else {
            "cycle": fw["cycle"], "trainedOn": fw["trainedOn"], "races": fw["races"],
            "polledRaces": fw["polledRaces"], "coverage80": fw["coverage80"], "coverage95": fw["coverage95"],
            "coverage80Band": fw["coverage80Band"], "meanAbsError": fw["meanAbsError"],
            "brier": {"combined": fw["polled"]["combined"]["brier"],
                      "fundamentals": fw["polled"]["fundamentals"]["brier"],
                      "pollsOnly": fw["polled"]["pollsOnly"]["brier"]},
            "logLoss": fw["polled"]["combined"]["logLoss"],
        },
    }


def build(db: Session, revisions: dict[str, dict] | None, today: date | None = None,
          house_titles: list[str] | None = None) -> dict | None:
    """The map's data, or None outside the campaign (election day on)."""
    election = active_election(db, today)
    if election.phase != CAMPAIGN:
        return None
    today = today or utcnow().date()
    days_left = (election.election_day - today).days
    horizon = max(h for h in model.HORIZONS if h <= max(days_left, 0))
    fits = {chamber: data_refresh.read(name) for chamber, name in MODEL_FILES.items()}
    # Further out than the training polls reach, the method was never
    # checked with polls: every seat shows the structural range alone, its
    # polls listed beside it.
    window = min(f["fits"][str(horizon)]["windowDays"] for f in fits.values())
    polls_used = days_left <= window
    before = _previous(election.election_day)
    prev_national = before.get("national")
    try:
        national = _national((revisions or {}).get(generic_article(election.cycle)), today, election.cycle)
        national["status"] = "ok"
    except wiki.GateFailure as exc:
        if not prev_national:
            raise
        # The page shows this: the figure is the last good revision's, and
        # its date ("updated") says how old.
        national = dict(prev_national, status="kept", statusDetail=str(exc))

    leans = {"senate": _lean_table(), "house": _house_leans(election.cycle)}
    chambers, training = {}, {}
    for chamber, office in OFFICES.items():
        fitted = fits[chamber]
        params = fitted["fits"][str(horizon)]
        training[chamber] = fitted["_source"]
        previous = {s["id"]: s for s in (before.get("chambers", {}).get(chamber, {}).get("seats", []))}
        races = (db.query(Race).options(selectinload(Race.candidates))
                 .filter(Race.cycle_year == election.cycle, Race.office == office)
                 .order_by(Race.state, Race.district, Race.id).all())
        table, _ = leans[chamber]
        seats = []
        for race in races:
            key = race.state if chamber == "senate" else f"{race.state}-{race.district or 0}"
            titles = ([article_title(race)] if chamber == "senate"
                      else house_articles(race, house_titles or []))
            # A seat with no lean is still emitted (status "no_lean").
            seats.append(_seat(race, table.get(key), params, national, revisions, titles,
                               previous.get(race.id), today, election.election_day, polls_used))
        shift = params["beta"][3] * national["margin"]
        chambers[chamber] = {
            "shift": round(shift, 2),
            "shiftLabel": margin_label(shift),
            "calibration": _calibration(fitted["evaluation"][str(horizon)]),
            "model": {"beta": params["beta"], "priorSd": params["priorSd"], "halfLife": params["halfLife"],
                      "pollFloor": params["pollFloor"], "pollSpread": params["pollSpread"],
                      "scale": params["scale"], "priorScale": params["priorScale"],
                      "file": f"app/data/{MODEL_FILES[chamber]}", "fit": fitted["_regenerate"],
                      "refreshed": (fitted.get("_refreshed") or {}).get("at")},
            "seats": seats,
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "asOf": utcnow().isoformat(timespec="seconds") + "Z",
        "electionDay": election.election_day.isoformat(),
        "cycle": election.cycle,
        "daysToElection": days_left,
        "horizon": horizon,
        "pollWindowDays": window,
        # False further out than the window: ranges are the structural line's.
        "pollsInRange": polls_used,
        "national": national,
        "bins": {"leansMax": LEANS_MAX, "usuallyMax": USUALLY_MAX},
        "sources": {
            "lean": {chamber: meta for chamber, (_, meta) in leans.items()},
            "training": training,
            "trainingPolls": {"attribution": training_polls.ATTRIBUTION, "url": training_polls.PAGE_URL,
                              "terms": data_refresh.read(training_polls.FILE_NAME).get("terms")},
            "polls": {"attribution": wiki.POLL_ATTRIBUTION, "license": wiki.POLL_LICENSE,
                      "licenseUrl": wiki.POLL_LICENSE_URL,
                      "notice": "Poll figures, averages and the ranges built on them are derived from "
                                "Wikipedia's race articles (Wikipedia contributors) and shared under "
                                "CC BY-SA 4.0: reuse them under the same licence, with attribution."},
        },
        "chambers": chambers,
    }


def _seat(race, lean: float | None, params, national, revisions, titles: list[str], previous, today: date,
          election_day: date, polls_used: bool = True) -> dict:
    dem, rep = _nominee(race.candidates, "DEM"), _nominee(race.candidates, "REP")
    seat = {
        "id": race.id, "state": race.state, "stateName": STATE_NAMES[race.state], "special": race.is_special,
        "district": race.district,
        "lean": None if lean is None else round(lean, 2),
        "leanLabel": None if lean is None else lean_label(lean),
        "usualBin": None if lean is None else bin_of(lean),
        "dem": _display_name(dem) if dem else None, "rep": _display_name(rep) if rep else None,
        "incumbentParty": ("R" if rep and rep.incumbent_challenge == "I" else
                           "D" if dem and dem.incumbent_challenge == "I" else None),
    }
    if lean is None:
        logger.warning("Election map: no lean for %s", race.id)
        return dict(seat, status="no_lean",
                    reason="Civitas has no usual-vote figure for this seat, so it can show neither its usual "
                           "vote nor a range.")
    if not any(c.confirmed_general for c in race.candidates):
        return dict(seat, status="unconfirmed",
                    reason="Civitas has not yet confirmed who is on this race's November ballot (only campaign "
                           "filings so far), so it shows no range.")
    if dem is None or rep is None:
        missing = "Democratic" if dem is None else "Republican"
        return dict(seat, status="not_contested",
                    reason=f"No {missing} nominee is on the ballot, so there is no Democrat-versus-Republican "
                           "margin to describe.")
    article = {"title": titles[0]}
    rev = None
    parsed: list[dict] = []
    # What the poll source said, for the page to word: "ok" (the article was
    # read; its table had these polls, maybe none recent), "no_table" (read,
    # but it lists no poll of these two nominees), "no_article", "rejected"
    # (the table failed a gate) or "unreachable" (Wikipedia didn't answer).
    # A failure with the previous good revision's polls kept adds "keptFrom".
    source: dict = {}
    try:
        if revisions is None:
            raise _Unreachable("Wikipedia could not be reached")
        found = False
        for title in titles:
            rev = revisions.get(title)
            if rev is None:
                continue
            as_of = wiki.revision_date(rev)
            table = wiki.poll_table(rev["content"], _display_name(dem), _display_name(rep), as_of,
                                    wiki.named_refs(rev["content"]), race.cycle_year,
                                    district=race.district if race.office == "H" and race.district else None)
            article = {"title": title, "revid": rev["revid"], "timestamp": rev["timestamp"],
                       "url": wiki.revision_url(rev["title"], rev["revid"]), "status": "ok"}
            source = {"state": "no_table", "revisionDate": as_of.isoformat()}
            found = True
            if table is not None:
                parsed = [p.as_dict() for p in table.polls]
                source["state"] = "ok"
                break
        if not found:
            source = {"state": "no_article"}
    except (wiki.GateFailure, _Unreachable) as exc:
        source = {"state": "unreachable" if isinstance(exc, _Unreachable) else "rejected", "detail": str(exc),
                  "checkedOn": today.isoformat()}
        if rev is not None:
            source["revisionDate"] = wiki.revision_date(rev).isoformat()
        kept = (previous or {}).get("article", {})
        if previous and kept.get("revid") and previous.get("dem") == seat["dem"] and previous.get("rep") == seat["rep"]:
            parsed = previous.get("polls", {}).get("all", [])
            article.update({k: kept[k] for k in ("title", "revid", "timestamp", "url") if k in kept})
            if parsed and kept.get("timestamp"):
                source["keptFrom"] = kept["timestamp"][:10]
        article["status"] = f"{source['state']}: {exc}"
        logger.warning("Election map: %s %s", article["title"], article["status"])
    seat["pollSource"] = source
    seat["article"] = article

    others = [p["other"] for p in parsed if p.get("other") is not None]
    if others and sum(others) / len(others) > min(sum(p["dem"] for p in parsed) / len(parsed),
                                                   sum(p["rep"] for p in parsed) / len(parsed)):
        side = "Democratic" if sum(p["dem"] for p in parsed) < sum(p["rep"] for p in parsed) else "Republican"
        return dict(seat, status="not_two_party", polls={"count": len(parsed), "list": [], "all": parsed},
                    reason=f"In this race's polls a candidate of neither major party runs ahead of the {side} "
                           "nominee, so a Democrat-versus-Republican margin would not describe it. The method "
                           "was fitted only on Democrat-versus-Republican races.")

    poll_objs = [model.Poll(p["pollster"], (election_day - date.fromisoformat(p["end"])).days, p["sample"],
                            p["rep"] - p["dem"], p["partisan"]) for p in parsed]
    inc = 1 if seat["incumbentParty"] == "R" else -1 if seat["incumbentParty"] == "D" else 0
    days_left = (election_day - today).days
    live = dict(params, horizon=max(days_left, 0))
    if not polls_used:
        # The structural line alone, with its own calibrated spread
        # (priorScale): no poll enters the range.
        live["scale"] = params["priorScale"]
    r = model.predict({"lean": lean, "inc": inc}, poll_objs if polls_used else [], national["margin"], live)
    used = {(p.pollster, p.days_out, p.margin) for p in (r["average"].used if r["average"] else ())}
    for p, obj in zip(parsed, poll_objs):
        p["usedInAverage"] = (obj.pollster, obj.days_out, obj.margin) in used
    poll_share = 0.0
    if r["pollSd"] is not None:
        wp, wa = 1 / params["priorSd"] ** 2, 1 / r["pollSd"] ** 2
        poll_share = wa / (wp + wa)
    strength = 0
    if r["average"] is not None:
        if not r["low95"] <= r["prior"] <= r["high95"]:
            strength = 2
        elif not r["low80"] <= r["prior"] <= r["high80"]:
            strength = 1
    latest = max((p["end"] for p in parsed if p.get("usedInAverage")), default=None)
    return dict(
        seat, status="range",
        prior=round(r["prior"], 1), priorLabel=margin_label(r["prior"]),
        mean=round(r["mean"], 1),
        meanLabel=margin_label(r["mean"]),
        range80=[round(r["low80"], 1), round(r["high80"], 1)],
        range95=[round(r["low95"], 1), round(r["high95"], 1)],
        range80Label=range_label(r["low80"], r["high80"]),
        # Decided here on the range as written, never re-derived by the page.
        close=range_close(r["low80"], r["high80"]),
        pollsBin=range_bin(r["low80"], r["high80"]),
        pollAverage=None if r["pollAverage"] is None else round(r["pollAverage"], 1),
        pollShare=round(poll_share, 2),
        # "list" is what the map shows (the polls in the average, newest
        # first); "all" keeps every parsed row for the next build to fall
        # back on if a revision fails its gates, and the API leaves it out.
        polls={"count": len(parsed), "used": r["pollsUsed"], "latest": latest,
               # Beyond the window no poll is in the range, so the list is
               # the newest polls, shown beside it.
               "list": sorted((p for p in parsed if p["usedInAverage"] or not polls_used), key=lambda p: p["end"],
                              reverse=True)[:POLLS_SHOWN],
               "all": parsed},
        fundamentalsOnly=not polls_used,
        difference={"strength": strength,
                    "direction": None if strength == 0 else ("D" if r["mean"] < r["prior"] else "R"),
                    "seatPolls": r["average"] is not None},
    )


async def refresh(db: Session) -> dict | None:
    """Fetch the current revisions, build, and write the map's file. An
    unreachable Wikipedia (None, never an empty result) still builds, every
    seat saying its polls could not be read, and raises an ops alert."""
    election = active_election(db)
    if election.phase != CAMPAIGN:
        return None
    races = db.query(Race).filter(Race.cycle_year == election.cycle, Race.office.in_(("S", "H"))).all()
    house_titles = await wiki.titles_with_prefix(house_article_prefix(election.cycle))
    titles = {generic_article(election.cycle)}
    for r in races:
        titles.update([article_title(r)] if r.office == "S" else house_articles(r, house_titles or []))
    revisions = None if house_titles is None else await wiki.fetch_current(sorted(titles))
    if revisions is None:
        send_ops_alert("Election map: Wikipedia unreachable",
                       "The election map's refresh could not read Wikipedia's race articles; seats keep their "
                       "previous polls and say so.",
                       dedupe_key=f"election-map-wikipedia-{utcnow():%Y-%m-%d}", condition=ALERT_CONDITION)
    data = build(db, revisions, house_titles=house_titles)
    if data is not None:
        _alert_missing_house_lines(data, election.cycle)
        write_text_atomic(map_path(), json.dumps(data, separators=(",", ":")))
        logger.info("Election map: %s seats written",
                    {c: len(v["seats"]) for c, v in data["chambers"].items()})
        if revisions is not None:
            resolve_ops_alert(ALERT_CONDITION)
    return data


def _alert_missing_house_lines(data: dict, cycle: int) -> None:
    """House seats on the ballot but no computed lean for the lines they are
    fought on (district_lean.json not yet built for that Congress): every
    House seat shows "no_lean" until scripts/build_district_lean.py adds it."""
    congress = house_lines(cycle)
    seats = data["chambers"]["house"]["seats"]
    if seats and all(s["status"] == "no_lean" for s in seats):
        send_ops_alert("Election map: no district lean for the House lines",
                       f"The {cycle} House election is fought on the {ordinal(congress)} Congress's lines, and "
                       f"app/data/district_lean.json has no table for them, so every House seat shows no "
                       f"usual vote. Add the lines with backend/scripts/build_district_lean.py.",
                       dedupe_key=f"{HOUSE_LINES_CONDITION}-{congress}", condition=HOUSE_LINES_CONDITION)
    else:
        resolve_ops_alert(HOUSE_LINES_CONDITION)


def _coverage_line(chamber: str, doc: dict, due: int) -> list[str]:
    """What keeps one chamber's fit from reaching `due`, input by input."""
    cov = doc.get("_coverage") or {}
    missing = []
    if due not in cov.get("polls", []):
        missing.append(f"{training_polls.ATTRIBUTION}'s raw-poll file (app/data/{training_polls.FILE_NAME}, "
                       f"retrieved {data_refresh.read(training_polls.FILE_NAME).get('retrieved')}) has no {due} "
                       f"{'generic-ballot' if chamber == 'house' else 'Senate'} polls yet")
    if due not in cov.get("results", []):
        missing.append(f"MEDSL's U.S. Senate file (doi:10.7910/DVN/PEJ5QU) has no {due} results yet"
                       if chamber == "senate" else
                       f"the FEC has not published its Federal Elections {due} compilation (US House Results by "
                       "State); MEDSL's U.S. House file is behind a sign-in form Civitas does not answer")
    if chamber == "house" and due not in cov.get("lean", []):
        missing.append(f"app/data/forecast_house_lean.json has no {due} district lean "
                       "(backend/scripts/build_house_lean_history.py)")
    return missing or ["every input reaches it, but the last refit did not add it (see the forecast_model.json "
                       "refresh alert)"]


def check_training_staleness() -> None:
    """Alert once a general election the map's fits never saw has certified
    results, naming each missing input per chamber: the training polls
    (Silver Bulletin's rated file, refreshed by app/data_refresh.py), the
    certified results (MEDSL's Senate file; the FEC's House compilation),
    and for the House its lean history. The refit itself is automatic
    (data_refresh: forecast_model.json); a gate that refused a new poll file
    alerts on its own. Resolved once both fits include that election."""
    due = last_certified_general(utcnow().date())
    if due is None:
        return
    docs = {chamber: data_refresh.read(name) for chamber, name in TRAINING_FILES.items()}
    newest = {chamber: max(r["year"] for r in doc["races"]) for chamber, doc in docs.items()}
    behind = {chamber: year for chamber, year in newest.items() if year < due}
    if not behind:
        resolve_ops_alert(TRAINING_CONDITION)
        return
    lines = [f"{chamber.capitalize()} (fit stops at {year}): " + "; ".join(_coverage_line(chamber, docs[chamber], due))
             for chamber, year in sorted(behind.items())]
    send_ops_alert("Election map: fits are a cycle behind",
                   f"The {due} general election's certified results are official, but the election map's fits "
                   f"do not include it. " + " ".join(f"{line}." for line in lines) + " The map keeps the last "
                   "fit and names its cycles; each chamber refits on its own once its inputs are there.",
                   dedupe_key=f"{TRAINING_CONDITION}-{due}", condition=TRAINING_CONDITION)


def run_refresh() -> None:
    """The scheduled job's body (its own session and event loop). A failed
    refresh raises an ops alert (once a day); the page shows the map's age
    and marks it stale (api/election_map.STALE_AFTER)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        asyncio.run(refresh(db))
    except Exception as exc:
        logger.exception("Election map refresh failed")
        send_ops_alert("Election map refresh failed", f"{type(exc).__name__}: {exc}",
                       dedupe_key=f"election-map-refresh-{utcnow():%Y-%m-%d}", condition=ALERT_CONDITION)
    finally:
        db.close()
