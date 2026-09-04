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
from app.pipeline.fetch.state_candidates_common import normalize_party, pick_nominee, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LISTING_URL = "https://sos.ks.gov/elections/election-results.html"

_LINK_RE = re.compile(
    r'<a\s+href="([^"]+\.pdf)"[^>]*title="[^"]*?(\d{4})\s+Primary\s+Election\s+Official',
    re.IGNORECASE,
)

_RACE_SENATE_RE = re.compile(r"^United States Senate$")
_RACE_HOUSE_RE = re.compile(r"^United States House of Representatives\s+(\d+)$")
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


def _parse_totals_pdf(content: bytes) -> list[dict]:
    """Every confirmed federal nominee this document decides -- a
    race section this document never gives (Kansas's real 2026 ballot
    has no uncontested-primary gaps at the federal level, but a future
    cycle's could) simply contributes nothing, never a guess."""
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.split("\n"))

    by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    current: tuple[str, int | None] | None = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        m = _CANDIDATE_RE.match(raw)
        if m:
            if current is None:
                continue
            party = normalize_party(m.group(1))
            if party is None:
                continue
            name, votes = m.group(2).strip(), int(m.group(3).replace(",", ""))
            by_seat.setdefault((current[0], current[1], party), []).append((name, votes))
            continue
        if _BANNER_RE.match(stripped):
            continue
        house_m = _RACE_HOUSE_RE.match(stripped)
        if house_m:
            current = ("H", int(house_m.group(1)))
        elif _RACE_SENATE_RE.match(stripped):
            current = ("S", None)
        else:
            current = None  # a non-federal race section -- stop attributing here

    records = []
    for (office, district, party), choices in by_seat.items():
        choices = [(n, v) for n, v in ((surname(n), v) for n, v in choices) if n]
        won = pick_nominee(choices, runoff_threshold_pct=None)
        if won:
            records.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is KS-only by construction
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
        results = _parse_totals_pdf(resp.content)
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
