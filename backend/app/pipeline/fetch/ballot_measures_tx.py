"""Texas's ballot-measure strategy — the Legislative Reference Library's
constitutional-amendment election index (lrl.texas.gov/legis/
ConstAmends/electiondates.cfm).

Texas has no statewide initiative or statewide statutory referendum:
the only statewide measures a Texas ballot can carry are constitutional
amendments proposed by a two-thirds joint resolution of the Legislature
(Tex. Const. art. XVII §1). The Legislative Reference Library — the
Legislature's own library — keeps the record of every one, and its
"Constitutional amendment election dates" page lists every election at
which amendments were submitted, each linking a table of that
election's propositions (Joint resolution | Legislature | Prop. # |
Topic | Result). Special-session proposals are included under their own
dates (May 7, 2022 is one).

So the reader asks one question per run: does the index list this
year's November general-election date?

- It does: read that date's table. number = "Prop. #"; title =
  "Proposition N"; official_summary = the Caption cell, the proposition
  language ("The constitutional amendment to ...") the joint resolution
  prescribes for the ballot — hence title_authority. No YES/NO
  explanation or fiscal statement is part of this record — null.
- It doesn't: [] — CONFIRMED NONE, and re-checked every nightly run (an
  empty answer is cached for only 6h), so an
  amendment the Legislature sends to a November ballot later appears
  as soon as the library records it.

An index page that lists no election dates at all is treated as broken
(None), never as "none".

CONFIRMED NONE for 2026 (fetched 2026-09-28): the index's newest entry
is Nov 4, 2025 (the 17 proposals of the 89th Regular Session); nothing
is listed for November 3, 2026, and no called session of the 89th
Legislature appears among the sessions with proposals. Texas's odd-year
amendment elections are separate from the even-year general election.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_text

logger = logging.getLogger(__name__)

INDEX_URL = "https://lrl.texas.gov/legis/ConstAmends/electiondates.cfm"
TITLE_AUTHORITY = "Texas Legislature"
ORIGIN = "Texas Legislature"

_DATE_LINK_RE = re.compile(r"results\.cfm\?electionDate=(\d{4}-\d{2}-\d{2})")


def election_links(index_html: str) -> dict[str, str] | None:
    """{ISO date: results url} for every election the index lists, or
    None if it lists none (a broken page, never a real answer)."""
    links = {}
    for m in _DATE_LINK_RE.finditer(index_html):
        links.setdefault(m.group(1), urljoin(INDEX_URL, m.group(0)))
    return links or None


def parse_results(results_html: str) -> list[dict] | None:
    tree = lxml_html.fromstring(results_html)
    table = tree.xpath("//table[@id='tableToSort']")
    if not table:
        return None
    parsed = []
    for tr in table[0].xpath(".//tbody/tr"):
        cell = {td.get("data-label"): clean_text(td.text_content()) for td in tr.xpath("./td")}
        number, caption = cell.get("Proposition"), cell.get("Caption")
        if not number or not number.isdigit() or not caption:
            return None
        parsed.append({
            "number": number,
            "title": f"Proposition {number}",
            "origin": ORIGIN,
            "official_summary": caption,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return sorted(parsed, key=lambda p: int(p["number"]))


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    index = await get_text(client, INDEX_URL, "TX constitutional amendment election dates")
    if index is None:
        return None
    links = election_links(index)
    if links is None:
        logger.warning("TX amendment election index lists no elections at all — treating as broken")
        return None
    url = links.get(election_day(year).isoformat())
    if url is None:
        return []

    results_html = await get_text(client, url, f"TX amendments for {election_day(year)}")
    if results_html is None:
        return None
    try:
        parsed = parse_results(results_html)
    except Exception:
        logger.exception("TX amendment results page was not parseable HTML")
        return None
    if parsed is None:
        logger.warning("TX amendment results page didn't match the verified shape")
        return None
    return [(p, url) for p in parsed]
