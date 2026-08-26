"""Fetch + parse the sitting president's OGE Form 278-T periodic transaction
reports (disclosed securities and virtual-currency buys/sells).

Source: the Office of Government Ethics' public index of Presidential/PAS
financial disclosure filings (extapps2.oge.gov). The President is a covered
filer under the same STOCK Act reporting duty as Congress (5 U.S.C. §13103) —
purchases, sales and exchanges over $1,000 by the filer, spouse, or dependent
child, reported within 30 days of notification and no later than 45 days
after the transaction. The executive-branch form is OGE 278-T rather than the
House/Senate PTR, but it reports the same fields in the same shape (asset,
owner, transaction type, transaction date, notification date, amount
*bracket*), so ptr_common.py's parser handles it unchanged — see
parse_table_rows, which binds columns by header text rather than fixed
position precisely so form variants like this one work without a second
parser.

What is NOT derived here, deliberately: any profit, gain, or return figure.
278-T carries an amount bracket per transaction and no cost basis, share
count, or realized-gain field anywhere on the form, so a P&L number could
only be an estimate this platform invented — see models.py PresidentTrade's
docstring and president_service.py's own account of removing hand-set values
presented as computed ones. Everything below stops at "what was disclosed."

LIVE-VERIFIED (2026-08-26): the old Notes-view URL this module used to fetch
(`.../201/Presiden.nsf/PAS%20Index?OpenView`) now 301s to a page whose filing
table is populated client-side; the actual data comes from a DataTables
server-side-processing REST endpoint (OGE_API_URL below) found via a live
Playwright network trace of the new page. It's unauthenticated JSON, filtered
server-side by a `name` column search, so the response for one president is a
few dozen rows rather than the ~16k-row full index. Confirmed live: searching
"trump" returns the sitting president's 278-T filings with direct
`$FILE/....pdf` links.
"""

import hashlib
import logging
import re
from dataclasses import asdict
from difflib import SequenceMatcher
from urllib.parse import urlparse

from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.fetch.ptr_common import TradeRow, parse_pdf_bytes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# The DataTables server-side-processing endpoint backing OGE's public
# "Officials Individual Disclosures Search" page — the same JSON source the
# page's own filing table calls client-side. The White House also posts the
# same PDFs under its own /wp-content/uploads/ paths, which is why both
# hosts are allowed for the per-filing download below.
OGE_API_URL = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest"

# The columns this DataTables endpoint expects to be told about, in order.
# Documented data-format convention (the API's own contract), not a
# classification decision.
_OGE_API_COLUMNS = ("docDate", "title", "type", "name", "agency", "level")

# Comfortably above any one president's filing count over a term (a live
# check on 2026-08-26 found 26 total for the sitting president) — the
# endpoint orders newest-first, so a lower cap would silently drop the
# oldest filings of a long term rather than erroring.
_OGE_API_PAGE_SIZE = 500

# SSRF guard for the per-filing PDF fetch — the URL comes from a scraped
# index page, so it is untrusted input. Same pattern as
# presidential_actions.py's _ALLOWED_HOSTS.
_ALLOWED_PDF_HOSTS = {
    "extapps2.oge.gov",
    "www.oge.gov",
    "oge.gov",
    "www.whitehouse.gov",
    "whitehouse.gov",
}

_CACHE_TIER = "president_ptr"
# The index changes only when a new filing is posted (a few times a year);
# a parsed filing never changes at all.
_INDEX_MAX_AGE_HOURS = 24
_FILING_MAX_AGE_HOURS = 24 * 30

# Form-type vocabulary as printed on the filing itself. This is a documented
# data-format convention (the form's own name/number), not a classification
# decision — the same narrow exception ptr_common.py already relies on for
# OWNER_CODES and TXN_TYPE_PATTERNS, and the one AGENTS.md's "never hardcoded
# rules" principle explicitly allows for form values. Deciding *what an asset
# is* stays with the embedding classifier (see stock_pipeline.py).
_PTR_FORM_MARKERS = ("278-t", "278t", "periodic transaction", "278 transaction")

# The annual report (OGE 278e) and the initial "New Entrant" filing list
# holdings and income in ranges — not transactions. A row identifying as one
# is skipped even if it also carries a periodic-transaction marker: parsing
# a holdings table into the trades table would manufacture buy/sell events
# that were never disclosed, so an ambiguous row resolves toward ingesting
# nothing.
_ANNUAL_FORM_MARKERS = ("278-e", "278e", "annual report", "annual (", "annual term", "new entrant")

# A name match this close is the same name, not a coincidence — Ratcliff &
# Obershelp ratio, the same fuzzy-name technique donor_classifier_ai.py uses
# for self-funded detection. Deliberately strict: the index lists hundreds of
# PAS appointees, and ingesting another official's filing under the
# president's own id would be a factual error, not a near miss.
_NAME_MATCH_THRESHOLD = 0.92

# Initials and "Jr."-style suffixes are dropped before matching: the index
# and the UCSB roster disagree about them constantly ("Trump, Donald J." vs
# "Donald Trump"), so requiring them would reject every real row.
_MIN_NAME_TOKEN_LEN = 3

# Exact cell values that identify the filer as the President. Matched
# against a whole cell rather than searched for in the row text, which is
# what keeps "Vice President" and "Assistant to the President" — both real
# values in this index's position column — from reading as the office.
# Documented data-format convention, same exception class as the form
# markers above.
_OFFICE_CELL_VALUES = {
    "president",
    "the president",
    "president of the united states",
}

_rate_limiter = RateLimiter(settings.PRESIDENT_PTR_RPS)


def _alert(subject: str, body: str, *, dedupe_key: str) -> None:
    """Best-effort ops alert — lazily imported and never allowed to raise,
    the same pattern member_lifecycle.py uses for mid-pipeline alerting."""
    try:
        from app.ops_alerts import send_ops_alert
        send_ops_alert(subject, body, dedupe_key=dedupe_key)
    except Exception:
        logger.exception("Failed to send ops alert: %s", subject)


def _filing_id_for(pdf_url: str) -> str:
    """Stable, collision-free dedupe key for one filing.

    Always carries a digest of the full path, never the bare filename: a
    Domino attachment link ends in whatever the filer named the file, so
    generic names ("download.pdf", "attachment.pdf") repeat across
    documents, and a filename-only key would silently collapse every such
    filing into the first one ingested — a whole filing's transactions
    missing, with nothing to indicate it. The readable slug is kept as a
    prefix so the id still identifies its filing at a glance.

    Keyed on the path alone so a scheme/host/query change on an otherwise
    identical link doesn't mint a second id and re-ingest the filing.
    """
    path = urlparse(pdf_url).path.rstrip("/")
    digest = hashlib.sha256(path.encode()).hexdigest()[:12]
    stem = re.sub(r"\.pdf$", "", path.rsplit("/", 1)[-1], flags=re.I)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:40]
    return f"{slug}-{digest}" if slug else digest


def _significant_name_tokens(name: str) -> list[str]:
    """Name tokens worth matching on — initials and suffixes dropped."""
    return [t for t in re.findall(r"[A-Za-z]+", (name or "").lower()) if len(t) >= _MIN_NAME_TOKEN_LEN]


def _names_the_office(cells: list[str]) -> bool:
    """True when one of the row's cells is exactly the President's office."""
    return any(cell.strip().lower().strip(".") in _OFFICE_CELL_VALUES for cell in cells)


def _names_this_president(cells: list[str], president_name: str) -> bool:
    """True when this index row is a filing by this president.

    Surname alone is not enough. The index lists hundreds of appointees and
    presidential relatives have held appointed positions — matching on
    surname would have filed Eric Trump's or Melania Trump's disclosures
    under the president's own id, which is a factual error about who traded
    what, not a near miss. So a row qualifies only when the given name(s)
    also match, or when the row's position cell is exactly the office.

    The office fallback is what keeps a roster/index first-name mismatch
    ("Jimmy Carter" in the roster, "Carter, James E." in the index) from
    rejecting a genuine presidential filing — and because it tests a whole
    cell for the exact office, "Vice President" and "Assistant to the
    President" don't satisfy it.
    """
    tokens = _significant_name_tokens(president_name)
    if not tokens:
        return False

    row_tokens = re.findall(r"[A-Za-z]+", " ".join(cells).lower())

    def present(target: str) -> bool:
        return any(
            SequenceMatcher(None, target, token).ratio() >= _NAME_MATCH_THRESHOLD
            for token in row_tokens
        )

    surname, given = tokens[-1], tokens[:-1]
    if not present(surname):
        return False
    if given and all(present(g) for g in given):
        return True
    return _names_the_office(cells)


def _oge_api_params(surname: str) -> dict:
    """DataTables server-side-processing params for OGE_API_URL.

    Filtered server-side on the `name` column so the response is a few dozen
    rows instead of the ~16k-row full index. Surname only, not the full
    president name: _names_this_president below still re-checks given name
    and the office cell, since a surname-only server filter would also
    return, e.g., a presidential relative's own filings.
    """
    params = {
        "draw": "1",
        "order[0][column]": "0",
        "order[0][dir]": "desc",
        "start": "0",
        "length": str(_OGE_API_PAGE_SIZE),
        "search[value]": "",
        "search[regex]": "false",
    }
    for i, column in enumerate(_OGE_API_COLUMNS):
        params[f"columns[{i}][data]"] = column
        params[f"columns[{i}][name]"] = ""
        params[f"columns[{i}][searchable]"] = "true"
        params[f"columns[{i}][orderable]"] = "true"
        params[f"columns[{i}][search][value]"] = surname if column == "name" else ""
        params[f"columns[{i}][search][regex]"] = "false"
    return params


def _parse_index(rows: list[dict], president_name: str) -> list[dict]:
    """Extract this president's PTR filings from the OGE API's rows.

    Each row's `type` field is an HTML fragment — an anchor to the PDF for a
    postable filing, or an anchor to a "Request this Document" form for one
    that isn't directly downloadable (filtered out below by the .pdf-suffix
    check, same as a scraped page would be).
    """
    filings: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        type_html = row.get("type") or ""
        anchor = lxml_html.fromstring(f"<div>{type_html}</div>").find(".//a")
        if anchor is None:
            continue
        pdf_url = (anchor.get("href") or "").strip()
        if not pdf_url:
            continue
        parsed = urlparse(pdf_url)
        if not parsed.path.lower().endswith(".pdf"):
            continue
        # Checked here as well as at download time (fetch_and_parse_ptr):
        # the download guard is the security boundary, but filtering here
        # too keeps an off-host link from being counted as a filing this
        # president made and then quietly yielding no rows.
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_PDF_HOSTS:
            continue

        haystack = f"{type_html} {pdf_url}".lower()
        if any(marker in haystack for marker in _ANNUAL_FORM_MARKERS):
            continue
        if not any(marker in haystack for marker in _PTR_FORM_MARKERS):
            continue
        if not _names_this_president([row.get("name") or "", row.get("title") or ""], president_name):
            continue

        filing_id = _filing_id_for(pdf_url)
        if filing_id in seen:
            continue
        seen.add(filing_id)
        filings.append({
            "doc_id": filing_id,
            "filing_date": (row.get("docDate") or "")[:10] or None,
            "pdf_url": pdf_url,
        })

    return filings


async def fetch_ptr_filing_index(db: Session, president_name: str) -> list[dict]:
    """Fetch and parse OGE's index of the sitting president's 278-T filings.

    Returns one dict per filing: {doc_id, filing_date, pdf_url}. Returns an
    empty list (never None) on failure — a caller must treat "couldn't fetch
    this run" as "leave existing rows alone," not as "the president disclosed
    no trades."
    """
    cache_key = f"ptr-index-{president_name.lower().replace(' ', '-')}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_INDEX_MAX_AGE_HOURS)
    if cached is not None:
        return cached

    surname_tokens = _significant_name_tokens(president_name)
    surname = surname_tokens[-1] if surname_tokens else president_name

    resp = await fetch_with_retry_requests(
        _rate_limiter, "GET", OGE_API_URL, log_label="OGE presidential disclosure index",
        params=_oge_api_params(surname),
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch OGE presidential disclosure index (%s)", OGE_API_URL)
        return []

    try:
        filings = _parse_index(resp.json().get("data") or [], president_name)
    except Exception:
        logger.exception("Failed to parse OGE presidential disclosure index")
        return []

    if not filings:
        # Never cached as a real result: a zero here is far more likely to be
        # a changed API contract than a president who has filed nothing, and
        # caching it would hide the breakage for a day at a time. See this
        # module's docstring on why that failure mode is the one to guard
        # against.
        #
        # Alerted, not just logged: nothing downstream can tell "the API
        # contract moved" from "no filings exist" — both render as a card
        # with no disclosure section — so a human has to look. Deduped per
        # president so a genuinely-empty result (a just-inaugurated
        # president who hasn't filed yet) costs one alert, not one per
        # nightly run.
        logger.warning(
            "OGE disclosure API returned 0 periodic transaction reports for %s — "
            "the response contract may have changed (see president_ptr.py module docstring)",
            president_name,
        )
        _alert(
            "Presidential 278-T index parsed to zero filings",
            f"{OGE_API_URL} fetched successfully but yielded no periodic transaction "
            f"reports for {president_name}. Either the API contract changed (see "
            f"president_ptr.py's LIVE-VERIFIED note) or this president has genuinely "
            f"filed none yet — the parser cannot tell these apart. Check the live page.",
            dedupe_key=f"president-ptr-empty-index-{president_name.lower().replace(' ', '-')}",
        )
        return []

    api_cache_set(db, _CACHE_TIER, cache_key, filings, normal_ttl_hours=_INDEX_MAX_AGE_HOURS)
    return filings


async def fetch_and_parse_ptr(db: Session, filing: dict) -> list[TradeRow]:
    """Download and parse one 278-T PDF into transaction rows.

    Returns rows tagged with parse_confidence ("text" or "ocr"), or an empty
    list if the PDF can't be fetched or holds no parseable transaction table
    — never a fabricated row.
    """
    cache_key = f"ptr-parsed-{filing['doc_id']}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_FILING_MAX_AGE_HOURS)
    if cached is not None:
        return [TradeRow(**row) for row in cached]

    pdf_url = filing["pdf_url"]
    parsed_url = urlparse(pdf_url)
    if parsed_url.scheme != "https" or parsed_url.hostname not in _ALLOWED_PDF_HOSTS:
        logger.warning("Rejected non-allowlisted PTR URL: %s", pdf_url[:120])
        return []

    resp = await fetch_with_retry_requests(
        _rate_limiter, "GET", pdf_url, log_label="Presidential 278-T", timeout=60.0,
    )
    if resp is None or resp.status_code != 200:
        return []

    try:
        rows, confidence = parse_pdf_bytes(resp.content)
    except Exception as e:
        logger.error("Failed to parse presidential 278-T PDF %s: %s", pdf_url, e)
        return []

    for row in rows:
        row.parse_confidence = confidence
        row.source_url = pdf_url
        row.filing_id = filing["doc_id"]

    # The API cache stores plain JSON, not dataclasses — same convert-here/
    # reconstruct-on-read boundary as house_ptr.py, with the write TTL
    # matching the read's max_age_hours above.
    api_cache_set(
        db, _CACHE_TIER, cache_key, [asdict(row) for row in rows],
        normal_ttl_hours=_FILING_MAX_AGE_HOURS,
    )
    return rows
