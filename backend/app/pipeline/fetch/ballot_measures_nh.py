"""New Hampshire's ballot-measure strategy — the Secretary of State's
"Questions on the General Election Ballot" PDF, linked from the
Secretary's per-year election-details page (one of
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

Discovery, for `year`: www.sos.nh.gov/{year}-election-details (verified
2024 and 2026; 2022's is not on this pattern and 404s) must carry the
page title "{year} Election Details", and exactly one link on it must
read "Questions on the General Election Ballot" (2026:
mm.nh.gov/files/uploads/sos/docs/questions-on-general-election-ballot-
2026-for-web.pdf). The Secretary does not post that document every
cycle — the 2024 page carried a Voters' Guide for that year's amendment
instead and no such link — so a page with no link is NotYetPublished
with no deadline (not yet covered: never "none", never an alarm), and a
404 for the year's page is the same. Any other fetch failure is None.

The document, verified live 2026-09-28 (1 page):

    2026 General Election
    The following questions will be placed on the 2026 General Election ballot.
    Questions Relating to Constitutional Amendments proposed by the 2026 General Court
    1. “Are you in favor of ... judge.” (Passed by the N.H. House 325 Yes 15 No;
       Passed by the Senate 23 Yes 1 No) CACR 13
    Yes No
    Statutory Question required by HB 1300, Chapter 324, 2026
    2. “Shall the [name of municipality] limit property tax growth ... RSA 32:5-i.”
    Yes No

Both lines naming the year's general election are required. Each
numbered question runs to its own "Yes No" line (the ballot's answer
boxes), and whatever sits between one question's "Yes No" and the next
number is the heading of the questions under it. Per question, verbatim:
the printed question from its opening quotation mark through anything
the document prints after the closing one (the legislative vote record
and the CACR number are printed with the question), as official_summary;
its heading, as title (a line from this document, not a ballot title —
official_title stays None). Numbers must run 1, 2, ... with no gap, and
a numbered line that doesn't open a quotation, or a question with no
"Yes No" line, refuses the state (None): an item this reader recognises
but cannot read is never dropped from the list.

Question 2 is the statutory question HB 1300 (2026 N.H. Laws ch. 324)
puts on every municipality's November ballot, each voting on its own
school property-tax cap; the Secretary lists it as a question "placed on
the 2026 General Election ballot", and it is stored exactly as printed,
placeholders ("[name of municipality]") included — never filled in.

The General Court writes each question's text (the CACR; HB 1300), so
title_authority names it, as published by the Secretary of State. No
yes/no explanation or fiscal statement is printed — those stay None.

Headers: www.sos.nh.gov and mm.nh.gov answer the standard BROWSER_HEADERS
with an Akamai 403 and the same request without the User-Agent's
"(+contact)" comment with 200 (measured 2026-09-28), so this module sends
HEADERS_NO_CONTACT.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished, join_lines
from app.pipeline.fetch.ballot_measures_state_common import (
    HEADERS_NO_CONTACT,
    get_bytes,
    get_text_or_missing,
    pdf_text,
)

logger = logging.getLogger(__name__)

DETAILS_URL = "https://www.sos.nh.gov/{year}-election-details"
LINK_TEXT = "questions on the general election ballot"
ORIGIN = "New Hampshire General Court"
TITLE_AUTHORITY = "New Hampshire General Court, as published by the New Hampshire Secretary of State"

_NUMBERED_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_YES_NO_RE = re.compile(r"^Yes\s+No$")


def find_questions_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok): the one "Questions on the General Election Ballot"
    link on the year's election-details page. page_ok False means this
    isn't that page (its title doesn't name the year) or it carries the
    link more than once — a failure, not an absence."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if f"{year} Election Details" not in title:
        return None, False
    hrefs = {
        urljoin(DETAILS_URL.format(year=year), a.get("href"))
        for a in tree.xpath("//a[@href]")
        if " ".join(a.text_content().split()).lower() == LINK_TEXT
    }
    if len(hrefs) > 1:
        return None, False
    return (hrefs.pop() if hrefs else None), True


def parse_questions(text: str, year: int) -> list[dict] | None:
    """Every numbered question in the document, or None when it isn't
    this year's general-election list or anything in it can't be read."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or lines[0] != f"{year} General Election":
        logger.warning("NH questions document does not open with '%d General Election'", year)
        return None
    if lines[1] != f"The following questions will be placed on the {year} General Election ballot.":
        logger.warning("NH questions document lacks its %d general-election statement", year)
        return None

    results: list[dict] = []
    heading: str | None = None
    pending: list[str] = []
    current: tuple[str, list[str]] | None = None
    for line in lines[2:]:
        if current is not None:
            if not _YES_NO_RE.match(line):
                current[1].append(line)
                continue
            number, body = current
            current = None
            question = join_lines(body)
            if not question or not question.startswith("“") or "”" not in question:
                logger.warning("NH question %s is not a quoted question — refusing", number)
                return None
            results.append({
                "number": number,
                "title": heading,
                "official_title": None,
                "origin": ORIGIN,
                "official_summary": question,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": TITLE_AUTHORITY,
                "fiscal_authority": None,
            })
            continue
        m = _NUMBERED_RE.match(line)
        if m is None:
            # A heading line; it heads every question below it until the
            # next heading.
            pending.append(line)
            continue
        if not m.group(2).startswith("“"):
            logger.warning("NH numbered line %r is not a quoted question — refusing", line[:60])
            return None
        if pending:
            heading, pending = join_lines(pending), []
        if heading is None:
            logger.warning("NH question %s has no heading above it — refusing", m.group(1))
            return None
        current = (m.group(1), [m.group(2)])
    if current is not None or pending:
        # A question with no answer line, or text after the last question.
        logger.warning("NH questions document ends mid-question or with unread text — refusing")
        return None
    if not results or [m["number"] for m in results] != [str(i) for i in range(1, len(results) + 1)]:
        logger.warning("NH questions are not numbered 1..n: %s", [m["number"] for m in results])
        return None
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    details_url = DETAILS_URL.format(year=year)
    page_html, missing = await get_text_or_missing(
        client, details_url, f"NH {year} election details", headers=HEADERS_NO_CONTACT,
    )
    awaited = f"the New Hampshire Secretary of State's {year} 'Questions on the General Election Ballot'"
    if missing:
        raise NotYetPublished(awaited, deadline_applies=False)
    if page_html is None:
        return None
    try:
        url, page_ok = find_questions_url(page_html, year)
    except Exception:
        logger.exception("NH %d election details page was not parseable", year)
        return None
    if not page_ok:
        logger.warning("NH %d election details page is not the page this reader knows", year)
        return None
    if url is None:
        # Posted only in some cycles (2024 had a Voters' Guide instead):
        # its absence is neither a failure nor a "none".
        raise NotYetPublished(awaited, deadline_applies=False)
    raw = await get_bytes(client, url, "NH general election questions", headers=HEADERS_NO_CONTACT)
    if raw is None:
        return None
    try:
        parsed = parse_questions(pdf_text(raw), year)
    except Exception:
        logger.exception("NH general election questions PDF was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
