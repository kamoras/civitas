"""Oklahoma's ballot-measure strategy — the Secretary of State's own
"Search State Questions" register (sos.ok.gov/gov/questions.aspx), read
as HTML (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

In Oklahoma the Secretary of State numbers every State Question and,
once one qualifies, notifies the State Election Board of its number,
ballot title and election date (the Election Board's own State
Questions page says exactly this). The register lists every State
Question newest-first as a table — SQ number (linking the question's
filed documents), type, petition/legislative-referendum numbers, the
resolution or bill number, a citation line, a short subject line, and
the election date. Verified live 2026-09-28: two rows dated "ELECTION
DATE: November 3, 2026" — SQ 845 (HJR1024, "Judicial Nominating
Commission") and SQ 847 (SJR 39, "Real Property Valuation") — matching
the Election Board's own "Two state questions have qualified for the
November 3, 2026 election."

The ballot title is read only from a born-digital copy. The register's
linked PDFs are scans (Adobe Paper Capture OCR, with character errors —
"ta1rable", "e1rceed") that also carry every superseded version of the
title: the resolution's, the Attorney General's preliminary and final
rewrites, and for SQ 845 an August correction of the final one. OCR'd
text is not verbatim text and is never stored. The State Election Board
posts the final title, as filed, for some questions as an accessible
(text) PDF at BALLOT_TITLE_URL — verified 2026-10-08: SQ 845's ("SQ 845,
LR 379 Final Ballot Title / Corrected (CLEAN)", filed August 13, 2026)
answers 200 with a text layer; SQ 847 has only a scanned
...-final-ballot-title.pdf there and no accessible copy (404). Where the
accessible copy exists, its text between the heading and "Shall the
proposal be approved?" is the official_title, quoted, and it is the
measure's source; its heading must name this SQ and the referral number
the register files it under, and the "For the proposal – YES / Against
the proposal – NO" lines must follow, or the state is refused (None). A
404 leaves that question as the register gives it: the SQ number, the
Secretary of State's subject line as the title (not the official ballot
title — official_title stays None), and its filed PDF as the source.
The ballot's own yes/no framing is the bare "For/Against the proposal",
which says nothing about what a vote does, so yes_means/no_means stay
None. No drafter is named (title_authority None): the accessible copy
does not say who wrote the title, and the scanned filing that does (for
SQ 845, the Attorney General's rewrite) is OCR.

The register is read page after page (its own WebForms pager) until a
page whose every dated question is for an election more than
READ_BACK_MARGIN_DAYS before this one (numbers follow filing order, not
election order — see that constant); a page budget running out first, or
the register ending first, is a failure.
Page 1 alone once was: SQ 848 (April 2027) sits above November's 845 and
847, so one or two more filings would push them onto page 2. Pages read
to that point with no row dated `year`'s general election raise
NotYetPublished (not yet covered — never [] and
never a nightly ingest failure): the register isn't a certified list, so
it can't say "none", but nothing is broken either. A row whose election
date is printed in any other form refuses the page (None).
"""

import logging
import re
from datetime import date, timedelta
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.election_calendar import next_election_day
from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished, join_lines
from app.pipeline.fetch.ballot_measures_state_common import pdf_text
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_text_with_retry, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.sos.ok.gov/gov/questions.aspx"

_rate_limiter = RateLimiter(rps=1.0)

_DATE_RE = re.compile(r"ELECTION DATE:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_ORIGINS = {
    "legislative": "Oklahoma Legislature",
    "initiative": "Oklahoma voters (initiative petition)",
    "referendum": "Oklahoma voters (referendum petition)",
}


def _general_election_label(year: int) -> str:
    day = next_election_day(date(year, 1, 1))
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def parse_register(
    page_html: str, year: int, removed: list[dict] | None = None,
) -> list[tuple[dict, str]] | None:
    """The State Questions this register page dates for `year`'s general,
    or None when a row can't be read. Every other numbered question on the
    page is appended to `removed` (when given) as a removed marker — see
    fetch_measures."""
    tree = lxml_html.fromstring(page_html)
    target = _general_election_label(year)
    header_row = next(
        (tr for tr in tree.xpath("//tr[th]") if "SQ Num" in " ".join(tr.text_content().split())),
        None,
    )
    if header_row is None:
        return None
    headers = [" ".join(th.text_content().split()) for th in header_row.xpath("th")]
    try:
        col = {name: headers.index(name) for name in ("SQ Num", "Type", "Summary", "Status")}
    except ValueError:
        return None
    if "Election Date" in headers:
        col["Election Date"] = headers.index("Election Date")

    results = []
    table = next(header_row.iterancestors("table"))
    for tr in table.xpath(".//tr[td]"):
        cells = tr.xpath("td")
        row_text = " ".join(tr.text_content().split())
        if len(cells) != len(headers):
            if "ELECTION DATE" in row_text.upper():
                # A register row in a shape this reader doesn't know —
                # skipping it could drop a question set for this ballot.
                logger.warning("OK register row with %d cells (header has %d) — refusing", len(cells), len(headers))
                return None
            continue  # the pager row
        status = " ".join(cells[col["Status"]].text_content().split())
        m = _DATE_RE.search(status)
        column_date = _column_date(cells[col["Election Date"]]) if "Election Date" in col else None
        if column_date == next_election_day(date(year, 1, 1)) and (m is None or m.group(1) != target):
            # The register's own Election Date column sets it for this
            # election but its status line doesn't say so in the verified
            # form: refuse rather than drop it.
            logger.warning("OK register row dated %s by its Election Date column only — refusing", column_date)
            return None
        if m is None and "ELECTION DATE" in status.upper():
            logger.warning("OK register election date %r not in the verified form — refusing", status[:80])
            return None
        number = clean_text(cells[col["SQ Num"]].text_content())
        if m is None or m.group(1) != target:
            # On the register but not dated for this election. For a State
            # Question we listed before, that is the state saying it is no
            # longer on this ballot (struck, or moved to another election):
            # reported as removed — the pipeline marks only rows it has on
            # file, so the rest of the register's questions change nothing.
            if number and number.isdigit() and removed is not None:
                removed.append({
                    "number": number, "title": clean_text(cells[col["Summary"]].text_content()) or "",
                    "origin": None, "official_summary": None, "fiscal_impact": None,
                    "yes_means": None, "no_means": None, "removed": True,
                })
            continue
        link = cells[col["SQ Num"]].xpath(".//a[@href]")
        subject = clean_text(cells[col["Summary"]].text_content())
        if not link or not number or not number.isdigit() or not subject:
            logger.warning("OK register row for %s didn't match the verified shape — refusing", target)
            return None
        kind = (clean_text(cells[col["Type"]].text_content()) or "").lower()
        filed_as = {
            clean_text(cells[headers.index(h)].text_content())
            for h in ("Petition Num", "Leg Num", "Ref Num") if h in headers
        } - {None}
        results.append((
            {
                "number": number,
                "title": subject,
                "official_title": None,
                "origin": _ORIGINS.get(kind),
                "official_summary": None,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": None,
                "fiscal_authority": None,
                "filed_as": sorted(n for n in filed_as if n.isdigit()),
            },
            urljoin(URL, link[0].get("href")),
        ))
    return results


# The State Election Board's accessible copy of a question's final ballot
# title (see the module docstring). The folder is the election year.
BALLOT_TITLE_URL = (
    "https://oklahoma.gov/content/dam/ok/en/elections/state-questions/"
    "sq{year}/sq{number}-final-ballot-title-accessible.pdf"
)
_HEADING_RE = re.compile(r"^SQ (\d+), [A-Z]{2} (\d+) Final Ballot Title$")
# The one sub-heading verified between the heading and the text: the
# Board's clean (not redline) copy of a corrected title.
_SUBHEADINGS = {"Corrected (CLEAN)"}
_QUESTION_LINES = ["Shall the proposal be approved?", "For the proposal – YES", "Against the proposal – NO"]


def parse_ballot_title(text: str, number: str, filed_as: list[str]) -> str | None:
    """The ballot title's text, verbatim, from the Board's accessible copy
    — or None when the document isn't that question's final ballot title
    in the verified shape."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    m = _HEADING_RE.match(lines[0]) if lines else None
    if m is None or m.group(1) != number or m.group(2) not in filed_as:
        logger.warning("OK ballot title for SQ %s: heading %r is not this question's", number, lines[:1])
        return None
    body = lines[1:]
    while body and body[0] in _SUBHEADINGS:
        body = body[1:]
    if _QUESTION_LINES[0] not in body:
        logger.warning("OK ballot title for SQ %s: no %r line", number, _QUESTION_LINES[0])
        return None
    end = body.index(_QUESTION_LINES[0])
    if body[end:end + 3] != _QUESTION_LINES or not body[:end]:
        logger.warning("OK ballot title for SQ %s: not in the verified shape", number)
        return None
    return join_lines(body[:end])


async def _with_ballot_title(
    client: httpx.AsyncClient, year: int, parsed: dict, url: str,
) -> tuple[dict, str] | None:
    """`parsed` with its ballot title quoted from the Board's accessible
    copy, when there is one; unchanged on a 404; None on any other
    failure, or a copy that isn't this question's title."""
    title_url = BALLOT_TITLE_URL.format(year=year, number=parsed["number"])
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", title_url, log_label=f"OK SQ {parsed['number']} ballot title",
        headers=BROWSER_HEADERS, expected_statuses=(404,),
    )
    if resp is None:
        return None
    if resp.status_code == 404:
        return parsed, url
    try:
        text = pdf_text(resp.content)
    except Exception:
        logger.exception("OK SQ %s ballot title PDF was not readable", parsed["number"])
        return None
    ballot_title = parse_ballot_title(text, parsed["number"], parsed["filed_as"])
    if ballot_title is None:
        return None
    return {**parsed, "official_title": ballot_title}, title_url


_GRID_TARGET = "ctl00$DefaultContent$Questiongrid1$GridView1"
# The register shows 15 State Questions a page, newest first. A November
# question is filed months ahead, so it sits within the first few pages;
# this bound only stops a runaway loop (a pager that never advances).
MAX_PAGES = 8


_COLUMN_DATE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")


def _column_date(cell) -> date | None:
    """The register's "Election Date" column ("11-03-2026"), or None."""
    m = _COLUMN_DATE_RE.match(clean_text(cell.text_content()) or "")
    if m is None:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


# How far before `year`'s general a page's newest dated question must be
# before paging stops. SQ numbers follow FILING order, not election order:
# SQ 832 was filed in 2023 and set for the June 2026 primary, so a question
# for November can sit on a page among much older numbers. Two years is a
# full cycle of lead time; on the live register (2026-09-28) it stops
# after page 3, whose newest dated question is November 2020.
READ_BACK_MARGIN_DAYS = 730


def reaches_before(page_html: str, year: int) -> bool:
    """Whether paging can stop at this register page: it carries at least
    one dated State Question, and every dated one is for an election more
    than READ_BACK_MARGIN_DAYS before `year`'s general (by the register's
    own Election Date column). While any row is dated for the target, a
    later election, or anything within the margin, the next page could
    still hold a question set for this ballot."""
    target = next_election_day(date(year, 1, 1))
    cutoff = target - timedelta(days=READ_BACK_MARGIN_DAYS)
    tree = lxml_html.fromstring(page_html)
    header_row = next(
        (tr for tr in tree.xpath("//tr[th]") if "SQ Num" in " ".join(tr.text_content().split())),
        None,
    )
    if header_row is None:
        return False
    headers = [" ".join(th.text_content().split()) for th in header_row.xpath("th")]
    if "Election Date" not in headers:
        return False
    idx = headers.index("Election Date")
    table = next(header_row.iterancestors("table"))
    dated = [
        d for tr in table.xpath(".//tr[td]")
        if len(cells := tr.xpath("td")) == len(headers) and (d := _column_date(cells[idx])) is not None
    ]
    return bool(dated) and max(dated) < cutoff


def next_page_form(page_html: str, page: int) -> dict[str, str] | None:
    """The WebForms postback the register's own pager makes for `page`, or
    None when the page carries no link to it (the register has ended)."""
    if f"Page${page}" not in page_html:
        return None
    tree = lxml_html.fromstring(page_html)
    form = {
        i.get("name"): i.get("value") or ""
        for i in tree.xpath("//form//input[@type='hidden']") if i.get("name")
    }
    form.update({"__EVENTTARGET": _GRID_TARGET, "__EVENTARGUMENT": f"Page${page}"})
    return form


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """Every State Question the register dates for `year`'s general,
    reading page after page until reaches_before says the pages have gone
    back past any question that could be set for it. Page 1 alone isn't enough: a question filed later
    for another election (SQ 848, April 2027, sits above 845 and 847)
    pushes November's rows onto page 2, and a list read from page 1 would
    be published short — or read as "not yet" — without a sign of it."""
    page_html = await fetch_text_with_retry(client, _rate_limiter, URL, "OK state questions register")
    if page_html is None:
        return None
    results: list[tuple[dict, str]] = []
    others: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            parsed = parse_register(page_html, year, others)
            done = reaches_before(page_html, year)
        except Exception:
            logger.exception("OK state questions register page %d was not parseable", page)
            return None
        if parsed is None:
            return None
        results.extend(p for p in parsed if p[0]["number"] not in {r[0]["number"] for r in results})
        if done:
            break
        form = next_page_form(page_html, page + 1)
        if form is None:
            logger.warning("OK register ended without a question dated before %d's general", year)
            return None
        resp = await fetch_with_retry(
            client, _rate_limiter, "POST", URL, log_label=f"OK register page {page + 1}",
            headers=BROWSER_HEADERS, data=form,
        )
        if resp is None:
            return None
        page_html = resp.text
    else:
        logger.warning("OK register: %d pages read without reaching an earlier election", MAX_PAGES)
        return None
    titled = []
    for parsed, url in results:
        pair = await _with_ballot_title(client, year, parsed, url)
        if pair is None:
            return None
        titled.append(pair)
    results = titled
    listed = {p["number"] for p, _ in results}
    others = list({o["number"]: o for o in others if o["number"] not in listed}.values())
    if not results:
        # The register lists no State Question for this election (yet).
        # It is not a certified ballot list, so that is never "none" —
        # and nothing failed either: not yet covered, no alert. The
        # questions it dates otherwise travel with it, so one we listed
        # before is marked removed rather than read as a lost document.
        raise NotYetPublished(
            f"an Oklahoma State Question dated {_general_election_label(year)}", deadline_applies=False,
            removed=others,
        )
    return results + [(o, URL) for o in others]
