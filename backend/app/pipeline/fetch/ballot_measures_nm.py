"""New Mexico's ballot-measure strategy — two official documents,
because New Mexico's general-election ballot carries two kinds of
statewide question and no single state publication lists both (one of
potentially many per-state strategies; see ballot_measures_pdf.py,
MULTI_DOCUMENT_STRATEGIES).

1. Constitutional amendments: the Legislative Council Service's
   "Summary of and Arguments For and Against the Constitutional
   Amendments" (nmlegis.gov, evergreen {year} URL verified on two real
   generals, 2024 and 2026 — same layout both years). Its "Proposed
   Amendments to Appear on the <date> General Election Ballot (ballot
   text)" page lists each "Constitutional Amendment N:" with its ballot
   text in quotes (the document states that this title "is the only
   language that will appear on the ballot"); each amendment's
   "▸ SUMMARY of Proposed Constitutional Amendment N" section (up to
   "▸ BACKGROUND AND INFORMATION") is the LCS's own summary. Both are
   kept verbatim. The arguments for/against are staff-suggested
   arguments by the document's own disclaimer and are not used.

2. General obligation bond questions: New Mexico's GO bond acts write
   the ballot question into the statute itself ("The ballots used at
   the 2026 general election shall contain substantially the following
   language: (1) "The 2026 Capital Projects General Obligation Bond Act
   authorizes ... Shall the state be authorized ...?" ..."). The
   Secretary of State publishes no list of them before the election (its
   2026 general-election proclamation names offices only), so the source
   is the enacted act's final version on nmlegis.gov. Which bill carries
   the act changes every session (HB 248 in 2026), and nothing on the
   legislature's site names it by a stable path, so it is configured per
   year in ballot_measure_pdf_sources.json ("bond_acts"). A year with no
   configured act returns None (ingest_failed), never an amendments-only
   list: New Mexico has put GO bond questions on every even-year general
   ballot, and a list missing them would read as the whole ballot.
   Verified 2026-09-28 on HB 248's final version (signed March 10, 2026):
   three questions (senior citizen facilities, libraries, higher
   education). The amounts differ from the bill as introduced, which is
   exactly why the final version — not the introduced text — is read.

Verified live 2026-09-28: 4 amendments + 3 bond questions = 7, matching
Ballotpedia's count (whose bond amounts are still the introduced bill's).
Neither document publishes "A YES vote means / A NO vote means"
framing, so yes_means/no_means stay null; no fiscal statement either.
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

AMENDMENTS_URL_PATTERN = (
    "https://www.nmlegis.gov/Publications/New_Mexico_State_Government/"
    "Constitutional_Amendment/Constitutional_Amendments_{year}.pdf"
)
AMENDMENT_AUTHORITY = "New Mexico Legislature (ballot text); New Mexico Legislative Council Service (summary)"
BOND_AUTHORITY = "New Mexico Legislature (ballot language written into the bond act)"

_rate_limiter = RateLimiter(rps=1.0)

_FOOTER_RES = (
    re.compile(r"^CONSTITUTIONAL AMENDMENTS PROPOSED IN .*\d+$"),
    re.compile(r"^\d+ SUMMARY OF AND ARGUMENTS FOR AND AGAINST$"),
)
_AMENDMENT_ITEM_RE = re.compile(r"^Constitutional Amendment (\d+):\s*$")
_SUMMARY_HEADING_RE = re.compile(r"^▸\s*SUMMARY of Proposed Constitutional Amendment (\d+)\s*$")
_SECTION_HEADING_RE = re.compile(r"^▸\s*")


def _join(lines: list[str]) -> str:
    """Lines joined with spaces, except that a line ending in a hyphen
    continues its word on the next line ("twenty-" / "five")."""
    out = ""
    for ln in lines:
        out = out + ln if out.endswith("-") else (f"{out} {ln}" if out else ln)
    return out


def _lines(pages_text: list[str]) -> list[str]:
    out = []
    for text in pages_text:
        for ln in text.splitlines():
            ln = ln.strip()
            if ln and not any(r.match(ln) for r in _FOOTER_RES):
                out.append(ln)
    return out


def parse_amendments(pages_text: list[str]) -> list[dict]:
    """Every amendment's ballot text + LCS summary. Raises if an
    amendment named on the ballot-text page has no summary section, so
    the caller fails the state rather than publishing a partial list."""
    lines = _lines(pages_text)
    start = next((i for i, ln in enumerate(lines) if ln == "(ballot text)"), None)
    if start is None:
        raise ValueError("NM amendments: no '(ballot text)' page")
    end = next((i for i in range(start, len(lines)) if lines[i] == "General Information"), len(lines))

    ballot_text: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for ln in lines[start + 1:end] + ["Constitutional Amendment 0:"]:
        m = _AMENDMENT_ITEM_RE.match(ln)
        if m:
            if current is not None:
                text = clean_text(_join(buf)) or ""
                ballot_text[current] = text.strip('"“”')
            current, buf = m.group(1), []
            continue
        buf.append(ln)

    summaries: dict[str, str] = {}
    for i, ln in enumerate(lines):
        m = _SUMMARY_HEADING_RE.match(ln)
        if not m:
            continue
        j = next((k for k in range(i + 1, len(lines)) if _SECTION_HEADING_RE.match(lines[k])), len(lines))
        summaries[m.group(1)] = clean_text(_join(lines[i + 1:j])) or ""

    results = []
    for number, text in ballot_text.items():
        summary = summaries.get(number)
        if not text or not summary:
            raise ValueError(f"NM Constitutional Amendment {number}: missing ballot text or summary")
        results.append({
            "number": number,
            # The ballot text runs to ~700 characters (over the title
            # column's 500), so it leads the summary instead, followed by
            # the LCS summary under the document's own heading.
            "title": f"Constitutional Amendment {number}",
            "origin": "New Mexico Legislature",
            "official_summary": f"{text} SUMMARY of Proposed Constitutional Amendment {number}: {summary}",
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": AMENDMENT_AUTHORITY,
            "fiscal_authority": None,
        })
    return results


_BILL_LINE_NUMBER_RE = re.compile(r"^\d{1,2}\s+")
_BILL_RUNNING_TAG_RE = re.compile(r"\s+\S+/[HS]B \d+/\S+$")
_BILL_PAGE_RE = re.compile(r"^Page \d+$")
_BOND_QUESTION_RE = re.compile(
    r"\((\d+)\)\s*\"(The \d{4} Capital Projects General Obligation Bond Act authorizes .+?\?)\s*For_+\s*Against_+\"",
    re.DOTALL,
)


def parse_bond_act(pages_text: list[str], bill_label: str) -> list[dict]:
    """Every ballot question the GO bond act writes into its ELECTION
    section. The legislature's bill PDFs carry a line number at the
    start of every line and a running committee tag ("HTRC/HB 248/ec")
    at the end of each page's last line; both are layout, not text."""
    cleaned = []
    for text in pages_text:
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or _BILL_PAGE_RE.match(ln):
                continue
            ln = _BILL_RUNNING_TAG_RE.sub("", _BILL_LINE_NUMBER_RE.sub("", ln))
            cleaned.append(ln)
    joined = _join(cleaned)
    marker = joined.find("shall contain substantially the following language")
    if marker < 0:
        raise ValueError(f"NM {bill_label}: no ballot-language section")
    results = []
    for n, question in _BOND_QUESTION_RE.findall(joined[marker:]):
        question = clean_text(question)
        title = question.split(". Shall ", 1)[0] + "."
        results.append({
            "number": f"{bill_label} ({n})",
            "title": title,
            "origin": "New Mexico Legislature",
            "official_summary": question,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": BOND_AUTHORITY,
            "fiscal_authority": None,
        })
    if not results:
        raise ValueError(f"NM {bill_label}: ballot-language section has no questions")
    return results


async def _pdf_pages_text(client: httpx.AsyncClient, url: str, label: str) -> list[str] | None:
    body = await fetch_bytes_with_retry(client, _rate_limiter, url, label)
    if body is None or body[:5] != b"%PDF-":
        return None
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    source = source_for_state("NM") or {}
    bond_act = (source.get("bond_acts") or {}).get(str(year))
    if not bond_act:
        logger.warning("NM %d: no GO bond act configured — refusing an amendments-only list", year)
        return None

    amendments_url = AMENDMENTS_URL_PATTERN.format(year=year)
    try:
        amend_pages = await _pdf_pages_text(client, amendments_url, f"NM amendments {year}")
        bond_pages = await _pdf_pages_text(client, bond_act["url"], f"NM bond act {year}")
        if amend_pages is None or bond_pages is None:
            return None
        amendments = parse_amendments(amend_pages)
        bonds = parse_bond_act(bond_pages, bond_act["bill"])
    except Exception:
        logger.exception("NM %d ballot measures failed to parse", year)
        return None
    return [(a, amendments_url) for a in amendments] + [(b, bond_act["url"]) for b in bonds]
