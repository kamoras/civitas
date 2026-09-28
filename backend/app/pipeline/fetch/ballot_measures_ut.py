"""Utah's ballot-measure strategy — the Lieutenant Governor's "<year>
GENERAL ELECTION CERTIFICATION" (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py).

The certification is the Lieutenant Governor's signed statement, under
Utah Code §20A-5a-209(2) and §20A-7-103(4), of everything that goes on
the general-election ballot. Verified live 2026-09-28 (dated August 28,
2026, 25 pages), its first page certifies in numbered paragraphs:

    4. Pursuant to Utah Code Annotated§ 20A-5a-209(2)(c), no statewide ballot
       propositions have qualified for placement on the 2026 Utah General
       Election ballot; and,
    5. Pursuant to Utah Code Annotated§ 20A-7-103(4), the following proposed
       constitutional amendments as passed by the Utah State Legislature shall
       appear on the 2026 General Election ballot; and,

and its "PROPOSED CONSTITUTIONAL AMENDMENTS" page lists them: headings
"Constitutional Amendment A." and "Constitutional Amendment B.", each
followed by its ballot question and a "( ) For ( ) Against" line.

Both statements are required, in exactly those words: the
no-propositions sentence is the state's own word that nothing else
statewide qualified (a certification that lists propositions instead is
one this reader can't read, and refuses the state), and the amendments
sentence says the section after it is the whole list. The number of
amendment headings must equal the number of "For / Against" answer
lines in that section.

What is NOT stored: the ballot question text. The certification is a
scan (Creator "RICOH IM C4510") whose text layer is Acrobat Paper Capture
OCR — elsewhere in the same document it reads "RlLEYOWEN" and
"WRlGHT" for printed capitals. OCR'd text is not verbatim text (the
same rule as Oklahoma's scanned ballot titles, ballot_measures_ok.py), so
each amendment is stored as its letter and its heading,
"Constitutional Amendment A" (official_title None), linked to the
certification itself. The heading pattern is strict, so an OCR misread
there refuses the state rather than inventing a letter.

Discovery: vote.utah.gov/current-election-information/ ("<year> Election
Cycle") links the certification as .../<year>-General-Election-
Certification.pdf. A page for this cycle with no such link is
NotYetPublished (the Lieutenant Governor certifies in late August); a
page for another cycle, or a failed fetch, is None.

Fetching: from the development environment, vote.utah.gov answered
curl with 200 but this codebase's httpx client with a Cloudflare
"Just a moment..." challenge (403), with and without the User-Agent's
contact comment — the fixture was fetched with curl. Whether production
passes that challenge was not verifiable from here; if it doesn't, the
state reads as ingest_failed (the challenge page carries no link and no
"Election Cycle" heading), never as not-yet or none.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import get_bytes, get_text, pdf_text

logger = logging.getLogger(__name__)

CURRENT_URL = "https://vote.utah.gov/current-election-information/"
ORIGIN = "Utah State Legislature"
TITLE_AUTHORITY = None  # nothing drafted is quoted (see docstring)

_HEADING_RE = re.compile(r"^Constitutional Amendment ([A-Z])\.$")
_ANSWER_RE = re.compile(r"^\(\s*\)\s*For\s*\(\s*\)\s*Against$")
_SECTION = "PROPOSED CONSTITUTIONAL AMENDMENTS"
_SECTION_END = "REGISTERED POLITICAL PARTIES"


def find_certification_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok). page_ok False: not this cycle's page, or the
    certification is linked more than once."""
    tree = lxml_html.fromstring(page_html)
    if f"{year} Election Cycle" not in " ".join(tree.text_content().split()):
        return None, False
    pattern = re.compile(rf"/{year}-General-Election-Certification\.pdf$", re.IGNORECASE)
    hrefs = {
        urljoin(CURRENT_URL, a.get("href")) for a in tree.xpath("//a[@href]")
        if pattern.search(a.get("href").strip())
    }
    if len(hrefs) > 1:
        return None, False
    return (hrefs.pop() if hrefs else None), True


def parse_certification(text: str, year: int) -> list[dict] | None:
    """The certified amendments, or None when the certification isn't
    `year`'s, lists statewide propositions, or can't be read whole."""
    flat = clean_text(text) or ""
    if f"{year} GENERAL ELECTION CERTIFICATION" not in flat:
        logger.warning("UT certification does not name the %d general election", year)
        return None
    if f"no statewide ballot propositions have qualified for placement on the {year} Utah General Election ballot" not in flat:
        logger.warning("UT %d certification lacks its no-propositions statement — refusing", year)
        return None
    if (
        "the following proposed constitutional amendments as passed by the Utah State Legislature "
        f"shall appear on the {year} General Election ballot"
    ) not in flat:
        logger.warning("UT %d certification lacks its amendments statement — refusing", year)
        return None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    try:
        start = lines.index(_SECTION)
    except ValueError:
        logger.warning("UT %d certification has no '%s' section", year, _SECTION)
        return None
    section = []
    for line in lines[start + 1:]:
        if line == _SECTION_END:
            break
        section.append(line)
    else:
        logger.warning("UT %d amendments section has no end marker — refusing", year)
        return None

    letters = []
    answers = 0
    for line in section:
        m = _HEADING_RE.match(line)
        if m:
            letters.append(m.group(1))
        elif _ANSWER_RE.match(line):
            answers += 1
        elif line.lower().startswith("constitutional amendment") and len(line) < 40:
            # Looks like a heading but not in the verified form (an OCR
            # misread): refuse rather than drop or guess the letter.
            logger.warning("UT amendment heading %r not in the verified form — refusing", line)
            return None
    if not letters or answers != len(letters) or len(set(letters)) != len(letters):
        logger.warning("UT %d: %d amendment headings, %d answer lines — refusing", year, len(letters), answers)
        return None
    return [
        {
            "number": letter,
            "title": f"Constitutional Amendment {letter}",
            "official_title": None,
            "origin": ORIGIN,
            "official_summary": None,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        }
        for letter in letters
    ]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await get_text(client, CURRENT_URL, "UT current election information")
    if page_html is None:
        return None
    try:
        url, page_ok = find_certification_url(page_html, year)
    except Exception:
        logger.exception("UT current election page was not parseable")
        return None
    if not page_ok:
        logger.warning("UT current election page is not the %d cycle's page this reader knows", year)
        return None
    if url is None:
        raise NotYetPublished(f"the Utah Lieutenant Governor's {year} General Election Certification")
    raw = await get_bytes(client, url, "UT general election certification")
    if raw is None:
        return None
    try:
        parsed = parse_certification(pdf_text(raw), year)
    except Exception:
        logger.exception("UT general election certification was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
