"""Arkansas's ballot-measure strategy — the Secretary of State's
Initiatives and Referenda page and the per-issue public notices it
links.

The page lists, under "<year> Proposed Initiatives as Certified by the
General Assembly", one PDF per legislatively referred issue. Each PDF is
the constitutionally required notice (Ark. Const. art. 19 §22): "the
95th General Assembly refers the following ... to a vote of the people
on November 3, 2026, and will appear on the ballot as Issue No. 1",
followed by the Popular Name and Ballot Title the General Assembly
wrote (an amendment), or a Ballot Title and Ballot Question (a bond
referral such as 2026's Issue 4). Those are stored verbatim:

- title: the Popular Name, or the Ballot Title where the notice has no
  Popular Name.
- official_summary: the Ballot Title under a Popular Name, otherwise the
  Ballot Question.

Arkansas publishes no YES/NO explanation (Issue 4's "FOR Issuance ..."
/ "AGAINST Issuance ..." lines are the ballot's choice labels, not an
explanation) and no fiscal statement, so those stay null.

Citizen initiatives: the page's other section, "<year> Proposed
Initiatives as Certified by the Attorney General", is NEVER read as
ballot content — its own text says a listing there is not confirmation
that a measure is on the ballot (those are petitions whose wording the
Attorney General approved for circulation). And no Secretary of State
page records that an initiative's petition was found sufficient and
certified for the ballot: that determination goes out as a letter and
news coverage, not a list. Checked against a real cycle that had one —
2024's Issue 2 (casino amendment, certified sufficient July 31, 2024):
the archived Initiatives and Referenda page from October 16, 2024
(web.archive.org) lists no ballot issues at all, referred or initiated,
and the Secretary's news listing carries no certification notice.

So the reader cannot confirm a list that includes initiatives, and it
refuses to confirm one that might be missing any. An initiative can
only reach the ballot after the Attorney General certifies its wording,
which puts it in that section; so the list of General Assembly
referrals is returned only when the Attorney General section for
`year` exists AND lists nothing ("None for the 2026 General
Election"). A section listing any petition, or no section for `year`,
makes the whole fetch None (not covered) rather than a possibly
incomplete "covered" list. 2026 passes: the section reads "None", and
the one proposal circulated fell short of signatures.

A notice that doesn't name this election's date, whose printed issue
number disagrees with its link, or that isn't a General Assembly
referral fails the whole fetch (None). No heading for this year's
General Assembly referrals means the page isn't set up for the cycle
(None); a heading with no issues under it is a checked answer ([]).

Verified live 2026-09-28: Issues 1-4 on the November 3, 2026 ballot,
4/4 parse.
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
    names_date,
    pdf_text,
)

logger = logging.getLogger(__name__)

LANDING_URL = "https://www.sos.arkansas.gov/elections/initiatives-and-referenda/"
TITLE_AUTHORITY = "Arkansas General Assembly"
ORIGIN = "Arkansas General Assembly"

_LINK_TEXT_RE = re.compile(r"^Issue\s+(\d+)$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\bISSUE\s+NO\.\s*(\d+)", re.IGNORECASE)
_POPULAR_RE = re.compile(r"\bPopular Name\s*(.*?)\s*\bBallot Title\b", re.DOTALL)
_BALLOT_TITLE_RE = re.compile(
    r"\bBallot Title\s*(.*?)\s*(?:\bBE IT RESOLVED\b|\bBallot Question\b)", re.DOTALL,
)
_QUESTION_RE = re.compile(r"\bBallot Question\s*(.*?)\s*(?:_+\s*FOR\b|$)", re.DOTALL)


_NONE_RE = re.compile(r"^None\b", re.IGNORECASE)


def _section(heading) -> list:
    out = []
    el = heading.getnext()
    while el is not None and el.tag not in ("h1", "h2", "h3", "h4", "h5"):
        out.append(el)
        el = el.getnext()
    return out


def _no_initiatives_certified(tree, year: int) -> bool:
    """Whether the "<year> ... Certified by the Attorney General" section
    exists and says, in its own words, that it lists nothing — the only
    state in which the General Assembly referrals are the whole ballot
    (see module docstring). Any link or entry there, or no such section,
    is False."""
    heading = next(
        (
            h for h in tree.xpath("//h1|//h2|//h3|//h4|//h5")
            if str(year) in h.text_content()
            and "certified by the attorney general" in h.text_content().lower()
        ),
        None,
    )
    if heading is None:
        return False
    section = _section(heading)
    if any(True for el in section for _ in el.iter("a")) or any(el.tag in ("ul", "ol", "table") for el in section):
        return False
    return any(_NONE_RE.match(clean_text(el.text_content()) or "") for el in section)


def issue_links(page_html: str, base_url: str, year: int) -> dict[str, str] | None:
    """{issue number: notice PDF url} from the "<year> ... Certified by the
    General Assembly" section, or None if the page has no such heading, a
    link there names an issue in a shape this reader doesn't know, or the
    Attorney General section doesn't say "None" (a citizen initiative
    might be on the ballot, and nothing here can confirm which)."""
    tree = lxml_html.fromstring(page_html)
    heading = next(
        (
            h for h in tree.xpath("//h1|//h2|//h3|//h4|//h5")
            if str(year) in h.text_content()
            and "certified by the general assembly" in h.text_content().lower()
        ),
        None,
    )
    if heading is None:
        return None
    if not _no_initiatives_certified(tree, year):
        return None
    links: dict[str, str] = {}
    el = heading.getnext()
    while el is not None and el.tag not in ("h1", "h2", "h3", "h4", "h5"):
        for a in el.iter("a"):
            m = _LINK_TEXT_RE.match(clean_text(a.text_content()) or "")
            href = a.get("href") or ""
            if m and ".pdf" in href.lower():
                links.setdefault(m.group(1), urljoin(base_url, href))
            elif "issue" in (a.text_content() or "").lower():
                # A measure link in a shape this reader doesn't know. An
                # empty result here would read as "no measures" — refuse.
                return None
        el = el.getnext()
    return links


def parse_notice(text: str, number: str, year: int) -> dict | None:
    """One issue's public notice (extract_text() output)."""
    head = clean_text(text[:600]) or ""
    if "GENERAL ASSEMBLY" not in head.upper() or not names_date(text, election_day(year)):
        return None
    printed = _NUMBER_RE.search(text)
    if printed is None or printed.group(1) != number:
        return None

    popular = _POPULAR_RE.search(text)
    ballot_title = _BALLOT_TITLE_RE.search(text)
    if ballot_title is None:
        return None
    if popular is not None:
        title = clean_text(popular.group(1))
        summary = clean_text(ballot_title.group(1))
    else:
        question = _QUESTION_RE.search(text)
        title = clean_text(ballot_title.group(1))
        summary = clean_text(question.group(1)) if question else None
    if not title or not summary:
        return None
    return {
        "number": number,
        "title": title,
        "origin": ORIGIN,
        "official_summary": summary,
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page = await get_text(client, LANDING_URL, "AR initiatives and referenda")
    if page is None:
        return None
    try:
        links = issue_links(page, LANDING_URL, year)
    except Exception:
        logger.exception("AR initiatives and referenda page was not parseable HTML")
        return None
    if links is None:
        logger.warning("AR page has no General Assembly referral heading for %d", year)
        return None

    results = []
    for number in sorted(links, key=int):
        raw = await get_bytes(client, links[number], f"AR Issue {number} notice")
        if raw is None:
            return None
        try:
            parsed = parse_notice(pdf_text(raw), number, year)
        except Exception:
            logger.exception("AR Issue %s notice parse failed", number)
            return None
        if parsed is None:
            logger.warning("AR Issue %s notice didn't match the verified shape", number)
            return None
        results.append((parsed, links[number]))
    return results
