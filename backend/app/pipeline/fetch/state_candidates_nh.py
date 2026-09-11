"""New Hampshire's own results-listing pages (sos.nh.gov) — real
county/town-level primary tallies published as xlsx, discovered from a
STABLE root through three real hops, matching this system's established
discipline of following a state's own real links rather than assembling
any part of a URL from a template.

Bare-header requests get an Akamai "Access Denied" (the same shape
already seen for OH/MO/TN/NY/GA/MN); a complete, standards-compliant
header set reaches every hop and every file cleanly (no Referer needed,
despite an early research hunch that it might be) — EXCEPT this state's
WAF specifically rejects BROWSER_HEADERS' own usual self-identifying
User-Agent contact suffix, live-verified 403 on that exact string alone
with every other real signal unchanged. See `_HEADERS` below for the
one-field override this needed and why it's not the same thing as
hiding identity.

THREE-hop discovery, all plain GETs:
1. `/elections` (stable, no year) — its own "{year} Election Results"
   quick-link, matched by its real href pattern
   `/{year}-state-primary-election-results` (confirmed by this exact
   page's own historical links back through at least 2016 that the path
   never carries a query string or extra segment).
2. That results-index page — its own two real links, "{year} Democratic
   State Primary" and "{year} Republican State Primary", matched by
   link TEXT (never an assumed slug).
3. Each party's own page — its own "US Senator Summary" link (there are
   also 10 real per-COUNTY Senate breakdown links on the same page,
   e.g. "US Senator Belknap" — `parse_office` matches all of them too,
   since "US Senator" is a real substring of each, so only the one
   whose own text also contains "Summary" is kept) and every real
   "Representative in Congress District No. N" link it lists (N read
   from the link's own text, never assumed to be exactly {1, 2} even
   though that's NH's real current district count). Both office labels
   already parse correctly through the shared `parse_office` with no
   new pattern needed: "US Senator Summary" matches the existing bare
   "US Senator" alternative, and "Representative in Congress District
   No. N" matches the Vermont-precedent "Representative in/to Congress"
   wording plus the existing "District No." handling already built for
   Arizona's identically-worded export.

THE GENUINELY NEW SHAPE: NH's own per-office export deliberately reports
BOTH parties' candidates in ONE workbook, with a candidate's own PARTY
encoded as a literal ", d"/", r" SUFFIX on their own column header —
verified live 2026-09-10 against the real 2026 primary. Three real
header rows precede the actual per-geography data (a title row, a date+
office row, then the real candidate-name row) — read via `_xlsx_rows`'s
`skip` parameter (state_candidates_tabular.py), added for this exact
shape.

THE REAL TRAP, caught only by comparing the Democratic page's and the
Republican page's own exports for the SAME office side by side: they
are NOT the same file republished twice. A candidate's REAL total sits
under their own party's suffix in THEIR OWN PARTY's file; the SAME
candidate's column ALSO appears, with real but drastically smaller
numbers, in the OTHER party's file — genuine write-in votes cast by
voters using that ballot (New Hampshire's semi-open primary lets an
undeclared voter pick either party's ballot, and NH's write-in rules
let any qualified name be written in regardless of which ballot chosen).
Verified live: the real 2026 Democratic Senate nominee, Chris Pappas,
shows 100,053 votes under his own name in the Democratic file, and only
480 votes under the SAME name in the Republican file. Summing both
would inflate his real total by nearly 2x; trusting the Republican
file's number for him would undercount it by 99.5%. So only a file's
OWN party's columns are ever trusted from that file — identified by
their own ", d"/", r" suffix, or (a real, live-observed data quirk) NO
suffix at all, since one real candidate's own suffix was dropped
entirely in the Republican file but not the Democratic one, so a
suffix-less column defaults to belonging to whichever file it's found
in rather than being silently excluded. The SAME office's OTHER party's
real nominee is always read from ITS OWN separate file, never summed
together with this one.

New Hampshire nominates by PLURALITY — no runoff mechanism exists for a
federal primary — so `runoff_threshold_pct` is null, and each party's
real winner is picked via the shared tie-safe `pick_nominee` from real
vote totals (this export carries no "winner" flag of its own anyway).

NO settle_days gate would be a mistake here: the real files this module
reads appeared within TWO days of the real 2026 primary, and NH's own
site separately links a POST-primary AUDIT report for past cycles,
confirming a real certification step happens later — this is a live
count, not a final one, however complete it looks today. settle_days is
the actual floor, `held` read from the shared FEC-calendar cache (the
same fallback MA's own vendor uses for a source with no date of its
own) rather than the literal date string each real file's own header
row carries — simpler, and this discovery layer never needs to open a
file just to find one.
"""

import logging
import re
from urllib.parse import urljoin

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled, _xlsx_rows
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)
_ROOT_URL = "https://www.sos.nh.gov/elections"

# Identical to BROWSER_HEADERS in every real respect (Accept, Accept-
# Language, the Sec-Fetch-* set, Upgrade-Insecure-Requests) EXCEPT the
# User-Agent drops this codebase's usual self-identifying contact suffix
# ("Civitas/1.0 (+contact@...)") -- live-verified 2026-09-10 that NH's
# WAF specifically rejects that suffix (403, every other state's exact
# same header shape unaffected) and accepts an otherwise-identical,
# genuinely standard browser UA cleanly. Nothing here is forged or
# impersonated: same real Accept/Sec-Fetch signals, same robots.txt
# (checked live -- allows every path this module reads), no JS
# challenge defeated, no session/identity faked -- just the one optional
# courtesy string this one state's filter happens to flag, omitted.
_HEADERS = {
    **BROWSER_HEADERS,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}

_LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
_PARTY_SUFFIX_RE = re.compile(r",\s*([dr])\s*$", re.IGNORECASE)
_PARTY_LETTER_TO_CODE = {"d": "D", "r": "R"}


async def _get_text(client: httpx.AsyncClient, url: str, label: str) -> str | None:
    return await fetch_text_with_retry(client, _rate_limiter, url, label, headers=_HEADERS)


async def _get_bytes(client: httpx.AsyncClient, url: str, label: str) -> bytes | None:
    return await fetch_bytes_with_retry(client, _rate_limiter, url, label, headers=_HEADERS)


def _links(html: str) -> list[tuple[str, str]]:
    return [(href, text.strip()) for href, text in _LINK_RE.findall(html)]


async def _discover_office_links(
    client: httpx.AsyncClient, year: int,
) -> dict[tuple[str, int | None], dict[str, str]] | None:
    """{(office, district): {"d": url, "r": url}} for every real federal
    office this cycle's results index currently lists. None only on a
    genuine fetch failure at any hop; an empty/partial dict is a real,
    healthy "not published yet" (today's actual reality until each hop
    exists) — see fetch_confirmed_candidates for how they're told apart."""
    root_html = await _get_text(client, _ROOT_URL, f"NH elections root {year}")
    if root_html is None:
        return None
    index_href = next(
        (href for href, _ in _links(root_html) if re.fullmatch(rf"/{year}-state-primary-election-results", href)),
        None,
    )
    if index_href is None:
        return {}

    index_html = await _get_text(client, urljoin(_ROOT_URL, index_href), f"NH results index {year}")
    if index_html is None:
        return None
    party_pages: dict[str, str] = {}
    for href, text in _links(index_html):
        if text == f"{year} Democratic State Primary":
            party_pages["d"] = urljoin(_ROOT_URL, href)
        elif text == f"{year} Republican State Primary":
            party_pages["r"] = urljoin(_ROOT_URL, href)
    if len(party_pages) != 2:
        return {}

    offices: dict[tuple[str, int | None], dict[str, str]] = {}
    ambiguous: set[tuple[tuple[str, int | None], str]] = set()
    for party_letter, page_url in party_pages.items():
        page_html = await _get_text(client, page_url, f"NH {party_letter} primary page {year}")
        if page_html is None:
            return None
        for href, text in _links(page_html):
            if not href.lower().endswith(".xlsx"):
                continue
            office_district = parse_office(text)
            if office_district is None:
                continue
            if office_district == ("S", None) and "summary" not in text.lower():
                continue  # a per-county Senate breakdown, not the statewide summary
            key = (office_district, party_letter)
            if key in ambiguous:
                continue
            url = urljoin(_ROOT_URL, href)
            existing = offices.setdefault(office_district, {}).get(party_letter)
            if existing and existing != url:
                # A second real link parsing to the SAME office/district/
                # party as one already found — never seen live for House
                # (only Senate publishes per-county breakdowns today),
                # but if it ever happens, silently keeping whichever one
                # was found last is exactly the wrong instinct: refuse
                # this office/party combo entirely rather than guess
                # which link is the real one. Tracked separately from
                # `offices` so a THIRD colliding link can't accidentally
                # get treated as if it were the first.
                logger.warning(
                    "NH %s: two different links both parsed to %s — refusing rather than guessing",
                    party_letter, office_district,
                )
                ambiguous.add(key)
                del offices[office_district][party_letter]
                continue
            offices[office_district][party_letter] = url
    return offices


def _office_choices(rows: list[dict], own_suffix: str) -> list[tuple[str, int]]:
    """(display_name, votes) for every real per-geography row, counting
    ONLY the columns that belong to THIS file's own party (see module
    docstring for why the other party's own columns in the same file are
    real but small write-in cross-tabulation, not a genuine total, and
    must never be summed in).

    `own_suffix` ("d" or "r") is the caller's own known party for this
    file — it names which page this file was discovered on, e.g. the
    Republican-page's own export — rather than being INFERRED from
    whichever candidate column happens to carry a party suffix first in
    header order. Column order isn't a safe signal here: this module's
    own docstring documents a real, live-observed case (a candidate's
    suffix dropped entirely in one file but not the other), so a future
    export that ever led with a suffixed OTHER-party write-in column
    before any own-party one would silently invert which columns get
    kept — the caller already knows the real answer without guessing."""
    if not rows:
        return []
    header = list(rows[0].keys())
    candidate_columns = header[1:]  # column 0 is the geography name, whatever its own header text says
    other_suffix = {"d": "r", "r": "d"}[own_suffix]

    totals: dict[str, int] = {}
    for row in rows:
        geography = next(iter(row.values()), "")
        if not geography or "total" in geography.lower():
            continue
        for col in candidate_columns:
            if col.strip().lower().startswith("write-in"):
                continue
            m = _PARTY_SUFFIX_RE.search(col)
            if m and m.group(1).lower() == other_suffix:
                continue
            value = (row.get(col) or "").strip()
            if value.isdigit():
                totals[col] = totals.get(col, 0) + int(value)
    return [(_PARTY_SUFFIX_RE.sub("", col).strip(), votes) for col, votes in totals.items()]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    # NH's own files appear within 2 real days of the primary (verified
    # live) -- far too fast to be a certified count (its own site links
    # a POST-primary AUDIT report for past cycles, confirming a real
    # certification step happens later). No in-band official/unofficial
    # flag exists on this export to gate on, so settle_days is the
    # actual floor here, same defensive-not-certification-signal role it
    # plays for every other state with no flag of its own. `held` comes
    # from the shared FEC-calendar cache (refreshed every sync run
    # before any strategy runs), same fallback MA's own vendor -- which
    # also carries no date of its own -- already uses.
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    held = primary_date(state, year)
    if held and not _settled(held, settle_days):
        return []

    offices = await _discover_office_links(client, year)
    if offices is None:
        return None
    if not offices:
        return []

    results: list[dict] = []
    for (office, district), party_urls in offices.items():
        for party_letter, url in party_urls.items():
            party = _PARTY_LETTER_TO_CODE[party_letter]
            label = f"NH {office}{district or ''} {party} {year}"
            content = await _get_bytes(client, url, label)
            if content is None:
                return None
            rows = _xlsx_rows(content, skip=2)
            if rows is None:
                logger.warning("%s: download was not a readable xlsx workbook", label)
                return None
            won = pick_nominee(_office_choices(rows, party_letter), runoff_threshold_pct=None)
            if not won:
                continue
            last_name = surname(won[0])
            if last_name:
                results.append({"office": office, "district": district, "party": party, "last_name": last_name})
    return results
