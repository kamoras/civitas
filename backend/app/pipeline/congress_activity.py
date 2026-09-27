"""Keeps the /congress record current: roll calls, floor logs, Daily Digest.

Three sources, three jobs, one run every half hour (scheduler.py):

1. Roll calls. Each chamber numbers its votes in sequence per session, so
   a run fetches from the highest stored number up until the chamber has
   no next vote. Every member's position is stored with the tally.
2. Floor logs, today's and yesterday's (Eastern). This is the live view
   of a day until the Digest arrives. A chamber that did not meet has no
   log (404), which is not an error.
3. The Daily Digest. GPO publishes a day's Record the next day. Each run
   re-reads recent days that are not final yet, and back-fills older days
   a batch at a time from a cursor, starting with the 119th Congress.

A failed fetch never becomes "no activity". Only a 404 means the source
has nothing for that day. Any other failure leaves the day as it was, and
the back-fill cursor stops before the failed day so the next run retries
it. The run's per-source outcome is stored so the API can show a source as
unavailable rather than empty.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from lxml import etree
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.http_client import make_async_client
from app.models import CongressDay, CongressEvent, RollCall, RollCallPosition
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch import daily_digest, floor_logs
from app.pipeline.fetch.congress import (
    congress_first_year,
    expected_current_congress,
    parse_house_vote_xml,
    parse_senate_vote_xml,
)
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")
_rate_limiter = RateLimiter(1.0)

GOVINFO_API_BASE = "https://api.govinfo.gov"
GOVINFO_CONTENT_BASE = "https://www.govinfo.gov/content/pkg"

# The 119th Congress convened 2025-01-03: the back-fill starts there.
_BACKFILL_START = date(2025, 1, 3)
# Recent days are re-read until final. GPO posts a day's Record by the
# next evening (2026-09-24's posted 2026-09-25 18:00 UTC); a week covers a
# late or corrected issue.
_RECENT_DAYS = 7
# Back-fill per run: ~4 requests a day at one per second, so a batch is a
# couple of minutes and the 119th Congress (~630 days) fills in about a
# day of half-hourly runs.
_DIGEST_BACKFILL_BATCH = 20
# Roll calls per chamber per run. A session has several hundred; this
# fills one in two or three runs without a single run taking long.
_ROLL_CALL_BATCH = 250
# How many numbers past a missing roll call to probe before deciding the
# chamber has no more votes (a file can be late while the next is up).
_ROLL_CALL_LOOKAHEAD = 3

_DIGEST_CURSOR_KEY = "congress-digest-backfill-cursor"
_LAST_RUN_KEY = "congress-sync-last-run"
_CACHE_TIER = "congress"

_ABSENT = object()  # the source answered 404: nothing for that day

_running_since: datetime | None = None


def is_congress_sync_running() -> bool:
    return _running_since is not None


def congress_sync_age() -> timedelta | None:
    return utcnow() - _running_since if _running_since else None


def eastern_today() -> date:
    return datetime.now(_EASTERN).date()


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def _get(client: httpx.AsyncClient, url: str, *, label: str, request_url: str | None = None):
    """The body, _ABSENT when the source has no such file, or None when
    the fetch failed. senate.gov answers a missing file with a redirect to
    an HTML page that answers 200 (a floor log for a day the Senate did not
    meet goes to file_not_found.htm, a roll call past the last one to
    roll-call-vote-not-available.htm). A redirect to another file of the
    same kind is the file moved, not missing: clerk.house.gov sends
    /FloorSummary/X.xml to /floor/X.xml."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, expected_statuses=(404,), log_label=label,
        request_url=request_url,
    )
    if resp is None:
        return None
    if resp.status_code == 404 or (resp.history and _suffix(resp.url.path) != _suffix(httpx.URL(url).path)):
        return _ABSENT
    return resp.content


def _replace_events(db: Session, chamber: str, day: str, source: str, events: list[dict]) -> None:
    db.query(CongressEvent).filter(
        CongressEvent.chamber == chamber, CongressEvent.date == day, CongressEvent.source == source,
    ).delete()
    for seq, e in enumerate(events):
        db.add(CongressEvent(
            chamber=chamber, date=day, kind=e["kind"], seq=seq, name=(e.get("name") or "")[:500],
            text=e["text"], bill_id=e.get("bill_id"), time=e.get("time"), pages=(e.get("pages") or "")[:60],
            source=source,
        ))


def _day_row(db: Session, chamber: str, day: str) -> CongressDay:
    row = db.query(CongressDay).filter_by(chamber=chamber, date=day).one_or_none()
    if row is None:
        row = CongressDay(chamber=chamber, date=day, in_session=False, source="floor_log")
        db.add(row)
    return row


# ── Floor logs ────────────────────────────────────────────────────

def _next_meeting_from_iso(iso: str | None) -> str | None:
    """"20260928T12:00" -> "12 noon, Monday, September 28", the Digest's
    own wording for a next meeting, so the live and final rows read alike."""
    if not iso:
        return None
    try:
        when = datetime.strptime(iso, "%Y%m%dT%H:%M")
    except ValueError:
        return None
    if when.hour == 12 and when.minute == 0:
        clock = "12 noon"
    else:
        clock = when.strftime("%I:%M %p").lstrip("0").replace(":00", "").replace("AM", "a.m.").replace("PM", "p.m.")
    return f"{clock}, {when:%A}, {when:%B} {when.day}"


async def sync_floor_logs(client: httpx.AsyncClient, db: Session, day: date) -> dict[str, str]:
    """Both chambers' floor logs for one day -> {"house": outcome,
    "senate": outcome}, outcome "ok" | "absent" | "failed". A day the
    Digest has already made final keeps its final row; its floor-log
    events are still refreshed (the House log carries times the Digest
    does not)."""
    iso = day.isoformat()
    outcome: dict[str, str] = {}
    for chamber, url, parse in (
        ("house", floor_logs.house_floor_url(day), floor_logs.parse_house_floor),
        ("senate", floor_logs.senate_floor_url(day), floor_logs.parse_senate_floor),
    ):
        body = await _get(client, url, label=f"{chamber} floor log")
        if body is None:
            outcome[chamber] = "failed"
            continue
        if body is _ABSENT:
            outcome[chamber] = "absent"
            continue
        parsed = parse(body)
        if parsed is None:
            outcome[chamber] = "failed"
            continue
        row = _day_row(db, chamber, iso)
        if not row.is_final:
            row.in_session = True
            row.source = "floor_log"
            row.source_url = url
            row.convened_at = parsed.get("convened_at")
            row.adjourned_at = parsed.get("adjourned_at")
            row.adjournment_text = parsed.get("adjournment_text") or ""
            if parsed.get("next_meeting_iso"):
                row.next_meeting = _next_meeting_from_iso(parsed["next_meeting_iso"])
            row.fetched_at = utcnow()
        _replace_events(db, chamber, iso, "floor_log", parsed["events"])
        db.commit()
        outcome[chamber] = "ok"
    return outcome


# ── Daily Digest ──────────────────────────────────────────────────

async def sync_digest(client: httpx.AsyncClient, db: Session, day: date) -> str:
    """One day's Daily Digest -> "ok" | "absent" (no Record that day) |
    "failed". Writes nothing unless every granule it needs was read, so a
    day is never marked final from half a Digest."""
    package = f"CREC-{day.isoformat()}"
    listing_url = f"{GOVINFO_API_BASE}/packages/{package}/granules?pageSize=1000&offsetMark=*"
    listing = await _get(
        client, listing_url, label="GovInfo CREC granules",
        request_url=str(httpx.URL(listing_url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY})),
    )
    if listing is None:
        return "failed"
    if listing is _ABSENT:
        return "absent"
    try:
        granules = httpx.Response(200, content=listing).json().get("granules", [])
    except ValueError:
        return "failed"

    texts: dict[tuple[str, str], str] = {}
    for g in granules:
        if (g.get("granuleClass") or "").upper() != "DAILYDIGEST":
            continue
        role = daily_digest.granule_role(g.get("title") or "")
        if role is None:
            continue
        url = f"{GOVINFO_CONTENT_BASE}/{package}/html/{g['granuleId']}.htm"
        body = await _get(client, url, label="Daily Digest granule")
        if body is None or body is _ABSENT:
            return "failed"
        texts[role] = daily_digest.digest_text(body.decode("utf-8", errors="replace"))
    if not any(kind == "floor" for _, kind in texts):
        # A Record issue with no Digest floor section yet (still posting).
        return "failed"

    nxt = daily_digest.parse_next_meetings(texts.get(("both", "next"), ""))
    iso = day.isoformat()
    for chamber in ("senate", "house"):
        floor_text = texts.get((chamber, "floor"))
        if floor_text is None:
            continue
        action = daily_digest.parse_chamber_action(floor_text)
        committees = daily_digest.parse_committee_meetings(texts.get((chamber, "committees"), ""))
        row = _day_row(db, chamber, iso)
        row.in_session = action["in_session"]
        row.adjournment_text = action["adjournment_text"]
        row.convened_at = action["convened_at"]
        row.adjourned_at = action["adjourned_at"]
        row.bills_introduced = action["bills_introduced"]
        row.resolutions_introduced = action["resolutions_introduced"]
        row.introduced_text = action["introduced_text"]
        if chamber in nxt:
            row.next_meeting = nxt[chamber]["when"][:120]
            row.next_program = nxt[chamber]["program"]
        row.source = "digest"
        row.source_url = f"https://www.govinfo.gov/app/details/{package}"
        row.is_final = True
        row.fetched_at = utcnow()
        _replace_events(db, chamber, iso, "digest", action["events"] + committees)
    db.commit()
    return "ok"


def _date_cursor(db: Session) -> date:
    stored = api_cache_get(db, _CACHE_TIER, _DIGEST_CURSOR_KEY, max_age_hours=24 * 365 * 10)
    if stored and stored.get("date"):
        return date.fromisoformat(stored["date"])
    return _BACKFILL_START - timedelta(days=1)


async def sync_digests(client: httpx.AsyncClient, db: Session, today: date) -> dict:
    """Recent days that are not final, then the back-fill batch."""
    outcomes: dict[str, str] = {}
    recent_start = today - timedelta(days=_RECENT_DAYS)
    for offset in range(1, _RECENT_DAYS + 1):
        day = today - timedelta(days=offset)
        final = db.query(func.count(CongressDay.id)).filter(
            CongressDay.date == day.isoformat(), CongressDay.is_final.is_(True),
        ).scalar()
        if final >= 2:
            continue
        outcomes[day.isoformat()] = await sync_digest(client, db, day)

    cursor = _date_cursor(db)
    for _ in range(_DIGEST_BACKFILL_BATCH):
        day = cursor + timedelta(days=1)
        if day >= recent_start:
            break
        result = await sync_digest(client, db, day)
        outcomes[day.isoformat()] = result
        if result == "failed":
            break  # the cursor stays before the failed day; the next run retries it
        cursor = day
        api_cache_set(db, _CACHE_TIER, _DIGEST_CURSOR_KEY, {"date": cursor.isoformat()},
                      normal_ttl_hours=24 * 365 * 10)
    return outcomes


# ── Roll calls ────────────────────────────────────────────────────

def _senate_vote_date(text: str) -> str | None:
    """"September 24, 2026,  11:46 AM" -> "2026-09-24"."""
    m = re.match(r"\s*([A-Za-z]+ \d{1,2}, \d{4})", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _senate_amended_bill(xml_text: str) -> str | None:
    """The bill a Senate vote on an amendment concerns. Such a vote's
    <document> is the amendment with no number ("S.Amdt."), and the bill is
    in <amendment_to_document_number> ("S. 4668")."""
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None
    return floor_logs.bill_id_from_number(root.findtext(".//amendment/amendment_to_document_number"))


def _store_roll_call(db: Session, chamber: str, congress: int, session: int, number: int,
                     parsed: dict, url: str, bill_id: str | None = None) -> None:
    counts = floor_logs.tally(parsed["members"])
    vote_date = parsed.get("voteDate") or ""
    if chamber == "senate":
        vote_date = _senate_vote_date(vote_date) or ""
    row = RollCall(
        chamber=chamber, congress=congress, session=session, number=number, date=vote_date,
        question=parsed.get("question") or "", title=parsed.get("documentTitle") or parsed.get("voteTitle") or "",
        result=(parsed.get("result") or "")[:120], rejected=parsed.get("rejected"),
        majority_requirement=(parsed.get("majorityRequirement") or "")[:10],
        bill_id=floor_logs.bill_id_from_number(parsed.get("documentName")) or bill_id,
        source_url=url, **counts,
    )
    db.add(row)
    db.flush()
    for m in parsed["members"]:
        db.add(RollCallPosition(
            roll_call_id=row.id, member_id=(m.get("bioguideId") or m.get("lisId") or "")[:12],
            last_name=(m.get("lastName") or "")[:80], first_name=(m.get("firstName") or "")[:80],
            party=(m.get("party") or "")[:2], state=(m.get("state") or "")[:2],
            position=(m.get("voteCast") or "")[:12],
        ))
    db.commit()


async def sync_roll_calls(client: httpx.AsyncClient, db: Session, chamber: str,
                          congress: int, session: int, limit: int = _ROLL_CALL_BATCH) -> tuple[int, str]:
    """New roll calls for one chamber's session -> (stored, outcome)."""
    highest = db.query(func.max(RollCall.number)).filter_by(
        chamber=chamber, congress=congress, session=session,
    ).scalar() or 0
    year = congress_first_year(congress) + session - 1
    stored = 0
    number = highest + 1
    misses = 0
    while stored < limit:
        if chamber == "senate":
            url = floor_logs.senate_roll_call_url(congress, session, number)
        else:
            url = floor_logs.house_roll_call_url(year, number)
        body = await _get(client, url, label=f"{chamber} roll call")
        if body is None:
            return stored, "failed"
        if body is _ABSENT:
            misses += 1
            if misses > _ROLL_CALL_LOOKAHEAD:
                return stored, "ok"
            number += 1
            continue
        text = body.decode("utf-8", errors="replace")
        parsed = (parse_senate_vote_xml(text, congress, session, number) if chamber == "senate"
                  else parse_house_vote_xml(text, year, number))
        if parsed is None:
            return stored, "failed"
        if misses:
            # A number with no file between two that have one: the chamber
            # skipped it. Logged, not retried, so it cannot stall the run.
            logger.warning("%s roll call(s) %d-%d missing before %d (%d-%d)", chamber,
                           number - misses, number - 1, number, congress, session)
            misses = 0
        amended = _senate_amended_bill(text) if chamber == "senate" else None
        _store_roll_call(db, chamber, congress, session, number, parsed, url, bill_id=amended)
        stored += 1
        number += 1
    return stored, "ok"


# ── The run ───────────────────────────────────────────────────────

async def run_congress_sync() -> dict:
    """One pass of all three jobs. Returns and stores its outcome."""
    global _running_since
    _running_since = utcnow()
    db = SessionLocal()
    result: dict = {"startedAt": _running_since.isoformat()}
    try:
        today = eastern_today()
        congress = expected_current_congress()
        async with make_async_client() as client:
            votes: dict[str, dict] = {}
            for chamber in ("senate", "house"):
                for session in (1, 2):
                    n, status = await sync_roll_calls(client, db, chamber, congress, session)
                    votes[f"{chamber}-{session}"] = {"stored": n, "status": status}
            result["rollCalls"] = votes
            result["floorLogs"] = {
                d.isoformat(): await sync_floor_logs(client, db, d)
                for d in (today - timedelta(days=1), today)
            }
            result["digests"] = await sync_digests(client, db, today)
        result["finishedAt"] = utcnow().isoformat()
        api_cache_set(db, _CACHE_TIER, _LAST_RUN_KEY, result, normal_ttl_hours=24 * 30)
        return result
    except Exception:
        logger.exception("Congress sync failed")
        db.rollback()
        raise
    finally:
        db.close()
        _running_since = None


def last_run(db: Session) -> dict | None:
    return api_cache_get(db, _CACHE_TIER, _LAST_RUN_KEY, max_age_hours=24 * 30)


def run_congress_sync_blocking() -> dict:
    return asyncio.run(run_congress_sync())
