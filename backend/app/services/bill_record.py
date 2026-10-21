"""A bill's full public record, for its detail page: any bill, not only
one a current member sponsored.

From Congress.gov: the bill, its CRS summaries, every action, cosponsors
and text versions. From the roll calls congress_activity.py stored: every
recorded vote on it, with each member's position. Members are linked to
their Civitas pages where the site has one.

A part Congress.gov could not return is named in `unavailable` rather
than shown empty, and only a successful answer is cached, so an outage
never reads as a bill with no actions or no cosponsors.
"""

import asyncio
import html as html_lib
import re
import string
import time
import unicodedata
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.orm import Session

from app.bill_ids import BILL_TYPES
# Re-exported: callers (the pipeline's trend evidence among them) import it from here.
from app.bill_ids import canonical_bill_id as canonical_bill_id
from app.models import ExploreDocument, RepSponsoredBill, RollCall, RollCallPosition, Representative, Senator, SponsoredBill
from app.config import settings
from app.database import off_loop
from app.pipeline.cache import api_cache_get, api_cache_set_async, api_cache_set_many_async
from app.pipeline.fetch.congress import CONGRESS_API_BASE, congress_first_year, congress_gov_bill_url, congress_of_date
from app.pipeline.fetch.daily_digest import heading_bill_ids
from app.pipeline.fetch import floor_logs
from app.pipeline.fetch.bill_display_titles import cached_display_titles, titles_key
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.pipeline.transform.normalize_votes import resolve_senate_lis_ids
from app.services import bill_journey
from app.services.congress_service import amendment_fields, bill_coverage, bill_days, bill_label
from app.services.explore_keys import public_key
from app.services.nomination_links import nomination_votes
from app.services.roll_call_shape import candidate_of, shape

# Congress.gov bill types (app/bill_ids.py, the one definition).
_TYPE_PATH = BILL_TYPES
_CACHE_TIER = "congress"
# A bill's record changes a few times a day at most while it moves.
_CACHE_HOURS = 6
_PARTS = {
    "bill": "",
    "summaries": "/summaries",
    "actions": "/actions?limit=250",
    "cosponsors": "/cosponsors?limit=250",
    "text": "/text",
}
# Where each part's answer sits in Congress.gov's response.
_ANSWER_KEY = {"bill": "bill", "summaries": "summaries", "actions": "actions",
               "cosponsors": "cosponsors", "text": "textVersions"}


def parse_bill_id(bill_id: str) -> tuple[str, int] | None:
    """"S.4668" -> ("s", 4668); None for anything that is not a bill id.
    Split, not matched: the id comes from the URL (CodeQL py/polynomial-redos)."""
    if not bill_id or len(bill_id) > 16 or bill_id.count(".") != 1:
        return None
    prefix, number = bill_id.split(".")
    if prefix not in _TYPE_PATH or not number.isdigit():
        return None
    return _TYPE_PATH[prefix], int(number)


def typed_bill_number(query: str) -> str | None:
    """A search box's text as a bill number, dots, spaces and punctuation
    dropped ("S. 2398" -> "S2398", "h.r.99" -> "HR99", "123" -> "123"), or
    None when it has no digit: "S" alone would match every Senate bill."""
    number = re.sub(r"[^A-Za-z0-9]", "", query or "").upper()
    return number if any(c.isdigit() for c in number) else None


def bill_number_matches(bill_id: str, number: str) -> bool:
    """Whether a stored bill id starts with a typed bill number: a prefix,
    so "HR 99" finds H.R. 997 while it is being typed, but "S 1" never finds
    S. Res. 15; a number alone ("997") matches any bill type. The rule
    `_scorecard_common.vote_search` applies in SQL to a member's votes."""
    bare = bill_id.replace(".", "").replace(" ", "").upper()
    if number.isdigit():
        bare = bare.lstrip(string.ascii_uppercase)
    return bare.startswith(number)


NOT_FOUND = object()

# A reader's lookups get their own spacing, not the pipeline's 1.2/s
# (fetch/congress.py's limiter, which exists to keep a run of thousands of
# requests inside the key's hour). A cold bill page is five requests: at
# 1.2/s they could not finish in under 3.3 s however fast Congress.gov
# answered, and the page waited 3.6-5.8 s for its first byte (measured on
# production, 2026-10-14). How many a reader may make is bounded by the hour
# instead (rate_limit.try_spend_upstream, 200 calls for every reader
# together), well inside what the pipeline leaves of the key's 5,000; this
# only keeps one page's four parallel parts from leaving in the same instant.
_READER_RPS = 10.0
_reader_limiter = RateLimiter(_READER_RPS, name="congress_reader")


async def _congress_get(client: httpx.AsyncClient, url: str):
    """Congress.gov JSON, NOT_FOUND for a 404, or None when the fetch
    failed. The key goes only in the request URL, never the logged one."""
    full = str(httpx.URL(url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY, "format": "json"}))
    resp = await fetch_with_retry(client, _reader_limiter, "GET", url, request_url=full,
                                  expected_statuses=(404,), log_label="Congress API")
    if resp is None:
        return None
    if resp.status_code == 404:
        return NOT_FOUND
    try:
        return resp.json()
    except ValueError:
        return None


async def fetch_bill_record(
    client: httpx.AsyncClient, db: Session, congress: int, bill_id: str,
    spend: Callable[[int], Awaitable[bool]] | None = None, deadline_s: float | None = None,
) -> dict:
    """{bill, summaries, actions, cosponsors, text, unavailable: [...],
    not_found}: not_found when Congress.gov has no such bill.

    `spend(n)` is charged, before the requests it pays for go out, with the
    parts not already cached — the bill first, the rest once it exists (the
    public route's upstream budget, rate_limit.try_spend_upstream). When it
    refuses, those parts are not fetched and are named in `unavailable`,
    like a part Congress.gov didn't answer: the reader gets what is stored,
    and a later visit fetches the rest. Refused with a 503, a spent budget
    took the bill's page down (500, 2026-10-11). A bill Congress.gov
    has no record of is cached too, so the same wrong id asked again costs
    nothing upstream.

    `deadline_s` bounds the whole fetch, since a reader's page waits on it:
    a part still unfetched when time runs out (Congress.gov slow, or a
    retry backing off) is named in `unavailable`, not cached, and fetched
    by a later visit."""
    started = time.monotonic()
    type_path, number = parse_bill_id(bill_id)
    out: dict = {"unavailable": [], "not_found": False}
    keys = {part: f"bill-record-{part}-{congress}-{type_path}-{number}" for part in _PARTS}
    # One thread hop for every part's cache read, and whether a sitting
    # member's sponsored bill is this one: off the event loop.
    cached, known = await off_loop(
        db, lambda session: (
            {part: api_cache_get(session, _CACHE_TIER, key, max_age_hours=_CACHE_HOURS) for part, key in keys.items()},
            bool(_stored_bill(session, congress, bill_id)),
        ),
    )
    if (cached["bill"] or {}).get("not_found"):
        out["not_found"] = True
        return out
    missing = [part for part in _PARTS if cached[part] is None]

    async def charge(n: int) -> bool:
        return spend is None or not n or await spend(n)

    # The bill itself is charged first, the other parts once it exists: a
    # wrong id stops after that one request and must not be charged for the
    # four it never makes. Each charge still comes before its requests. The
    # other parts then go out together: one round trip after the bill's, not
    # four more in a row. A bill the site already holds can't be a wrong id,
    # so all five go out at once.
    fetched: dict = {}  # written in one transaction, in the finally below

    async def get(part: str):
        """Congress.gov's answer for `part` (NOT_FOUND, or None when it
        failed or the deadline passed first)."""
        url = f"{CONGRESS_API_BASE}/bill/{congress}/{type_path}/{number}{_PARTS[part]}"
        if deadline_s is None:
            return await _congress_get(client, url)
        remaining = deadline_s - (time.monotonic() - started)
        try:
            return await asyncio.wait_for(_congress_get(client, url), remaining) if remaining > 0 else None
        except TimeoutError:
            return None

    def keep(part: str, data) -> None:
        if data is None:
            out[part] = None
            out["unavailable"].append(part)
            return
        if data is NOT_FOUND:  # a bill with no summaries, say
            data = {}
        value = data.get(_ANSWER_KEY[part])
        out[part] = value
        fetched[keys[part]] = {"value": value}

    async def fetch_part(part: str) -> None:
        # Kept as it arrives, so a part that came back before a cancellation
        # is still cached below.
        keep(part, await get(part))

    for part in _PARTS:
        if cached[part] is not None:
            out[part] = cached[part].get("value")
    first = missing if known else [part for part in missing if part == "bill"]
    rest = [part for part in missing if part not in first]
    try:
        if first and not await charge(len(first)):
            for part in first:
                keep(part, None)
        elif first == ["bill"]:
            data = await get("bill")
            if data is NOT_FOUND:
                out["not_found"] = True
                await api_cache_set_async(db, _CACHE_TIER, keys["bill"], {"not_found": True},
                                          normal_ttl_hours=_CACHE_HOURS)
                return out
            keep("bill", data)
        elif first:
            await asyncio.gather(*(fetch_part(part) for part in first))
        if rest and ("bill" in out["unavailable"] or not await charge(len(rest))):
            # The bill request failed (an outage, most likely), so the rest
            # would fail the same way; or the budget refused them. Not
            # fetched, and not charged.
            for part in rest:
                keep(part, None)
        elif rest:
            await asyncio.gather(*(fetch_part(part) for part in rest))
        # Named in the order the parts are listed, whichever answered first.
        out["unavailable"].sort(key=list(_PARTS).index)
    finally:
        # Written even when the request is cancelled or fails partway: the
        # parts already fetched were charged to the shared budget, and the
        # next reader shouldn't pay for them again. Shielded, so the
        # cancellation that brought us here doesn't also stop the write.
        await asyncio.shield(api_cache_set_many_async(db, _CACHE_TIER, fetched, normal_ttl_hours=_CACHE_HOURS))
    return out


# ── Shaping ───────────────────────────────────────────────────────

def summary_paragraphs(summary_html: str) -> list[str]:
    """CRS summary HTML -> plain paragraphs. The page renders text, never
    another site's markup."""
    parts = re.split(r"</p>|<br\s*/?>", summary_html or "", flags=re.IGNORECASE)
    out = []
    for p in parts:
        text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", p)).split())
        if text:
            out.append(text)
    return out


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()


def _surname_in(name: str, last: str) -> bool:
    """Whether a roll call's surname is whole words of a folded full name.
    A suffix test missed "pat q. smith jr." for "smith": every Senate vote
    of a senator with a generational suffix went unlinked, and the pages
    that count votes by this lookup (compare, votes with the president)
    found none of them (live, 2026-10-15)."""
    words, key = re.findall(r"[a-z]+", name), re.findall(r"[a-z]+", last)
    return bool(key) and any(words[i:i + len(key)] == key for i in range(len(words) - len(key) + 1))


class MemberLinks:
    """Civitas page ids for members named by bioguide id (House votes,
    sponsors) or by last name and state (Senate votes carry a LIS id)."""

    def __init__(self, db: Session):
        self.by_bioguide: dict[str, str] = {}
        # A House roll call names a member as the Clerk prints them
        # ("Joyce (PA)"), with no first name or district: a reader looking
        # for their own representative needs both.
        self.house_names: dict[str, tuple[str, int | None]] = {}
        self.senators_by_state: dict[str, list[tuple[str, str]]] = {}
        for s in db.query(Senator):
            if s.bioguide_id:
                self.by_bioguide[s.bioguide_id] = s.id
            self.senators_by_state.setdefault(s.state, []).append((_fold(s.name), s.id))
        for r in db.query(Representative):
            if r.bioguide_id:
                self.by_bioguide[r.bioguide_id] = r.id
                self.house_names[r.bioguide_id] = (r.name, r.district)
        self.senate_lis = self._senate_lis_ids(db)

    def _senate_lis_ids(self, db: Session) -> dict[str, str]:
        """{senator id: LIS id} where the roll calls give one last name and
        state to two people (resolve_senate_lis_ids): one senator's votes
        linked to the page of the other senator of that surname and state."""
        people = db.query(
            RollCallPosition.member_id, RollCallPosition.first_name,
            RollCallPosition.last_name, RollCallPosition.state,
        ).join(RollCall, RollCall.id == RollCallPosition.roll_call_id).filter(
            RollCall.chamber == "senate",
        ).distinct().all()
        seen = [{"lisId": m, "firstName": f, "lastName": last, "state": st} for m, f, last, st in people]
        members = []
        for state, senators in self.senators_by_state.items():
            for name, sid in senators:
                last = max((p["lastName"] for p in seen if p["state"] == state and _surname_in(name, _fold(p["lastName"]))),
                           key=len, default="")
                if last:
                    members.append({"id": sid, "name": name, "lastNameForVoteMatch": last, "state": state})
        return resolve_senate_lis_ids(members, seen)

    def page(
        self, *, bioguide: str | None = None, last_name: str = "", state: str = "", lis_id: str | None = None,
    ) -> str | None:
        """The member's Civitas page ("/politicians/ted-cruz"), or None for
        a member the site has no page for (a former member)."""
        found = self.by_bioguide.get(bioguide or "")
        if found is None and last_name and state:
            last = _fold(last_name)
            matches = [sid for name, sid in self.senators_by_state.get(state, []) if _surname_in(name, last)]
            if lis_id is not None:
                # Two senators of one surname and state (a seat passed
                # between them, both on file through the grace period) are
                # told apart by the LIS id each was given; one given another
                # id is someone else. Checking the id only after requiring a
                # single surname match left both senators' votes unlinked.
                mine = [sid for sid in matches if self.senate_lis.get(sid) == lis_id]
                matches = mine or [sid for sid in matches if sid not in self.senate_lis]
            found = matches[0] if len(matches) == 1 else None
        return f"/politicians/{found}" if found else None


def _bucket(position: str) -> str:
    """The same reading as the stored tally (floor_logs.tally), so a
    party's counts add up to the totals beside them — "Guilty" on an
    impeachment article is a yea there, and was "not voting" here. A
    candidate's name (an election of the Speaker) is "candidate", which the
    stored tally counts as not voting (roll_call_shape)."""
    if candidate_of(position):
        return "candidate"
    p = (position or "").strip().lower()
    if p in floor_logs.YEA_POSITIONS:
        return "yea"
    if p in floor_logs.NAY_POSITIONS:
        return "nay"
    if p == "present":
        return "present"
    return "notVoting"


def party_breakdown(positions: list[RollCallPosition]) -> list[dict]:
    """Per party: yeas, nays, present, not voting, counted from positions,
    and in an election of the Speaker each candidate's votes
    (`candidates`, {name: votes}, present only then)."""
    parties: dict[str, dict] = {}
    for p in positions:
        row = parties.setdefault(p.party or "?", {"party": p.party or "?", "yea": 0, "nay": 0, "present": 0,
                                                 "notVoting": 0, "candidates": {}})
        bucket = _bucket(p.position)
        if bucket == "candidate":
            name = candidate_of(p.position)
            row["candidates"][name] = row["candidates"].get(name, 0) + 1
        else:
            row[bucket] += 1
    for row in parties.values():
        if not row["candidates"]:
            del row["candidates"]
    order = {"R": 0, "D": 1, "I": 2, "ID": 2}
    return sorted(parties.values(), key=lambda r: (order.get(r["party"], 9), r["party"]))


def roll_call_summaries(db: Session, refs: list[str | None]) -> dict[str, dict]:
    """The Congress record's own account of each roll call a member's vote
    row names (key_votes.roll_call, "house-119-2-221"): its date, question,
    result, bill, and how each party voted — what a reader needs to see
    why a vote counts as a break with the party. Keyed by ref; a ref the
    record doesn't hold is left out."""
    wanted: dict[tuple, str] = {}
    for ref in refs:
        parts = (ref or "").split("-")
        if len(parts) == 4 and all(x.isdigit() for x in parts[1:]):
            wanted[(parts[0], int(parts[1]), int(parts[2]), int(parts[3]))] = ref
    if not wanted:
        return {}
    rows = db.query(RollCall).filter(
        RollCall.chamber.in_({k[0] for k in wanted}),
        RollCall.congress.in_({k[1] for k in wanted}),
        RollCall.number.in_({k[3] for k in wanted}),
    ).all()
    rows = [rc for rc in rows if (rc.chamber, rc.congress, rc.session, rc.number) in wanted]
    positions: dict[int, list[RollCallPosition]] = {}
    for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_([rc.id for rc in rows])):
        positions.setdefault(p.roll_call_id, []).append(p)
    short = roll_call_short_titles(db, rows)
    nominations = nomination_votes(db, rows)
    return {
        wanted[(rc.chamber, rc.congress, rc.session, rc.number)]: {
            "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
            "date": rc.date, "question": rc.question, "title": rc.title, "result": rc.result,
            "billId": rc.bill_id, "billLabel": bill_label(rc.bill_id), "sourceUrl": rc.source_url,
            **shape(rc, positions.get(rc.id, [])),
            "billShortTitle": short.get(rc.id),
            **amendment_fields(rc),
            # What the vote decided about the nominations its question
            # names (a nomination has no bill); null for any other vote.
            "nominationVote": nominations.get(rc.id),
            "parties": party_breakdown(positions.get(rc.id, [])),
        }
        for rc in rows
    }


def roll_call_short_titles(db: Session, rows: list[RollCall]) -> dict[int, str]:
    """{roll call id: its bill's short or popular title} for the roll calls
    on a bill whose cached Congress.gov titles list names one their own
    title isn't (fetch/bill_display_titles): a vote list then says "One Big
    Beautiful Bill Act" where the Senate's record says "A bill to provide
    for reconciliation pursuant to title II of H. Con. Res. 14." The
    chamber's own title stays on the roll call's page and in `title`."""
    on_bills = [rc for rc in rows if rc.bill_id]
    found = cached_display_titles(db, [(rc.congress, rc.bill_id, rc.title or "") for rc in on_bills])
    return {
        rc.id: found[titles_key(rc.congress, rc.bill_id)].title
        for rc in on_bills if titles_key(rc.congress, rc.bill_id) in found
    }


def vote_detail(db: Session, rc: RollCall, links: MemberLinks | None = None) -> dict:
    links = links or MemberLinks(db)
    positions = db.query(RollCallPosition).filter_by(roll_call_id=rc.id).all()
    def member(p: RollCallPosition) -> dict:
        house = rc.chamber == "house"
        name, district = links.house_names.get(p.member_id or "", (None, None)) if house else (None, None)
        return {
            "lastName": p.last_name, "firstName": p.first_name, "party": p.party, "state": p.state,
            "position": p.position, "bucket": _bucket(p.position), "candidate": candidate_of(p.position),
            # The id the chamber's record gives the member (House: bioguide;
            # Senate: LIS), for the public API's positions.
            "chamberMemberId": p.member_id,
            "page": (links.page(bioguide=p.member_id) if house
                     else links.page(last_name=p.last_name, state=p.state, lis_id=p.member_id)),
            # The member's own name and district where the site knows them;
            # null for a former member (the Clerk's name is all there is).
            "name": name, "district": district,
        }

    members = [member(p) for p in positions]
    members.sort(key=lambda m: (m["lastName"], m["state"]))
    return {
        "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
        "date": rc.date, "question": rc.question, "title": rc.title, "result": rc.result,
        "rejected": rc.rejected, "majorityRequirement": rc.majority_requirement,
        "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
        "billId": rc.bill_id, "billLabel": bill_label(rc.bill_id), "sourceUrl": rc.source_url,
        "billShortTitle": roll_call_short_titles(db, [rc]).get(rc.id),
        **amendment_fields(rc),
        "nominationVote": nomination_votes(db, [rc]).get(rc.id),
        **shape(rc, positions),
        "parties": party_breakdown(positions), "members": members,
    }


_FULL_NAME_RE = re.compile(r"^(?:Sen\.|Rep\.|Del\.|Resident Commissioner)\s+([^,]+),\s+(.+?)\s*\[")


def display_name(p: dict) -> str:
    """"Jane Doe" for Congress.gov's "Sen. Doe, Jane [R-TX]": the page
    shows party and state beside the name already."""
    if p.get("firstName") and p.get("lastName"):
        return " ".join(x for x in (p["firstName"], p.get("middleName"), p["lastName"]) if x)
    m = _FULL_NAME_RE.match(p.get("fullName") or "")
    return f"{m.group(2)} {m.group(1)}" if m else (p.get("fullName") or "")


def _person(p: dict, links: MemberLinks) -> dict:
    return {
        "name": display_name(p), "party": p.get("party") or "", "state": p.get("state") or "",
        "district": p.get("district"), "bioguideId": p.get("bioguideId"),
        "isOriginalCosponsor": p.get("isOriginalCosponsor"), "sponsorshipDate": p.get("sponsorshipDate"),
        "page": links.page(bioguide=p.get("bioguideId")),
    }


def _stored_bill(db: Session, congress: int, bill_id: str) -> dict:
    """The bill as the site's own sponsored-bill rows hold it, in
    Congress.gov's shape, for a record whose bill part could not be fetched
    (Congress.gov down, or the upstream budget spent); empty when no
    sitting member sponsored it."""
    for model in (SponsoredBill, RepSponsoredBill):
        row = db.query(model).filter(model.bill_id == bill_id, model.congress == congress).first()
        if row is not None:
            return {"title": row.title, "introducedDate": row.introduced_date,
                    "latestAction": {"actionDate": row.latest_action_date, "text": row.latest_action}}
    return {}


def bill_votes(db: Session, congress: int, bill_id: str) -> list[dict]:
    """Every stored roll call on the bill, newest first, with its tally and
    each party's split (the bill page's vote panel, and the public API's
    /bills/{id}/votes)."""
    votes = db.query(RollCall).filter(RollCall.bill_id == bill_id, RollCall.congress == congress) \
        .order_by(RollCall.date.desc(), RollCall.number.desc()).all()
    positions: dict[int, list[RollCallPosition]] = {}
    for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_([rc.id for rc in votes])):
        positions.setdefault(p.roll_call_id, []).append(p)
    return [{
        "chamber": rc.chamber, "session": rc.session, "number": rc.number, "date": rc.date,
        "question": rc.question, "title": rc.title, "result": rc.result, "rejected": rc.rejected,
        "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
        "sourceUrl": rc.source_url, **shape(rc, positions.get(rc.id, [])),
        "parties": party_breakdown(positions.get(rc.id, [])),
        **amendment_fields(rc),
    } for rc in votes]


# Explore's floor speeches (explore_pipeline._SPEECH_TYPES).
SPEECH_DOC_TYPES = ("Senate Floor Speech", "House Floor Speech")
# floor_speech.titled_speeches titles a turn "Remarks on <heading>": the
# heading after it is the Record's own.
_REMARKS_ON = re.compile(r"^Remarks on ")


def speech_bills(title: str | None, date: str | None) -> list[dict]:
    """The bills a floor speech's Record heading names ("S.J. RES. 188"),
    each with the Congress of the day it was given: a bill number names a
    different bill in each Congress. Stated identifiers only, never a guess
    from the speech's words."""
    congress = congress_of_date(date or "")
    if congress is None:
        return []
    return [{"billId": b, "label": bill_label(b), "congress": congress} for b in heading_bill_ids(title or "")]


def floor_debate(db: Session, congress: int, bill_id: str) -> list[dict]:
    """The floor speeches whose Record heading names this bill, in the
    bill's Congress, grouped by day, chamber and heading (a debate is one
    heading with many speakers, each listed once at their first turn),
    newest first. Read from the stored
    headings when the page is served; the speech text is never searched."""
    number = bill_id.split(".", 1)[-1]
    rows = (
        db.query(ExploreDocument.id, ExploreDocument.external_id, ExploreDocument.title, ExploreDocument.date,
                 ExploreDocument.chamber, ExploreDocument.politician_name, ExploreDocument.politician_id)
        .filter(ExploreDocument.doc_type.in_(SPEECH_DOC_TYPES), ExploreDocument.title.contains(number))
        .order_by(ExploreDocument.date.desc(), ExploreDocument.id)
        .all()
    )
    groups: dict[tuple, dict] = {}
    for r in rows:
        if congress_of_date(r.date or "") != congress or bill_id not in heading_bill_ids(r.title or ""):
            continue
        heading = _REMARKS_ON.sub("", r.title or "")
        group = groups.setdefault((r.date, r.chamber, heading), {
            "date": r.date, "chamber": (r.chamber or "").lower(), "heading": heading, "speakers": {},
        })
        # A debate is a back-and-forth: each member once, at their first
        # turn, with how many turns they took (each turn is a speech).
        key = r.politician_id or r.politician_name or r.id
        speaker = group["speakers"].setdefault(key, {
            "speaker": r.politician_name or "", "politicianId": r.politician_id, "speechId": r.id,
            "speechKey": public_key(r.external_id, r.id), "speeches": 0,
        })
        speaker["speeches"] += 1
    return [{**g, "speakers": list(g["speakers"].values())} for g in groups.values()]


def _floor(debate: list[dict]) -> dict:
    count = sum(sp["speeches"] for d in debate for sp in d["speakers"])
    return {"floorDebate": debate, "floorSpeechCount": count}


def shape_record(db: Session, congress: int, bill_id: str, raw: dict) -> dict:
    links = MemberLinks(db)
    bill = raw.get("bill") or (_stored_bill(db, congress, bill_id) if "bill" in (raw.get("unavailable") or []) else {})
    summaries = sorted(raw.get("summaries") or [], key=lambda s: s.get("updateDate") or s.get("actionDate") or "")
    latest_summary = summaries[-1] if summaries else None
    vote_summaries = bill_votes(db, congress, bill_id)
    # Its short or popular title from Congress.gov's titles list, as cached
    # (fetch/bill_display_titles): the h1 when there is one, with `title`
    # beside it. Read from the cache, never fetched here, so the page costs
    # no extra request.
    named = cached_display_titles(db, [(congress, bill_id, bill.get("title") or "")]).get(
        titles_key(congress, bill_id),
    )
    actions = bill_journey.fold_actions(raw.get("actions") or [], lambda a: [
        {"chamber": (v.get("chamber") or "").lower(), "number": v.get("rollNumber"),
         "session": v.get("sessionNumber"), "sourceUrl": _roll_call_source(congress, v)}
        for v in a.get("recordedVotes") or []
    ])
    return {
        "billId": bill_id,
        "billLabel": bill_label(bill_id),
        "congress": congress,
        "title": bill.get("title"),
        "shortTitle": named.title if named else None,
        "shortTitleType": named.title_type if named else None,
        "officialTitle": named.official if named else None,
        "introducedDate": bill.get("introducedDate"),
        "originChamber": bill.get("originChamber"),
        "policyArea": (bill.get("policyArea") or {}).get("name"),
        "latestAction": bill.get("latestAction"),
        "sponsors": [_person(p, links) for p in bill.get("sponsors") or []],
        "cosponsors": [_person(p, links) for p in raw.get("cosponsors") or []],
        "cboCostEstimates": [
            {"title": c.get("title"), "description": " ".join((c.get("description") or "").split()),
             "pubDate": c.get("pubDate"), "url": c.get("url")}
            for c in bill.get("cboCostEstimates") or []
        ],
        "summary": ({
            "actionDesc": latest_summary.get("actionDesc"), "actionDate": latest_summary.get("actionDate"),
            "paragraphs": summary_paragraphs(latest_summary.get("text") or ""),
        } if latest_summary else None),
        # Same-day twins folded, each with its code's plain meaning; how
        # each chamber decided it; and its path, dated (bill_journey).
        "actions": actions,
        "passage": bill_journey.passage(actions),
        "journey": bill_journey.journey(actions, parse_bill_id(bill_id)[0]),
        "textVersions": [
            {"type": t.get("type"), "date": (t.get("date") or "")[:10],
             "formats": {f.get("type"): f.get("url") for f in t.get("formats") or []}}
            for t in raw.get("text") or []
        ],
        "votes": vote_summaries,
        **_floor(floor_debate(db, congress, bill_id)),
        "days": bill_days(db, bill_id, congress),
        "congressGovUrl": congress_gov_bill_url(
            congress, _CONGRESS_GOV_TYPE.get(parse_bill_id(bill_id)[0], "bill"), parse_bill_id(bill_id)[1],
        ),
        "unavailable": raw.get("unavailable") or [],
        # Which parts from Civitas's own record are held for this bill's
        # Congress: an empty one outside it is "not held", not "none".
        "coverage": bill_coverage(db, congress),
    }


def _roll_call_source(congress: int, vote: dict) -> str | None:
    """The chamber's own record of a roll call a Congress.gov action names
    ({"chamber", "rollNumber", "sessionNumber"}): the link a reader follows
    to a vote Civitas doesn't hold. None when the action doesn't say which."""
    chamber = (vote.get("chamber") or "").lower()
    try:
        number, session = int(vote.get("rollNumber")), int(vote.get("sessionNumber"))
    except (TypeError, ValueError):
        return None
    if chamber == "senate":
        return floor_logs.senate_roll_call_url(congress, session, number)
    if chamber == "house":
        return floor_logs.house_roll_call_url(congress_first_year(congress) + session - 1, number)
    return None


_CONGRESS_GOV_TYPE = {
    "hr": "house-bill", "s": "senate-bill", "hres": "house-resolution", "sres": "senate-resolution",
    "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution",
}
