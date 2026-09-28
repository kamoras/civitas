"""Alabama's ballot-measure strategy — the Fair Ballot Commission's
per-amendment "Ballot Statement" PDFs, listed on the Secretary of
State's Statewide Ballot Measures page.

Ala. Code §17-6-81 has the Fair Ballot Commission approve, for every
statewide measure, a statement carrying (1) the text of the question as
it will appear on the ballot, (2) implementing legislation, (3) its
placement on the ballot, and (4) a plain-language summary that must
state the effect of a YES and of a NO vote and the cost. The Secretary
of State links one PDF per statewide amendment under a heading naming
the election ("2026 Statewide November 3, 2026, General Election").

Read from each statement, verbatim:
- title: the quoted ballot text from section (1) — what the Legislature
  wrote into the Act and what is printed on the ballot, so
  title_authority is the Alabama Legislature.
- official_summary: section (4)'s summary paragraphs.
- yes_means / no_means: section (4)'s own "If the majority of voters
  vote “yes” on Amendment N, ..." sentences — the state's framing,
  lifted whole, never derived.
- fiscal_impact: the sentence(s) between the "no" sentence and the
  "Constitutional authority" sentence ("There are no costs or taxes
  associated with Amendment 1." on every 2026 statement), attributed to
  the Fair Ballot Commission.

Scope is the page's own: it lists STATEWIDE amendments only; Alabama's
many local amendments appear on county ballots and are not on it.

A statement is refused (the whole fetch fails, None) if it doesn't name
this election's date and "GENERAL ELECTION", or its own amendment
number disagrees with the link that led to it. The page heading must
name this election too: no heading means the page hasn't been set up
for this election, which is not the same as "no amendments" (None). A
heading with no statements under it is a real, checked answer ([]).

Verified live 2026-09-28 against the November 3, 2026 general election:
4 statements (Amendments 1-4), all four parse.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    names_date,
    pdf_text,
)

logger = logging.getLogger(__name__)

LANDING_URL = "https://www.sos.alabama.gov/alabama-votes/voter/ballot-measures/statewide"
TITLE_AUTHORITY = "Alabama Legislature"
FISCAL_AUTHORITY = "Alabama Fair Ballot Commission"
ORIGIN = "Alabama Legislature"

_LINK_TEXT_RE = re.compile(r"Statewide\s+Amendment\s+(\d+)\s*$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"BALLOT STATEMENT FOR STATEWIDE AMENDMENT\s+(\d+)\s*:", re.IGNORECASE)
_QUESTION_RE = re.compile(r"“(.*)”\s*This description shall be followed", re.DOTALL)
_SUMMARY_START_RE = re.compile(r"if it is\s+defeated\.", re.IGNORECASE)
_YES_RE = re.compile(r"If the majority of voters vote\s+“yes”.*?\.(?=\s)", re.DOTALL)
_NO_RE = re.compile(r"If the majority of voters vote\s+“no”.*?\.(?=\s)", re.DOTALL)
_AUTHORITY_RE = re.compile(r"The Constitutional authority for passage", re.IGNORECASE)


def statement_links(page_html: str, base_url: str, year: int) -> dict[str, str] | None:
    """{amendment number: statement PDF url} under the heading that names
    `year`'s general election, or None if no such heading exists or a
    link there names an amendment in a shape this reader doesn't know."""
    tree = lxml_html.fromstring(page_html)
    wanted = long_date(election_day(year))
    heading = next(
        (
            h for h in tree.xpath("//h2")
            if wanted in (clean_text(h.text_content()) or "")
            and "general election" in h.text_content().lower()
        ),
        None,
    )
    if heading is None:
        return None
    links: dict[str, str] = {}
    el = heading.getnext()
    while el is not None and el.tag != "h2":
        for a in el.iter("a"):
            m = _LINK_TEXT_RE.search(clean_text(a.text_content()) or "")
            href = a.get("href") or ""
            if m and href.lower().endswith(".pdf"):
                links.setdefault(m.group(1), urljoin(base_url, href))
            elif "amendment" in (a.text_content() or "").lower():
                # A measure link in a shape this reader doesn't know. An
                # empty result here would read as "no measures" — refuse.
                return None
        el = el.getnext()
    return links


def parse_statement(text: str, number: str, year: int) -> dict | None:
    """One Fair Ballot Commission statement (extract_text() output)."""
    if not names_date(text, election_day(year)) or "GENERAL ELECTION" not in text:
        return None
    printed = _NUMBER_RE.search(text)
    if printed is None or printed.group(1) != number:
        return None

    question = _QUESTION_RE.search(text)
    start = _SUMMARY_START_RE.search(text)
    yes = _YES_RE.search(text)
    no = _NO_RE.search(text)
    authority = _AUTHORITY_RE.search(text)
    if not (question and start and yes and no and authority):
        return None
    if not (start.end() <= yes.start() < no.start() < authority.start()):
        return None

    summary = clean_text(text[start.end():yes.start()])
    fiscal = clean_text(text[no.end():authority.start()])
    title = clean_text(question.group(1))
    if not summary or not title:
        return None
    return {
        "number": number,
        "title": title,
        "origin": ORIGIN,
        "official_summary": summary,
        "fiscal_impact": fiscal,
        "yes_means": clean_text(yes.group(0)),
        "no_means": clean_text(no.group(0)),
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": FISCAL_AUTHORITY if fiscal else None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page = await get_text(client, LANDING_URL, "AL statewide ballot measures")
    if page is None:
        return None
    try:
        links = statement_links(page, LANDING_URL, year)
    except Exception:
        logger.exception("AL statewide ballot measures page was not parseable HTML")
        return None
    if links is None:
        logger.warning("AL ballot measures page has no heading for the %d general election", year)
        return None

    results = []
    for number in sorted(links, key=int):
        raw = await get_bytes(client, links[number], f"AL Amendment {number} statement")
        if raw is None:
            return None
        try:
            parsed = parse_statement(pdf_text(raw), number, year)
        except Exception:
            logger.exception("AL Amendment %s statement parse failed", number)
            return None
        if parsed is None:
            logger.warning("AL Amendment %s statement didn't match the verified shape", number)
            return None
        results.append((parsed, links[number]))
    return results
