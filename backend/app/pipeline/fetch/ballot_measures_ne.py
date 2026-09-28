"""Nebraska's ballot-measure strategy — the Secretary of State's
elections page, the initiative pamphlet it links, and the Legislature's
own text of each legislature-referred amendment it links (one of
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py: three documents
from two offices, cross-checked against each other).

sos.nebraska.gov/elections lists everything on the general-election
ballot in two places, verified live 2026-09-28:

  - "Ballot Measures for 2026 General Election / Initiative Nos.
    440-442", linking the "Informational Pamphlet" (and a Spanish
    edition, not read).
  - "Constitutional Amendment passed by the Nebraska Legislature for the
    2026 General Election", linking "LR19CA" (the Legislature's bill page)
    and a scanned image of the Executive Board's ballot statement for it.

Initiatives. The pamphlet ("Informational Pamphlet on Initiative
Measures", 22 pages in 2026) prints each initiative's ballot text on a
page headed "Ballot Title and Text for Initiative Measure <n>":

    Proposed by Initiative Petition
    A vote “FOR” will amend the Nebraska Constitution to ...
    A vote “AGAINST” means the Nebraska Constitution will not be amended ...
    Shall the Nebraska Constitution be amended to ...?
    For
    Against

The "FOR"/"AGAINST" paragraphs are Nebraska's own yes/no framing and are
stored verbatim as yes_means/no_means; the "Shall ...?" question is the
official summary. The pamphlet does not say who drafted these, so no
drafter is named. The set of initiative numbers read must equal the
page's own "Initiative Nos." range or the read is refused.

Legislature-referred amendments. The Executive Board's ballot statement
is published only as a scanned image (no text layer, verified), and
OCR'd text is not verbatim text, so it is not read. The Legislature's
own resolution is: the SOS page links the Legislature's bill page, which
links the "Slip Law" PDF, whose "Sec. 2" gives "the following ballot
language: <text> For Against." That ballot language — enacted by the
Legislature — is stored as the official summary, with the Legislature
named as drafter; the resolution's own "At the general election in
November <year>" is checked so a resolution for another election can't
be attached to this one. yes_means/no_means stay None for these (they
live only in the scanned statement).

Every listed measure must be read or the whole read is None — the ballot
is never published short. The nebraskalegislature.gov host resets
connections intermittently (seen repeatedly during research); the shared
retry helper absorbs most of that, and a failure that survives it is an
ingest failure, not a shorter ballot.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import join_lines
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

ELECTIONS_URL = "https://sos.nebraska.gov/elections"
LEGISLATURE = "Nebraska Legislature"

_rate_limiter = RateLimiter(rps=1.0)

_RANGE_RE = re.compile(r"Initiative Nos?\.\s*([\d,\s–-]+)")
_LR_TEXT_RE = re.compile(r"^LR\d+CA$")
_PAGE_HEAD_RE = re.compile(r"^Ballot Title and Text for Initiative Measure (\d+)$")
_PROPOSED_BY_RE = re.compile(r"^Proposed by (.+)$")
_FOR_RE = re.compile(r"^A vote “FOR”")
_AGAINST_RE = re.compile(r"^A vote “AGAINST”")
_LR_TITLE_RE = re.compile(r"LEGISLATIVE RESOLUTION (\d+CA)")
_LR_BALLOT_RE = re.compile(r"with\s+the\s+following\s+ballot\s+language:\s*(.*?)\s*\nFor\s*\nAgainst", re.DOTALL)


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _expand_numbers(spec: str) -> set[str]:
    numbers: set[str] = set()
    for part in re.split(r"\s*,\s*", spec.strip()):
        bounds = re.split(r"\s*[–-]\s*", part)
        if len(bounds) == 2 and all(b.isdigit() for b in bounds):
            numbers.update(str(n) for n in range(int(bounds[0]), int(bounds[1]) + 1))
        elif part.isdigit():
            numbers.add(part)
    return numbers


def read_elections_page(page_html: str, year: int) -> dict | None:
    """{"initiatives": set of numbers, "pamphlet": url | None,
    "amendments": [bill-page url, ...]} for `year`'s general election,
    or None when the page names nothing for that election at all."""
    tree = lxml_html.fromstring(page_html)
    initiatives: set[str] = set()
    for p in tree.xpath("//p"):
        text = _norm(p.text_content())
        if text.startswith(f"Ballot Measures for {year} General Election"):
            m = _RANGE_RE.search(text)
            if m:
                initiatives |= _expand_numbers(m.group(1))

    pamphlets = {
        urljoin(ELECTIONS_URL, a.get("href"))
        for a in tree.xpath("//a[@href]")
        if _norm(a.text_content()) == "Informational Pamphlet" and f"/{year}/" in a.get("href")
    }

    amendments = []
    for a in tree.xpath("//a[@href]"):
        if not _LR_TEXT_RE.match(_norm(a.text_content())):
            continue
        parent = a.getparent()
        if parent is not None and f"for the {year} General Election" in _norm(parent.text_content()):
            amendments.append(urljoin(ELECTIONS_URL, a.get("href")))

    if not initiatives and not amendments:
        return None
    if initiatives and len(pamphlets) != 1:
        logger.warning("NE %d: initiatives %s listed but %d pamphlet link(s)", year, sorted(initiatives), len(pamphlets))
        return None
    return {
        "initiatives": initiatives,
        "pamphlet": pamphlets.pop() if initiatives else None,
        "amendments": list(dict.fromkeys(amendments)),
    }


def parse_pamphlet(page_texts: list[str]) -> dict[str, dict] | None:
    found: dict[str, dict] = {}
    for text in page_texts:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        head = next((i for i, ln in enumerate(lines) if _PAGE_HEAD_RE.match(ln)), None)
        if head is None:
            continue
        number = _PAGE_HEAD_RE.match(lines[head]).group(1)
        body = lines[head + 1:]
        proposed = _PROPOSED_BY_RE.match(body[0]) if body else None
        for_idx = next((i for i, ln in enumerate(body) if _FOR_RE.match(ln)), None)
        against_idx = next((i for i, ln in enumerate(body) if _AGAINST_RE.match(ln)), None)
        box_idx = next(
            (i for i in range(len(body) - 1) if body[i] == "For" and body[i + 1] == "Against"),
            None,
        )
        if proposed is None or for_idx is None or against_idx is None or box_idx is None:
            logger.warning("NE Initiative Measure %s: pamphlet page didn't match the verified shape", number)
            return None
        if not (for_idx < against_idx < box_idx):
            return None
        # The AGAINST paragraph runs until the question, which on every
        # verified page opens with "Shall".
        question_idx = next(
            (i for i in range(against_idx + 1, box_idx) if body[i].startswith("Shall ")), None,
        )
        if question_idx is None:
            return None
        found[number] = {
            "number": number,
            "title": f"Initiative Measure {number}",
            "origin": "Nebraska voters (initiative petition)" if "initiative" in proposed.group(1).lower() else None,
            "official_summary": join_lines(body[question_idx:box_idx]),
            "fiscal_impact": None,
            "yes_means": join_lines(body[for_idx:against_idx]),
            "no_means": join_lines(body[against_idx:question_idx]),
            "title_authority": None,
            "fiscal_authority": None,
        }
    return found


def slip_law_url(bill_page_html: str, bill_page_url: str) -> str | None:
    tree = lxml_html.fromstring(bill_page_html)
    hrefs = {
        urljoin(bill_page_url, a.get("href"))
        for a in tree.xpath("//a[@href]")
        if _norm(a.text_content()) == "Slip Law" and a.get("href").lower().endswith(".pdf")
    }
    return hrefs.pop() if len(hrefs) == 1 else None


def parse_slip_law(text: str, year: int) -> dict | None:
    title = _LR_TITLE_RE.search(text)
    ballot = _LR_BALLOT_RE.search(text)
    if title is None or ballot is None:
        return None
    if not re.search(rf"At the general election in November {year}\b", " ".join(text.split())):
        return None
    number = f"LR{title.group(1)}"
    return {
        "number": number,
        "title": f"Legislative Resolution {title.group(1)}",
        "official_title": None,
        "origin": LEGISLATURE,
        "official_summary": join_lines(ballot.group(1)),
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": LEGISLATURE,
        "fiscal_authority": None,
    }


def _pdf_pages(raw: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await fetch_text_with_retry(client, _rate_limiter, ELECTIONS_URL, "NE elections page")
    if page_html is None:
        return None
    try:
        listing = read_elections_page(page_html, year)
    except Exception:
        logger.exception("NE elections page was not parseable")
        return None
    if listing is None:
        return None

    results: list[tuple[dict, str]] = []
    if listing["initiatives"]:
        raw = await fetch_bytes_with_retry(client, _rate_limiter, listing["pamphlet"], "NE initiative pamphlet")
        if raw is None:
            return None
        try:
            pamphlet = parse_pamphlet(_pdf_pages(raw))
        except Exception:
            logger.exception("NE initiative pamphlet parse failed")
            return None
        if pamphlet is None or set(pamphlet) != listing["initiatives"]:
            logger.warning(
                "NE %d: page lists initiatives %s, pamphlet yields %s — refusing",
                year, sorted(listing["initiatives"]), sorted(pamphlet or {}),
            )
            return None
        results.extend((pamphlet[n], listing["pamphlet"]) for n in sorted(pamphlet, key=int))

    for bill_url in listing["amendments"]:
        bill_html = await fetch_text_with_retry(client, _rate_limiter, bill_url, "NE legislature bill page")
        if bill_html is None:
            return None
        pdf_url = slip_law_url(bill_html, bill_url)
        if pdf_url is None:
            logger.warning("NE: no single Slip Law link on %s", bill_url)
            return None
        raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "NE slip law PDF")
        if raw is None:
            return None
        try:
            parsed = parse_slip_law("\n".join(_pdf_pages(raw)), year)
        except Exception:
            logger.exception("NE slip law parse failed: %s", pdf_url)
            return None
        if parsed is None:
            logger.warning("NE slip law didn't match the verified shape: %s", pdf_url)
            return None
        results.append((parsed, pdf_url))
    return results
