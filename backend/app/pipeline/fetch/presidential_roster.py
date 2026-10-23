"""Fetch the canonical roster of US presidents — name, term dates, party
— from UCSB's American Presidency Project, replacing what used to be a
hand-typed SEED_PRESIDENTS list.

Two real sources, joined by name:
  - presidency.ucsb.edu/presidents: a single "People Listing" page with
    every president in reverse-chronological order, each with an exact
    ISO term-start/term-end date (or a single date for the two who died
    within a day of inauguration: Garfield, W.H. Harrison). Ordering is
    real and consistent (verified live, 2026-07) — Washington is last,
    the row count is exactly 47 — so a president's historical "number"
    (1st, 44th, etc., counting Cleveland's and Trump's non-consecutive
    terms as their own real, separately-numbered entries) falls straight
    out of position, no separate source or guess needed.
  - historical_executive_orders.py's already-built UCSB EO-table fetch:
    reused here for party affiliation, since that table already lists it
    ("George Washington (F)") and this avoids a third fetch + a second
    name-matching pass just for one field.

Name matching against this platform's existing president_id scheme
(historical_executive_orders.NAME_TO_ID / presidential_elections'
equivalent) — those ids pre-date this module and are reused as-is rather
than re-derived, since they already encode the same real disambiguation
UCSB's own site does (e.g. "Donald J. Trump - I"/"- II" for his two
non-consecutive terms) and are the join key the rest of this pipeline's
fetchers are already built around.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.contact import BOT_USER_AGENT
from app.ordinals import ordinal
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.historical_executive_orders import NAME_TO_ID, derived_president_id
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.presidency.ucsb.edu/presidents"
UCSB_BASE = "https://www.presidency.ucsb.edu"

# Display names UCSB writes differently from the White House's own list of
# presidents (whitehouse.gov/about-the-white-house/presidents): "Martin van
# Buren" (he signed "Van Buren"), and plain "George Bush" for the 41st
# president, which beside his son's "George W. Bush" names neither
# unambiguously. Only the display changes; ids and the name joins still
# use UCSB's text.
_DISPLAY_NAME_CORRECTIONS = {
    "Martin van Buren": "Martin Van Buren",
    "George Bush": "George H. W. Bush",
}

# A president's own UCSB page: "Dates In Office: March 04, 1881 to
# September 19, 1881".
_DATES_IN_OFFICE_RE = re.compile(
    r"Dates In Office:\s*([A-Z][a-z]+ \d{1,2}, \d{4})\s+to\s+([A-Z][a-z]+ \d{1,2}, \d{4})"
)

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "presidential-roster"
_CACHE_KEY = "all-presidents"
_CACHE_MAX_AGE_HOURS = 20
_NO_EXPIRY_HOURS = 24 * 365 * 100


@dataclass
class RosterEntry:
    id: str
    name: str
    term_start: str  # YYYY-MM-DD
    term_end: str | None  # None means still serving
    number: int  # position in US presidential history (1 = Washington)
    page: str = ""  # the president's own UCSB page path
    # term_end is the successor's first day, standing in for a date the
    # roster page didn't give (see _parse_roster)
    end_from_successor: bool = False
    # Set only on a president added from the Federal Register and Wikidata
    # (reconcile_with_register), whom UCSB's EO table can't give a party yet.
    party: str | None = None


def _normalize_name(text: str) -> str:
    # UCSB's roster page renders repeat-term presidents as "Donald J.
    # Trump (2nd Term)" / "Grover Cleveland" (twice, undistinguished by
    # text alone — see the term-suffix handling in _parse_roster below)
    # rather than the EO table's "- I"/"- II" convention this platform's
    # NAME_TO_ID already keys on — normalized to that convention here so
    # both sources' names resolve to the same id.
    text = re.sub(r"\(1st Term\)", "- I", text)
    text = re.sub(r"\(2nd Term\)", "- II", text)
    text = text.replace(".", "")
    return re.sub(r"\s+", " ", text.strip().lower())


# NAME_TO_ID's keys come from the EO table's own name text, which is
# inconsistent about middle initials for exactly these two presidents
# ("James Garfield"/"Chester Arthur", no initial) vs the roster page's
# fuller "James A. Garfield"/"Chester A. Arthur" — reconciled with a
# small alias table rather than a general fuzzy matcher for two rows.
_ROSTER_NAME_ALIASES = {
    "james a garfield": "james garfield",
    "chester a arthur": "chester arthur",
}

# Periods are stripped from both sides at lookup time (see _normalize_name
# above) since the roster page renders "Harry S Truman" (no period) while
# NAME_TO_ID's key has "harry s. truman".
_LOOKUP: dict[str, str] = {k.replace(".", ""): v for k, v in NAME_TO_ID.items()}


def _resolve_id(name: str) -> str | None:
    normalized = _normalize_name(name)
    normalized = _ROSTER_NAME_ALIASES.get(normalized, normalized)
    return _LOOKUP.get(normalized)


def _parse_roster(html: str) -> list[RosterEntry]:
    """Parses the roster page's rows (real HTML div/span structure, not a
    <table> — verified live, 2026-07) into RosterEntry objects, oldest
    (Washington) numbered 1 through the current term."""
    doc = lxml_html.fromstring(html)
    rows = doc.cssselect(".views-field-title .field-content a")
    if not rows:
        return []

    # Each entry tracks a clean display name plus a separate lookup_name
    # that may carry a disambiguator (Trump's "(1st/2nd Term)", Cleveland's
    # "- I"/"- II") needed only to resolve the right id — never shown to
    # a user.
    parsed: list[tuple[str, str, str, str | None]] = []
    pages: list[str] = []
    cleveland_seen = 0
    for a in rows:
        full_text = a.text_content().strip()
        dates = a.cssselect("[property='dc:date']")
        if not dates:
            continue
        start = dates[0].get("content", "")[:10]
        end = dates[1].get("content", "")[:10] if len(dates) > 1 else None
        # Strip the trailing year(s) UCSB embeds inside the same link
        # text (e.g. "Barack Obama20092017") to recover the plain name.
        raw_name = re.sub(r"\d{4}.*$", "", full_text).strip()
        lookup_name = raw_name
        display_name = re.sub(r"\s*\((?:1st|2nd) Term\)\s*$", "", raw_name).strip()
        display_name = _DISPLAY_NAME_CORRECTIONS.get(display_name, display_name)

        # Grover Cleveland's two non-consecutive terms render with
        # identical text on this page (unlike Trump's, which are
        # labelled) — disambiguate by encounter order, oldest first in
        # the underlying list (see the reverse() below), matching
        # NAME_TO_ID's "- I"/"- II" convention.
        if display_name == "Grover Cleveland":
            cleveland_seen += 1
            lookup_name = f"{display_name} - {'I' if cleveland_seen == 1 else 'II'}"

        parsed.append((display_name, lookup_name, start, end))
        pages.append(a.get("href") or "")

    # Page order is newest-first; reverse so Washington is entry 0 and
    # "number" falls directly out of position — but that means Cleveland's
    # two terms get visited newest-first above, so fix up the numbering
    # label after reversing (his 1st term, 1885, must get "- I").
    parsed.reverse()
    pages.reverse()
    cleveland_order = [i for i, (n, _, _, _) in enumerate(parsed) if n == "Grover Cleveland"]
    if len(cleveland_order) == 2:
        earlier, later = cleveland_order
        dn, ln, s, e = parsed[earlier]
        parsed[earlier] = (dn, ln.replace("- II", "- I"), s, e)
        dn, ln, s, e = parsed[later]
        parsed[later] = (dn, ln.replace("- I", "- II"), s, e)

    entries: list[RosterEntry] = []
    for i, (display_name, lookup_name, start, end) in enumerate(parsed, start=1):
        # A president the id table doesn't list (one sworn in after it was
        # written) gets a derived id rather than being dropped: skipping
        # them left the sitting president off the site, and the row count
        # still passed the sanity floor below.
        pid = _resolve_id(lookup_name) or derived_president_id(display_name, i)
        entries.append(RosterEntry(id=pid, name=display_name, term_start=start, term_end=end, number=i,
                                   page=pages[i - 1]))

    # The roster page has no end date for two presidents who died in office
    # (a single dc:date span, verified live 2026-07). The successor's first
    # day stands in until fetch_presidential_roster reads the president's
    # own page: it is not the end of the term, since a successor can take
    # the oath days after the death (one died on April 4, the next was
    # sworn on April 6). Only the current president (no successor yet)
    # keeps term_end=None.
    for i in range(len(entries) - 1):
        if entries[i].term_end is None:
            entries[i].term_end = entries[i + 1].term_start
            entries[i].end_from_successor = True

    return entries


async def fetch_presidential_roster(db: Session) -> list[RosterEntry]:
    """Fetch + parse the full presidential roster in one request, then
    reconcile its sitting president with the Federal Register
    (reconcile_with_register).

    UCSB unreadable, the last roster read (any age) is reconciled instead;
    with none ever read, returns an empty list (never None) — callers treat
    "couldn't fetch this run" as "leave existing rows alone," not as "the
    presidency has no history."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return await reconcile_with_register(db, [RosterEntry(**e) for e in cached["entries"]])
    entries = await _read_roster()
    if entries is None:
        last = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_NO_EXPIRY_HOURS)
        return await reconcile_with_register(db, [RosterEntry(**e) for e in last["entries"]]) if last else []
    for e in entries:
        if e.end_from_successor and e.page:
            end = await _end_from_own_page(e.page)
            if end:
                e.term_end, e.end_from_successor = end, False
            else:
                logger.warning("No term end on UCSB's page for %s; the successor's first day stands in", e.name)

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {
        "entries": [
            {"id": e.id, "name": e.name, "term_start": e.term_start, "term_end": e.term_end, "number": e.number}
            for e in entries
        ],
    })
    return await reconcile_with_register(db, entries)


async def _read_roster() -> list[RosterEntry] | None:
    """UCSB's roster parsed, or None when it can't be read or parses short."""
    resp = await fetch_with_retry_requests(
        _RATE_LIMITER, "GET", ROSTER_URL, log_label="UCSB presidential roster",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch UCSB presidential roster (%s)", ROSTER_URL)
        return None

    try:
        entries = _parse_roster(resp.text)
    except Exception:
        logger.exception("Failed to parse UCSB presidential roster")
        return None

    if len(entries) < 40:  # sanity floor — real page has 47 as of 2026-07
        logger.warning(
            "UCSB presidential roster parsed to only %d entries — page structure may have changed",
            len(entries),
        )
        # 2026-07 (#218 review S1): used to return the partial list anyway
        # (`entries or []`). `number` is pure list position, so even one
        # silently-dropped row (e.g. 47->46) mis-numbers every president
        # after it — and _sync_roster would still write that wrong
        # numbering to the DB. Below the sanity floor, a partial parse is
        # worse than none: return nothing and let the prior DB rows stand.
        return None
    return entries


def parse_dates_in_office(html: str) -> str | None:
    """The last day in office from a president's own UCSB page, ISO."""
    text = re.sub(r"\s+", " ", lxml_html.fromstring(html).text_content().replace("\xa0", " "))
    m = _DATES_IN_OFFICE_RE.search(text)
    if not m:
        return None
    return datetime.strptime(m.group(2), "%B %d, %Y").date().isoformat()


async def _end_from_own_page(path: str) -> str | None:
    resp = await fetch_with_retry_requests(
        _RATE_LIMITER, "GET", UCSB_BASE + path, log_label="UCSB president page",
    )
    if resp is None or resp.status_code != 200:
        return None
    return parse_dates_in_office(resp.text)


# ── The Federal Register as the second official source ────────────────
#
# Each presidential document (proclamation, executive order, memorandum) in
# the Federal Register names the president who signed it: the official
# record of who holds the office, published within days of a signing. When
# its newest signer, signing after the roster's sitting president took
# office, is someone else, UCSB's roster has fallen behind (an
# inauguration or a succession it hasn't added yet), and the roster is
# completed from the Register and Wikidata's record of the office (its
# series ordinal, start date and the holder's current party) until UCSB
# lists the new president itself.

PRESIDENTIAL_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
_SPARQL_URL = "https://query.wikidata.org/sparql"
# Every holder of the office (Q11696) with the office's own series ordinal,
# the holding's start and the holder's party with no end date.
_HOLDERS_QUERY = """
SELECT ?ordinal ?start ?personLabel ?partyLabel WHERE {
  ?person p:P39 ?held . ?held ps:P39 wd:Q11696 ; pq:P1545 ?ordinal .
  OPTIONAL { ?held pq:P580 ?start . }
  OPTIONAL { ?person p:P102 ?ps . ?ps ps:P102 ?party . FILTER NOT EXISTS { ?ps pq:P582 ?end } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
} ORDER BY DESC(xsd:integer(?ordinal)) LIMIT 4
"""
_REGISTER_CHECK_KEY = "register-check"
_OTHER_LIMITER = RateLimiter(rps=1.0, name="roster_cross_check")


def person_key(name: str) -> tuple[str, str] | None:
    """(first, last) of a name with initials, suffixes and punctuation
    dropped, so the Federal Register's "First Last" and the roster's "First
    M. Last, Jr." compare equal; None for an empty name."""
    words = [w for w in "".join(c if c.isalpha() or c == " " else "" for c in name.lower()).split()
             if len(w) > 1 and w not in {"jr", "sr", "ii", "iii", "iv"}]
    return (words[0], words[-1]) if words else None


def party_code(label: str) -> str | None:
    """The roster's party code for a party's name, by the convention UCSB's
    tables use: each hyphenated word's initial ("Republican Party" R,
    "Democratic-Republican Party" D-R)."""
    words = [w.strip() for w in re.sub(r"\s+Party$", "", label.strip()).split("-") if w.strip()]
    return "-".join(w[0].upper() for w in words) if words else None


async def newest_presidential_signer() -> dict | None:
    """{"name", "identifier", "signed"} of the newest presidential document
    in the Federal Register, or None when it can't be read."""
    resp = await fetch_with_retry_requests(
        _OTHER_LIMITER, "GET", PRESIDENTIAL_DOCUMENTS_URL, log_label="Federal Register presidential documents",
        params={"conditions[type][]": "PRESDOCU", "order": "newest", "per_page": 20,
                "fields[]": ["president", "signing_date"]},
    )
    try:
        rows = [r for r in resp.json().get("results") or []
                if (r.get("president") or {}).get("name") and r.get("signing_date")] if resp is not None else []
    except ValueError:
        rows = []
    if not rows:
        logger.warning("Federal Register presidential documents could not be read")
        return None
    newest = max(rows, key=lambda r: r["signing_date"])
    return {"name": newest["president"]["name"], "identifier": newest["president"].get("identifier"),
            "signed": newest["signing_date"]}


async def _first_signing(identifier: str, since: str) -> str | None:
    """The earliest signing date on or after `since` of a president's
    documents in the Federal Register."""
    resp = await fetch_with_retry_requests(
        _OTHER_LIMITER, "GET", PRESIDENTIAL_DOCUMENTS_URL, log_label="Federal Register first signing",
        params={"conditions[type][]": "PRESDOCU", "conditions[president][]": identifier,
                "conditions[signing_date][gte]": since, "order": "oldest", "per_page": 1,
                "fields[]": ["signing_date"]},
    )
    try:
        rows = resp.json().get("results") or [] if resp is not None else []
    except ValueError:
        return None
    return rows[0].get("signing_date") if rows else None


async def _office_holders() -> list[dict] | None:
    """Wikidata's newest holders of the office: {"ordinal", "start", "name",
    "party"}, newest first, or None when it can't be read."""
    resp = await fetch_with_retry_requests(
        _OTHER_LIMITER, "GET", _SPARQL_URL, log_label="Wikidata office holders",
        params={"query": _HOLDERS_QUERY}, headers={"Accept": "application/sparql-results+json",
                                                   "User-Agent": BOT_USER_AGENT},
    )
    try:
        rows = resp.json()["results"]["bindings"] if resp is not None and resp.status_code == 200 else None
    except (ValueError, KeyError):
        rows = None
    if rows is None:
        return None
    value = lambda b, k: (b.get(k) or {}).get("value")  # noqa: E731
    return [{"ordinal": int(value(b, "ordinal")), "start": (value(b, "start") or "")[:10] or None,
             "name": value(b, "personLabel") or "", "party": party_code(value(b, "partyLabel") or "")}
            for b in rows if (value(b, "ordinal") or "").isdigit()]


async def reconcile_with_register(db: Session, entries: list[RosterEntry]) -> list[RosterEntry]:
    """The roster with its sitting president checked against the Federal
    Register's newest presidential document, and completed when UCSB is
    behind it: the outgoing entry ends on the new term's first day, and the
    new president is added with Wikidata's ordinal, start date (else the
    first document they signed) and party.

    The outcome is stored (api_cache, `register_check`) for the nightly
    check, which raises an alert only when this could not resolve a
    disagreement. A Register that can't be read changes nothing."""
    sitting = entries[-1] if entries and entries[-1].term_end is None else None
    signer = await newest_presidential_signer() if entries else None
    if signer is None:
        return entries
    outcome = {"signer": signer["name"], "signed": signer["signed"], "problem": None}
    key = person_key(signer["name"])
    if sitting is not None and (person_key(sitting.name) == key or signer["signed"] < sitting.term_start):
        api_cache_set(db, _CACHE_TIER, _REGISTER_CHECK_KEY, outcome)
        return entries  # the same person, or the Register behind the roster

    holders = await _office_holders()
    holder = next((h for h in holders or [] if person_key(h["name"]) == key), None)
    since = sitting.term_start if sitting else ""
    first = await _first_signing(signer["identifier"], since) if signer.get("identifier") else None
    start = holder["start"] if holder and holder["start"] and holder["start"] >= since else first
    if holders is None:
        outcome["problem"] = "Wikidata's record of the office could not be read"
    elif holder is None:
        outcome["problem"] = f"Wikidata does not yet record {signer['name']} as holding the office"
    elif not holder["party"]:
        outcome["problem"] = f"Wikidata records no current party for {signer['name']}"
    elif not start:
        outcome["problem"] = f"no start date for {signer['name']}'s term in Wikidata or the Register"
    api_cache_set(db, _CACHE_TIER, _REGISTER_CHECK_KEY, outcome)
    if outcome["problem"]:
        logger.warning("Roster behind the Federal Register (%s): %s", signer["name"], outcome["problem"])
        return entries

    number = max(holder["ordinal"], (entries[-1].number + 1) if entries else 1)
    if sitting is not None:
        sitting.term_end = start
    name = signer["name"]
    # A derived id, as _parse_roster gives a president the id table doesn't
    # list, so UCSB's own entry for them later keeps it.
    entries = [*entries, RosterEntry(id=derived_president_id(name, number), name=name, term_start=start,
                                     term_end=None, number=number, party=holder["party"])]
    logger.warning("UCSB's roster lacks %s, who signs the Federal Register's presidential documents; added "
                   "as the %s president from %s (Wikidata, the Register)", name, ordinal(number), start)
    return entries


def register_check(db: Session) -> dict | None:
    """The last reconcile_with_register outcome: {"signer", "signed",
    "problem"} (problem None when the roster agreed or was completed)."""
    return api_cache_get(db, _CACHE_TIER, _REGISTER_CHECK_KEY, max_age_hours=_NO_EXPIRY_HOURS)
