"""Statements of Administration Policy (SAPs), from OMB's own index.

A SAP is the Executive Office of the President's formal position on a
measure before a floor vote, issued by the Office of Management and Budget
under OMB Circular A-19 and addressed to one chamber. OMB lists every SAP of
the current administration on one page of whitehouse.gov, each a link to the
signed PDF:

    https://www.whitehouse.gov/omb/information-resources/legislative/statements-of-administration-policy/

(read 2026-10-13: 201 links, January 2025 on, no pagination). The index and
the PDFs are served without a bot wall; the site's WordPress JSON API answers
403 and is not used. robots.txt allows both.

What is read from a SAP is its document format, never a judgement:

  - the chamber it is addressed to, printed under the date as "(House)",
    "(House Rules)" or "(Senate)";
  - the measures it covers, the bill and resolution citations in its title
    block (GPO citation style: "H.R. 9340", "S.J. Res. 199",
    "H. Con. Res. 89"), and an amendment's number where the title names one
    ("S. Amendment 6715 to H.R. 5334");
  - its opening and closing paragraphs, verbatim. The position itself is
    read from those by analyze/sap_position.py (embedding similarity), not
    here.

A failed fetch is never "no statements": the index returns None and the
caller keeps what it has (see feedback on outage vs no data in AGENTS.md
§7's discipline).
"""

import io
import logging
import re
import statistics
from datetime import datetime
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.floor_logs import bill_id_from_number
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

INDEX_URL = (
    "https://www.whitehouse.gov/omb/information-resources/legislative/"
    "statements-of-administration-policy/"
)
# Bump when what parse_statement returns changes: stored statements read
# under an older version are read again.
PARSER_VERSION = 1

_rate_limiter = RateLimiter(1.0)

# GPO citation forms of a bill or resolution, as SAP title blocks print them.
# Longest first, so "S. J. Res. 1" is not read as "S. 1".
_CITATION_RE = re.compile(
    r"\b(H\.\s*Con\.\s*Res\.|S\.\s*Con\.\s*Res\.|H\.\s*J\.\s*Res\.|S\.\s*J\.\s*Res\.|"
    r"H\.\s*Res\.|S\.\s*Res\.|H\.\s*R\.|S\.)\s*(\d+)",
)
# "S. Amendment 6715", "S.Amdt. 6715", "Amendment No. 6715".
_AMENDMENT_RE = re.compile(r"\b(?:S\.\s*Amendment|S\.\s*Amdt\.|H\.\s*Amdt\.|Amendment\s+No\.)\s*(\d+)")
_CHAMBER_RE = re.compile(r"^\s*\((House|Senate)\b[^)]*\)\s*$")
_DATE_RE = re.compile(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b")
# The sponsor line that ends a title block: "(Rep. Doe, R-OK)",
# "(Sen. Roe, D-VA, and 11 cosponsors)", "(Senator Poe, R-SC)".
_SPONSOR_RE = re.compile(r"^\s*\((?:Rep|Sen|Senator)\b")
# The heading, compared without spaces over the text read so far. It is set
# in small capitals with larger initials, which the text layer puts on a
# line of their own ("S A P" over "TATEMENT OF DMINISTRATION OLICY") or
# splits word by word, so the initials are optional.
_HEADING_RE = re.compile(r"S?TATEMENTOFA?DMINISTRATIONP?OLICY")


def parse_index(page_html: str, base_url: str = INDEX_URL) -> list[dict]:
    """[{"url", "title"}] for each PDF the index links, in page order, each
    URL once."""
    try:
        root = lxml_html.fromstring(page_html)
    except (ValueError, lxml_html.etree.ParserError):
        return []
    out, seen = [], set()
    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        if not href.lower().endswith(".pdf"):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": " ".join(a.text_content().split())})
    return out


async def fetch_index(client: httpx.AsyncClient) -> list[dict] | None:
    """The index's entries, or None when it could not be read (an outage is
    not an empty list)."""
    page = await fetch_text_with_retry(client, _rate_limiter, INDEX_URL, "OMB SAP index")
    if page is None:
        return None
    entries = parse_index(page)
    if not entries:
        logger.warning("OMB SAP index read but lists no PDF: page layout changed?")
        return None
    return entries


async def fetch_statement_pdf(client: httpx.AsyncClient, url: str) -> bytes | None:
    return await fetch_bytes_with_retry(client, _rate_limiter, url, "OMB SAP")


def pdf_lines(raw: bytes) -> list[tuple[int, float, float, float, str]]:
    """(page, x0, top, bottom, text) for each text line of the PDF. Bold
    title text is printed twice over itself in these PDFs; dedupe_chars
    folds it back to one character."""
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page_no, page in enumerate(pdf.pages):
            for line in page.dedupe_chars().extract_text_lines():
                text = " ".join((line.get("text") or "").split())
                if text:
                    out.append((page_no, float(line["x0"]), float(line["top"]), float(line["bottom"]), text))
    return out


def _paragraphs(body: list[tuple[int, float, float, float, str]]) -> list[str]:
    """Join body lines into paragraphs. A paragraph break is a vertical gap
    clearly wider than the document's own line spacing (its median gap);
    lines that are only a page number or the closing row of asterisks are
    left out."""
    gaps = [b[2] - a[3] for a, b in zip(body, body[1:]) if a[0] == b[0] and b[2] > a[2]]
    spacing = statistics.median(gaps) if gaps else 0.0
    paragraphs: list[list[str]] = []
    previous = None
    for line in body:
        page, _, top, _, text = line
        if re.fullmatch(r"[*\s]+|\d+", text):
            continue
        new = previous is None or (page == previous[0] and top - previous[3] > 1.8 * spacing + 1.0)
        if new:
            paragraphs.append([])
        paragraphs[-1].append(text)
        previous = line
    return [" ".join(p) for p in paragraphs if any(re.search(r"[A-Za-z]", t) for t in p)]


def parse_statement(lines: list[tuple[int, float, float, float, str]]) -> dict | None:
    """One SAP's document fields from its text lines:

        {"issued": "YYYY-MM-DD", "chamber": "house" | "senate",
         "measures": ["HR.9340", ...], "amendments": ["6715", ...],
         "opening": str, "closing": str}

    None when the lines are not a SAP (no heading, no chamber, no date, no
    citation): such a file is skipped and logged, never guessed at."""
    heading, seen = None, ""
    for i, ln in enumerate(lines):
        seen += ln[4].upper().replace(" ", "")
        if _HEADING_RE.search(seen):
            heading = i
            break
    if heading is None:
        return None
    head = lines[:heading]
    chamber = next((m.group(1).lower() for ln in head if (m := _CHAMBER_RE.match(ln[4]))), None)
    issued = None
    for ln in head:
        if m := _DATE_RE.search(ln[4]):
            try:
                issued = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
                break
            except ValueError:
                continue
    after = lines[heading + 1:]
    wide = [ln for ln in after if len(ln[4]) > 40]
    if chamber is None or issued is None or not wide:
        return None
    # The title block is centred; the body starts at the left margin. A long
    # title can reach the margin too, so where a sponsor line closes the
    # title block the body starts after it.
    margin = min(ln[1] for ln in wide)
    start = next(i for i, ln in enumerate(after) if ln[1] <= margin + 2 and len(ln[4]) > 40)
    sponsor = next((i for i, ln in enumerate(after[:12]) if _SPONSOR_RE.match(ln[4])), None)
    if sponsor is not None and sponsor >= start:
        start = sponsor + 1
    title = " ".join(ln[4] for ln in after[:start])
    paragraphs = _paragraphs(after[start:])
    if not paragraphs:
        return None
    measures = _citations(title) or _citations(paragraphs[0])
    if not measures:
        return None
    return {
        "issued": issued,
        "chamber": chamber,
        "measures": measures,
        "amendments": list(dict.fromkeys(_AMENDMENT_RE.findall(title))),
        "opening": paragraphs[0],
        "closing": paragraphs[-1] if len(paragraphs) > 1 else "",
    }


def _citations(text: str) -> list[str]:
    """Site bill ids ("HR.9340") of every citation in `text`, each once, in
    order. An amendment's own number is not a bill."""
    text = _AMENDMENT_RE.sub(" ", text)
    out = []
    for prefix, number in _CITATION_RE.findall(text):
        bill = bill_id_from_number(f"{prefix} {number}")
        if bill and bill not in out:
            out.append(bill)
    return out


def read_statement(raw: bytes) -> dict | None:
    """parse_statement over a PDF's bytes; None for a file pdfplumber can't
    open or that isn't a SAP."""
    try:
        lines = pdf_lines(raw)
    except Exception:
        logger.warning("OMB SAP did not open as a PDF", exc_info=True)
        return None
    return parse_statement(lines)
