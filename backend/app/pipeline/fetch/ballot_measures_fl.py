"""Florida's ballot-measure strategy — the Division of Elections'
Initiatives / Amendments / Revisions Database
(constitutionalinitiatives.dos.fl.gov).

Florida's certified list exists only as the answer to that database's
own search form: Year=<year>, Made Ballot=YES, Status=ALL. Each result
row carries the election ("2026 GEN"), the title, the ballot number
("(1)") and the sponsor, and links the measure's detail page, which
carries the ballot summary and the record's own "Ballot Number" and
"Election Year" fields. Stored verbatim:

- number: the ballot number, cross-checked against the detail page's
  own "Ballot Number" (a disagreement fails the fetch).
- title / official_title: the detail page's heading (the ballot title).
- official_summary: the detail page's Summary, paragraph breaks kept.
- origin: the Sponsor exactly as listed ("The Florida Legislature/House").

Who drafted the title and summary is read from the record, not assumed.
A legislative joint resolution's ballot statement is the Legislature's
own — unless a court strikes it, in which case s.101.161(3)(c), F.S.
has the Attorney General rewrite it, and the record then links an "AG
Letter (Title & Summary rewritten)". That is exactly what happened to
2026's Amendment 3 (the Attorney General's revised statement, dated
August 13, 2026, after Save Our Voters From Misleading Ballot Language,
Inc. v. Byrd). A citizen initiative's title and summary are its
sponsor's.

Florida publishes no YES/NO explanation, and no Financial Impact
Statement exists for legislative referrals (the Financial Impact
Estimating Conference prepares one only for citizen initiatives, and
the detail page carries only its dates, not its text) — so those stay
null rather than being filled from anywhere else.

The search answer for a year is a real, checked answer: zero "made
ballot" rows for `year`'s general election is [] (none certified yet),
not a failure. A search page that can't be read, or a detail page that
can't, is None.

Verified live 2026-09-28: three amendments on the November 3, 2026
ballot (1 Budget Stabilization Fund, 2 agricultural tangible personal
property exemption, 3 homestead exemption / non-homestead cap), 3/3
parse.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import get_text, post_text

logger = logging.getLogger(__name__)

SEARCH_URL = "https://constitutionalinitiatives.dos.fl.gov/"
LEGISLATURE = "Florida Legislature"
ATTORNEY_GENERAL = "Florida Attorney General"

# "(1)" for a legislative referral; "22-05 (3)" for a citizen initiative,
# whose petition serial precedes the ballot number (2024's Amendments 3
# and 4, 2018's 3 and 4 — verified on the live database).
_BALLOT_NUMBER_RE = re.compile(r"^(?:\d{2}-\d{2}\s*)?\((\d+)\)$")
# The Status column's vocabulary on made-ballot rows (every value seen
# live across 2018-2026): "Active" is on the coming ballot, "Removed" was
# struck from it (2018's Amendment 8), and "Passed"/"Defeated" are
# results of an election already held. Anything else is refused.
_ON_BALLOT_STATUS = "Active"
_KNOWN_STATUSES = {"Active", "Removed", "Passed", "Defeated"}
_AG_REWRITE_RE = re.compile(r"Title\s*&\s*Summary rewritten", re.IGNORECASE)


def _text(el) -> str:
    return clean_text(el.text_content()) or ""


def search_form_fields(page_html: str) -> dict[str, str] | None:
    """The anti-forgery token the search form requires, or None."""
    tree = lxml_html.fromstring(page_html)
    token = tree.xpath("//form//input[@name='__RequestVerificationToken']/@value")
    return {"__RequestVerificationToken": token[0]} if token else None


def listed_measures(results_html: str, year: int) -> list[dict] | None:
    """[{number, title, detail_url, sponsor}] for every "made ballot" row
    of `year`'s general election still Active, or None if the results
    table is missing (the answer page didn't render — not the same as
    zero rows) or any of `year`'s rows can't be read."""
    tree = lxml_html.fromstring(results_html)
    table = tree.xpath("//table[@id='tableResult']")
    if not table:
        return None
    rows = []
    for tr in table[0].xpath(".//tbody/tr"):
        cells = tr.xpath("./td")
        if len(cells) < 6 or _text(cells[0]) != f"{year} GEN":
            continue
        # A row the search answered as made-ballot for this election is a
        # measure; one this reader can't read refuses the whole answer
        # rather than dropping out of it (a skipped row would publish the
        # ballot one amendment short, as "covered").
        status = _text(cells[1])
        m = _BALLOT_NUMBER_RE.match(_text(cells[4]))
        link = cells[3].xpath(".//a/@href")
        if status not in _KNOWN_STATUSES or m is None or not link:
            logger.warning(
                "FL %d made-ballot row %r / %r / %r didn't match the verified shape — refusing",
                year, status, _text(cells[4]), _text(cells[3])[:60],
            )
            return None
        if status != _ON_BALLOT_STATUS:
            continue  # struck (Removed), or an election already decided
        sponsor_links = cells[5].xpath(".//a")
        rows.append({
            "number": m.group(1),
            "title": _text(cells[3]),
            "detail_url": link[0],
            "sponsor": _text(sponsor_links[0]) if sponsor_links else _text(cells[5]),
        })
    return rows


def _labelled(tree, label: str) -> str | None:
    """A detail-page value from its "Label:" table row."""
    for tr in tree.xpath("//tr"):
        cells = tr.xpath("./td")
        if len(cells) >= 2 and _text(cells[0]) == label:
            return _text(cells[1]) or None
    return None


def parse_detail(detail_html: str, listed: dict, year: int) -> dict | None:
    tree = lxml_html.fromstring(detail_html)
    heading = tree.xpath("//div[contains(@class,'alert')]//h5")
    if not heading:
        return None
    if _labelled(tree, "Ballot Number:") != listed["number"]:
        return None
    if _labelled(tree, "Election Year:") != str(year):
        return None
    # The record's own "Made Ballot" date. The search is already filtered
    # to made-ballot rows; this is the backstop against a form change that
    # silently returns the default listing, which is initiatives still
    # SEEKING ballot position.
    if not _labelled(tree, "Made Ballot:"):
        return None

    summary_dd = tree.xpath("//dt[normalize-space()='Summary']/following-sibling::dd[1]")
    if not summary_dd:
        return None
    paragraphs = []
    for p in summary_dd[0].xpath(".//p"):
        if p.xpath(".//a"):
            continue  # the "View Full Text" link paragraph
        for line in p.text_content().splitlines():
            if clean_text(line):
                paragraphs.append(clean_text(line))
    summary = "\n".join(paragraphs)
    title = _text(heading[0])
    if not summary or not title:
        return None

    related = " ".join(_text(a) for a in tree.xpath(
        "//dt[normalize-space()='Related Links:']/following-sibling::dd[1]//a",
    ))
    sponsor = listed["sponsor"]
    if _AG_REWRITE_RE.search(related):
        title_authority = ATTORNEY_GENERAL
    elif "florida legislature" in sponsor.lower():
        title_authority = LEGISLATURE
    else:
        title_authority = sponsor or None

    return {
        "number": listed["number"],
        "title": title,
        "official_title": title,
        "origin": sponsor or None,
        "official_summary": summary,
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": title_authority,
        "fiscal_authority": None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    form_page = await get_text(client, SEARCH_URL, "FL initiatives database")
    if form_page is None:
        return None
    try:
        fields = search_form_fields(form_page)
    except Exception:
        logger.exception("FL initiatives database page was not parseable HTML")
        return None
    if fields is None:
        logger.warning("FL initiatives database: search form token not found")
        return None

    results_html = await post_text(
        client, SEARCH_URL, f"FL made-ballot search {year}",
        data={**fields, "Year": str(year), "Status": "ALL", "MadeBallot": "Y", "Sponsor": "ALL", "Title": ""},
    )
    if results_html is None:
        return None
    try:
        rows = listed_measures(results_html, year)
    except Exception:
        logger.exception("FL made-ballot search results were not parseable HTML")
        return None
    if rows is None:
        logger.warning("FL made-ballot search returned no results table")
        return None

    results = []
    for row in sorted(rows, key=lambda r: int(r["number"])):
        detail = await get_text(client, row["detail_url"], f"FL Amendment {row['number']} detail")
        if detail is None:
            return None
        try:
            parsed = parse_detail(detail, row, year)
        except Exception:
            logger.exception("FL Amendment %s detail parse failed", row["number"])
            return None
        if parsed is None:
            logger.warning("FL Amendment %s detail didn't match the verified shape", row["number"])
            return None
        results.append((parsed, row["detail_url"]))
    return results
