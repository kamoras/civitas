"""South Carolina's ballot-measure strategy — the State Election
Commission's own referendum listing (vrems.scvotes.sc.gov, "Candidate
Tracking" -> Referendums).

The Commission's VREMS system lists every referendum on a given
election's ballot, statewide and local, from the same records that
build the ballots:

1. /Candidate/GetElections?electionType=General&year=<year> names the
   statewide general election and its id (2026: 22596, "11/3/2026
   Statewide General Election"). The one whose date is this year's
   general-election day is used; none is None (the election isn't set
   up yet — not the same as "no measures").
2. POSTing /Candidate/ReferendumSearch/ for that election, with no
   filters, returns every referendum on its ballots as one table
   (Referendum Title | Responsible County | Referendum Type | Political
   Party). STATEWIDE rows are the ones with no responsible county and
   no party — local questions always name their county, and party
   advisory questions (primary ballots) name their party. That is
   structure, not wording.
3. Each statewide row's ReferendumDetail page gives the Election Date
   (checked) and the Referendum Text, the question exactly as printed.

Stored verbatim: title = the referendum's title, official_summary =
its Referendum Text. The question is written into the General
Assembly's joint resolution (S.C. Const. art. XVI §1), hence
title_authority. The listed Responses ("Yes, In Favor of the
Question") are ballot choices, not an explanation — yes_means/no_means
stay null; no fiscal statement is published. South Carolina's
amendments aren't numbered on this record, so `number` is empty and the
Commission's own referendum id keys each one.

CONFIRMED NONE for 2026, re-checked every run: the listing for the
November 3, 2026 Statewide General Election holds 42 referendums, every
one a county's local question — none statewide (fetched 2026-09-28).
The same query against the November 5, 2024 general election returns
its one statewide question ("Constitutional Amendment Question",
referendum 691, the citizens-only voting amendment), which is what
makes the 2026 zero a reading rather than a blind spot.
"""

import logging
import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_json, get_text, post_text

logger = logging.getLogger(__name__)

BASE_URL = "https://vrems.scvotes.sc.gov"
ELECTIONS_URL = BASE_URL + "/Candidate/GetElections?electionType=General&year={year}"
SEARCH_URL = BASE_URL + "/Candidate/ReferendumSearch/"
TITLE_AUTHORITY = "South Carolina General Assembly"
ORIGIN = "South Carolina General Assembly"

_REFERENDUM_ID_RE = re.compile(r"referendumId=(\d+)")


def _text(el) -> str:
    return clean_text(el.text_content()) or ""


def general_election_id(elections: list, year: int) -> str | None:
    wanted = election_day(year).isoformat()
    for e in elections or []:
        if (e.get("electionDate") or "").startswith(wanted) and "general" in (e.get("electionName") or "").lower():
            return str(e.get("electionId"))
    return None


def statewide_rows(results_html: str) -> list[dict] | None:
    """[{title, detail_url, referendum_id}] for statewide rows, or None if
    the answer has no results table at all."""
    tree = lxml_html.fromstring(results_html)
    header = [c for c in tree.xpath("//table//th") if "Referendum Title" in _text(c)]
    if not header:
        return None
    rows = []
    for tr in tree.xpath("//table//tr[td]"):
        cells = tr.xpath("./td")
        link = cells[0].xpath(".//a/@href") if cells else []
        if len(cells) < 4 or not link:
            continue
        county, party = _text(cells[1]), _text(cells[3])
        if county or party:
            continue
        m = _REFERENDUM_ID_RE.search(link[0])
        if m is None:
            continue
        rows.append({
            "title": _text(cells[0]),
            "detail_url": urljoin(BASE_URL, link[0]).replace("http://", "https://", 1),
            "referendum_id": m.group(1),
        })
    return rows


def _field(tree, label: str) -> str | None:
    for span in tree.xpath("//span[contains(@class,'label-min-width')]"):
        if _text(span) == label:
            value = span.getnext()
            return _text(value) if value is not None else None
    return None


def parse_detail(detail_html: str, row: dict, year: int) -> dict | None:
    tree = lxml_html.fromstring(detail_html)
    day = election_day(year)
    if _field(tree, "Election Date") != f"{day.month}/{day.day}/{day.year}":
        return None
    if _field(tree, "Responsible County"):
        return None
    text = _field(tree, "Referendum Text")
    title = next((_text(h) for h in tree.xpath("//h2")), "") or row["title"]
    if not text or not title:
        return None
    return {
        "number": "",
        "id_key": row["referendum_id"],
        "title": title,
        "origin": ORIGIN,
        "official_summary": text,
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    elections = await get_json(client, ELECTIONS_URL.format(year=year), f"SC elections {year}")
    if not isinstance(elections, list):
        return None
    election_id = general_election_id(elections, year)
    if election_id is None:
        logger.warning("SC VREMS lists no statewide general election on %s", election_day(year))
        return None

    form = {
        "ElectionId": election_id, "ExportFileName": "", "ElectionDate": "",
        "SearchType": "Default", "SelectedGroup": "All", "SelectedPoliticalParty": "All",
    }
    results_html = await post_text(
        client, SEARCH_URL, f"SC referendum search {election_id}",
        files={k: (None, v) for k, v in form.items()},
    )
    if results_html is None:
        return None
    try:
        rows = statewide_rows(results_html)
    except Exception:
        logger.exception("SC referendum search results were not parseable HTML")
        return None
    if rows is None:
        logger.warning("SC referendum search returned no results table")
        return None

    results = []
    for row in rows:
        detail = await get_text(client, row["detail_url"], f"SC referendum {row['referendum_id']}")
        if detail is None:
            return None
        try:
            parsed = parse_detail(detail, row, year)
        except Exception:
            logger.exception("SC referendum %s detail parse failed", row["referendum_id"])
            return None
        if parsed is None:
            logger.warning("SC referendum %s detail didn't match the verified shape", row["referendum_id"])
            return None
        results.append((parsed, row["detail_url"]))
    return results
