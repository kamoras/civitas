"""Maryland's ballot-measure strategy — the State Board of Elections'
"Ballot Questions for the <date> Election" page
(elections.maryland.gov/elections/{year}/ballot_questions.html).

The page is the Board's posting under Election Law §7-103: every
question on the general-election ballot, grouped in <details> blocks —
one summarised "SBE" for the statewide questions, then one per county
(Anne Arundel's charter amendments, Baltimore City's bond questions,
...). Only the SBE block is read; county questions are local, not
statewide, and are skipped by structure rather than by wording.

Each statewide question is an <h2>"Question N"</h2> followed by a bold
type line ("Constitutional Amendment (Ch. 155 of the 2026 Legislative
Session)"), a bold title, the question text, and a list of the two
choices. Where the Secretary of State's language explains the choice
("For the Constitutional Amendment - A vote FOR this amendment means
..."), the explanation after the dash is stored verbatim as yes_means /
no_means. Where it doesn't — 2026's Question 3, whose language the
Supreme Court of Maryland ordered replaced on September 3, 2026, lists
only "For the Constitutional Amendment" / "Against the Constitutional
Amendment" — they stay null. The court-order preamble the page prints
above that question's language is kept in official_summary as printed:
it is the page's own statement of where the words came from.

The Spanish translation of each question (a <div lang="es"> sibling) is
skipped; the English text is the ballot of record.

title_authority is the Secretary of State, who prepares Maryland's
ballot language (Election Law §7-103(c)); the page says so above the
questions ("The Secretary of State provided the following updated
questions") — except where the page says the language was replaced by
court order (Question 3), where the attribution says that too. No
fiscal statement is published here — null. A statewide <h2> that isn't
"Question N" refuses the page rather than being skipped.

The page is refused (None) unless its <h1> names this election's date:
a page for another year, or the site's soft-404 page (returned with a
200 — verified for /elections/2024/ballot_questions.html), is not this
ballot. An SBE block with no questions is a checked answer ([]).

Verified live 2026-09-28: Questions 1-3 on the November 3, 2026 ballot.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_text, names_date

logger = logging.getLogger(__name__)

URL_PATTERN = "https://elections.maryland.gov/elections/{year}/ballot_questions.html"
TITLE_AUTHORITY = "Maryland Secretary of State"
# Where the page itself says the question's language was replaced by
# court order, naming only the Secretary of State would misattribute it.
COURT_ORDERED_AUTHORITY = (
    "Maryland Secretary of State; question language as ordered by the Supreme Court of Maryland"
)
_COURT_ORDER_RE = re.compile(r"^Pursuant to the Supreme Court Order\b", re.IGNORECASE)

_QUESTION_RE = re.compile(r"^Question\s+(\d+)$", re.IGNORECASE)
_CHOICE_RE = re.compile(r"^(For|Against)\b.*?\s-\s?(A vote\b.*)$", re.IGNORECASE | re.DOTALL)


def _text(el) -> str:
    return clean_text(el.text_content()) or ""


def _origin(type_line: str) -> str | None:
    """Read from the question's own type line, never assumed: a chapter
    of a Legislative Session is a General Assembly referral; a petitioned
    referendum on a law would say so there instead."""
    return "Maryland General Assembly" if "legislative session" in type_line.lower() else None


def parse_page(page_html: str, year: int) -> list[dict] | None:
    tree = lxml_html.fromstring(page_html)
    h1 = tree.xpath("//h1")
    if not h1 or not names_date(h1[0].text_content(), election_day(year)):
        return None
    sbe = next(
        (d for d in tree.xpath("//details") if _text(next(iter(d.xpath("./summary")), d)) == "SBE"),
        None,
    )
    if sbe is None:
        return None

    results = []
    children = [c for c in sbe if c.tag != "summary"]
    for i, el in enumerate(children):
        if el.tag != "h2":
            continue
        m = _QUESTION_RE.match(_text(el))
        if not m:
            # A statewide heading in a shape this reader doesn't know —
            # skipping it could turn a real question into "none".
            return None
        block = []
        for sib in children[i + 1:]:
            if sib.tag == "h2" or sib.get("lang"):
                break
            block.append(sib)
        paragraphs = [p for p in block if p.tag == "p"]
        choices = next((u for u in block if u.tag == "ul"), None)
        if len(paragraphs) < 3 or choices is None:
            return None
        type_line, title = _text(paragraphs[0]), _text(paragraphs[1])
        summary = clean_text(" ".join(_text(p) for p in paragraphs[2:]))

        yes_means = no_means = None
        for li in choices.xpath("./li"):
            c = _CHOICE_RE.match(_text(li))
            if c and c.group(1).lower() == "for":
                yes_means = clean_text(c.group(2))
            elif c:
                no_means = clean_text(c.group(2))

        if not title or not summary:
            return None
        results.append({
            "number": m.group(1),
            "title": title,
            "origin": _origin(type_line),
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": yes_means,
            "no_means": no_means,
            "title_authority": (
                COURT_ORDERED_AUTHORITY if _COURT_ORDER_RE.match(summary) else TITLE_AUTHORITY
            ),
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = URL_PATTERN.format(year=year)
    page = await get_text(client, url, f"MD ballot questions {year}")
    if page is None:
        return None
    try:
        parsed = parse_page(page, year)
    except Exception:
        logger.exception("MD ballot questions page for %d was not parseable HTML", year)
        return None
    if parsed is None:
        logger.warning("MD ballot questions page for %d is not this election's, or is malformed", year)
        return None
    return [(p, url) for p in parsed]
