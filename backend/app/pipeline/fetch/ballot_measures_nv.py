"""Nevada's ballot-measure strategy — the Secretary of State's
"Statewide Ballot Questions" booklet (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py).

The booklet is the Secretary's guide to every statewide question on the
general-election ballot (NRS 293.253). Verified against the real 2026
booklet (30 pages, "To Appear on the November 3, 2026, General Election
Ballot", PDF author "Nevada Secretary of State", created 2026-08-25):
Questions 6 and 7 — the two 2024 constitutional initiatives (abortion
right, voter ID) that must pass a second time — and its page "State
Question: No. 1 – No. 5" stating "There will be no statewide questions
1–5 on the 2026 General Election ballot."

Per question, from its "State Question - No. N" section, verbatim:
- the "Condensation (Ballot Question)" — the question as printed on the
  ballot — as official_summary (the booklet's own label for it is not a
  title, so official_title stays None); title_authority the Secretary of
  State, who issues the booklet;
- the Explanation's own sentences 'A “Yes” vote would ...' and
  'A “No” vote would ...', whole, as yes_means / no_means;
- the "Fiscal Note" through its "Prepared by the Fiscal Analysis Division
  of the Legislative Counsel Bureau – <date>" line, as fiscal_impact, and
  that drafter as fiscal_authority;
- the lines under the section heading ("Amendment to the Nevada
  Constitution", "Initiative Petition C-05-2023") as title and origin.
The booklet's running header ("2026 Statewide Ballot Questions"), page
footer ("Nevada Secretary of State Page N of 30") and "[REMAINDER OF
THIS PAGE INTENTIONALLY LEFT BLANK]" are dropped before reading, so a
paragraph broken by a page reads whole.

Checks, each refusing the state (None): the cover must name `year`'s
general-election date; the CONTENTS page's "State Question - No. N"
entries must equal the question sections found; every section must yield
a condensation, both vote sentences and a fiscal note with its drafter.
The county and city questions the booklet summarises at the end are
never read (the reading stops at its "County & City Ballot" page).

Discovery: www.nvsos.gov/elections/{year}-petitions ("{year} Petitions &
General Election Ballot Questions"), the one PDF link whose text or
address names `year` and "ballot question". NOT VERIFIED FROM THE
DEVELOPMENT ENVIRONMENT: nvsos.gov (and the Secretary's
silverstateelection.nv.gov) answered every request from there with an
Imperva JavaScript challenge, and web.archive.org was refused by that
environment's egress policy, so neither the page's markup nor the
booklet's address on it could be seen. The booklet itself is real — the
copy the fixture was made from is the Secretary's document as republished
by Eureka County's clerk (see the fixture's _source). A challenge page,
a page that isn't the year's petitions page, or more than one matching
link is a failure (None) — never "not yet" and never "none"; a real
page with no booklet link is NotYetPublished.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished, join_lines
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    pdf_pages,
)

logger = logging.getLogger(__name__)

PETITIONS_URL = "https://www.nvsos.gov/elections/{year}-petitions"
TITLE_AUTHORITY = "Nevada Secretary of State"

_SECTION_RE = re.compile(r"^State Question - No\. (\d+)$")
_CONTENTS_RE = re.compile(r"^State Question - No\. (\d+)\s*\.{3,}\s*\d+$")
_FOOTER_RE = re.compile(r"^Nevada Secretary of State Page \d+ of \d+$")
_PREPARED_RE = re.compile(r"^Prepared by the (Fiscal Analysis Division of the Legislative Counsel Bureau) – .+$")
_YES_START = "A “Yes” vote"
_NO_START = "A “No” vote"


def find_booklet_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok). page_ok False: not the year's petitions page (a
    bot challenge has no such title), or more than one candidate link."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if f"{year} Petitions" not in title:
        return None, False
    base = PETITIONS_URL.format(year=year)
    hrefs = set()
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        haystack = f"{href} {' '.join(a.text_content().split())}".lower().replace("-", " ").replace("_", " ")
        if str(year) in haystack and "ballot question" in haystack and ".pdf" in href.lower():
            hrefs.add(urljoin(base, href))
    if len(hrefs) > 1:
        return None, False
    return (hrefs.pop() if hrefs else None), True


def _body_lines(pages: list[str], year: int) -> list[str]:
    running = f"{year} Statewide Ballot Questions"
    out = []
    for page in pages:
        for line in page.splitlines():
            line = line.strip()
            if not line or line == running or _FOOTER_RE.match(line):
                continue
            if line == "[REMAINDER OF THIS PAGE INTENTIONALLY LEFT BLANK]":
                continue
            out.append(line)
    return out


def _between(lines: list[str], start: int, stop) -> tuple[list[str], int]:
    out = []
    i = start
    while i < len(lines) and not stop(lines[i]):
        out.append(lines[i])
        i += 1
    return out, i


def _read_section(number: str, lines: list[str]) -> dict | None:
    try:
        kind, petition = lines[0], lines[1]
        cond_at = lines.index("Condensation (Ballot Question)")
    except (IndexError, ValueError):
        logger.warning("NV question %s: no condensation", number)
        return None
    condensation, _ = _between(lines, cond_at + 1, lambda ln: ln == "Yes")
    yes_at = [i for i, ln in enumerate(lines) if ln.startswith(_YES_START)]
    no_at = [i for i, ln in enumerate(lines) if ln.startswith(_NO_START)]
    if len(yes_at) != 1 or len(no_at) != 1 or not no_at[0] > yes_at[0]:
        logger.warning("NV question %s: %d yes / %d no vote sentences", number, len(yes_at), len(no_at))
        return None
    yes_lines, _ = _between(lines, yes_at[0], lambda ln: ln.startswith(_NO_START))
    no_lines, _ = _between(lines, no_at[0], lambda ln: ln in ("Digest:", f"QUESTION {number}"))
    fiscal_at = [
        i for i in range(1, len(lines)) if lines[i] == "Fiscal Note" and lines[i - 1] == f"QUESTION {number}"
    ]
    if len(fiscal_at) != 1:
        logger.warning("NV question %s: %d fiscal notes", number, len(fiscal_at))
        return None
    fiscal_lines, end = _between(lines, fiscal_at[0] + 1, lambda ln: bool(_PREPARED_RE.match(ln)))
    if end >= len(lines):
        logger.warning("NV question %s: fiscal note has no 'Prepared by' line", number)
        return None
    summary = join_lines(condensation)
    if not summary or not summary.endswith("?"):
        logger.warning("NV question %s: condensation is not a question", number)
        return None
    return {
        "number": number,
        "title": kind,
        "official_title": None,
        "origin": petition,
        "official_summary": summary,
        "fiscal_impact": join_lines(fiscal_lines + [lines[end]]),
        "yes_means": join_lines(yes_lines),
        "no_means": join_lines(no_lines),
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": _PREPARED_RE.match(lines[end]).group(1),
    }


def parse_booklet(pages: list[str], year: int) -> list[dict] | None:
    """Every statewide question in the booklet, or None when it isn't
    `year`'s general-election booklet or can't be read whole."""
    cover = " ".join((pages[0] if pages else "").split())
    if f"To Appear on the {long_date(election_day(year))}, General Election Ballot" not in cover:
        logger.warning("NV booklet cover does not name the %d general election", year)
        return None
    lines = _body_lines(pages, year)
    contents = [m.group(1) for ln in lines if (m := _CONTENTS_RE.match(ln))]
    starts = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _SECTION_RE.match(ln))]
    if not starts:
        logger.warning("NV booklet has no 'State Question - No. N' section")
        return None
    end_of_state = next(
        (i for i, ln in enumerate(lines) if i > starts[-1][0] and ln.startswith(f"{year} County & City Ballot")),
        len(lines),
    )
    if [n for _, n in starts] != contents:
        logger.warning("NV booklet: contents list %s, sections %s — refusing", contents, [n for _, n in starts])
        return None
    results = []
    for k, (i, number) in enumerate(starts):
        stop = starts[k + 1][0] if k + 1 < len(starts) else end_of_state
        parsed = _read_section(number, lines[i + 1:stop])
        if parsed is None:
            return None
        results.append(parsed)
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_url = PETITIONS_URL.format(year=year)
    page_html = await get_text(client, page_url, f"NV {year} petitions")
    if page_html is None:
        return None
    try:
        url, page_ok = find_booklet_url(page_html, year)
    except Exception:
        logger.exception("NV petitions page was not parseable")
        return None
    if not page_ok:
        logger.warning("NV %d petitions page is not the page this reader knows (bot challenge?)", year)
        return None
    if url is None:
        raise NotYetPublished(f"the Nevada Secretary of State's {year} Statewide Ballot Questions booklet")
    raw = await get_bytes(client, url, "NV statewide ballot questions booklet")
    if raw is None:
        return None
    try:
        parsed = parse_booklet(pdf_pages(raw), year)
    except Exception:
        logger.exception("NV ballot questions booklet was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
