"""Minnesota's ballot-measure strategy — the Secretary of State's own
"Constitutional amendments" page under What's On My Ballot, read as
HTML (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py: the
page itself is the document, there is no guide PDF).

Minnesota has no citizen initiative; the only statewide questions are
legislature-referred constitutional amendments, and this page is where
the Secretary of State publishes each cycle's ballot text. Verified live
2026-09-28 against the November 3, 2026 ballot. The page states its own
count in a sentence —

    "In 2026, there will be one proposed constitutional amendment on the
     ballot in Minnesota."

— then prints, under "Ballot language and instructions", each question
exactly as the ballot does: a bold title set by the authorizing law
("Increasing funding to school districts"), the "Shall the Minnesota
Constitution be amended ...?" question, then "Yes"/"No" (one paragraph
when verified; since 2026-10 one paragraph each, per language — both
are read, see parse_page). That title and
question are what this module stores, verbatim (title -> title,
question -> official_summary). The page publishes no yes/no framing and
no fiscal statement, so those stay None.

The stated count is a cross-check, not decoration: the number of
questions read must equal it, or the read is refused (None). The page
sits behind a bot manager that intermittently answers with a challenge
page instead (seen live during research); that page has no count
sentence, so it reads as None — a failed fetch — never as "no
amendments". Only an explicit "there will be no proposed constitutional
amendment(s)" sentence for `year` produces [].

Numbering: the page prints none. The number is the question's position
in the Secretary of State's own list, which is the order the ballot
prints them in.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.sos.mn.gov/elections-voting/whats-on-my-ballot/constitutional-amendments/"
ORIGIN = "Minnesota Legislature"

_rate_limiter = RateLimiter(rps=1.0)

_WORD_COUNTS = {"no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_COUNT_RE = re.compile(
    r"In (\d{4}), there will be (\w+) proposed constitutional amendments? on the ballot in Minnesota",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"Shall the Minnesota Constitution be amended .*\?")


def _stated_count(text: str, year: int) -> int | None:
    for m in _COUNT_RE.finditer(text):
        if m.group(1) == str(year):
            word = m.group(2).lower()
            return int(word) if word.isdigit() else _WORD_COUNTS.get(word)
    return None


def parse_page(page_html: str, year: int) -> list[dict] | None:
    tree = lxml_html.fromstring(page_html)
    stated = _stated_count(" ".join(tree.text_content().split()), year)
    if stated is None:
        return None
    if stated == 0:
        return []

    # The page's paragraphs as lines, in document order. The ballot language
    # has been laid out two ways: one paragraph, <strong><span>title</span>
    # <br><br>question<br><br>Yes</strong><br><br><strong>No</strong>
    # (2026-09-28), and one paragraph each for title, question, "Yes" and
    # "No", per language in an accordion (2026-10-18). Read as lines, both
    # are the same sequence: title, question, Yes, No. Only the English
    # question begins "Shall the Minnesota Constitution be amended".
    for br in tree.iter("br"):
        br.tail = "\n" + (br.tail or "")
    lines = [
        line for p in tree.iter("p")
        for line in (clean_text(part) for part in p.text_content().split("\n")) if line
    ]
    questions = []
    for i, line in enumerate(lines):
        if not (_QUESTION_RE.fullmatch(line) and 0 < i and lines[i + 1:i + 3] == ["Yes", "No"]):
            continue
        questions.append((lines[i - 1], line))

    if len(questions) != stated:
        logger.warning(
            "MN page states %d amendment(s) for %d but %d question(s) were read — refusing",
            stated, year, len(questions),
        )
        return None
    return [
        {
            "number": str(i),
            "title": title,
            "origin": ORIGIN,
            "official_summary": question,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": ORIGIN,
            "fiscal_authority": None,
        }
        for i, (title, question) in enumerate(questions, start=1)
    ]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await fetch_text_with_retry(client, _rate_limiter, URL, "MN constitutional amendments")
    if page_html is None:
        return None
    try:
        parsed = parse_page(page_html, year)
    except Exception:
        logger.exception("MN constitutional amendments page was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, URL) for m in parsed]
