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

STATE OFFICES (`statewide_offices`). The same report, asked for the
Division's other office groups (`state_office_groups`: "CAB", Governor and
Cabinet; "LEG", Senate and House), lists the executive contests and both
chambers in the same shape, each heading ("Governor", "Chief Financial
Officer", "State Senator" + its District column) read through the shared
gates. Only "Qualified" is the ballot here: a state candidate the report
calls "Unopposed" was elected when qualifying closed and is not printed,
and a legislative section that counts seats contested must not count
theirs. A governor's line names the running mate after a slash; the
governor is the one shown, as for every other state. Florida's primaries
cannot supply this: it cancels a primary nobody contests, so its 2026
results never mention an Attorney General at all (James Uthmeier and Jose
Javier Rodriguez were both unopposed for their nominations).

A special election held with the general is a separate election id on the
Division's index ("20261103-S01", the 2026 State Senate District 21
vacancy) but the same November ballot, so under the opt-in each such id
found on `special_index_url` is read too, for state offices only.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.fec import general_election_day
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_text_with_retry, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    ballot_list_party,
    clean_display_name,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
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
# A state office's ballot: "Unopposed" there was elected at qualifying.
_STATE_ON_BALLOT = frozenset({"QUALIFIED"})


def _state_record(heading: str, district: int | None, name_cell: str) -> dict | None:
    """The statewide-executive or legislative record for one qualified
    candidate under `heading`, or None when the gates refuse it."""
    label = f"{heading} District {district}" if district is not None else heading
    statewide = parse_statewide_office(label)
    seat = None
    if statewide is not None:
        office, seat_district = statewide
    else:
        legislative = parse_state_leg_office(label)
        if legislative is None:
            return None
        office, seat_district, seat = legislative
    m = _NAME_RE.match(name_cell)
    if not m or m.group("party") == _WRITE_IN:
        return None
    party = ballot_list_party(m.group("party"))
    name = clean_display_name(f"{m.group('first')} {m.group('last').strip()}".replace("*Incumbent", ""))
    if party is None or len(name.split()) < 2:
        return None
    record = {"office": office, "district": seat_district, "party": party[0], "last_name": name}
    if seat is not None:
        record["seat"] = seat
    if party[1]:
        record["party_label"] = party[1]
    return record


def _cells(tr) -> list[str]:
    return [" ".join((td.text_content() or "").replace("\xa0", " ").split()) for td in tr.xpath("./td")]


def _state_rows(table, heading: str) -> list[dict]:
    """Every qualified state-office candidate in one office's table. A
    district office's table leads with a District column filled only on
    each district's first row, exactly as for the U.S. House."""
    records = []
    district = None
    for tr in table.xpath(".//tr[td]"):
        cells = _cells(tr)
        if len(cells) >= 5:
            if cells[0].isdigit():
                district = int(cells[0])
            cells = cells[1:]
        if len(cells) < 2 or cells[1].upper() not in _STATE_ON_BALLOT:
            continue
        record = _state_record(heading, district, cells[0])
        if record is not None:
            records.append(record)
    return records


def parse_canlist(page: str, state_offices: bool = False, federal: bool = True) -> list[dict]:
    """Candidates still on the general ballot, from the report: federal
    ones, and with `state_offices` the state executive and legislative
    ones too."""
    tree = lxml_html.fromstring(page)
    records = []
    for table in tree.xpath('//table[contains(@class, "results")]'):
        heading = table.xpath("preceding::b[1]")
        heading_text = " ".join((heading[0].text_content() or "").split()) if heading else ""
        office = parse_office(heading_text) if heading_text else None
        if office is None:
            if state_offices and heading_text:
                records += _state_rows(table, heading_text)
            continue
        if not federal:
            continue
        # A district office's table leads with a District column that is
        # filled only on each district's first row.
        district = office[1]
        for tr in table.xpath(".//tr[td]"):
            cells = _cells(tr)
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
    general_date = general_election_day(year).strftime("%Y%m%d")
    election_id = general_date + "-GEN"

    async def _report(elecid: str, group: str) -> str | None:
        resp = await fetch_with_retry(
            client, _rate_limiter, "POST", url, log_label=f"{state} candidate list {elecid} {group}",
            headers=BROWSER_HEADERS,
            data={"elecid": elecid, "OfficeGroup": group, "StatusCode": "ALL", "OfficeCode": "ALL",
                  "CountyCode": "ALL", "PartyCode": "ALL", "FormsButton1": "RUN QUERY"},
        )
        return None if resp is None else resp.text

    page = await _report(election_id, "FED")
    if page is None:
        return None
    records = parse_canlist(page)
    if not records:
        logger.warning("%s candidate list for %s had no qualified federal candidate", state, election_id)
        return None
    federal_count = len(records)
    if source.get("statewide_offices"):
        groups = source.get("state_office_groups") or []
        index_url = source.get("special_index_url")
        if not groups or not index_url:
            # Without them the opt-in would record the state as checked
            # from a report that holds only federal offices.
            logger.warning("%s dos_canlist statewide_offices needs state_office_groups and special_index_url", state)
            return None
        index = await fetch_text_with_retry(client, _rate_limiter, index_url, f"{state} candidate list index")
        if index is None:
            return None
        specials = sorted(set(re.findall(rf'value="?({general_date}-S\d+)', index)))
        for elecid, group in [(election_id, g) for g in groups] + [(sid, "ALL") for sid in specials]:
            page = await _report(elecid, group)
            if page is None:
                # A missing group would publish its offices as absent.
                return None
            part = parse_canlist(page, state_offices=True, federal=False)
            if not part and elecid == election_id:
                logger.warning("%s candidate list %s %s had no qualified state candidate", state, elecid, group)
                return None
            records += part
    logger.info(
        "%s candidate list %s: %d federal candidates on the ballot, %d state-office",
        state, election_id, federal_count, len(records) - federal_count,
    )
    return records
