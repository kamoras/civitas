"""Mississippi's confirmed-general-candidate strategy — the Secretary of
State's own "Official Recapitulation" PDF, one per party, published after
each federal primary.

DISCOVERY, three hops, nothing cycle-specific written down: the SOS's own
election-results INDEX page (stable across every year back to 2003) links
each year's results pages, but under a slug that is NOT consistent cycle to
cycle (2024's was "2024-democratic-primary-results", 2026's is "march-10-
2026-democratic-primary-results") — so the link is matched by its constant
SUFFIX ("-{party}-primary-results"), never assembled from a template. That
results page itself carries no results at all; it only embeds an <iframe>
pointing at a SEPARATE aspx application (sos.ms.gov/elections/...), which is
where the real, party-labeled PDF links live. All three hops are server-
rendered plain HTML — no JS execution needed, verified live 2026-09-04 with
a plain httpx GET at every hop.

THE PDF ITSELF is a wide table — one row per candidate, one column per
COUNTY (all 82, so the real 2026 file spans 9-17 pages depending on how many
candidates run) — printed by a report generator (ActiveReports) that has no
Excel/CSV counterpart, only ever this PDF. Confirmed via pdftotext/pdffonts:
real embedded text, not scanned, and a plain `httpx` GET with BROWSER_HEADERS
reaches it with no bot-detection friction at all (a bare header set gets an
Akamai "Access Denied", exactly the shape already seen for OH/MO/TN/NY/GA/MN
— see http_utils.BROWSER_HEADERS).

Because a candidate's own row REPEATS across however many pages it takes to
list all 82 counties (the same row, sliding further right each page), a
candidate's TRUE statewide total is only ever complete on the LAST page
their row appears on — every earlier occurrence's rightmost value is just
that page's own county's count, not a running sum, and only the genuinely
final page's header row carries a "TOTAL" column at all. Rather than trying
to detect which specific occurrence IS the final one, this keeps a running
{(office, district, RAW printed name): (party, votes)} map and OVERWRITES it
on every occurrence of that exact name found anywhere in the document —
since a name's LAST occurrence in reading order is, by construction, the
last page its row appears on, the map holds each candidate's true final
total once the whole document has been read, with no special detection
needed. Keyed on the full raw name rather than the surname alone — reducing
to a surname (state_candidates_common.surname()) happens only once, when
building the final per-seat groups below, never as the resolution key
itself: two different real candidates sharing a surname in the same contest
(not rare in a crowded primary) would otherwise silently collide into one
dict entry and one of them would vanish with no error, the same class of
bug already learned the hard way building Tennessee's wide-format parser —
never use the final output's identifying field as the INTERNAL aggregation
key when a richer one is available.

This also transparently absorbs a real oddity in the 2026 Democratic file:
a late-qualified candidate (Jeffrey Hulum III, confirmed via AP/local
reporting to be a candidate for the 4th Congressional District) is appended
in his own trailing block, AFTER the shared table's own pagination has
already finished, with no contest header repeated for him — he simply
inherits "CD4" as the last contest header seen before his block starts,
which the same persistent `current contest` tracking gets right with no
special-casing, exactly the way it already carries a contest across an
ordinary same-row page break for every other candidate.

Mississippi nominates on a MAJORITY (Miss. Const. of 1890's primary-runoff
requirement, still the law): a candidate under 50% goes to a runoff three
weeks later. Verified live against the real 2026-03-10 primary: every
federal contest in both parties cleared 50% on its own (the closest,
Republican CD2, at 51.1%), so the 2026-04-07 runoff decided no FEDERAL race
— this module reads only the primary PDFs, and the threshold (read from
state_candidate_sources.json's "MS" entry, not hardcoded here) is what
correctly withholds a race if a future cycle's primary doesn't clear that
bar, exactly like every other majority-runoff state already on this system
(AL, AR, GA, OK, SC, TX).

ponytail: no runoff PDF is fetched — there is no code path that would ever
resolve a race that DOES miss the 50% bar, unlike GA's config, which fetches
and merges a real second-primary stage for exactly this reason. A future
cycle where a race actually goes to a runoff will confirm nobody for that
seat, forever, until someone adds a fourth discovery hop for the runoff
recap PDF (same SOS site, same shape, a later date) alongside the primary
one already here.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import _votes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_INDEX_URL = "https://www.sos.ms.gov/elections-voting/election-results"
_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)

_SENATE_RE = re.compile(r"United States-Senate")
_HOUSE_RE = re.compile(r"US House Of Rep (\d+)-")
_PARTY_WORDS = {"Republican", "Democrat"}


async def _get(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=_HEADERS,
    )


async def _discover_pdf_url(client: httpx.AsyncClient, year: int, party_label: str) -> str | None:
    """The current cycle's statewide recap PDF for `party_label`
    ("republican"/"democratic"), found by following the SOS's own real
    links rather than assembling any part of the path from a template."""
    index = await _get(client, _INDEX_URL, f"MS election-results index {year}")
    if index is None:
        return None
    page_m = re.search(
        rf'href="(/elections-voting/election-results/{year}/[^"]*-{party_label}-primary-results)"',
        index.text,
    )
    if not page_m:
        logger.info("MS: no %d %s primary results page listed yet", year, party_label)
        return None

    results_page = await _get(
        client, urljoin(_INDEX_URL, page_m.group(1)),
        f"MS {party_label} primary results page {year}",
    )
    if results_page is None:
        return None
    iframe_m = re.search(r'<iframe[^>]*\bsrc="([^"]+)"', results_page.text)
    if not iframe_m:
        logger.info("MS: %s primary results page has no results iframe", party_label)
        return None

    iframe = await _get(client, iframe_m.group(1), f"MS {party_label} primary iframe {year}")
    if iframe is None:
        return None
    pdf_m = re.search(
        rf'href="([^"]+\.pdf)"[^<]*>\s*{year}\s+{party_label.capitalize()}'
        r"\s+Primary\s+Election\s+Results",
        iframe.text,
    )
    if not pdf_m:
        logger.info("MS: %s primary iframe has no statewide results PDF link", party_label)
        return None
    return urljoin(iframe_m.group(1), pdf_m.group(1))


def _process_rows(line_tokens: list[list[str]], runoff_threshold_pct: float | None) -> list[dict]:
    """The actual row-interpretation logic, taking each row's tokens
    already in left-to-right reading order — split out from
    `_parse_recap_pdf`'s pdfplumber/geometry plumbing so it can be
    exercised directly with a small constructed row list, the same way
    KY's own PDF module tests its row logic separately from the PDF
    extraction around it.

    See the module docstring for why a running {raw name: total} map,
    overwritten on every occurrence, is what correctly resolves a
    candidate's real final total out of a table that repeats their row
    once per page — and why the key is the raw printed name, not the
    surname."""
    running: dict[tuple[str, int | None, str], tuple[str, int]] = {}
    current: tuple[str, int | None] | None = None

    for tokens in line_tokens:
        line = " ".join(tokens)
        if _SENATE_RE.search(line):
            current = ("S", None)
            continue
        house_m = _HOUSE_RE.search(line)
        if house_m:
            current = ("H", int(house_m.group(1)))
            continue
        if current is None:
            continue
        party_idx = next((i for i, t in enumerate(tokens) if t in _PARTY_WORDS), None)
        if party_idx is None:
            continue
        trailing = tokens[party_idx + 1:]
        if not trailing:
            continue
        raw_name = " ".join(tokens[:party_idx])
        party = normalize_party(tokens[party_idx])
        if not raw_name or not party:
            continue
        office, district = current
        running[(office, district, raw_name)] = (party, _votes(trailing[-1]))

    by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for (office, district, raw_name), (party, votes) in running.items():
        last_name = surname(raw_name)
        if not last_name:
            continue
        by_seat.setdefault((office, district, party), []).append((last_name, votes))

    results: list[dict] = []
    for (office, district, party), choices in by_seat.items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if won:
            results.append({
                "office": office, "district": district,
                "party": party, "last_name": won[0],
            })
    return results


def _parse_recap_pdf(content: bytes, runoff_threshold_pct: float | None) -> list[dict]:
    """Every confirmed nominee in this one party's recap PDF — extracts
    each page's words into row-clustered, left-to-right token lists (see
    ballot_measure_pdf_geometry.rows()) and hands them to `_process_rows`
    for the actual interpretation."""
    line_tokens: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            clustered = rows(page.extract_words())
            for row_id in sorted(clustered):
                line_tokens.append(
                    [w["text"] for w in sorted(clustered[row_id], key=lambda w: w["x0"])],
                )
    return _process_rows(line_tokens, runoff_threshold_pct)


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is MS-only by construction
) -> list[dict] | None:
    threshold = source.get("runoff_threshold_pct")
    urls: dict[str, str] = {}
    for party_label in ("republican", "democratic"):
        pdf_url = await _discover_pdf_url(client, year, party_label)
        if pdf_url is not None:
            urls[party_label] = pdf_url

    if not urls:
        # Neither party's results page exists yet this cycle — healthy
        # unknown (before the primary), not a fetch failure.
        return []
    if len(urls) == 1:
        # One party's results page exists and the other doesn't: both
        # primaries are held the same day and published together, so
        # this split is a much stronger signal of a broken discovery
        # regex (a page rename, a changed link format) than of a
        # genuinely one-sided primary — treated as a fetch failure
        # rather than silently returning half the state's nominees with
        # no error.
        missing = "democratic" if "republican" in urls else "republican"
        logger.warning(
            "MS: %s primary results page found but %s's is missing — "
            "treating as a broken discovery rather than a one-sided primary",
            next(iter(urls)), missing,
        )
        return None

    results: list[dict] = []
    for party_label, pdf_url in urls.items():
        resp = await _get(client, pdf_url, f"MS {party_label} primary recap {year}")
        if resp is None:
            return None
        try:
            party_results = _parse_recap_pdf(resp.content, threshold)
        except Exception:
            logger.exception("MS %s primary recap PDF for %d failed to parse", party_label, year)
            return None
        if not party_results:
            logger.warning(
                "MS %s primary recap PDF for %d fetched but yielded no "
                "candidates — a format change rather than a real empty "
                "primary is the likely cause", party_label, year,
            )
        results.extend(party_results)

    if not results:
        logger.warning("MS primary recap PDFs for %d yielded no confirmed nominees", year)
        return None
    return results
