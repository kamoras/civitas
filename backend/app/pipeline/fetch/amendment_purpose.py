"""What an amendment a roll call was on would do, in the record's own words.

A vote on an amendment is titled by its sponsor and number ("Smith Amdt.
No. 6805"), and its question names the bill only by number ("... to S. 4668
(No short title on file)"): neither says what the amendment does. The
record does:

- Senate: the roll call's own XML on senate.gov carries <amendment_number>
  and <amendment_purpose>, the purpose the Senate printed for it.
- House: the Clerk's XML names the amendment ("Self of Texas Part A
  Amendment No. 28") but not its number in the Congress's sequence;
  Congress.gov's house-vote record gives that number (`amendmentNumber`,
  `amendmentType` "HAMDT"), and its amendment record the `purpose`.

The purpose is stored verbatim (RollCall.amendment_purpose) and shown as
the record's, never reworded. A failed read leaves the row unread (NULL)
for the next run: an outage is never "no purpose stated". A record that
isn't there (not published yet, or a 404) is retried after a day, then
less often (`amendment_misses`, `amendment_checked_at`). Rows are read a
batch at a time, newest first, so the first runs fill the back catalogue
and later ones keep up with new votes. Runs after each Congress sync
(scheduler._congress_activity_sync).
"""

import logging
from datetime import datetime, timedelta

import httpx
from lxml import etree
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import RollCall
from app.pipeline.cache import api_cache_get, api_cache_set
from app.config import settings
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _rate_limiter as _congress_limiter
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Roll calls read per run. A Congress has a few hundred amendment votes, so
# the back catalogue fills in a handful of half-hourly runs; after that a
# run reads only the votes taken since the last.
BATCH = 60
_senate_limiter = RateLimiter(1.0)
# Real failures (a timeout, a 5xx) in a row that stop a chamber's reads
# for the run: its source is down, not one file. Counted per chamber, so
# a House outage never holds back the Senate's rows.
_FAILURES_TO_STOP = 3
# A row whose record isn't there (Congress.gov publishes a House vote's
# house-vote record a day or so after the vote; a file that 404s) is
# skipped and asked again after a day, then twice as long each time, up to
# a month: it never sits at the top of every batch.
_RETRY_AFTER = timedelta(days=1)
_RETRY_CAP = timedelta(days=30)
# What a read found when the record has no such file (yet).
NOT_THERE = object()
# An amendment's number and purpose don't change once it is offered.
_CACHE_HOURS = 24 * 365
# Congress.gov's amendment type, as the record prints it.
_TYPE_LABEL = {"HAMDT": "H.Amdt.", "SAMDT": "S.Amdt."}
_NO_PURPOSE = "no statement of purpose on file."


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _due(rc: RollCall, now: datetime) -> bool:
    if rc.amendment_checked_at is None or not rc.amendment_misses:
        return True
    wait = min(_RETRY_AFTER * 2 ** (rc.amendment_misses - 1), _RETRY_CAP)
    return now - rc.amendment_checked_at >= wait


def candidates(db: Session, limit: int = BATCH, now: datetime | None = None) -> list[RollCall]:
    """Unread roll calls that may be on an amendment and are due a read,
    newest first. A Senate vote names one in its question or title
    ("S.Amdt.", "Amdt. No."); a House vote's question says "Amendment"
    (which also takes in a motion to concur in the Senate's amendments:
    the house-vote record then names no amendment, and the row is read as
    not on one). A row whose record wasn't there waits out its back-off."""
    now = now or utcnow()
    rows = db.query(RollCall).filter(
        RollCall.amendment.is_(None),
        or_(
            (RollCall.chamber == "senate")
            & (RollCall.question.contains("Amdt") | RollCall.title.contains("Amdt")),
            (RollCall.chamber == "house") & RollCall.question.contains("Amendment"),
        ),
    ).order_by(RollCall.date.desc(), RollCall.id.desc()).all()
    return [rc for rc in rows if _due(rc, now)][:limit]


def parse_senate(xml: bytes) -> tuple[str, str] | None:
    """(amendment, purpose) from a Senate roll call's XML: ("", "") for a
    vote not on an amendment, None for a body that isn't a roll call."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    if root.tag != "roll_call_vote":
        return None
    purpose = _clean(root.findtext("amendment/amendment_purpose"))
    # The Senate's own placeholder for an amendment it printed no purpose
    # for, a fixed form value: stored as "" (the record states none).
    if purpose.casefold() == _NO_PURPOSE:
        purpose = ""
    return _clean(root.findtext("amendment/amendment_number")), purpose


async def _senate(client: httpx.AsyncClient, rc: RollCall):
    """(amendment, purpose); NOT_THERE for a missing file (a 404, or
    senate.gov's redirect to an HTML "not available" page); None when the
    read failed."""
    resp = await fetch_with_retry(client, _senate_limiter, "GET", rc.source_url,
                                  expected_statuses=(404,), log_label="Senate roll call")
    if resp is None:
        return None
    if resp.status_code == 404:
        return NOT_THERE
    return parse_senate(resp.content) or NOT_THERE


async def _congress_json(client: httpx.AsyncClient, db: Session, key: str, path: str):
    """A Congress.gov record, cached once read: the JSON, NOT_THERE for a
    404 (a House vote not published as a house-vote record yet), or None
    when the read failed."""
    cached = api_cache_get(db, "congress", key, max_age_hours=_CACHE_HOURS)
    if cached is not None:
        return cached
    url = f"{CONGRESS_API_BASE}/{path}"
    # The key travels in request_url only, never in what is logged.
    request_url = str(httpx.URL(url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY, "format": "json"}))
    resp = await fetch_with_retry(client, _congress_limiter, "GET", url, request_url=request_url,
                                  expected_statuses=(404,), log_label="Congress API")
    if resp is None:
        return None
    if resp.status_code == 404:
        return NOT_THERE
    try:
        data = resp.json()
    except ValueError:
        return None
    api_cache_set(db, "congress", key, data, normal_ttl_hours=_CACHE_HOURS)
    return data


def house_amendment(vote: dict) -> tuple[str, str] | None:
    """(Congress.gov type, number) of the amendment a house-vote record
    names, or None when it names none."""
    record = vote.get("houseRollCallVote") or {}
    kind, number = record.get("amendmentType"), record.get("amendmentNumber")
    return (str(kind).upper(), str(number)) if kind and number else None


async def _house(client: httpx.AsyncClient, db: Session, rc: RollCall):
    vote = await _congress_json(
        client, db, f"house-vote-{rc.congress}-{rc.session}-{rc.number}",
        f"house-vote/{rc.congress}/{rc.session}/{rc.number}",
    )
    if vote is None or vote is NOT_THERE:
        return vote
    named = house_amendment(vote)
    if named is None:
        return "", ""
    kind, number = named
    record = await _congress_json(
        client, db, f"amendment-{rc.congress}-{kind.lower()}-{number}",
        f"amendment/{rc.congress}/{kind.lower()}/{number}",
    )
    if record is None or record is NOT_THERE:
        return record
    label = f"{_TYPE_LABEL.get(kind, kind)} {number}"
    return label, _clean((record.get("amendment") or {}).get("purpose"))


async def fill_amendment_purposes(client: httpx.AsyncClient, db: Session, limit: int = BATCH) -> dict:
    """Read the purpose of up to `limit` due amendment votes ->
    {"read", "waiting", "failed", "status": "ok" | "failed"}.

    - Read: stored, verbatim.
    - Not there (not published yet, a 404): skipped, retried after its
      back-off, and never counted as a failure.
    - Failed (a timeout, a 5xx): left unread for the next run. After
      _FAILURES_TO_STOP in a row in one chamber, that chamber's source is
      down and its rows wait for the next run; the other chamber's go on.
    """
    now = utcnow()
    read = waiting = failed = 0
    streak = {"senate": 0, "house": 0}
    for rc in candidates(db, limit, now):
        if streak[rc.chamber] >= _FAILURES_TO_STOP:
            continue
        found = await (_senate(client, rc) if rc.chamber == "senate" else _house(client, db, rc))
        if found is None:
            logger.warning("Amendment purpose: could not read %s roll call %d-%d-%d",
                           rc.chamber, rc.congress, rc.session, rc.number)
            failed += 1
            streak[rc.chamber] += 1
            continue
        streak[rc.chamber] = 0
        if found is NOT_THERE:
            rc.amendment_misses = (rc.amendment_misses or 0) + 1
            rc.amendment_checked_at = now
            waiting += 1
        else:
            rc.amendment, rc.amendment_purpose = found[0][:24], found[1]
            rc.amendment_checked_at = now
            read += 1
        db.commit()
    return {"read": read, "waiting": waiting, "failed": failed, "status": "failed" if failed else "ok"}
