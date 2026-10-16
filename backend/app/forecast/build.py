"""Build the 2026 general-election map's data: every Senate seat on the
ballot, with its structural lean and, where Democrats and Republicans
both have a nominee, the poll-informed range of the vote margin.

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

from app.atomic_write import runtime_data_path, write_text_atomic
from app.election_phase import CAMPAIGN, active_election
from app.forecast import model
from app.forecast import polls as wiki
from app.models import Race
from app.state_names import STATE_NAMES
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[1] / "data"
MODEL_PATH = DATA / "forecast_model.json"
STATE_PVI_PATH = DATA / "state_pvi.json"
MAP_FILE = "election_map.json"
GENERIC_ARTICLE = "2026 United States House of Representatives elections"
# Display bins of the map, in points (the legend and the tiles read the
# bin the API names, so these edges live here alone): under LEANS_MAX a
# seat "leans", under USUALLY_MAX it "usually" goes that way, beyond it
# "strongly". A display grouping, not a fitted value.
LEANS_MAX = 5.0
USUALLY_MAX = 15.0
SCHEMA_VERSION = 1


def map_path() -> str:
    return runtime_data_path(MAP_FILE)


def article_title(race: Race) -> str:
    return (f"{race.cycle_year} United States Senate {'special ' if race.is_special else ''}"
            f"election in {STATE_NAMES[race.state]}")


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


def lean_label(lean: float) -> str:
    r = round(lean)
    return "EVEN" if r == 0 else f"{'R' if r > 0 else 'D'}+{abs(r)}"


def margin_label(margin: float) -> str:
    r = round(margin)
    return "EVEN" if r == 0 else f"{'R' if r > 0 else 'D'}+{abs(r)}"


def bin_of(value: float, close: bool) -> str:
    """'close', or '<side>_<leans|usually|strongly>' for a signed value."""
    if close:
        return "close"
    side = "R" if value > 0 else "D"
    size = abs(value)
    return f"{side}_{'leans' if size < LEANS_MAX else 'usually' if size < USUALLY_MAX else 'strongly'}"


def range_bin(low: float, high: float) -> str:
    """A range is binned by its edge nearest even, so a wide range reads
    paler and one that crosses even is 'close' (value-suppressing
    uncertainty; Correll, Moritz & Heer 2018)."""
    if low <= 0 <= high:
        return "close"
    return bin_of(low if low > 0 else high, False)


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


def _national(rev: dict | None, as_of: date) -> dict:
    if rev is None:
        raise wiki.GateFailure("Wikipedia could not be reached")
    rows = wiki.generic_ballot(rev["content"], as_of)
    margin = sum(r["rep"] - r["dem"] for r in rows) / len(rows)
    return {"margin": round(margin, 2), "averages": rows, "article": rev["title"], "revid": rev["revid"],
            "timestamp": rev["timestamp"], "url": wiki.revision_url(rev["title"], rev["revid"])}


def _previous() -> dict:
    try:
        return json.loads(Path(map_path()).read_text())
    except (OSError, ValueError):
        return {}


def build(db: Session, revisions: dict[str, dict] | None, today: date | None = None) -> dict | None:
    """The map's data, or None outside the campaign (election day on)."""
    election = active_election(db, today)
    if election.phase != CAMPAIGN:
        return None
    today = today or utcnow().date()
    days_left = (election.election_day - today).days
    horizon = max(h for h in model.HORIZONS if h <= max(days_left, 0))
    fitted = json.loads(MODEL_PATH.read_text())
    params = fitted["fits"][str(horizon)]
    evaluation = fitted["evaluation"][str(horizon)]
    leans, lean_source = _lean_table()
    before = _previous()
    previous = {s["id"]: s for s in before.get("seats", [])}
    prev_national = before.get("national")

    try:
        national = _national((revisions or {}).get(GENERIC_ARTICLE), today)
        national["status"] = "ok"
    except wiki.GateFailure as exc:
        if not prev_national:
            raise
        national = dict(prev_national, status=f"kept the previous revision: {exc}")
    shift = params["beta"][3] * national["margin"]

    races = (db.query(Race).options(selectinload(Race.candidates))
             .filter(Race.cycle_year == election.cycle, Race.office == "S").order_by(Race.id).all())
    seats = []
    for race in races:
        seats.append(_seat(race, leans, params, national, revisions, previous.get(race.id), today,
                           election.election_day))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "asOf": utcnow().isoformat(timespec="seconds") + "Z",
        "electionDay": election.election_day.isoformat(),
        "daysToElection": days_left,
        "horizon": horizon,
        "chamber": "senate",
        "national": dict(national, shift=round(shift, 2)),
        "bins": {"leansMax": LEANS_MAX, "usuallyMax": USUALLY_MAX},
        "calibration": {
            "cycles": [evaluation["cycles"][0], evaluation["cycles"][-1]],
            "races": evaluation["races"],
            "coverage80": evaluation["coverage80"],
            "coverage95": evaluation["coverage95"],
            "meanAbsError": evaluation["meanAbsError"],
            "table": evaluation["calibration"],
            "brier": {"combined": evaluation["polled"]["combined"]["brier"],
                      "fundamentals": evaluation["polled"]["fundamentals"]["brier"],
                      "pollsOnly": evaluation["polled"]["pollsOnly"]["brier"],
                      "polledRaces": evaluation["polledRaces"]},
        },
        "model": {"beta": params["beta"], "priorSd": params["priorSd"], "halfLife": params["halfLife"],
                  "pollFloor": params["pollFloor"], "pollSpread": params["pollSpread"], "scale": params["scale"],
                  "file": "app/data/forecast_model.json", "fit": fitted["_regenerate"]},
        "sources": {
            "lean": lean_source,
            "training": fitted["_source"],
            "polls": {"attribution": wiki.POLL_ATTRIBUTION, "license": wiki.POLL_LICENSE,
                      "licenseUrl": wiki.POLL_LICENSE_URL,
                      "notice": "Poll figures, averages and the ranges built on them are derived from "
                                "Wikipedia's race articles (Wikipedia contributors) and shared under "
                                "CC BY-SA 4.0: reuse them under the same licence, with attribution."},
        },
        "seats": seats,
    }


def _seat(race, leans, params, national, revisions, previous, today: date, election_day: date) -> dict:
    lean = leans[race.state]
    dem, rep = _nominee(race.candidates, "DEM"), _nominee(race.candidates, "REP")
    seat = {
        "id": race.id, "state": race.state, "stateName": STATE_NAMES[race.state], "special": race.is_special,
        "lean": round(lean, 2), "leanLabel": lean_label(lean),
        "usualBin": "close" if round(lean) == 0 else bin_of(lean, False),
        "dem": _display_name(dem) if dem else None, "rep": _display_name(rep) if rep else None,
        "incumbentParty": ("R" if rep and rep.incumbent_challenge == "I" else
                           "D" if dem and dem.incumbent_challenge == "I" else None),
    }
    if dem is None or rep is None:
        missing = "Democratic" if dem is None else "Republican"
        return dict(seat, status="not_contested",
                    reason=f"No {missing} nominee is on the ballot, so there is no Democrat-versus-Republican "
                           "margin to describe.")
    title = article_title(race)
    rev = (revisions or {}).get(title)
    article = {"title": title}
    parsed: list[dict] = []
    try:
        if rev is None:
            raise wiki.GateFailure("Wikipedia could not be reached" if revisions is None else "no such article")
        as_of = wiki.revision_date(rev)
        table = wiki.poll_table(rev["content"], _display_name(dem), _display_name(rep), as_of,
                                wiki.named_refs(rev["content"]))
        parsed = [p.as_dict() for p in (table.polls if table else [])]
        article.update(revid=rev["revid"], timestamp=rev["timestamp"],
                       url=wiki.revision_url(rev["title"], rev["revid"]), status="ok")
    except wiki.GateFailure as exc:
        kept = (previous or {}).get("article", {})
        if previous and kept.get("revid") and previous.get("dem") == seat["dem"] and previous.get("rep") == seat["rep"]:
            parsed = previous.get("polls", {}).get("list", [])
            article.update({k: kept[k] for k in ("revid", "timestamp", "url") if k in kept})
        article["status"] = f"rejected: {exc}" + ("; the previous good revision's polls are kept" if parsed else "")
        logger.warning("Election map: %s %s", title, article["status"])
    seat["article"] = article

    others = [p["other"] for p in parsed if p.get("other") is not None]
    if others and sum(others) / len(others) > min(sum(p["dem"] for p in parsed) / len(parsed),
                                                   sum(p["rep"] for p in parsed) / len(parsed)):
        side = "Democratic" if sum(p["dem"] for p in parsed) < sum(p["rep"] for p in parsed) else "Republican"
        return dict(seat, status="not_two_party", polls={"count": len(parsed), "list": parsed},
                    reason=f"In this race's polls a candidate of neither major party runs ahead of the {side} "
                           "nominee, so a Democrat-versus-Republican margin would not describe it. The method "
                           "was fitted only on Democrat-versus-Republican races.")

    poll_objs = [model.Poll(p["pollster"], (election_day - date.fromisoformat(p["end"])).days, p["sample"],
                            p["rep"] - p["dem"], p["partisan"]) for p in parsed]
    inc = 1 if seat["incumbentParty"] == "R" else -1 if seat["incumbentParty"] == "D" else 0
    days_left = (election_day - today).days
    live = dict(params, horizon=max(days_left, 0))
    r = model.predict({"lean": lean, "inc": inc}, poll_objs, national["margin"], live)
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
        range80=[round(r["low80"], 1), round(r["high80"], 1)],
        range95=[round(r["low95"], 1), round(r["high95"], 1)],
        pollsBin=range_bin(r["low80"], r["high80"]),
        pollAverage=None if r["pollAverage"] is None else round(r["pollAverage"], 1),
        pollShare=round(poll_share, 2),
        polls={"count": len(parsed), "used": r["pollsUsed"], "latest": latest, "list": parsed},
        difference={"strength": strength,
                    "direction": None if strength == 0 else ("D" if r["mean"] < r["prior"] else "R"),
                    "seatPolls": r["average"] is not None},
    )


async def refresh(db: Session) -> dict | None:
    """Fetch the current revisions, build, and write the map's file."""
    election = active_election(db)
    if election.phase != CAMPAIGN:
        return None
    races = db.query(Race).filter(Race.cycle_year == election.cycle, Race.office == "S").all()
    titles = sorted({article_title(r) for r in races}) + [GENERIC_ARTICLE]
    revisions = await wiki.fetch_current(titles)
    data = build(db, revisions)
    if data is not None:
        write_text_atomic(map_path(), json.dumps(data, separators=(",", ":")))
        logger.info("Election map: %d seats written", len(data["seats"]))
    return data


def run_refresh() -> None:
    """The scheduled job's body (its own session and event loop)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        asyncio.run(refresh(db))
    except Exception:
        logger.exception("Election map refresh failed")
    finally:
        db.close()
