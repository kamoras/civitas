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
for the next run: an outage is never "no purpose stated". Rows are read a
batch at a time, newest first, so the first runs fill the back catalogue
and later ones keep up with new votes. Runs after each Congress sync
(scheduler._congress_activity_sync).
"""

import logging

import httpx
from lxml import etree
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import RollCall
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _fetch_with_retry
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Roll calls read per run. A Congress has a few hundred amendment votes, so
# the back catalogue fills in a handful of half-hourly runs; after that a
# run reads only the votes taken since the last.
BATCH = 60
_senate_limiter = RateLimiter(1.0)
# Failed reads in a row that end a run: the source is down, not one file.
_FAILURES_TO_STOP = 3
# An amendment's number and purpose don't change once it is offered.
_CACHE_HOURS = 24 * 365
# Congress.gov's amendment type, as the record prints it.
_TYPE_LABEL = {"HAMDT": "H.Amdt.", "SAMDT": "S.Amdt."}
_NO_PURPOSE = "no statement of purpose on file."


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def candidates(db: Session, limit: int = BATCH) -> list[RollCall]:
    """Unread roll calls that may be on an amendment, newest first. A
    Senate vote names one in its question or title ("S.Amdt.", "Amdt.
    No."); a House vote's question says "Amendment" (which also takes in a
    motion to concur in the Senate's amendments: the house-vote record
    then names no amendment, and the row is read as not on one)."""
    return db.query(RollCall).filter(
        RollCall.amendment.is_(None),
        or_(
            (RollCall.chamber == "senate")
            & (RollCall.question.contains("Amdt") | RollCall.title.contains("Amdt")),
            (RollCall.chamber == "house") & RollCall.question.contains("Amendment"),
        ),
    ).order_by(RollCall.date.desc(), RollCall.id.desc()).limit(limit).all()


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


async def _senate(client: httpx.AsyncClient, rc: RollCall) -> tuple[str, str] | None:
    resp = await fetch_with_retry(client, _senate_limiter, "GET", rc.source_url, log_label="Senate roll call")
    if resp is None or resp.status_code != 200:
        return None
    return parse_senate(resp.content)


async def _congress_json(client: httpx.AsyncClient, db: Session, key: str, path: str) -> dict | None:
    cached = api_cache_get(db, "congress", key, max_age_hours=_CACHE_HOURS)
    if cached is not None:
        return cached
    data = await _fetch_with_retry(client, f"{CONGRESS_API_BASE}/{path}")
    if data is None:
        return None
    api_cache_set(db, "congress", key, data, normal_ttl_hours=_CACHE_HOURS)
    return data


def house_amendment(vote: dict) -> tuple[str, str] | None:
    """(Congress.gov type, number) of the amendment a house-vote record
    names, or None when it names none."""
    record = vote.get("houseRollCallVote") or {}
    kind, number = record.get("amendmentType"), record.get("amendmentNumber")
    return (str(kind).upper(), str(number)) if kind and number else None


async def _house(client: httpx.AsyncClient, db: Session, rc: RollCall) -> tuple[str, str] | None:
    vote = await _congress_json(
        client, db, f"house-vote-{rc.congress}-{rc.session}-{rc.number}",
        f"house-vote/{rc.congress}/{rc.session}/{rc.number}",
    )
    if vote is None:
        return None
    named = house_amendment(vote)
    if named is None:
        return "", ""
    kind, number = named
    record = await _congress_json(
        client, db, f"amendment-{rc.congress}-{kind.lower()}-{number}",
        f"amendment/{rc.congress}/{kind.lower()}/{number}",
    )
    if record is None:
        return None
    label = f"{_TYPE_LABEL.get(kind, kind)} {number}"
    return label, _clean((record.get("amendment") or {}).get("purpose"))


async def fill_amendment_purposes(client: httpx.AsyncClient, db: Session, limit: int = BATCH) -> dict:
    """Read the purpose of up to `limit` unread amendment votes ->
    {"read": n, "failed": n, "status": "ok" | "failed"}. A row that can't
    be read stays unread for the next run, and the run goes on past it (one
    dead file must not hold back every older vote); _FAILURES_TO_STOP in a
    row means the source is down, and the run stops."""
    read = failed = streak = 0
    for rc in candidates(db, limit):
        found = await (_senate(client, rc) if rc.chamber == "senate" else _house(client, db, rc))
        if found is None:
            logger.warning("Amendment purpose: could not read %s roll call %d-%d-%d",
                           rc.chamber, rc.congress, rc.session, rc.number)
            failed += 1
            streak += 1
            if streak >= _FAILURES_TO_STOP:
                return {"read": read, "failed": failed, "status": "failed"}
            continue
        streak = 0
        rc.amendment, rc.amendment_purpose = found[0][:24], found[1]
        db.commit()
        read += 1
    return {"read": read, "failed": failed, "status": "failed" if failed else "ok"}
