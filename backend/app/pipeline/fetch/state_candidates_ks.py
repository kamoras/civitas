"""Kansas's confirmed-general-candidate strategy — the Secretary of
State's own "Official Vote Totals" PDF, one per election, linked from a
stable results-listing page (sos.ks.gov/elections/election-results.html)
that a plain, unauthenticated GET reaches with no bot-detection friction
at all (verified live 2026-09-04).

The link is found by TEXT, never a URL template: each PDF's own anchor
carries a `title` of "Click to open the {year} Primary Election Official
results in a new window", confirmed identical in shape across the real
2024 and 2026 listings even though the file's own SLUG isn't consistent
year to year ("2024-Primary-Official-Vote-Totals.pdf" vs "2026-Primary-
Election-Official-Vote-Totals.pdf") — so the year is read out of the
anchor's own text, never assumed from a filename pattern.

The PDF itself (real embedded text, not scanned; pdfplumber's plain
`extract_text()` reads it cleanly with no word-geometry clustering
needed, unlike KY/MS/AL's PDFs) is the SIMPLEST shape of any PDF this
system reads: one race name per section header ("United States Senate",
"United States House of Representatives 4"), one row per candidate
directly under it ("D-Adam Hamilton    77,607   34.63%"), already
reduced to a single statewide total — no per-county columns to sum, no
rotated text, no running-total tracking across pages needed. Party is a
literal PREFIX on the candidate's own name, the same shape Arkansas's
contest names use one level up. A section header that ISN'T one of the
two federal patterns (Governor, a state legislative seat, a
constitutional amendment) resets the current race to "not tracked" so
its own candidate-shaped rows are never misattributed to whichever
federal race happened to print last — verified directly: without this,
every one of Kansas's ~140 state house/senate races printed AFTER the
last federal race on the page falsely inherited it.

Kansas nominates on a PLURALITY — no runoff exists in state law for a
federal primary — so `runoff_threshold_pct: null`.

With the source's `statewide_offices` opt-in, the same document's state
contests are read as well, by the same plurality rule: Governor / Lt.
Governor (a joint ticket, kept whole under the Governor), Secretary of
State, Attorney General, State Treasurer, Commissioner of Insurance, the
State Board of Education seats, and both legislative chambers. Each
header goes through the shared gates (parse_statewide_office, then
parse_state_leg_office); this module only restates the document's bare
trailing seat number ("Kansas Senate 24") as the "District 24" those
gates read. The race reset above still applies: a section neither gate
claims (a judgeship, the constitutional amendment) attributes nothing.

No settle_days/require_official gate: unlike a live results API (this
system's other bespoke modules read one, e.g. Arkansas's), this is a
single PDF the Secretary of State's office files, explicitly titled
"Official Vote Totals" — the same shape as New Jersey's post-
certification PDF (also gate-free), and every other one-shot-document
strategy in this system (NJ/KY/MS/AL) makes the same call for the same
reason. That said, this is a WEAKER case than NJ's: NJ's document is
dated and explicitly framed as filed FOR a specific general election
after certification; Kansas's page offers no equivalent proof this PDF
is never live-updated pre-certification. The listing page's OWN text
("unofficial and precinct specific election results are available upon
request") shows Kansas does NOT publish a separate unofficial-results
page online, so that can't be checked either. The Internet Archive has
no earlier capture of this exact 2026 URL near the real 2026-08-04
primary to check against. Documented honestly as an unverified
assumption resting on the vendor's own "Official" framing and the NJ
precedent, not a proven fact — a real, disclosed limitation rather than
a resolved one.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    parse_state_leg_office,
    parse_statewide_office,
    resolve_confirmed_nominees,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LISTING_URL = "https://sos.ks.gov/elections/election-results.html"

_LINK_RE = re.compile(
    r'<a\s+href="([^"]+\.pdf)"[^>]*title="[^"]*?(\d{4})\s+Primary\s+Election\s+Official',
    re.IGNORECASE,
)

_RACE_SENATE_RE = re.compile(r"^United States Senate$")
_RACE_HOUSE_RE = re.compile(r"^United States House of Representatives\s+(\d+)$")
# This document's own convention for a districted contest: the number
# follows the office with nothing between ("Kansas Senate 24", "Kansas
# House of Representatives 1", "Member, State Board of Education 3") --
# the same shape as its federal "United States House of Representatives
# 4" above. The shared gates read a district only after the word
# "District", so the header is restated in that vocabulary before it is
# asked; the gates still make every decision. A bare trailing number
# only: "District Court Judge 13-1" keeps its hyphenated seat untouched
# (and is refused by both gates regardless).
_TRAILING_SEAT_RE = re.compile(r"^(.*\D)\s+0*(\d+)$")
_CANDIDATE_RE = re.compile(r"^\s*([A-Z])-(.+?)\s+([\d,]+)\s+[\d.]+%\s*$")
# The listing page's own chrome/banners repeated on every one of the
# PDF's 15 pages -- anything else is a race-section header of SOME kind.
# `\s+` between every word, never a literal single space, as defense in
# depth against any extraction quirk that widens a gap.
_BANNER_RE = re.compile(
    r"^(Kansas\s+Secretary\s+of\s+State|\d{4}\s+(Primary|General)\s+Election"
    r"|Official\s+Vote\s+Totals|Page\s+\d+\s+of\s+\d+|Race\s+Candidate.*Votes.*Percent)$",
    re.IGNORECASE,
)

_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


async def _discover_pdf_url(client: httpx.AsyncClient, year: int) -> str | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", LISTING_URL, timeout=30.0,
        log_label=f"KS results listing {year}", headers=_HEADERS,
    )
    if resp is None:
        return None
    matches = _LINK_RE.findall(resp.text)
    if not matches:
        # A page that answers 200 with none of its own links on it is
        # usually a bot-manager challenge rather than a genuinely empty
        # page (see state_candidates_tabular.py's identical retry, added
        # for Minnesota) -- one retry only, since a genuinely empty page
        # stays empty.
        logger.info("KS results listing had no links — retrying once")
        retry = await fetch_with_retry(
            client, _rate_limiter, "GET", LISTING_URL, timeout=30.0,
            log_label=f"KS results listing {year} (retry)", headers=_HEADERS,
        )
        matches = _LINK_RE.findall(retry.text) if retry is not None else matches
    for href, link_year in matches:
        if link_year == str(year):
            return urljoin(LISTING_URL, href)
    return None


def _state_contest(header: str) -> tuple[str, str | None, str | None] | None:
    """(office code, district, seat) for a Kansas statewide-executive or
    legislative section header, or None for anything else (a judgeship,
    the constitutional amendment). Statewide is asked first, as every
    other adapter asks it."""
    match = _TRAILING_SEAT_RE.match(header)
    label = f"{match.group(1)} District {match.group(2)}" if match else header
    statewide = parse_statewide_office(label)
    if statewide is not None:
        office, district = statewide
        return office, district, None
    seat = parse_state_leg_office(label)
    if seat is not None:
        return seat
    return None


def _parse_totals_pdf(content: bytes, state_offices: bool = False) -> list[dict]:
    """Every confirmed federal nominee this document decides -- a
    race section this document never gives (Kansas's real 2026 ballot
    has no uncontested-primary gaps at the federal level, but a future
    cycle's could) simply contributes nothing, never a guess.

    With `state_offices` (the source's `statewide_offices` opt-in), the
    statewide executive contests and legislative seats printed after the
    federal ones are resolved too, by the same plurality rule: the
    party's primary winner is its November nominee. Kept as the whole
    printed name -- a joint ticket reads "Cindy Holscher / KC Ohaebosim",
    exactly as the state prints it under "Governor / Lt. Governor"."""
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.split("\n"))

    by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    by_state_seat: dict[tuple, list[tuple[str, int]]] = {}
    current: tuple[str, int | None] | None = None
    current_state: tuple[str, str | None, str | None] | None = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        m = _CANDIDATE_RE.match(raw)
        if m:
            if current is None and current_state is None:
                continue
            party = normalize_party(m.group(1))
            if party is None:
                continue
            name, votes = m.group(2).strip(), int(m.group(3).replace(",", ""))
            if current is not None:
                by_seat.setdefault((current[0], current[1], party), []).append((name, votes))
            else:
                office, district, seat = current_state
                key = (office, district, party, seat) if seat else (office, district, party)
                by_state_seat.setdefault(key, []).append((name, votes))
            continue
        if _BANNER_RE.match(stripped):
            continue
        # Every other line is a race header, and each one resets BOTH
        # trackers before deciding what it is -- a section this module
        # does not read must never inherit the previous one's rows.
        current = current_state = None
        house_m = _RACE_HOUSE_RE.match(stripped)
        if house_m:
            current = ("H", int(house_m.group(1)))
        elif _RACE_SENATE_RE.match(stripped):
            current = ("S", None)
        elif state_offices:
            current_state = _state_contest(stripped)

    # Kansas has no runoff, so choices are reduced to surname (dropping any
    # unresolvable name from the vote pool entirely) before ranking, not
    # after -- unlike Vermont's "keep every real vote in the pool, only
    # refuse to declare a winner without a resolvable name" approach.
    by_seat = {
        seat: [(n, v) for n, v in choices if surname(n)]
        for seat, choices in by_seat.items()
    }
    records = resolve_confirmed_nominees(by_seat, runoff_threshold_pct=None, name_transform=surname)
    # A state office has no FEC row to match, so its winner keeps the whole
    # printed name; the same tie-safe plurality pick decides it.
    records += resolve_confirmed_nominees(
        by_state_seat, runoff_threshold_pct=None, name_transform=clean_display_name,
    )
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is KS-only by construction
) -> list[dict] | None:
    pdf_url = await _discover_pdf_url(client, year)
    if pdf_url is None:
        return []  # not published yet this cycle — healthy unknown

    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", pdf_url, timeout=60.0,
        log_label=f"KS official totals {year}", headers=_HEADERS,
    )
    if resp is None:
        return None
    try:
        results = _parse_totals_pdf(resp.content, bool(source.get("statewide_offices")))
    except Exception:
        logger.exception("KS official totals PDF for %d failed to parse", year)
        return None

    if not results:
        # The PDF fetched and opened fine but not one row matched the
        # race-header/candidate-row shapes -- almost certainly means
        # Kansas changed the document's layout, not that a Senate+House
        # cycle genuinely confirmed zero nominees. Reported as a failure
        # (None), not an empty confirmed list, so it doesn't silently
        # read as "0 candidates this cycle" downstream.
        logger.warning("KS official totals PDF for %d parsed no candidate rows", year)
        return None
    return results
