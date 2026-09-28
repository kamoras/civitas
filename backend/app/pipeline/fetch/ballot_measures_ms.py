"""Mississippi's ballot-measure strategy — proves absence only, from the
Secretary of State's statewide sample ballot for the November general
election (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

The Secretary posts one composite "SAMPLE Official Election Ballot" for
the whole state on sos.ms.gov/elections-voting (2026: "Sample Ballot
9-9-26.pdf" under /content/documents/Elections/2026/). Verified live
2026-09-28: "STATE OF MISSISSIPPI / Federal and Judicial Election /
Tuesday, November 3, 2026", 12 pages — every federal contest, every
chancery and circuit judgeship and the special legislative and district
attorney elections, each page but the last ending "TURN BALLOT OVER TO
CONTINUE VOTING" and the last "END OF BALLOT" — and no ballot measure.
A statewide measure (a legislative constitutional amendment; Mississippi's
initiative process has had no valid route to the ballot since In re
Initiative Measure No. 65, 2021) is on every voter's ballot, so it is
on this composite; a complete composite with none is the Secretary's
own ballot saying there is none.

So this reader never returns a measure. It returns [] (confirmed_none)
only when the document names `year`'s general-election date and
"STATE OF MISSISSIPPI", and runs complete — every page but the last
ends "TURN BALLOT OVER TO CONTINUE VOTING", the last "END OF BALLOT".
Anything that could be a measure — the words amendment, initiative,
measure, referendum, proposition, question, or a "Shall ...?" line —
refuses the state (None): there is no verified Mississippi measure
layout to read one from, and a measure is never dropped to reach "none".
Re-checked every run (an empty answer is cached only
EMPTY_RESPONSE_TTL_HOURS), so a late-certified measure turns the next
night's read into a refusal, not a stale "none".

Discovery: every PDF link under /<year>/ on the elections page whose
address or label reads "Sample Ballot" is read, and the one whose first
page names the state and the general-election date is used — the 2026
link names neither ("Sample Ballot 9-9-26.pdf"), so a primary's ballot
beside it is told apart by its content. No such ballot among them (none
linked, or only a primary's) is NotYetPublished — unless a link couldn't
be read, when it is a failure, since that one may have been the general.
A link that fails beside a general ballot that was read changes nothing.
Two general ballots (a reissued or corrected one beside the original)
are accepted only when each on its own confirms none.
"""

import logging
import re
from urllib.parse import unquote, urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    pdf_pages,
)

logger = logging.getLogger(__name__)

ELECTIONS_URL = "https://www.sos.ms.gov/elections-voting"

_CONTINUE = "TURN BALLOT OVER TO CONTINUE VOTING"
_END = "END OF BALLOT"
_MEASURE_WORDS_RE = re.compile(
    r"\b(amendments?|initiatives?|measures?|referend(?:um|a)|propositions?|questions?)\b|^\s*shall\b",
    re.IGNORECASE | re.MULTILINE,
)


def sample_ballot_urls(page_html: str, year: int) -> list[str] | None:
    """Every PDF under /<year>/ the elections page labels "Sample Ballot",
    or None when this isn't that page. Which of them is the general
    election's is decided by reading each (is_general_ballot): the 2026
    link names neither the election nor its date ("Sample Ballot
    9-9-26.pdf"), so a primary's sample ballot posted beside it can only
    be told apart by what it says."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if "Elections" not in title or "MS SOS" not in title:
        return None
    urls = []
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        haystack = f"{unquote(href)} {a.get('aria-label') or ''} {' '.join(a.text_content().split())}".lower()
        if href.lower().endswith(".pdf") and f"/{year}/" in href and "sample ballot" in haystack:
            url = urljoin(ELECTIONS_URL, href)
            if url not in urls:
                urls.append(url)
    return urls


def is_general_ballot(pages: list[str], year: int) -> bool:
    """Whether this sample ballot is the statewide one for `year`'s
    general election: its first page names the state and the date."""
    first = " ".join((pages[0] if pages else "").split())
    return "STATE OF MISSISSIPPI" in first and long_date(election_day(year)) in first


def confirms_none(pages: list[str], year: int) -> bool:
    """Whether this sample ballot is `year`'s complete statewide general-
    election composite with no measure on it."""
    if not pages:
        return False
    if not is_general_ballot(pages, year):
        logger.warning("MS sample ballot does not name the %d general election", year)
        return False
    for i, page in enumerate(pages):
        last_line = next((ln.strip() for ln in reversed(page.splitlines()) if ln.strip()), "")
        expected = _END if i == len(pages) - 1 else _CONTINUE
        if last_line != expected:
            logger.warning("MS sample ballot page %d ends %r, not %r — not a complete ballot", i + 1, last_line, expected)
            return False
    for page in pages:
        m = _MEASURE_WORDS_RE.search(page)
        if m:
            logger.warning("MS sample ballot carries %r — possibly a measure this reader can't read; refusing", m.group(0))
            return False
    return True


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await get_text(client, ELECTIONS_URL, "MS elections page")
    if page_html is None:
        return None
    try:
        urls = sample_ballot_urls(page_html, year)
    except Exception:
        logger.exception("MS elections page was not parseable")
        return None
    if urls is None:
        logger.warning("MS elections page is not the page this reader knows")
        return None
    generals = []
    failed = []
    for url in urls:
        raw = await get_bytes(client, url, "MS sample ballot")
        pages = None
        if raw is not None:
            try:
                pages = pdf_pages(raw)
            except Exception:
                logger.exception("MS sample ballot %s was not parseable", url)
        if pages is None:
            # A stale link (a primary's ballot taken down) needn't sink a
            # general ballot found beside it; it only matters when no
            # general ballot was read (below).
            failed.append(url)
            continue
        if is_general_ballot(pages, year):
            generals.append(pages)
    if not generals:
        if failed:
            # The general's ballot may be the one that couldn't be read.
            logger.warning("MS: no general sample ballot read and %d link(s) failed: %s", len(failed), failed)
            return None
        # Every linked sample ballot was read and none is this general's
        # (only a primary's, or none posted yet): not yet, nothing broken.
        raise NotYetPublished(f"the Mississippi Secretary of State's {year} general-election sample ballot")
    # A reissued or corrected ballot can sit beside the original; it is
    # accepted only when every general ballot gives the same answer.
    answers = {confirms_none(pages, year) for pages in generals}
    if answers != {True}:
        return None
    return []
