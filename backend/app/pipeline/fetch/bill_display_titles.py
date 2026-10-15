"""A bill's short or popular title, verbatim from Congress.gov's titles list.

A member's sponsored-legislation listing gives each bill the title it was
introduced under. A bill given its short title in committee keeps the
official one there: H.R. 1 of the 119th Congress read "An act to provide for
reconciliation pursuant to title II of H. Con. Res. 14." in the bill list, on
its page and in search, where Congress.gov's own titles list says "One Big
Beautiful Bill Act" (Short Title(s) as Passed House; Popular Titles). This
module picks that title from the list, word for word with its title type, and
keeps the titles of every bill that has moved past committee fetched, so the
bill list and the bill page can show it with the official title beside it.

Display only: it lives under fetch/, so the analysis-code fingerprint does not
cover it, and nothing classified or scored reads what it picks.
"""

import json
import logging
from typing import NamedTuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.config_definitions import BILL_STAGES
from app.database import SessionLocal
from app.http_client import make_async_client
from app.models import ApiCache, Representative, RepSponsoredBill, Senator, SponsoredBill
from app.pipeline.fetch.congress import _bill_record_get, fetch_bill_titles_or_none

logger = logging.getLogger(__name__)

# Congress.gov's title types, as its titles list names them (a documented
# vocabulary: "Short Title(s) as Passed House", "Short Titles as Enacted",
# "Popular Titles"). A "... for portions of this bill" title names one part
# of an omnibus ("FEHB Protection Act of 2025" inside H.R. 1), never the bill.
_SHORT = "short title"
_PORTION = "portion"
_ENACTED = "as enacted"
_POPULAR = "popular title"
_OFFICIAL = "official title"

# A short title is usually given at introduction, and then the listing title
# already is it. One added later is added in committee or on the floor, so
# the bills that can have one the listing lacks are those reported out of
# committee or further (V&W's "action beyond committee").
_REPORTED_ORDER = BILL_STAGES["REPORTED"]["order"]
ADVANCED_STAGES = tuple(code for code, s in BILL_STAGES.items() if s["order"] >= _REPORTED_ORDER)

# Titles fetched per pass, at most (each one request on the Congress.gov
# limiter). The first pass after this shipped has a few hundred to read; an
# hourly pass after that finds a handful of bills newly moved.
MAX_FETCHES_PER_PASS = 150


class DisplayTitle(NamedTuple):
    title: str
    # Its type exactly as Congress.gov's list names it.
    title_type: str
    # The bill's official title from the same list (the one as enacted, else
    # the newest), or None when the list gives none. Not the listing title:
    # that can be a short title the bill no longer goes by.
    official: str | None


def display_title(titles: list[dict], listed: str) -> DisplayTitle | None:
    """The short or popular title to show for the bill whose titles list is
    `titles` and whose listing title is `listed`, or None when the listing
    title is already one of the bill's short or popular titles, or the list
    has none.

    The short title as enacted if there is one; else the newest whole-bill
    short title (as passed, as reported, from a later text); else the
    popular title (the name the Library of Congress records a bill as
    commonly known by, which an enacted law whose short title the Senate
    struck can still have).

    A bill often has two short titles, an acronym and its expansion ("TAKE
    IT DOWN Act" and "Tools to Address Known Exploitation by Immobilizing
    Technological Deepfakes on Websites and Networks Act"), and the listing
    shows one of them; that is already its short title, and is kept."""
    def kind(t: dict) -> str:
        return (t.get("titleType") or "").lower()

    named = [t for t in titles if t.get("title") and (_SHORT in kind(t) or _POPULAR in kind(t))]
    if (listed or "").strip().casefold() in {t["title"].strip().casefold() for t in named}:
        return None
    whole = [t for t in named if _SHORT in kind(t) and _PORTION not in kind(t)]
    enacted = [t for t in whole if _ENACTED in kind(t)]
    newest = sorted(whole, key=lambda t: t.get("updateDate") or "", reverse=True)
    popular = [t for t in named if _POPULAR in kind(t)]
    officials = sorted(
        (t for t in titles if t.get("title") and kind(t).startswith(_OFFICIAL)),
        key=lambda t: (_ENACTED in kind(t), t.get("updateDate") or ""), reverse=True,
    )
    for pick in (*enacted, *newest, *popular):
        return DisplayTitle(pick["title"].strip(), pick.get("titleType") or "",
                            officials[0]["title"].strip() if officials else None)
    return None


def titles_key(congress: int, bill_id: str) -> str:
    """The api_cache key fetch_bill_titles_or_none stores a bill's titles
    under, for a site bill id ("HR.1": its type is the part before the dot)."""
    bill_type, _, number = bill_id.partition(".")
    return f"bill-titles-{congress}-{bill_type.lower()}-{number}"


def cached_display_titles(db: Session, bills: list[tuple[int, str, str]]) -> dict[str, DisplayTitle]:
    """{titles_key: DisplayTitle} for each of `bills`
    ((congress, bill id, listing title)) whose cached titles list names a
    short or popular title the listing title isn't (display_title). Read
    from the cache only, by primary key, for the read path: a bill whose
    list was never fetched has none."""
    listed = {titles_key(congress, bill_id): title for congress, bill_id, title in bills}
    found: dict[str, DisplayTitle] = {}
    # One range scan of the primary key per Congress ("bill-titles-119-…"),
    # not an IN list of every bill's key: the bill list asks for ~19,000,
    # and 39 IN queries doubled its rebuild.
    for congress in {congress for congress, _, _ in bills}:
        prefix = f"bill-titles-{congress}-"
        for key, data in (
            db.query(ApiCache.cache_key, ApiCache.data_json)
            .filter(ApiCache.tier == "congress", ApiCache.cache_key >= prefix,
                    ApiCache.cache_key < prefix[:-1] + ".")
        ):
            if key not in listed:
                continue
            try:
                titles = json.loads(data)
            except (TypeError, ValueError):
                continue
            picked = display_title(titles, listed[key]) if isinstance(titles, list) else None
            if picked:
                found[key] = picked
    return found


def _advanced_bills(db: Session) -> list[tuple[int, str, str]]:
    """(congress, bill_type, bill_id) of each sitting member's bill of the
    current Congress that has moved past committee."""
    found: set[tuple[int, str, str]] = set()
    for model, member, fk in (
        (SponsoredBill, Senator, SponsoredBill.senator_id),
        (RepSponsoredBill, Representative, RepSponsoredBill.representative_id),
    ):
        found.update(
            db.query(model.congress, model.bill_type, model.bill_id)
            .join(member, fk == member.id)
            .filter(member.is_current == True)  # noqa: E712
            .filter(model.congress >= settings.CURRENT_CONGRESS)
            .filter(model.stage.in_(ADVANCED_STAGES))
            .all()
        )
    return sorted(found)


async def refresh_display_titles(db: Session | None = None) -> dict:
    """Fetch the titles list of each advanced bill whose cached copy is
    missing or stale (bill_refresh marks a bill's records stale when the
    bill moves), up to MAX_FETCHES_PER_PASS; the rest wait for the next
    pass. A failed fetch is counted and retried next pass, never stored as
    "no titles". Pass `db` for tests; production opens its own session."""
    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        async with make_async_client() as client:
            return await _refresh(db, client)
    finally:
        if owns_session:
            db.close()


async def _refresh(db: Session, client: httpx.AsyncClient) -> dict:
    fetched = failed = pending = 0
    for congress, bill_type, bill_id in _advanced_bills(db):
        if _bill_record_get(db, congress, titles_key(congress, bill_id)) is not None:
            continue
        if fetched + failed >= MAX_FETCHES_PER_PASS:
            pending += 1
            continue
        number = int(bill_id.split(".")[-1])
        if await fetch_bill_titles_or_none(client, db, congress, bill_type.lower(), number) is None:
            failed += 1
        else:
            fetched += 1
    if failed:
        logger.warning("Display titles: %d of %d bill titles lists could not be read", failed, fetched + failed)
    return {"fetched": fetched, "failed": failed, "pending": pending}
