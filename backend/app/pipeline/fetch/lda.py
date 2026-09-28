"""Senate Lobbying Disclosure Act (LDA) filings — registered lobbying spend,
and the specific bills each filing says the client lobbied on.

The donor-vote overlap feature historically carried a lobbying_spend field
that was 0 for every match (the 2026-07 adversarial audit flagged the
feature as mislabeled). This module fills it with actual registered
federal lobbying activity: for an organization, the sum of
registrant-reported income (outside firms hired by the org) plus
self-reported expenses (in-house lobbying) across a filing year. The same
filings list, per issue area, the legislation lobbied on; those bill
numbers are matched against the member's own votes
(analyze/lobbying_records.py), which turns a topical overlap into a
record: a filing for a client of this donor's name names this bill, and
the member voted on it. The client is always shown: a name the donor's
begins can be a subsidiary or a separate company (is_same_client).

The registry moved from lda.senate.gov to lda.gov in 2026. The old host
answers with a 301, which httpx does not follow by default, so every lookup
failed and was reported as "no registered lobbying" — the spend on all 21
live senate matches read $0 (checked 2026-09-27, Goldman Sachs and BlackRock
among them). Hence three things below: the new base, redirects followed,
and a failed lookup that says it failed (None, `lobbyingChecked: False`)
instead of posing as a real zero, with an ops alert when a whole run's
lookups fail.

Notes on interpretation:
- Amounts are order-of-magnitude signals, not audited totals — quarterly
  amendments can double-count. Good enough to distinguish "this org lobbies
  Washington with $2M/yr" from "no registered lobbying at all".
- The registry's client-name search is loose ("APPLE" returns Appleton
  International Airport), so each filing's client is checked against the
  searched name before its amounts or bills count (is_same_client).
- The client searched for is a PAC's connected organization when the FEC
  records one ("JPMORGAN CHASE & CO." for its federal PAC): the registry
  lists the company, and the PAC's own name matches no client at all.
- The API is public, no key required; the anonymous rate limit is low, so
  results are cached hard in api_cache and only matched donor orgs are
  ever queried.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.pipeline.analyze.lobbying_records import (
    BILL_TITLE_MATCH_MIN,
    TitlePool,
    bill_mentions,
    names_bill,
    title_match_score,
)
from app.pipeline.cache import api_cache_get, api_cache_set, api_cache_stamp
from app.pipeline.fetch.congress import congress_first_year, congress_for_year
from app.pipeline.fetch.floor_logs import bill_id_from_number
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.services.congress_service import bill_label
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

LDA_API_BASE = "https://lda.gov/api/v1"

# The registry allows ~15 requests/minute anonymously; a free API key
# (LDA_API_KEY, sent as "Authorization: Token <key>", the API's Django REST
# Framework token scheme) raises the limit. One request a second stays well
# under the registered limit. Each matched organization costs a request per
# filing year per page, so the key is the difference between minutes and
# an hour of the nightly run when many organizations are new to the cache.
_rate_limiter = RateLimiter(1.0 if settings.LDA_API_KEY else 0.2)

# A finished year's filings change only by amendment. The current year's
# grow once a quarter (reports are due 20 days after it ends), so a week-old
# copy misses at most one quarter's reports for a few days.
_FINISHED_YEAR_CACHE_HOURS = 24 * 30
_CURRENT_YEAR_CACHE_HOURS = 24 * 7
# Bounds on what one org-year keeps: a heavy lobbyist files dozens of
# reports, and each names the same bills quarter after quarter.
_MAX_PAGES = 10
_MAX_MENTIONS = 1500

# Bills listed per match, newest filing first.
MAX_LOBBIED_BILLS = 10


@dataclass
class LobbyingActivity:
    """One organization's registered lobbying in one filing year."""

    total: float
    # {"billId", "before", "after", "filingYear", "filings": [{"url",
    #  "registrant", "posted"}]}: one mention per distinct wording, with every
    # filing that used it
    mentions: list[dict] = field(default_factory=list)
    # False when the page cap was hit: the total is a lower bound.
    complete: bool = True
    # [(client name, amount)] for the filings counted, largest first: the
    # registry's own names, shown beside the total (see is_same_client).
    clients: list[tuple[str, float]] = field(default_factory=list)


def _sum_filing_amounts(results: list[dict]) -> float:
    """Sum lobbying income+expenses across filings, skipping registrations.

    Registration filings (RR) carry no amounts; termination filings can.
    Income = what outside firms report earning from this client;
    expenses = what the org reports spending on in-house lobbying.
    """
    total = 0.0
    for filing in results or []:
        ftype = (filing.get("filing_type") or "").upper()
        if ftype.startswith("RR"):
            continue
        for field_name in ("income", "expenses"):
            val = filing.get(field_name)
            if val:
                try:
                    total += float(val)
                except (TypeError, ValueError):
                    pass
    return total


def _filing_mentions(results: list[dict]) -> list[dict]:
    """Every bill number named in the filings' activity descriptions, one
    entry per (filing, bill, wording)."""
    out: list[dict] = []
    for filing in results or []:
        registrant = (filing.get("registrant") or {}).get("name") or ""
        for activity in filing.get("lobbying_activities") or []:
            for bill_id, before, after in bill_mentions(activity.get("description") or ""):
                out.append({
                    "billId": bill_id,
                    "before": before,
                    "after": after,
                    "filingYear": filing.get("filing_year"),
                    "filings": [{
                        "url": filing.get("filing_document_url") or "",
                        "registrant": registrant,
                        "client": _client_name(filing),
                        "posted": filing.get("dt_posted") or "",
                    }],
                })
    return out


def _year_is_closed(year: int) -> bool:
    """Whether a filing year's reports are all in: the fourth-quarter
    report is due January 20 of the next year (2 U.S.C. 1604(a)), so until
    February a finished year is still growing."""
    now = utcnow()
    return year < now.year - 1 or (year == now.year - 1 and now.month >= 2)


# Legal-form words that end a company's name ("JPMORGAN CHASE & CO.",
# "PFIZER INC."). The FEC lists a PAC's sponsor under one form and the
# registry the client under another ("JPMORGAN CHASE HOLDINGS LLC"), so the
# search name drops a trailing one. A naming convention of business
# entities, not a classification: nothing is decided from these words.
_LEGAL_FORM_WORDS = frozenset({
    "INC", "INCORPORATED", "LLC", "LLP", "LP", "CO", "CORP", "CORPORATION",
    "COMPANY", "LTD", "PLC", "NA",
})


def _name_key(name: str) -> str:
    """Upper-case words and digits only, a leading "THE" dropped."""
    words = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).split()
    return " ".join(words[1:] if words[:1] == ["THE"] else words)


def search_name(org_name: str) -> str:
    """The name to search the registry for: the organization's name without
    trailing legal-form words."""
    words = _name_key(org_name).split()
    # "ELI LILLY AND COMPANY" and "ELI LILLY & COMPANY" are one name; the
    # ampersand is already gone as punctuation, so a dangling AND goes too.
    while len(words) > 1 and (words[-1] in _LEGAL_FORM_WORDS or words[-1] == "AND"):
        words.pop()
    return " ".join(words)


def is_same_client(searched: str, client_name: str) -> bool:
    """Whether a filing's client is the organization searched for.

    The registry's client_name filter matches loosely: measured on 2025
    filings, "APPLE" also returns Appleton International Airport and the US
    Apple Association, "KOCH" returns Kochava and a Dr. Kocho Angjushev,
    "NATIONAL EDUCATION ASSOCIATION" returns the National Association for
    Music Education. Only filings whose client is the organization count
    toward its spend and bills: the client's name begins with the searched
    name at a word boundary ("PFIZER INC.", "KOCH GOVERNMENT AFFAIRS,
    LLC"), or names it after "on behalf of", "OBO", "d/b/a" or an opening
    parenthesis (a registrant filing for the client: "WILMERHALE ON BEHALF
    OF APPLE INC."). A similarity ratio was tried and dropped: it accepted
    the American Veterinary Medical Association for the American Medical
    Association.

    A prefix also admits a separate company that shares the name: an
    independent bottler ("COCA-COLA BOTTLING COMPANY UNITED") beside The
    Coca-Cola Company, Boeing Employees' Credit Union beside Boeing. No
    name rule tells those from the organization's own filing entities
    (JPMORGAN CHASE HOLDINGS LLC, KOCH GOVERNMENT AFFAIRS, GOOGLE CLIENT
    SERVICES LLC — measured on 48 large clients' 2025 filings, 15 file
    only under such longer names), and choosing between them by rule
    failed in review both ways. So nothing here claims the client *is*
    the organization: every amount and every linked bill carries the
    client name the registry filed it under, and the page shows it.
    """
    q = searched
    if not q or not _name_key(client_name):
        return False
    return any(
        name == q or name.startswith(q + " ")
        for name in _names_in_client(client_name)
    )


# How the registry's client field is read. One tokenizer and one split,
# shared by the client test and the filer display, so the two can't drift:
# punctuation becomes spaces (every spelling of a marker — "O/B/O", "O.B.O.",
# "ON-BEHALF-OF" — reads the same) while parentheses are kept as tokens, so
# a parenthesised part still belongs to the side of the marker it was on.
_MARKER_RE = re.compile(r"(?<!\S)(?:ON BEHALF OF|OBO|O B O)(?!\S)")
_DBA_RE = re.compile(r"(?<!\S)(?:D B A|DBA)(?!\S)")
# A pronoun right after the last marker points back at the name before it:
# "ON BEHALF OF ITSELF AND ITS SUBSIDIARIES", "... OF ITS MEMBERS". The
# phrase can also go on to name another party ("ON BEHALF OF THEIR CLIENT
# ASLRRA", "ON BEHALF OF ITS WHOLLY OWNED SUBSIDIARY HOSPIRA"). Grammar,
# not a classification.
# "Its client ..." / "their clients ...": the pronoun introduces the firm's
# client. Only when CLIENT(S) is the next word: "ITSELF AND ITS CLIENTS"
# still points back at the filer first.
# "Itself"/"themselves" anywhere in the phrase: the filer is a party, in
# either order ("ITSELF AND ITS CLIENTS", "ITS CLIENTS AND ITSELF").
_REFLEXIVE_RE = re.compile(r"(?<!\S)(?:ITSELF|THEMSELVES)(?!\S)")
_CLIENT_OF_FIRM_RE = re.compile(r"^\s*\(?\s*(?:ITS|THEIR)\s+CLIENTS?(?!\S)")
_PRONOUN_RE = re.compile(r"^\s*\(?\s*(?:ITSELF|ITS|THEMSELVES|THEIR)(?!\S)")
_PAREN_RE = re.compile(r"\(([^()]*)\)")


def _client_tokens(text: str) -> str:
    """Upper-case words and digits, with "(" and ")" kept as their own
    tokens: "SMITH LLP (O.B.O. APPLE, INC.)" -> "SMITH LLP ( O B O APPLE INC )"."""
    spaced = re.sub(r"[^A-Z0-9()]+", " ", (text or "").upper())
    return " ".join(spaced.replace("(", " ( ").replace(")", " ) ").split())


def _split_client(client_name: str) -> tuple[str, list[str]]:
    """(filing firm part, party parts) of a client field, as tokens.

    A filing can pass through a chain of firms ("HARRIS LAW FIRM OBO ROBBINS
    SALOMON & PATT OBO COALITION OF GM CRASH VICTIMS"): the firm part is
    what precedes the first on-behalf-of marker and the party is what
    follows the last. "O/B/O" alone is used on 129 of 2025's filings
    ("GIBSON, DUNN & CRUTCHER LLP (O/B/O PEPSICO, INC.)"). With no marker
    there is no firm part and the field is the party.

    When the party begins with a pronoun, any name ending the phrase is a
    party: every run of its last words is offered ("THEIR CLIENT ASLRRA",
    "CLIENT ASLRRA", "ASLRRA"), since where a descriptor ends and a name
    starts can't be read from the field. The name before the marker is a
    party too when the pronoun points back at it ("ON BEHALF OF ITSELF AND
    ITS SUBSIDIARIES", "... OF THEIR MEMBERS"), not when it introduces the
    firm's client ("... OF ITS CLIENT BOEING").
    """
    tokens = _client_tokens(client_name)
    markers = list(_MARKER_RE.finditer(tokens))
    if not markers:
        return "", [tokens]
    firm = tokens[:markers[0].start()]
    party = tokens[markers[-1].end():]
    pronoun = _PRONOUN_RE.match(party)
    if not pronoun:
        return firm, [party]
    start = markers[-2].end() if len(markers) > 1 else 0
    before = tokens[start:markers[-1].start()]
    words = party.replace("(", " ").replace(")", " ").split()
    # Every trailing run, the pronoun's own word included: a client can be
    # named "ITS AMERICA".
    runs = [" ".join(words[i:]) for i in range(len(words))]
    # The pronoun points back at the name before the marker ("ITSELF", "ITS
    # MEMBERS", "THEIR MEMBERS") unless it introduces the firm's client
    # ("ITS CLIENT BOEING", "THEIR CLIENTS ..."), whose filing it then is.
    # One reading this can't separate: a client whose own name begins with
    # the pronoun, filed by a firm ("SMITH LLP ON BEHALF OF ITS AMERICA"),
    # also offers the firm; the page shows the full client field beside it.
    own = bool(_REFLEXIVE_RE.search(party)) or not _CLIENT_OF_FIRM_RE.match(party)
    return firm, ([before] if own else []) + runs


def _names_of(part: str) -> list[str]:
    """Name keys a token part carries: the part, each parenthesised piece
    of it, and each side of a d/b/a in any of those."""
    pieces = [part] + _PAREN_RE.findall(part)
    names: list[str] = []
    for piece in pieces:
        names.append(_name_key(piece))
        names.extend(_name_key(side) for side in _DBA_RE.split(piece))
    return [n for n in dict.fromkeys(names) if n]


def _names_in_client(client_name: str) -> list[str]:
    """The names of the party a registry client field is for (_split_client),
    with a registrant filing as "THE LIVINGSTON GROUP, LLC (VERIZON
    COMMUNICATIONS, INC.)" read as naming Verizon."""
    return [n for part in _split_client(client_name)[1] for n in _names_of(part)]


def _filed_by(client_name: str | None, registrant: str | None) -> str | None:
    """The registrant to name beside a filing's client, or None when the
    client field already names it: as the firm part before "on behalf of",
    as the text before a parenthesised client ("THE LIVINGSTON GROUP, LLC
    (VERIZON ...)"), as the whole field (in-house), or as either name of a
    d/b/a in any of those."""
    if not registrant:
        return None
    reg_key, reg_search = _name_key(registrant), search_name(registrant)
    firm, _ = _split_client(client_name or "")
    whole = _client_tokens(client_name or "")
    parts = [firm] if firm else [whole, whole.split("(")[0]]
    for name in (n for part in parts for n in _names_of(part)):
        if name == reg_key or search_name(name) == reg_search:
            return None
    return registrant


def _client_name(filing: dict) -> str:
    return (filing.get("client") or {}).get("name", "")


def _cache_key(org_key: str, year: int) -> str:
    # Include a stable hash of the full org key so two different orgs that
    # share an 80-char prefix (e.g. federal vs. state PAC variants of one
    # sponsor) can't collide onto one cached figure.
    key_hash = hashlib.sha256(org_key.encode()).hexdigest()[:12]
    return f"lda-activity-v7-{year}-{org_key[:60]}-{key_hash}"


async def fetch_lobbying_activity(
    client: httpx.AsyncClient, db: Session, org_name: str, year: int,
) -> LobbyingActivity | None:
    """Registered lobbying by an organization in a filing year: total spend
    and the bills its filings name.

    An organization with no registered lobbying gets a real zero (which is
    itself meaningful). A failed lookup returns None and is not cached, so
    it can't be mistaken for, or remembered as, "no lobbying".
    """
    org_key = search_name(org_name)
    if len(org_key) < 2:
        # Nothing to search for: unknown, not a verified zero.
        return None

    ttl = _FINISHED_YEAR_CACHE_HOURS if _year_is_closed(year) else _CURRENT_YEAR_CACHE_HOURS
    cache_key = _cache_key(org_key, year)
    stamp = api_cache_stamp(db, "lda", cache_key, max_age_hours=ttl)
    if stamp is not None:
        held = _activities.get(cache_key)
        if held and held[0] == stamp:
            return held[1]
        cached = api_cache_get(db, "lda", cache_key, max_age_hours=ttl)
        if cached is not None:
            return _remember(cache_key, stamp, _activity_from(
                org_key, cached.get("filings") or [], bool(cached.get("complete", True)),
            ))

    # Follow pagination: a heavy-lobbying client can file dozens to
    # low-hundreds of filings a year (multiple outside firms × quarterly
    # reports + in-house). Summing only the first page systematically
    # UNDERcounted exactly the biggest spenders — and the figure is shown
    # verbatim in user-facing text. Bounded to keep one pathological org
    # from stalling the enrichment loop; the cap is logged if hit so a
    # silent truncation can't masquerade as a complete total.
    filings: list[dict] = []
    url: str | None = f"{LDA_API_BASE}/filings/"
    params: dict | None = {"client_name": org_key, "filing_year": year, "page_size": 25}
    headers = {"Authorization": f"Token {settings.LDA_API_KEY}"} if settings.LDA_API_KEY else None
    pages = 0
    try:
        while url and pages < _MAX_PAGES:
            await _rate_limiter.acquire()
            resp = await client.get(
                url, params=params, headers=headers, timeout=DEFAULT_FETCH_TIMEOUT_S,
                follow_redirects=True,
            )
            if resp.status_code == 429:
                logger.warning("LDA rate limited for %s — skipping (uncached)", org_key)
                return None
            resp.raise_for_status()
            data = resp.json()
            filings.extend(data.get("results", []))
            url = data.get("next")  # absolute URL from the API, or None
            params = None  # `next` already encodes the query
            pages += 1
    except Exception as exc:
        logger.warning("LDA fetch failed for %s: %s", org_key, exc)
        return None

    complete = not (url and pages >= _MAX_PAGES)
    if not complete:
        logger.warning(
            "LDA activity for %s (%d) hit the %d-page cap — total may be a lower bound",
            org_key, year, _MAX_PAGES,
        )
    # The search results are cached as they came (only the fields read), and
    # which of them are the organization's is decided on every read: a
    # change to the matching rules applies at once, not when a 30-day entry
    # expires.
    compact = [_compact_filing(f) for f in filings]
    api_cache_set(db, "lda", cache_key, {"filings": compact, "complete": complete}, normal_ttl_hours=ttl)
    return _remember(cache_key, api_cache_stamp(db, "lda", cache_key, max_age_hours=ttl),
                     _activity_from(org_key, compact, complete))


# Derived activity by cache key, valid while the cache entry it came from is
# unchanged (same write time): one trade group can head a hundred members'
# matches, and re-matching its search results for each would repeat the
# same work. An expired, rewritten or cleared entry is read afresh. Held for
# one day at most, so the process doesn't accumulate years of searches.
_activities: dict[str, tuple[datetime | None, LobbyingActivity]] = {}
_activities_day: list[date] = []


def _remember(cache_key: str, stamp: datetime | None, activity: LobbyingActivity) -> LobbyingActivity:
    today = utcnow().date()
    if _activities_day != [today]:
        _activities.clear()
        _activities_day[:] = [today]
    if stamp is not None:
        _activities[cache_key] = (stamp, activity)
    return activity


def _compact_filing(filing: dict) -> dict:
    """The fields of a filing this module reads, in the API's own shape."""
    return {
        "client": {"name": _client_name(filing)},
        "registrant": {"name": (filing.get("registrant") or {}).get("name") or ""},
        "filing_type": filing.get("filing_type"),
        "income": filing.get("income"),
        "expenses": filing.get("expenses"),
        "filing_year": filing.get("filing_year"),
        "filing_document_url": filing.get("filing_document_url"),
        "dt_posted": filing.get("dt_posted"),
        "lobbying_activities": [
            {"description": a.get("description") or ""} for a in filing.get("lobbying_activities") or []
        ],
    }


def _activity_from(org_key: str, filings: list[dict], complete: bool) -> LobbyingActivity:
    """The organization's activity in one year's search results: the filings
    whose client matches (is_same_client), their total by client, and the
    bills they name."""
    own = [f for f in filings if is_same_client(org_key, _client_name(f))]
    by_client: dict[str, float] = {}
    for f in own:
        by_client[_client_name(f)] = by_client.get(_client_name(f), 0.0) + _sum_filing_amounts([f])
    clients = sorted(by_client.items(), key=lambda c: (-c[1], c[0]))
    # Quarterly reports repeat the same description word for word: keep one
    # entry per wording (the matcher's unit of work) carrying every filing
    # that used it, so the page can link the latest and count the rest.
    merged: dict[tuple[str, str, str], dict] = {}
    for m in _filing_mentions(own):
        key = (m["billId"], " ".join(m["before"].split()), " ".join(m["after"].split()))
        if key in merged:
            known = {f["url"] for f in merged[key]["filings"]}
            merged[key]["filings"].extend(f for f in m["filings"] if f["url"] not in known)
        else:
            merged[key] = m
    return LobbyingActivity(
        total=_sum_filing_amounts(own),
        mentions=list(merged.values())[:_MAX_MENTIONS],
        complete=complete,
        clients=clients,
    )


# What the vote shown decided, when it wasn't the vote on the bill itself,
# for each motion type bill_learning.classify_motion_type returns. Neutral
# on purpose: "veto" also covers motions to refer or table a veto message,
# where a Yea is not an override. Kept beside the classifier's types (the
# test holds them in step) so the page never guesses a label.
_VOTE_CONTEXT = {
    "passage": "",
    "amendment": "on an amendment to it",
    "cloture": "on a cloture motion",
    "procedural": "on a procedural motion",
    "veto": "on a motion about the President's veto",
    "nomination": "on a nomination",
}


def vote_context(motion_type: str | None) -> str:
    """"" for the vote on the bill itself; otherwise which vote it was. An
    unrecognized or unrecorded motion must not read as passage."""
    if motion_type in _VOTE_CONTEXT:
        return _VOTE_CONTEXT[motion_type]
    return "on a motion, not necessarily passage"


def _voted_bills(votes: list[dict] | None) -> dict[str, dict]:
    """The member's Yea/Nay vote on each bill, keyed the way bill_mentions
    names bills ("HR.1492"): the latest vote on passage when there is one,
    else the latest vote of any kind (motionType then says which).

    A vote's billId comes in the Senate's spelling ("H.R. 1492", "S. 4668")
    or the site's ("HR.1492"); a recent House roll call's billId is
    synthetic ("HouseRC-2026-309"), so the House pipeline carries the roll
    call's own measure as `measureId`. Cloture, amendment and recommit votes
    carry the bill they were on, which is why passage is preferred: the
    member's Nay on a motion to recommit is not their vote on the bill.
    Dates come as ISO (House, key votes) or Senate.gov's "October 14, 2025,
    05:34 PM", so they are compared as vote_date_iso. Nominations name no
    bill and are skipped."""
    from app.pipeline.transform.normalize_votes import vote_date_iso

    def rank(v: dict) -> tuple[bool, str]:
        return (v.get("motionType") == "passage", vote_date_iso(v.get("date")) or "")

    out: dict[str, dict] = {}
    for v in votes or []:
        if v.get("vote") not in ("Yea", "Nay"):
            continue
        key = v.get("measureId") or bill_id_from_number(v.get("billId"))
        if key and (key not in out or rank(v) > rank(out[key])):
            out[key] = v
    return out


async def _bill_titles(
    client: httpx.AsyncClient, db: Session, congress: int, bill_key: str,
) -> list[str] | None:
    """Every title Congress.gov records for a bill (cached; the
    significant-bill fetch has usually already paid for it). [] when the
    congress has no such bill; None when the fetch failed, which the caller
    must not read as "no such bill"."""
    from app.pipeline.fetch.congress import fetch_bill_titles_or_none

    prefix, _, number = bill_key.partition(".")
    try:
        rows = await fetch_bill_titles_or_none(client, db, congress, prefix.lower(), int(number))
    except Exception:
        logger.exception("Bill titles unavailable for %s", bill_key)
        return None
    if rows is None:
        return None
    return [r.get("title") for r in rows if r.get("title")]


# The index over a congress's ~16,000 titles is built once and shared by
# every member of a run: {congress: (pool or None, built at)}. A pool is
# reused for a day; a failed listing is retried after half an hour rather
# than on every member (each attempt is ~65 requests).
_pools: dict[int, tuple[TitlePool | None, datetime]] = {}
_POOL_REUSE = timedelta(days=1)
_POOL_RETRY = timedelta(minutes=30)


async def _title_pool(client: httpx.AsyncClient, db: Session, congress: int) -> TitlePool | None:
    """Every current-congress bill title, for the rival check in
    lobbying_records.names_bill. None when the full listing is unavailable,
    in which case nothing is claimed (see lobbied_bills_for): a partial pool
    can lack exactly the sibling that should have won."""
    from app.pipeline.fetch.congress import fetch_congress_bill_titles

    now = utcnow()
    held = _pools.get(congress)
    if held and now - held[1] < (_POOL_REUSE if held[0] is not None else _POOL_RETRY):
        return held[0]
    try:
        titles = await fetch_congress_bill_titles(client, db, congress)
    except Exception:
        logger.exception("Congress %d bill titles unavailable", congress)
        titles = None
    pool = TitlePool({k: [v] for k, v in titles.items()}) if titles else None
    _pools[congress] = (pool, now)
    _verdicts.clear()
    if pool is None:
        # Without the pool no filing is linked to any bill, which reads on
        # the page exactly like "no filing names one". Say so once a day.
        from app.ops_alerts import send_ops_alert

        send_ops_alert(
            "Lobbying bill links paused: bill list unavailable",
            f"The Congress.gov bill list for congress {congress} could not be read in full, so "
            "no donor-vote connection can link a bill named in a lobbying filing until it can "
            "(fetch_congress_bill_titles; see the server logs).",
            dedupe_key=f"lda-title-pool-{congress}-{now:%Y-%m-%d}",
        )
    return pool


# names_bill verdicts for the run, keyed by (congress, bill, before, after,
# titles):
# a trade group heading a hundred House members' matches names the same
# bills in the same words for every one of them. Cleared with the pool.
_verdicts: dict[tuple[int, str, str, str, tuple[str, ...]], bool] = {}


def _congress_years(congress: int) -> list[int]:
    first = congress_first_year(congress)
    return [y for y in (first, first + 1) if y <= utcnow().year]


async def lobbied_bills_for(
    client: httpx.AsyncClient,
    db: Session,
    activities: list[LobbyingActivity],
    voted: dict[str, dict],
    congress: int,
) -> list[dict]:
    """The bills the member voted on that these filings name, with the
    filing that names each. A named number counts only when the filer's
    words around it match one of that bill's current-congress titles and no
    other bill of the congress matches them better (see lobbying_records):
    filings still cite earlier congresses' bills by number, and sibling
    bills share nearly whole titles."""
    candidates: dict[str, list[dict]] = {}
    for activity in activities:
        for m in activity.mentions:
            year = m.get("filingYear")
            if not isinstance(year, int) or congress_for_year(year) != congress:
                continue
            if m.get("billId") in voted:
                candidates.setdefault(m["billId"], []).append(m)

    found: list[dict] = []
    if not candidates:
        return found
    pool = await _title_pool(client, db, congress)
    if pool is None:
        # Without the rest of the congress to compare against, a sibling
        # bill (the Education appropriations act for a Defense one) can't be
        # ruled out, so no filing is claimed to name anything.
        return found
    for bill_key, mentions in candidates.items():
        vote = voted[bill_key]
        titles = await _bill_titles(client, db, congress, bill_key)
        if titles is None:
            continue
        if vote.get("billName"):
            titles.append(vote["billName"])
        # The titles are part of the key: the vote's own billName is
        # appended to them, and it differs between chambers and votes.
        keyed = [(m, (congress, bill_key, m.get("before", ""), m.get("after", ""), tuple(titles))) for m in mentions]
        pending = [(m, key) for m, key in keyed if key not in _verdicts]
        if pending:
            fits = {
                key: (title_match_score(key[3], titles), title_match_score(key[2], titles))
                for _, key in pending
            }
            if any(max(f) >= BILL_TITLE_MATCH_MIN for f in fits.values()):
                previous = await _bill_titles(client, db, congress - 1, bill_key)
                # Without the previous congress's same-numbered bill to rule
                # out, the new wordings stay unjudged (not remembered, so a
                # later member can try again); verdicts already cached for
                # this bill still stand.
                if previous is not None:
                    for _, key in pending:
                        _verdicts[key] = names_bill(key[2], key[3], titles, bill_key, pool, previous, fits[key])
            else:
                # No wording fits this bill at all: nothing to rule out, so
                # the previous congress's titles aren't worth a request.
                for _, key in pending:
                    _verdicts[key] = False
        matching = [m for m, key in keyed if _verdicts.get(key)]
        if not matching:
            continue
        # One entry per client naming the bill: the registry's client name
        # is shown, not assumed to be the donor (is_same_client), so one
        # client's filings must not be counted under another's name.
        by_client: dict[str, dict[str, tuple[int, dict]]] = {}
        for m in matching:
            for f in m.get("filings", []):
                by_client.setdefault(f.get("client") or "", {})[f["url"]] = (m.get("filingYear") or 0, f)
        for client_name, filings in by_client.items():
            year, newest = max(filings.values(), key=lambda yf: (yf[0], yf[1].get("posted") or ""))
            found.append({
                "billId": bill_key,
                "label": bill_label(bill_key) or bill_key,
                "billName": (vote.get("billName") or "")[:160],
                "vote": vote.get("vote"),
                # None or "passage" when the vote shown is on the bill
                # itself; otherwise which motion it was ("cloture" ...).
                "motionType": vote.get("motionType"),
                # How the page says which vote this is ("" for passage).
                "voteContext": vote_context(vote.get("motionType")),
                "filingYear": year or None,
                "filingUrl": newest.get("url"),
                "registrant": newest.get("registrant"),
                "client": client_name or None,
                # Who filed it, when that isn't already said: None when the
                # registrant is the client (in-house, any spelling) or the
                # client name names the registrant ("X ON BEHALF OF Y").
                "filedBy": _filed_by(client_name, newest.get("registrant")),
                "filingCount": len(filings),
            })
    return _cap_bills(found)


def _cap_bills(rows: list[dict]) -> list[dict]:
    """Newest filings first, capped at MAX_LOBBIED_BILLS *bills*: several
    clients can name one bill, and each is its own row."""
    newest: dict[str, int] = {}
    for b in rows:
        newest[b["billId"]] = max(newest.get(b["billId"], 0), b.get("filingYear") or 0)
    # Bills by their newest filing, each bill's client rows together.
    rows = sorted(rows, key=lambda b: (
        -newest[b["billId"]], b["billId"], -(b.get("filingYear") or 0), b.get("client") or "",
    ))
    kept = sorted(newest, key=lambda bid: (-newest[bid], bid))[:MAX_LOBBIED_BILLS]
    return [b for b in rows if b["billId"] in kept]


async def enrich_lobbying_matches_with_lda(
    matches: list[dict],
    db: Session,
    lda_year: int,
    votes: list[dict] | None = None,
    congress: int | None = None,
) -> dict:
    """Mutate donor-vote lobbying matches in place: registered lobbying
    spend for `lda_year`, and the bills the member voted on that the org's
    current-congress filings name.

    Uses its own short-lived httpx client rather than a caller-supplied one:
    an earlier version reused the FETCH-phase client here, which was already
    closed by the time the analysis loop ran, and silently failed every LDA
    lookup with "client has been closed" (2026-07 finding: 184 failures in a
    single run — lobbyingSpend had effectively always been 0 in production).
    Best-effort per match: one org's lookup failing doesn't block the others.
    Shared by senate_pipeline.py and house_pipeline.py.

    Returns {"lookups", "failed"} so a caller can tell an outage from a
    quiet night.
    """
    stats = {"lookups": 0, "failed": 0}
    if not matches:
        return stats

    congress = congress or settings.CURRENT_CONGRESS
    years = sorted({lda_year, *_congress_years(congress)})
    voted = _voted_bills(votes)
    measure_of = {
        v.get("billId"): v.get("measureId") or bill_id_from_number(v.get("billId"))
        for v in votes or [] if v.get("billId")
    }

    async with make_async_client() as lda_client:
        for m in matches:
            # A match with no donor behind it carries an industry label
            # ("Finance industry") as its headline; searching the registry's
            # fuzzy client names for that would find somebody, wrongly.
            if "lobbyingClient" in m and not m["lobbyingClient"]:
                continue
            org = m.get("lobbyingClient") or m.get("lobbyistOrg", "")
            stats["lookups"] += 1
            failed = False
            try:
                activities: dict[int, LobbyingActivity | None] = {}
                for year in years:
                    activities[year] = await fetch_lobbying_activity(lda_client, db, org, year)
                # Any failed year counts toward the outage alert: a cached
                # finished year can succeed while every live request fails.
                failed = any(a is None for a in activities.values())
                spend_year = activities.get(lda_year)
                m["lobbyingChecked"] = spend_year is not None
                if spend_year is not None and not spend_year.clients and m.get("lobbyingClientIsCommittee"):
                    # Searched under a PAC's own name (the FEC lists no
                    # separate sponsor): registry clients are the sponsoring
                    # organizations, so finding none is not "none reported".
                    m["lobbyingChecked"] = None
                    m["description"] = (
                        m.get("description", "")
                        + f" No lobbying registry client matched \"{search_name(org)}\", the committee's"
                        " own name; the registry lists a PAC's sponsor, which the FEC does not name"
                        " separately for this committee, so its lobbying is unknown."
                    )
                elif spend_year is not None:
                    m["lobbyingSpend"] = round(spend_year.total)
                    # The total's parts by the registry's client names, so
                    # the structured field isn't read as one company's.
                    m["lobbyingClients"] = [
                        # complete=False: the year's filings ran past the
                        # page cap, so each amount is a floor.
                        {"client": name, "amount": round(spent), "complete": spend_year.complete}
                        for name, spent in spend_year.clients
                    ]
                    if spend_year.complete:
                        amount = f"${spend_year.total:,.0f}" if spend_year.total > 0 else "none reported"
                    else:
                        # The year ran past the page cap: the rest of the
                        # search, possibly the donor's own filings, is unread.
                        amount = (
                            f"at least ${spend_year.total:,.0f}" if spend_year.total > 0
                            else "none in the filings read, which stop before the end of the search"
                        )
                    # The name searched, not the donor's full name: a client
                    # matched to it may be a separate company, which is why
                    # every client counted is listed (lobbyingClients).
                    m["description"] = (
                        m.get("description", "")
                        + f" Registered federal lobbying (LDA {lda_year}) by clients matched to a"
                        f" registry search for \"{search_name(org)}\": {amount}."
                    )
                lobbied = await lobbied_bills_for(
                    lda_client, db, [a for a in activities.values() if a is not None], voted, congress,
                )
                m["lobbiedBills"] = lobbied
                if lobbied:
                    # billsInfluenced stays the topical list; a bill a filing
                    # names is listed there instead, not twice. Votes' ids
                    # map to the bill they were on (a House recent roll
                    # call's synthetic id through its measureId).
                    named = {b["billId"] for b in lobbied}
                    m["billsInfluenced"] = [
                        b for b in (m.get("billsInfluenced") or [])
                        if measure_of.get(b, bill_id_from_number(b)) not in named
                    ]
            except Exception:
                failed = True
                m.setdefault("lobbyingChecked", False)
                if m["lobbyingChecked"] is None:
                    m["lobbyingChecked"] = False
                logger.exception(
                    "LDA enrichment failed for %s (non-fatal)", m.get("lobbyistOrg", "?"),
                )
            finally:
                # Once per match, whichever way it failed.
                if failed:
                    stats["failed"] += 1
    return stats


def alert_if_lda_down(stats: dict, chamber: str) -> None:
    """One ops alert when every organization's lookups failed in part or
    whole — the signature of the 2026 host move, which read as "no
    lobbying" for months."""
    if stats.get("lookups", 0) and stats.get("failed", 0) >= stats["lookups"]:
        from app.ops_alerts import send_ops_alert

        send_ops_alert(
            "LDA lobbying lookups all failed",
            f"Every organization's Lobbying Disclosure Act lookups failed in whole or in "
            f"part in tonight's {chamber} run ({stats['failed']} of {stats['lookups']}). "
            "Where a spend year failed the match is marked lobbying-unchecked rather than "
            "$0; where only another year failed, filing-named bills from it are missing. "
            "See the server logs for the cause (a moved host, a changed API, a block, or "
            f"rate limiting). Base URL: {LDA_API_BASE}.",
            dedupe_key=f"lda-down-{chamber}-{utcnow():%Y-%m-%d}",
        )
