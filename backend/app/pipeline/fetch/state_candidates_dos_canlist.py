"""Florida's Division of Elections candidate list for a general election —
every federal candidate with the state's own status for them.

Florida's primary-results file has no rows at all for a district whose
primaries were not held. Florida cancels a party primary when only one
candidate qualifies, so in 2026 five districts (8, 10, 18, 26, 28) had
nothing to read, and those pages fell back to every FEC filer. The
Division's candidate list answers directly: each candidate under an office
heading, with a status — Qualified, Defeated, Withdrew, Did Not Qualify —
so the November ballot is everyone still "Qualified" (plus a seat's sole
"Unopposed" candidate, elected without being printed), less declared
write-ins (party WRI), who are not printed either.

Shape (canlist.asp, form POST, read live 2026-09-26):

    <b>United States Senator</b>
    <table class="results"> ... <td>Moody, Ashley (REP) *Incumbent</td>
                               <td>Qualified</td><td>Won</td> ...

The election id is the general-election date ("20261103-GEN"), derived
from the statute, so nothing here names a cycle.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.fec import general_election_day
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    parse_office,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_NAME_RE = re.compile(r"^(?P<last>[^,]+),\s*(?P<first>.*?)\s*\((?P<party>[A-Z]{2,4})\)")
# "Unopposed" is a candidate Florida deems elected without printing them
# (Maxwell Frost, FL-10, 2026): the seat's only candidate, so shown as such
# rather than falling back to every FEC filer who withdrew.
_ON_BALLOT = frozenset({"QUALIFIED", "UNOPPOSED"})
_WRITE_IN = "WRI"


def parse_canlist(page: str) -> list[dict]:
    """Federal candidates still on the general ballot, from the report."""
    tree = lxml_html.fromstring(page)
    records = []
    for table in tree.xpath('//table[contains(@class, "results")]'):
        heading = table.xpath("preceding::b[1]")
        office = parse_office(" ".join((heading[0].text_content() or "").split())) if heading else None
        if office is None:
            continue
        # A district office's table leads with a District column that is
        # filled only on each district's first row.
        district = office[1]
        for tr in table.xpath(".//tr[td]"):
            cells = [" ".join((td.text_content() or "").replace("\xa0", " ").split()) for td in tr.xpath("./td")]
            if len(cells) >= 5:
                if cells[0].isdigit():
                    district = int(cells[0])
                cells = cells[1:]
            if len(cells) < 2 or cells[1].upper() not in _ON_BALLOT:
                continue
            if office[0] == "H" and district is None:
                continue
            m = _NAME_RE.match(cells[0])
            if not m or m.group("party") == _WRITE_IN:
                continue
            last = m.group("last").strip()
            display = clean_display_name(f"{m.group('first')} {last}".replace("*Incumbent", ""))
            records.append({
                "office": office[0],
                "district": district,
                "party": normalize_party(m.group("party"), ballot_list=True),
                "last_name": last,
                "display_name": display,
                "party_label": m.group("party"),
            })
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    url = source.get("url")
    if not url:
        logger.warning("%s dos_canlist source has no url", state)
        return None
    election_id = general_election_day(year).strftime("%Y%m%d") + "-GEN"
    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", url, log_label=f"{state} general candidate list",
        headers=BROWSER_HEADERS,
        data={"elecid": election_id, "OfficeGroup": "FED", "StatusCode": "ALL", "OfficeCode": "ALL",
              "CountyCode": "ALL", "PartyCode": "ALL", "FormsButton1": "RUN QUERY"},
    )
    if resp is None:
        return None
    records = parse_canlist(resp.text)
    if not records:
        logger.warning("%s candidate list for %s had no qualified federal candidate", state, election_id)
        return None
    logger.info("%s candidate list %s: %d federal candidates on the ballot", state, election_id, len(records))
    return records
