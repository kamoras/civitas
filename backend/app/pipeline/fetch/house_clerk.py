"""When each current House member was sworn in, from the Clerk's member list.

clerk.house.gov/xml/lists/MemberData.xml lists every sitting member with a
<sworn-date date="YYYYMMDD">. For a member seated when the Congress convened
it is that January 3; for one who won a special election it is the day they
took the seat. Legislative Effectiveness uses it to compare a mid-Congress
arrival with the share of the Congress they have served (v6.23,
score_calculator.congress_exposure). A vacant seat's entry has no bioguide ID
and an empty date, and is skipped.
"""

import logging
import re
from datetime import date

import httpx
from lxml import etree
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

MEMBER_DATA_URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
_CACHE_KEY = "house-clerk-sworn-dates-v1"
# A special election seats someone a few times a year; a day-old list is
# current enough, and the nightly run reads it once.
_CACHE_HOURS = 20


def parse_sworn_dates(xml: bytes) -> dict[str, str]:
    """{bioguide ID: ISO sworn-in date} from MemberData.xml."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return {}
    dates: dict[str, str] = {}
    for info in root.iterfind("members/member/member-info"):
        bioguide = (info.findtext("bioguideID") or "").strip()
        sworn = info.find("sworn-date")
        raw = (sworn.get("date") or "").strip() if sworn is not None else ""
        if not bioguide or len(raw) != 8 or not raw.isdigit():
            continue
        try:
            dates[bioguide] = date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
        except ValueError:
            continue
    return dates


async def fetch_house_sworn_dates(client: httpx.AsyncClient, db: Session) -> dict[str, str]:
    """{bioguide ID: ISO date} for the sitting House, or {} when the list
    can't be read (the caller then keeps the dates it stored last)."""
    cached = api_cache_get(db, "congress", _CACHE_KEY, max_age_hours=_CACHE_HOURS)
    if cached:
        return cached
    try:
        resp = await client.get(MEMBER_DATA_URL, timeout=DEFAULT_FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("House Clerk member list unavailable: %s", e)
        return {}
    dates = parse_sworn_dates(resp.content)
    if dates:
        api_cache_set(db, "congress", _CACHE_KEY, dates, normal_ttl_hours=_CACHE_HOURS)
    else:
        logger.warning("House Clerk member list had no sworn-in dates — layout changed?")
    return dates


# A voting seat's <district> is "At Large" or an ordinal ("20th"); a
# delegate's reads "Delegate" or "Resident Commissioner" (DC, the
# territories). The Clerk's own wording, read as a form value.
_VOTING_DISTRICT = re.compile(r"At Large|\d+(?:st|nd|rd|th)")


def parse_apportionment(xml: bytes) -> dict[str, dict]:
    """{postal code: {"name": state name, "seats": voting seats}} from
    MemberData.xml, which lists every seat of the House, vacant ones
    included. The apportionment is read here rather than typed in, so a
    new census's reapportionment, or a new state, reaches every consumer
    the night the Clerk's list changes. {} when the list can't be read."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return {}
    out: dict[str, dict] = {}
    for info in root.iterfind("members/member/member-info"):
        state = info.find("state")
        code = (state.get("postal-code") or "").strip() if state is not None else ""
        name = (state.findtext("state-fullname") or "").strip() if state is not None else ""
        if not code or not name or not _VOTING_DISTRICT.fullmatch((info.findtext("district") or "").strip()):
            continue
        out.setdefault(code, {"name": name, "seats": 0})["seats"] += 1
    return out


async def fetch_house_apportionment(client: httpx.AsyncClient) -> dict[str, dict]:
    """The House's apportionment (parse_apportionment), or {} when the
    Clerk's list can't be read — the caller then keeps what it has."""
    try:
        resp = await client.get(MEMBER_DATA_URL, timeout=DEFAULT_FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("House Clerk member list unavailable: %s", e)
        return {}
    seats = parse_apportionment(resp.content)
    if not seats:
        logger.warning("House Clerk member list had no voting seats — layout changed?")
    return seats
