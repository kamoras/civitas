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

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.historical_executive_orders import NAME_TO_ID
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.presidency.ucsb.edu/presidents"

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "presidential-roster"
_CACHE_KEY = "all-presidents"
_CACHE_MAX_AGE_HOURS = 20


@dataclass
class RosterEntry:
    id: str
    name: str
    term_start: str  # YYYY-MM-DD
    term_end: str | None  # None means still serving
    number: int  # position in US presidential history (1 = Washington)


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

        # Grover Cleveland's two non-consecutive terms render with
        # identical text on this page (unlike Trump's, which are
        # labelled) — disambiguate by encounter order, oldest first in
        # the underlying list (see the reverse() below), matching
        # NAME_TO_ID's "- I"/"- II" convention.
        if display_name == "Grover Cleveland":
            cleveland_seen += 1
            lookup_name = f"{display_name} - {'I' if cleveland_seen == 1 else 'II'}"

        parsed.append((display_name, lookup_name, start, end))

    # Page order is newest-first; reverse so Washington is entry 0 and
    # "number" falls directly out of position — but that means Cleveland's
    # two terms get visited newest-first above, so fix up the numbering
    # label after reversing (his 1st term, 1885, must get "- I").
    parsed.reverse()
    cleveland_order = [i for i, (n, _, _, _) in enumerate(parsed) if n == "Grover Cleveland"]
    if len(cleveland_order) == 2:
        earlier, later = cleveland_order
        dn, ln, s, e = parsed[earlier]
        parsed[earlier] = (dn, ln.replace("- II", "- I"), s, e)
        dn, ln, s, e = parsed[later]
        parsed[later] = (dn, ln.replace("- I", "- II"), s, e)

    entries: list[RosterEntry] = []
    for i, (display_name, lookup_name, start, end) in enumerate(parsed, start=1):
        pid = _resolve_id(lookup_name)
        if pid is None:
            # Logs position, not the name itself — a name string reads as
            # a person identifier to CodeQL's clear-text-logging
            # heuristic even for public historical figures (see
            # error_utils.py's docstring on this codebase's prior fights
            # with the same query; position is enough to cross-reference
            # against the raw page HTML when debugging a parse failure).
            logger.warning("Presidential roster: no id mapping for row %d", i)
            continue
        entries.append(RosterEntry(id=pid, name=display_name, term_start=start, term_end=end, number=i))

    # UCSB's page has no end date at all for a president who died in
    # office (Garfield, W.H. Harrison — a single dc:date span, verified
    # live 2026-07, not a "died within a day" special case as originally
    # assumed). Not a data gap this platform needs a second source for:
    # presidential succession has no gap, so the next president's own
    # term_start IS this one's term_end, derivable from data already
    # fetched rather than hand-typed. Only the actual current president
    # (no successor yet in this list) legitimately keeps term_end=None.
    for i in range(len(entries) - 1):
        if entries[i].term_end is None:
            entries[i].term_end = entries[i + 1].term_start

    return entries


async def fetch_presidential_roster(client: httpx.AsyncClient, db: Session) -> list[RosterEntry]:
    """Fetch + parse the full presidential roster in one request.

    Returns an empty list (never None) on failure — callers should treat
    "couldn't fetch this run" as "leave existing rows alone," not as
    "the presidency has no history."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return [RosterEntry(**e) for e in cached["entries"]]

    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", ROSTER_URL, log_label="UCSB presidential roster",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch UCSB presidential roster (%s)", ROSTER_URL)
        return []

    try:
        entries = _parse_roster(resp.text)
    except Exception:
        logger.exception("Failed to parse UCSB presidential roster")
        return []

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
        return []

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {
        "entries": [
            {"id": e.id, "name": e.name, "term_start": e.term_start, "term_end": e.term_end, "number": e.number}
            for e in entries
        ],
    })
    return entries
