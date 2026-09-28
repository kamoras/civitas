"""South Dakota's ballot-measure strategy — the Secretary of State's
"<year> Ballot Questions" page plus the Ballot Question Pro/Con
Pamphlet it links (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py: two documents are read together and
cross-checked, which the single-PDF STRATEGIES shape can't express).

The page (sdsos.gov/.../<year> Election Information/<year>-ballot-
questions.aspx) separates what is ON the ballot from what is merely
circulating: under the heading "<year> General Election Ballot Measures"
each certified question is listed with its sponsor and the letter or
number the Secretary of State assigned it; petitions still gathering
signatures sit under separate "Approved for Circulation" / "Potential"
headings, which this module never reads. Verified live 2026-09-28:
four certified questions, Constitutional Amendments I, J, K and L, each
"Proposed and passed by the 2025 South Dakota Legislature".

The pamphlet (SDCL 12-13-23) prints, per question, the Attorney
General's title, the Attorney General's explanation, and the Attorney
General's "recitation of the effect of a 'Yes' or 'No' vote" — the
pamphlet's own first page says all three "were provided by the
Attorney General". Verified on the real 2026 pamphlet (6 pages, one
page per amendment):

    Constitutional Amendment I
    Title: An Amendment to the South Dakota Constitution that Repeals ...
    Attorney General Explanation: Medicaid is a program, ...
    Vote “Yes” to adopt the amendment.
    Vote “No” to leave the Constitution as it is.
    The text of this constitutional amendment is two pages long ...
    Pro – Constitutional Amendment I Con – Constitutional Amendment I
    ... (proponent/opponent statements, two columns — never read)

Title, explanation (-> official_summary) and the two "Vote ..." lines
(-> yes_means / no_means) are stored verbatim. A "Fiscal Note:" line,
which the statute requires "if applicable" (none of 2026's four has
one), becomes fiscal_impact when present.

The set of questions read from the pamphlet must equal the set the page
certifies; any difference (a question added after the pamphlet went to
print, or a pamphlet page this reader can't parse) refuses the whole
read (None) rather than publishing a short or padded list. A certified
section that is present and lists nothing reads as [].
"""

import io
import logging
import re
from urllib.parse import quote, urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import join_lines
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

PAGE_URL_PATTERN = (
    "https://sdsos.gov/elections-voting/upcoming-elections/general-information/"
    "{year} Election Information/{year}-ballot-questions.aspx"
)
TITLE_AUTHORITY = "South Dakota Attorney General"

_rate_limiter = RateLimiter(rps=1.0)

_ASSIGNED_RE = re.compile(r"(?:Letter|Number)\s+Assigned:\s*([A-Z0-9]+)\b")
_HEADING_RE = re.compile(r"^(Constitutional Amendment|Initiated Measure|Initiated Amendment|Referred Law)\s+([A-Z0-9]+)$")
_TITLE_RE = re.compile(r"^Title:\s*(.*)$")
_EXPLANATION_RE = re.compile(r"^Attorney General Explanation:\s*(.*)$")
_YES_RE = re.compile(r"^Vote “Yes” .*$")
_NO_RE = re.compile(r"^Vote “No” .*$")
_FISCAL_RE = re.compile(r"^Fiscal Note:\s*(.*)$")
_END_RE = re.compile(r"^(The text of this .* is |Pro – )")


def page_url(year: int) -> str:
    return quote(PAGE_URL_PATTERN.format(year=year), safe=":/")


def _section_after(tree, heading_text: str) -> list:
    heading = next(
        (h for h in tree.xpath("//h2") if " ".join(h.text_content().split()) == heading_text),
        None,
    )
    if heading is None:
        return None
    elements = []
    el = heading.getnext()
    while el is not None and el.tag != "h2":
        elements.append(el)
        el = el.getnext()
    return elements


def certified_questions(page_html: str, year: int) -> dict[str, str | None] | None:
    """{assigned letter/number: origin} for every question under the
    "<year> General Election Ballot Measures" heading, or None when the
    heading is absent."""
    tree = lxml_html.fromstring(page_html)
    section = _section_after(tree, f"{year} General Election Ballot Measures")
    if section is None:
        return None
    questions: dict[str, str | None] = {}
    for el in section:
        for answer in el.xpath(".//div[contains(@class, 'faq_answer')][not(div)]") or ([el] if el.tag == "p" else []):
            text = " ".join(answer.text_content().split())
            m = _ASSIGNED_RE.search(text)
            if not m:
                continue
            origin = None
            if re.search(r"Proposed and passed by the \d{4} South Dakota Legislature", text):
                origin = "South Dakota Legislature"
            questions[m.group(1)] = origin
    return questions


def pamphlet_url(page_html: str, year: int) -> str | None:
    tree = lxml_html.fromstring(page_html)
    section = _section_after(tree, f"{year} Ballot Question Information") or []
    hrefs = {
        a.get("href")
        for el in section for a in el.xpath(".//a[@href]")
        if "pamphlet" in a.text_content().lower()
        and "audio" not in a.text_content().lower()
        and a.get("href").lower().endswith(".pdf")
        and "spanish" not in a.get("href").lower()
    }
    if len(hrefs) != 1:
        return None
    return quote(urljoin(page_url(year), hrefs.pop()), safe=":/%")


def parse_pamphlet_page(text: str) -> dict | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = next((i for i, ln in enumerate(lines) if _HEADING_RE.match(ln)), None)
    if head is None:
        return None
    kind, number = _HEADING_RE.match(lines[head]).groups()
    fields: dict[str, list[str]] = {"title": [], "explanation": [], "fiscal": []}
    current = None
    yes_means = no_means = None
    for ln in lines[head + 1:]:
        if _END_RE.match(ln):
            break
        if m := _TITLE_RE.match(ln):
            current = "title"
            fields[current].append(m.group(1))
        elif m := _EXPLANATION_RE.match(ln):
            current = "explanation"
            fields[current].append(m.group(1))
        elif m := _FISCAL_RE.match(ln):
            current = "fiscal"
            fields[current].append(m.group(1))
        elif _YES_RE.match(ln):
            yes_means, current = ln, None
        elif _NO_RE.match(ln):
            no_means, current = ln, None
        elif current:
            fields[current].append(ln)
    title = join_lines(fields["title"])
    explanation = join_lines(fields["explanation"])
    if not title or not explanation or not yes_means or not no_means:
        return None
    fiscal = join_lines(fields["fiscal"])
    return {
        "number": number,
        "title": f"{kind} {number}",
        "official_title": title,
        "origin": None,
        "official_summary": explanation,
        "fiscal_impact": fiscal,
        "yes_means": yes_means,
        "no_means": no_means,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


def parse_pamphlet(page_texts: list[str]) -> dict[str, dict] | None:
    found: dict[str, dict] = {}
    for text in page_texts:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not any(_HEADING_RE.match(ln) for ln in lines):
            continue  # cover page, closing page
        parsed = parse_pamphlet_page(text)
        if parsed is None:
            return None
        found[parsed["number"]] = parsed
    return found


def combine(certified: dict[str, str | None], pamphlet: dict[str, dict]) -> list[dict] | None:
    if set(certified) != set(pamphlet):
        logger.warning(
            "SD: page certifies %s but pamphlet prints %s — refusing",
            sorted(certified), sorted(pamphlet),
        )
        return None
    results = []
    for number in sorted(pamphlet):
        measure = dict(pamphlet[number])
        measure["origin"] = certified[number]
        results.append(measure)
    return results


def _pdf_pages(raw: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = page_url(year)
    page_html = await fetch_text_with_retry(client, _rate_limiter, url, "SD ballot questions page")
    if page_html is None:
        return None
    try:
        certified = certified_questions(page_html, year)
    except Exception:
        logger.exception("SD ballot questions page was not parseable")
        return None
    if certified is None:
        return None
    if not certified:
        return []
    pdf_url = pamphlet_url(page_html, year)
    if pdf_url is None:
        logger.warning("SD: no single pro/con pamphlet link for %d", year)
        return None
    raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "SD ballot question pamphlet")
    if raw is None:
        return None
    try:
        pamphlet = parse_pamphlet(_pdf_pages(raw))
    except Exception:
        logger.exception("SD pamphlet parse failed")
        return None
    if pamphlet is None:
        return None
    combined = combine(certified, pamphlet)
    if combined is None:
        return None
    return [(m, pdf_url) for m in combined]
