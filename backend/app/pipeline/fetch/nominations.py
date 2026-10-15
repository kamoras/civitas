"""Presidential nominations, from Congress.gov's nomination API.

One row per citation ("PN999-1": Congress.gov splits a message naming
several people into numbered parts), holding the record's own wording: the
description, organization and position title, every nominee's name and
state as published, every action, the committee it was referred to and the
hearings Congress.gov lists for it. `status` reads Congress.gov's action
codes (STATUS_CODES) — a translation of its vocabulary, never a judgement.

A daily job (scheduler._nominations_sync), outside the nightly chain:

- Lists the Congress's nominations updated since the cursor (the whole
  Congress the first time), then reads each new or changed one oldest
  update first: its record, actions, nominees and — only when an action
  says a hearing was held — its hearings.
- Paces itself at _OWN_RPS on top of congress.py's shared limiter, so the
  key's hour (5,000 requests; the pipeline process's 1.2 requests/s plus the
  public routes' 200 an hour stay under it) is never this job's alone, and
  the first backfill (about 7,000 requests for a Congress) spreads over
  about eight days at MAX_PER_RUN a run.
- Commits each nomination as it is read, never across a request: a write
  transaction held through a network call is what blocked the nightly runs
  on 2026-10-15 (#990).
- Writes the cursor only after the pass: a listing or a nomination that
  could not be read ends the pass with the cursor at the last update applied
  in order, so nothing is skipped; an unreachable Congress.gov is never read
  as "no nominations changed".

Judicial nominations Congress.gov shows as confirmed are matched to the
Federal Judicial Center's directory (judges.csv, the file the justice
pipeline reads) on court, nomination date and surname, all three exact;
FJC states no PN, so this is the join's whole key, and anything but one
match is left unmatched.
"""

import csv
import io
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.models import Nomination
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _fetch_with_retry
from app.pipeline.fetch.justice_records import FJC_JUDGES, _get, _iso
from app.pipeline.rate_limiter import RateLimiter
from app.pipeline.run_tracker import PipelineRunTracker
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Congress.gov's action codes for how a nomination ended (checked live on the
# 119th Congress, 2026-10-15). Any other code leaves it pending.
STATUS_CODES = {
    "S05310": "confirmed",  # "Confirmed by the Senate by ..."
    "S05330": "withdrawn",  # "Received message of withdrawal of nomination from the President."
    "S05335": "returned",   # "Returned to the President under the provisions of Senate Rule XXXI ..."
}
STATUSES = ("pending", "confirmed", "returned", "withdrawn")
# "Hearings held." and "Hearings held prior to referral": the actions that
# say the hearings list has something in it.
HEARING_CODES = {"S05750", "S05198"}
# Congress.gov's organization for every judgeship.
JUDICIARY = "The Judiciary"

_CACHE_TIER = "nominations"
_FJC_KEY = "fjc-appointments"
_PAGE = 250
# Nominations read per run. Each is three or four requests at _OWN_RPS plus
# Congress.gov's own latency: 6 to 25 seconds a nomination, measured from the
# production Pi on 2026-10-15. So a run is at most about two hours, inside
# the lease's three, and a Congress's first backfill (2,285 citations in the
# 119th) takes about eight days. A run cut off at the lease's limit loses
# nothing: each nomination is committed as it is read and skipped next time.
MAX_PER_RUN = 300
_OWN_RPS = 0.5
# Congress.gov's updateDate and our clock won't agree to the second: each
# window re-lists a little before the cursor, and what was applied already is
# skipped by its stored updateDate.
_OVERLAP = timedelta(hours=1)
# Nominations unreadable in a row before a pass stops: Congress.gov is down,
# not one record broken.
_GIVE_UP_AFTER = 3

_own_limiter = RateLimiter(_OWN_RPS, name="nominations")
_tracker = PipelineRunTracker()


def nominations_tracker() -> PipelineRunTracker:
    """The in-process guard the scheduled sync runs under (lease.run_tracked)."""
    return _tracker


def _cursor_key(congress: int) -> str:
    return f"cursor-{congress}"


async def _get_json(client: httpx.AsyncClient, path: str) -> dict | None:
    """One Congress.gov request at this job's own pace; None when it could
    not be read."""
    await _own_limiter.acquire()
    return await _fetch_with_retry(client, f"{CONGRESS_API_BASE}{path}")


async def list_updated(client: httpx.AsyncClient, congress: int, since: datetime | None) -> list[dict] | None:
    """Every listing item of the Congress updated since `since` (all of
    them when None). The listing comes newest first whatever `sort` asks
    (checked live), so the whole window is read before anything is applied.
    None when any page could not be read: half a listing would read as the
    rest having no changes."""
    window = f"&fromDateTime={since.strftime('%Y-%m-%dT%H:%M:%SZ')}" if since else ""
    items: list[dict] = []
    offset = 0
    while True:
        data = await _get_json(client, f"/nomination/{congress}?limit={_PAGE}&offset={offset}{window}")
        if data is None or not isinstance(data.get("nominations"), list):
            return None
        page = data["nominations"]
        items.extend(page)
        if len(page) < _PAGE:
            return items
        offset += _PAGE


def _path(citation: str) -> str:
    """Congress.gov's address for a citation: "PN999-1" -> "999-1"."""
    return citation.removeprefix("PN")


def status_of(actions: list[dict]) -> tuple[str, str | None]:
    """(status, its date) from the actions: the latest action carrying one
    of STATUS_CODES, pending when none does."""
    ended = [a for a in actions if a.get("code") in STATUS_CODES and a.get("date")]
    if not ended:
        return "pending", None
    last = max(ended, key=lambda a: a["date"])
    return STATUS_CODES[last["code"]], last["date"]


def committee_of(raw_actions: list[dict]) -> tuple[str | None, str]:
    """(system code, name) of the committee named on the earliest action
    that names one: the referral."""
    for action in sorted(raw_actions, key=lambda a: a.get("actionDate") or ""):
        for committee in action.get("committees") or []:
            if committee.get("systemCode"):
                return committee["systemCode"], committee.get("name") or ""
    return None, ""


async def _nominees(client: httpx.AsyncClient, congress: int, citation: str, position: dict) -> list[dict] | None:
    names: list[dict] = []
    offset = 0
    while True:
        data = await _get_json(
            client, f"/nomination/{congress}/{_path(citation)}/{position.get('ordinal')}?limit={_PAGE}&offset={offset}",
        )
        if data is None or not isinstance(data.get("nominees"), list):
            return None
        page = data["nominees"]
        names.extend(
            {k: n.get(k) or "" for k in ("firstName", "middleName", "lastName", "state")}
            for n in sorted(page, key=lambda n: n.get("ordinal") or 0)
        )
        if len(page) < _PAGE:
            return names
        offset += _PAGE


async def read_nomination(client: httpx.AsyncClient, congress: int, item: dict) -> dict | None:
    """Everything stored for one listing item, or None when any part of it
    could not be read (the whole nomination waits for the next run rather
    than being stored with a part missing)."""
    citation = item["citation"]
    detail = await _get_json(client, f"/nomination/{congress}/{_path(citation)}")
    record = (detail or {}).get("nomination")
    if not isinstance(record, dict):
        return None
    actions_data = await _get_json(client, f"/nomination/{congress}/{_path(citation)}/actions?limit={_PAGE}")
    if actions_data is None or not isinstance(actions_data.get("actions"), list):
        return None
    raw_actions = actions_data["actions"]
    positions = []
    for position in record.get("nominees") or []:
        names = await _nominees(client, congress, citation, position)
        if names is None:
            return None
        positions.append({
            "ordinal": position.get("ordinal"),
            "positionTitle": position.get("positionTitle") or "",
            "organization": position.get("organization") or "",
            "introText": position.get("introText") or "",
            "nomineeCount": position.get("nomineeCount") or len(names),
            "names": names,
        })
    hearings: list[dict] = []
    if any(a.get("actionCode") in HEARING_CODES for a in raw_actions):
        data = await _get_json(client, f"/nomination/{congress}/{_path(citation)}/hearings")
        if data is None or not isinstance(data.get("hearings"), list):
            return None
        hearings = [
            {"citation": h.get("citation") or "", "jacketNumber": h.get("jacketNumber"), "date": h.get("date") or ""}
            for h in data["hearings"]
        ]
    actions = [
        {"date": a.get("actionDate") or "", "code": a.get("actionCode") or "", "type": a.get("type") or "",
         "text": a.get("text") or ""}
        for a in raw_actions
    ]
    actions.sort(key=lambda a: a["date"], reverse=True)
    status, status_date = status_of(actions)
    committee_code, committee_name = committee_of(raw_actions)
    latest = record.get("latestAction") or item.get("latestAction") or {}
    kind = record.get("nominationType") or item.get("nominationType") or {}
    return {
        "congress": congress,
        "citation": citation,
        "number": int(record.get("number") or item.get("number") or 0),
        "part_number": str(record.get("partNumber") or item.get("partNumber") or "00"),
        "description": record.get("description") or item.get("description") or "",
        "received_date": record.get("receivedDate") or item.get("receivedDate") or "",
        "organization": item.get("organization") or (positions[0]["organization"] if positions else ""),
        "position_title": positions[0]["positionTitle"] if positions else "",
        "is_military": bool(kind.get("isMilitary")),
        "is_list": bool(record.get("isList")),
        "nominee_count": sum(p["nomineeCount"] for p in positions),
        "nominees": json.dumps(positions),
        "actions": json.dumps(actions),
        "latest_action_date": latest.get("actionDate") or "",
        "latest_action_text": latest.get("text") or "",
        "status": status,
        "status_date": status_date,
        "committee_code": committee_code,
        "committee_name": committee_name,
        "hearings": json.dumps(hearings),
        "update_date": item.get("updateDate") or "",
    }


def store(db: Session, fields: dict) -> None:
    """Upsert one nomination and commit, so no write is held open into the
    next request."""
    row = db.execute(
        select(Nomination).filter_by(congress=fields["congress"], citation=fields["citation"])
    ).scalar_one_or_none()
    if row is None:
        row = Nomination(congress=fields["congress"], citation=fields["citation"])
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    row.fetched_at = utcnow()
    db.commit()


def _update_time(item: dict) -> datetime | None:
    raw = item.get("updateDate") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


# --- The FJC join ---------------------------------------------------------

# Congress.gov's judicial position titles, as the FJC directory names the
# court: a translation between the two sources' published wording, the only
# form the titles take (checked on every judgeship of the 119th Congress).
_FJC_COURTS = (
    ("United States District Judge for the ", "U.S. District Court for the "),
    ("United States Circuit Judge for the ", "U.S. Court of Appeals for the "),
    ("Judge of the United States Court of International Trade", "U.S. Court of International Trade"),
    ("Associate Justice of the Supreme Court of the United States", "Supreme Court of the United States"),
    ("Chief Justice of the United States", "Supreme Court of the United States"),
)


def fjc_court(position_title: str) -> str | None:
    """The FJC's name for the court a Congress.gov position title names;
    None for a judgeship the directory doesn't cover (the Court of Federal
    Claims, the territorial courts' terms...)."""
    title = position_title.strip().rstrip(".")
    for prefix, court in _FJC_COURTS:
        if title.startswith(prefix):
            return court + title[len(prefix):] if prefix.endswith(" ") else court
    return None


_JOINT = re.compile(r"^(U\.S\. District Court for the )(\w+) and (\w+) Districts of (.+)$")


def fjc_courts(court: str) -> set[str]:
    """The FJC court names a judgeship's court stands for: one, or for a
    seat shared by two districts ("the Eastern and Western Districts of
    Missouri") the two districts, each of which the directory lists as an
    appointment of its own."""
    joint = _JOINT.match(court)
    if not joint:
        return {court}
    head, first, second, state = joint.groups()
    return {f"{head}{first} District of {state}", f"{head}{second} District of {state}"}


def surname_key(name: str) -> str:
    """A surname as both sources can be compared on: accents dropped (NFD,
    as the vote matching does), case and punctuation ignored."""
    plain = unicodedata.normalize("NFD", name)
    return "".join(ch for ch in plain.casefold() if ch.isalpha())


def fjc_appointments(rows) -> list[dict]:
    """Every appointment in judges.csv as the join reads it: {"court",
    "nominated", "surname", "jid", "nid"}."""
    out = []
    for r in rows:
        for i in range(1, 7):
            court = (r.get(f"Court Name ({i})") or "").strip()
            nominated = _iso(r.get(f"Nomination Date ({i})") or "")
            if court and nominated:
                out.append({"court": court, "nominated": nominated, "surname": surname_key(r.get("Last Name") or ""),
                            "jid": (r.get("jid") or "").strip(), "nid": (r.get("nid") or "").strip()})
    return out


async def fetch_fjc_appointments(client: httpx.AsyncClient, db: Session) -> list[dict] | None:
    """The directory's appointments, cached a week; None when it could not
    be read."""
    cached = api_cache_get(db, _CACHE_TIER, _FJC_KEY, max_age_hours=24 * 7)
    db.commit()  # end the read before the download
    if cached is not None:
        return cached
    resp = await _get(client, FJC_JUDGES, "FJC judges")
    if resp is None:
        return None
    appointments = fjc_appointments(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    if len(appointments) < 3000:  # 4,700 in 2026; far fewer is a changed file
        logger.warning("FJC judges: only %d appointments read", len(appointments))
        return None
    api_cache_set(db, _CACHE_TIER, _FJC_KEY, appointments, normal_ttl_hours=24 * 7)
    return appointments


def fjc_match(nomination: Nomination, appointments: list[dict]) -> dict | None:
    """The one FJC judge with this judgeship's court, nomination date
    (Congress.gov's received date) and nominee's surname; None unless
    exactly one."""
    court = fjc_court(nomination.position_title or "")
    names = [n for p in json.loads(nomination.nominees or "[]") for n in p.get("names", [])]
    if court is None or len(names) != 1:
        return None
    surname = surname_key(names[0].get("lastName") or "")
    courts = fjc_courts(court)
    found = [a for a in appointments
             if a["court"] in courts and a["nominated"] == nomination.received_date and a["surname"] == surname]
    # A joint seat is two appointments of one judge.
    return found[0] if found and len({a["jid"] for a in found}) == 1 and len(found) == len(courts) else None


def link_fjc(db: Session, congress: int, appointments: list[dict]) -> int:
    """Set the FJC ids of the Congress's confirmed judgeships that have none
    yet; returns how many were linked."""
    rows = db.execute(select(Nomination).where(
        Nomination.congress == congress, Nomination.organization == JUDICIARY,
        Nomination.status == "confirmed", Nomination.fjc_judge_id.is_(None),
    )).scalars().all()
    linked = 0
    for row in rows:
        match = fjc_match(row, appointments)
        if match:
            row.fjc_judge_id, row.fjc_node_id = match["jid"], match["nid"]
            linked += 1
    db.commit()
    return linked


# --- The run --------------------------------------------------------------

async def sync_nominations(db: Session, congress: int | None = None, *, limit: int = MAX_PER_RUN) -> dict:
    """One pass: list what changed, read up to `limit` new or changed
    nominations oldest update first, link confirmed judgeships to the FJC,
    then move the cursor. Returns a summary; its "complete" is False when
    the pass stopped short (an unreadable listing or nomination, or the
    limit), and the next pass picks up from the first update not applied."""
    congress = congress or settings.CURRENT_CONGRESS
    started = utcnow()
    marker = api_cache_get(db, _CACHE_TIER, _cursor_key(congress))
    stored = dict(db.execute(
        select(Nomination.citation, Nomination.update_date).where(Nomination.congress == congress)
    ).all())
    db.commit()  # no read held open across the requests
    since = None
    if marker and marker.get("cursor"):
        since = datetime.fromisoformat(marker["cursor"]) - _OVERLAP
    summary = {"congress": congress, "since": since.isoformat() if since else None, "listed": 0,
               "stored": 0, "fjcLinked": 0, "complete": False, "unreadable": None}
    async with make_async_client() as client:
        listed = await list_updated(client, congress, since)
        if listed is None:
            summary["unreadable"] = ["listing"]
            logger.warning("Nominations: the %dth Congress's listing could not be read; cursor kept", congress)
            return summary
        summary["listed"] = len(listed)
        todo = [it for it in listed if it.get("citation") and stored.get(it["citation"]) != it.get("updateDate")]
        todo.sort(key=lambda it: it.get("updateDate") or "")
        # The cursor may move up to the update before the first nomination
        # that could not be read; later ones are still read (one bad record
        # must not stall the rest), and are skipped next time as stored.
        applied_through: str | None = None
        failed: list[str] = []
        in_a_row = 0
        for item in todo[:limit]:
            fields = await read_nomination(client, congress, item)
            if fields is None:
                failed.append(item["citation"])
                in_a_row += 1
                logger.warning("Nominations: %s could not be read", item["citation"])
                if in_a_row >= _GIVE_UP_AFTER:
                    logger.warning("Nominations: %d in a row unreadable; stopping the pass", in_a_row)
                    break
                continue
            in_a_row = 0
            store(db, fields)
            summary["stored"] += 1
            if not failed:
                applied_through = item.get("updateDate") or applied_through
        summary["unreadable"] = failed or None
        summary["complete"] = not failed and len(todo) <= limit
        appointments = await fetch_fjc_appointments(client, db)
    if appointments is not None:
        summary["fjcLinked"] = link_fjc(db, congress, appointments)
    if summary["complete"]:
        cursor = started
    elif applied_through:
        # Oldest update first, so everything before this one is applied.
        cursor = _update_time({"updateDate": applied_through})
    else:
        cursor = None
    if cursor is not None:
        api_cache_set(db, _CACHE_TIER, _cursor_key(congress), {"cursor": cursor.isoformat()})
    return summary
