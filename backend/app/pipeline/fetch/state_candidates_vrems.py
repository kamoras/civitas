"""The certified November ballot from a state's candidate-tracking system
(VREMS "Candidate Tracking", as South Carolina runs it).

South Carolina sat on `google_civic` all cycle. Its primary results ARE
reachable (a Clarity host, enr-scvotes.org), but reading nominees from
them would have published the wrong Senate nominee: Lindsey Graham won
the June 9 Republican primary outright, then a separate US Senate
Special Republican Primary (August 11) and its runoff nominated Darline
Graham. Any primary-results reader that stops at June names the wrong
person; one that tries to follow specials has to know every special
exists. The candidate-tracking list for the 11/3/2026 Statewide General
Election answers the question directly — Darline Graham, alongside the
Constitution, Libertarian and Workers candidates no primary ever lists.

Flow (read from the site's own SelectElection.js / CandidateSearch.js):
  GET  {base}/Candidate/GetElections?electionType=General&year={year}
       -> [{electionId, electionName, electionDate}]
  GET  {base}/Candidate/CandidateSearch?electionId={id}
       -> anti-forgery token + the office dropdown
  POST {base}/Candidate/CandidateSearch/  (multipart, one per office)
       -> an HTML table: Office | ... | Name on Ballot | ... | Party | ... | Candidate Status

The election is picked by the statutory general-election DATE and the
federal offices by what parse_office recognises in the dropdown, so
neither an election id nor an office id is pinned here.
"""

import logging

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.fec import general_election_day
from app.pipeline.fetch.http_utils import (
    BROWSER_HEADERS,
    fetch_json_with_retry,
    fetch_with_retry,
)
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    parse_office,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

# Everyone the general election lists who is still on it. "Withdrew",
# "Died", "Disqualified" and friends are separate statuses, so asking
# for Active is what drops them — the one filter this list needs.
_ACTIVE = "Active"


def _general_election_id(elections: list, year: int) -> str | None:
    wanted = general_election_day(year).isoformat()
    ids = [
        str(e.get("electionId"))
        for e in elections
        if isinstance(e, dict)
        and str(e.get("electionDate") or "").startswith(wanted)
        and "general" in str(e.get("electionName") or "").lower()
    ]
    # Two "general" elections on the federal day (a statewide general and,
    # say, a municipal one) would be a guess — refuse rather than pick.
    return ids[0] if len(ids) == 1 else None


def _search_form(page: str) -> tuple[dict, list[str]] | None:
    """Hidden fields to echo back, and the federal office ids offered."""
    tree = lxml_html.fromstring(page)
    forms = tree.xpath('//form[@id="searchForm"]')
    if not forms:
        return None
    form = forms[0]
    hidden = {
        i.get("name"): i.get("value") or ""
        for i in form.xpath('.//input[@type="hidden"][@name]')
    }
    offices = [
        o.get("value")
        for o in form.xpath('.//select[@id="SelectedOffice"]/option')
        if o.get("value") and parse_office(o.text_content() or "")
    ]
    return hidden, offices


def _rows(page: str) -> list[dict]:
    """The result table as header -> cell dicts, keyed by the site's own
    column names so a reordered column cannot shift a party into a name."""
    tree = lxml_html.fromstring(page) if page.strip() else None
    if tree is None:
        return []
    tables = tree.xpath('//table[@id="gridCandidateSearch"]') or tree.xpath("//table")
    if not tables:
        return []
    headers = [(th.text_content() or "").strip() for th in tables[0].xpath(".//thead//th")]
    out = []
    for tr in tables[0].xpath(".//tbody/tr"):
        cells = [(td.text_content() or "").strip() for td in tr.xpath("./td")]
        if len(cells) == len(headers):
            out.append(dict(zip(headers, cells)))
    return out


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    base = (source.get("base_url") or "").rstrip("/")
    if not base:
        logger.warning("%s vrems source has no base_url", state)
        return None

    elections = await fetch_json_with_retry(
        client, _rate_limiter,
        f"{base}/Candidate/GetElections?electionType=General&year={year}",
        f"{state} candidate-tracking elections",
    )
    if not isinstance(elections, list):
        return None
    election_id = _general_election_id(elections, year)
    if not election_id:
        logger.info("%s candidate tracking lists no single %d general election yet", state, year)
        return None

    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", f"{base}/Candidate/CandidateSearch?electionId={election_id}",
        log_label=f"{state} candidate search page", headers=BROWSER_HEADERS,
    )
    if resp is None:
        return None
    form = _search_form(resp.text)
    if not form or not form[1]:
        logger.warning("%s candidate search page has no federal office to ask for", state)
        return None
    hidden, office_ids = form

    records: list[dict] = []
    for office_id in office_ids:
        fields = {
            **hidden,
            "ElectionId": election_id,
            "SelectedOffice": office_id,
            "SelectedCandidateStatus": _ACTIVE,
            "CandidateFirstName": "",
            "CandidateLastName": "",
        }
        resp = await fetch_with_retry(
            client, _rate_limiter, "POST", f"{base}/Candidate/CandidateSearch/",
            log_label=f"{state} candidates for office {office_id}", headers=BROWSER_HEADERS,
            files={k: (None, v) for k, v in fields.items()},
        )
        if resp is None:
            # A missing office would read as "nobody is running" for it.
            return None
        for row in _rows(resp.text):
            if row.get("Candidate Status") != _ACTIVE:
                continue
            parsed = parse_office(row.get("Office") or "")
            display = clean_display_name(row.get("Name on Ballot") or "")
            last = surname(display)
            if not parsed or not last:
                continue
            records.append({
                "office": parsed[0],
                "district": parsed[1],
                "party": normalize_party(row.get("Party") or "", ballot_list=True),
                "last_name": last,
                "display_name": display,
            })

    if not records:
        logger.warning("%s candidate tracking returned no federal candidate for election %s", state, election_id)
        return None
    logger.info("%s candidate tracking: %d federal candidates (election %s)", state, len(records), election_id)
    return records
