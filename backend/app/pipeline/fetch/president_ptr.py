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

NOT LIVE-VERIFIED (2026-07-31): unlike house_ptr.py/senate_ptr.py, whose
notes record what a real fetch returned, neither extapps2.oge.gov nor
www.whitehouse.gov is reachable from the environment this module was written
in (the egress proxy 403s the CONNECT for both hosts), so the OGE index's
exact markup could not be checked against the live page. The index parse is
therefore written defensively — it walks anchors and their surrounding row
text rather than assuming a table layout, matches the filer by fuzzy name
similarity, and treats "zero filings parsed" as a loud warning rather than a
silent empty result. senate_ptr.py's own history is the reason for that
caution: its original column-order assumption was wrong and would have
silently produced zero rows on every run. Confirm against the live page
before trusting a zero-row outcome.
"""

import hashlib
import logging
import re
from dataclasses import asdict
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.fetch.ptr_common import TradeRow, normalize_date, parse_pdf_bytes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# OGE's public index of Presidential and PAS-appointee disclosure filings.
# This is the canonical cross-administration archive; the White House also
# posts the same PDFs under its own /wp-content/uploads/ paths, which is why
# both hosts are allowed for the per-filing download below.
OGE_INDEX_URL = "https://extapps2.oge.gov/201/Presiden.nsf/PAS%20Index?OpenView"

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
_PTR_FORM_MARKERS = ("278-t", "278t", "periodic transaction")

# A surname match this close is the same name, not a coincidence — Ratcliff &
# Obershelp ratio, the same fuzzy-name technique donor_classifier_ai.py uses
# for self-funded detection. Deliberately strict: the index lists hundreds of
# PAS appointees, and ingesting another official's filing under the
# president's own id would be a factual error, not a near miss.
_SURNAME_MATCH_THRESHOLD = 0.92

_DATE_PATTERNS = (
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
)

_rate_limiter = RateLimiter(settings.PRESIDENT_PTR_RPS)


def _filing_id_for(pdf_url: str) -> str:
    """Stable dedupe key for one filing.

    The filename is the natural key when the index links straight to a PDF;
    a hash of the full URL is the fallback for Domino-style links whose last
    path segment isn't unique on its own.
    """
    last = urlparse(pdf_url).path.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.pdf$", "", last, flags=re.I).strip()
    if len(stem) >= 8:
        return stem
    return hashlib.sha1(pdf_url.encode()).hexdigest()[:16]


def _row_date(text: str) -> str | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        return normalize_date(raw) or (raw if raw.count("-") == 2 else None)
    return None


def _node_text(node) -> str:
    """Flatten an element to space-separated text.

    Not text_content(): that concatenates adjacent cells with no separator,
    so a row of <td>s renders as "...Periodic Transaction Report11/14/2025"
    and the date regex's leading \\b never matches (the filing date silently
    came back None until a test caught it). Joining the text nodes keeps
    cell boundaries visible to both the date and filer matching below.
    """
    return " ".join(t.strip() for t in node.itertext() if t.strip())


def _surname_matches(row_text: str, president_name: str) -> bool:
    """True when the index row names this president.

    Compares the president's surname against every token in the row rather
    than substring-matching the full display name: the index prints filers
    as "Trump, Donald J." while the roster stores "Donald Trump", so a
    substring test on either form fails on the other.
    """
    surname = (president_name or "").strip().split()[-1:] or [""]
    target = surname[0].lower()
    if len(target) < 3:
        return False
    # Known limit: another PAS filer who shares the president's surname
    # would match too. Nothing else in the row is reliable enough to
    # disambiguate on (the position column's wording isn't guaranteed), and
    # the alternative — matching the full "Last, First M." string — fails
    # outright whenever the index's name formatting differs at all, which
    # is the far more likely failure. Worth revisiting once the live page's
    # actual columns are confirmed.
    for token in re.findall(r"[A-Za-z]+", row_text.lower()):
        if SequenceMatcher(None, target, token).ratio() >= _SURNAME_MATCH_THRESHOLD:
            return True
    return False


def _parse_index(page_html: str, base_url: str, president_name: str) -> list[dict]:
    """Extract this president's PTR filings from the OGE index page.

    Walks anchors and the text of each anchor's enclosing row, so a change
    to the view's column layout can't silently break the parse the way a
    fixed-position table read would.
    """
    tree = lxml_html.fromstring(page_html)
    filings: list[dict] = []
    seen: set[str] = set()

    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        pdf_url = urljoin(base_url, href)
        if not urlparse(pdf_url).path.lower().endswith(".pdf"):
            continue

        # Row text, falling back to the link's own text when the anchor
        # isn't inside a table row.
        row = anchor
        for _ in range(4):
            parent = row.getparent()
            if parent is None or parent.tag == "tr":
                row = parent if parent is not None else row
                break
            row = parent
        row_text = _node_text(row if row is not None else anchor)
        if not row_text:
            row_text = _node_text(anchor)

        haystack = f"{row_text} {pdf_url}".lower()
        if not any(marker in haystack for marker in _PTR_FORM_MARKERS):
            continue
        if not _surname_matches(row_text, president_name):
            continue

        filing_id = _filing_id_for(pdf_url)
        if filing_id in seen:
            continue
        seen.add(filing_id)
        filings.append({
            "doc_id": filing_id,
            "filing_date": _row_date(row_text),
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

    resp = await fetch_with_retry_requests(
        _rate_limiter, "GET", OGE_INDEX_URL, log_label="OGE presidential disclosure index",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch OGE presidential disclosure index (%s)", OGE_INDEX_URL)
        return []

    try:
        filings = _parse_index(resp.text, OGE_INDEX_URL, president_name)
    except Exception:
        logger.exception("Failed to parse OGE presidential disclosure index")
        return []

    if not filings:
        # Never cached as a real result: a zero here is far more likely to be
        # a changed page structure than a president who has filed nothing,
        # and caching it would hide the breakage for a day at a time. See
        # this module's docstring on why that failure mode is the one to
        # guard against.
        logger.warning(
            "OGE disclosure index parsed to 0 periodic transaction reports for %s — "
            "page structure may have changed (see president_ptr.py module docstring)",
            president_name,
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
