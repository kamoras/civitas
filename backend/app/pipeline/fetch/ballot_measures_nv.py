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
General Election Ballot Questions"): every PDF link whose text or
address names `year` and "ballot question" is read, and the one whose
cover is the English statewide booklet's (is_statewide_booklet) is used,
so a Spanish edition or county summary beside it changes nothing.
nvsos.gov (and the Secretary's silverstateelection.nv.gov) answers every
request with an Imperva JavaScript challenge — from the production host
too, checked 2026-10-01 — so the petitions page has never been seen. A
challenge page, or a page that can't be fetched, is SourceBlocked: the
state's site can't be read at all, and ballot_measures_pdf then reads the
Secretary's booklet from a county clerk's republication of it
(fetch_republished; Eureka County's, in the registry's `republished_by`),
with every check below applied to that copy. The fixture is that copy.
A document that can't be fetched, two documents with the booklet's
cover, or a document that looks like the statewide booklet (names the
year, "Statewide" and "Question", or has no text at all) but lacks its
verified cover, is a failure (None) — never "not yet" and never "none";
a real page none of whose documents even looks like the booklet is
NotYetPublished, with no deadline (a general with no statewide question
may have no booklet). A county page without the booklet is a failure,
never NotYetPublished: it says nothing about the state's ballot.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished, SourceBlocked, join_lines
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    pdf_pages,
    republished_candidates,
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


def booklet_urls(page_html: str, year: int) -> list[str] | None:
    """Every PDF on the year's petitions page whose text or address names
    `year` and "ballot question", or None when this isn't that page (a
    bot challenge has no such title). Which one is the Secretary's
    statewide booklet is decided by reading each (is_statewide_booklet):
    a Spanish edition or a county/city questions summary can sit beside
    it and match the same words."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if f"{year} Petitions" not in title:
        return None
    base = PETITIONS_URL.format(year=year)
    urls: list[str] = []
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        haystack = f"{href} {' '.join(a.text_content().split())}".lower().replace("-", " ").replace("_", " ")
        if str(year) in haystack and "ballot question" in haystack and ".pdf" in href.lower():
            url = urljoin(base, href)
            if url not in urls:
                urls.append(url)
    return urls


def looks_like_statewide_booklet(pages: list[str], year: int) -> bool:
    """A loose reading of a candidate document: its first page names
    `year`, "Statewide" and "Question", or it has no text at all (a scan,
    or a lost text layer) — either could be the statewide booklet, so a
    candidate like this that fails is_statewide_booklet refuses the state
    rather than being passed over. A Spanish edition ("Preguntas ...
    Estatales") or a county/city summary names none of those. It errs
    closed: another same-year statewide-question document on the page
    (arguments and rebuttals published separately, say) would also match
    and refuse the state until the reader is taught it — the page itself
    could not be seen from the development environment."""
    first = " ".join((pages[0] if pages else "").split())
    if not "".join(pages).strip():
        return True
    return str(year) in first and "statewide" in first.lower() and "question" in first.lower()


def is_statewide_booklet(pages: list[str], year: int) -> bool:
    """The English statewide booklet's cover, as the Secretary prints it
    (2026: "S T A T E O F N E V A D A / Statewide Ballot Questions / 2026 /
    To Appear on the November 3, 2026, General Election Ballot / I S S U E D
    B Y ... SECRETARY OF STATE"). A Spanish edition's cover is in Spanish
    and a county/city summary has no such cover, so neither matches."""
    cover = " ".join((pages[0] if pages else "").split())
    return (
        "Statewide Ballot Questions" in cover
        and f"To Appear on the {long_date(election_day(year))}, General Election Ballot" in cover
        and "SECRETARY OF STATE" in cover
    )


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


async def _choose_booklet(
    client: httpx.AsyncClient, urls: list[str], year: int,
) -> tuple[str, list[str]] | None:
    """The one candidate whose cover is the English statewide booklet's,
    read from each candidate itself. None (a failure) when a candidate
    can't be fetched or read, when one looks like the booklet without its
    verified cover, or when two carry it. NotYetPublished when none does."""
    booklets = []
    for url in urls:
        raw = await get_bytes(client, url, "NV ballot questions document")
        if raw is None:
            return None
        try:
            pages = pdf_pages(raw)
        except Exception:
            logger.exception("NV ballot questions document %s was not parseable", url)
            return None
        if is_statewide_booklet(pages, year):
            booklets.append((url, pages))
        elif looks_like_statewide_booklet(pages, year):
            # Its cover names the year's statewide questions but not in
            # the verified form (a reworded cover, or no text layer at
            # all): the booklet is there and can't be read — a failure,
            # never "not yet".
            logger.warning("NV %s looks like the %d statewide booklet but its cover isn't the verified one — refusing", url, year)
            return None
    if not booklets:
        # Published for a general that has a statewide question; a year
        # with none may have no booklet at all, so no deadline.
        raise NotYetPublished(
            f"the Nevada Secretary of State's {year} Statewide Ballot Questions booklet", deadline_applies=False,
        )
    if len(booklets) > 1:
        logger.warning("NV: %d documents carry the %d statewide booklet's cover — refusing", len(booklets), year)
        return None
    return booklets[0]


def _read(url: str, pages: list[str], year: int) -> list[tuple[dict, str]] | None:
    try:
        parsed = parse_booklet(pages, year)
    except Exception:
        logger.exception("NV ballot questions booklet was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_url = PETITIONS_URL.format(year=year)
    page_html = await get_text(client, page_url, f"NV {year} petitions")
    if page_html is None:
        raise SourceBlocked(f"the Nevada Secretary of State's {year} petitions page could not be fetched")
    try:
        urls = booklet_urls(page_html, year)
    except Exception:
        logger.exception("NV petitions page was not parseable")
        return None
    if urls is None:
        raise SourceBlocked(
            f"the Nevada Secretary of State's {year} petitions page answered with something other than that "
            "page (a bot challenge)"
        )
    chosen = await _choose_booklet(client, urls, year)
    return _read(*chosen, year) if chosen else None


async def fetch_republished(client: httpx.AsyncClient, year: int, page_url: str) -> list[tuple[dict, str]] | None:
    """The Secretary's booklet as a county election office republishes it:
    the same checks as the Secretary's own copy (is_statewide_booklet, the
    CONTENTS list, every section complete), on whichever of the county
    page's same-year "ballot question" documents carries the verified
    cover. None when the page can't be read or no copy verifies — a county
    that hasn't posted the booklet is no word on the state's ballot."""
    urls = await republished_candidates(client, page_url, year, ("ballot question",))
    if not urls:
        return None
    try:
        chosen = await _choose_booklet(client, urls, year)
    except NotYetPublished:
        logger.warning("NV: no document on %s carries the %d statewide booklet's cover", page_url, year)
        return None
    return _read(*chosen, year) if chosen else None
