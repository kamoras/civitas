"""South Dakota — the certified general-election ballot, not results.

South Dakota was configured for `totalvote_enr` and returned nothing for
months after its primary. The adapter is fine and the vendor is fine:
SD's election-night site serves whatever election is CURRENT, and South
Dakota held a runoff on 2026-07-28 that contained only Governor and
Secretary of State. The June primary's federal results are no longer in
the view that URL returns, and the site exposes no archive to reach them
(probed 2026-09-26: every other path 302s, no election picker, no
`eid` discoverable from the page). Montana and Nebraska run the
identical strategy and work precisely because neither had a runoff —
which is what makes this a runoff-state problem wearing a South Dakota
costume.

So this reads a different thing entirely. `vip.sdsos.gov/candidatelist.aspx`
is the Secretary of State's Voter Information Portal listing of who is
ON the November ballot — the exact question `confirmed_general` asks,
answered directly rather than inferred from primary vote totals. That
makes it a better source than the one it replaces, not merely a
substitute: nothing has to be reconstructed from counts, and a
general-only independent (Brian Bengs, IND, running for the US Senate
seat here) appears, where a primary-results reading structurally cannot
see one.

Two things the page does that this has to handle:

  * **Withdrawn candidates stay listed**, marked in the name itself
    ("Julian Beaudion (Withdrawn 08/04/2026)"). They are on the page and
    not on the ballot, so they are dropped — the one case where taking
    the page literally would be wrong.
  * **The grid is paged** — 745 rows across 15 pages, and Telerik pages
    by postback, so no GET reaches page 2. The grid is rendered in
    BALLOT ORDER, which puts the federal contests on page one. Rather
    than trust that, this verifies it: a page that yields no federal
    contest at all returns None (a real failure worth a warning), never
    an empty list that would read as "South Dakota has nobody running".

The election id is discovered from the SOS's own general-election page
rather than pinned here, so this does not need editing every cycle —
the same evergreen-discovery rule the ballot-measure PDF sources follow.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.state_candidates_common import (
    normalize_party,
    parse_office,
    parse_statewide_office,
    surname,
    clean_display_name,
)

logger = logging.getLogger(__name__)

# Where the election id is published. A landing page, not a dated
# document — the id changes every cycle and must never be pinned here.
_SOS_ELECTIONS_URL = "https://sdsos.gov/elections-voting/default.aspx"
_VIP_LIST_URL = "https://vip.sdsos.gov/candidatelist.aspx?eid={eid}"
_EID_RE = re.compile(r"candidatelist\.aspx\?eid=(\d+)", re.IGNORECASE)

# The suffix the portal appends to a name it still lists but that is off
# the ballot. Matched case-insensitively on the word alone so a change to
# the date format cannot silently let a withdrawal through.
_WITHDRAWN_RE = re.compile(r"\(\s*withdrawn\b", re.IGNORECASE)

_TIMEOUT = 30.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; civitas/1.0)"}


async def _get_text(client: httpx.AsyncClient, url: str, label: str) -> str | None:
    try:
        resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("SD VIP: %s failed (%s)", label, exc)
        return None


def _discover_eid(html: str) -> str | None:
    """The newest election id the SOS links to.

    Several may be linked (a past election alongside the upcoming one);
    the highest id is the most recent, which is the same
    largest-is-newest rule the other discovery sources here use.
    """
    ids = {m.group(1) for m in _EID_RE.finditer(html)}
    if not ids:
        return None
    return max(ids, key=int)


def _rows(html: str) -> list[tuple[str, str, str]]:
    """(office, raw candidate name, raw party) for every data row."""
    tree = lxml_html.fromstring(html)
    grids = tree.xpath('//table[contains(@class,"rgMasterTable")]')
    if not grids:
        return []
    out = []
    for tr in grids[0].xpath(".//tr"):
        cells = [(td.text_content() or "").strip() for td in tr.xpath("./td")]
        # office | name | party | filed | ... — the leading "select"
        # control is its own row in this grid, not a cell on every row.
        if len(cells) < 3:
            continue
        office, name, party = cells[0], cells[1], cells[2]
        if not office or not name:
            continue
        out.append((office, name, party))
    return out


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    eid = source.get("eid")
    if not eid:
        landing = await _get_text(client, _SOS_ELECTIONS_URL, "SOS elections page")
        if landing is None:
            return None
        eid = _discover_eid(landing)
        if not eid:
            logger.warning("SD VIP: no candidatelist eid linked from the SOS elections page")
            return None

    html = await _get_text(client, _VIP_LIST_URL.format(eid=eid), f"candidate list eid={eid}")
    if html is None:
        return None

    rows = _rows(html)
    if not rows:
        logger.warning("SD VIP: candidate grid missing or empty for eid=%s", eid)
        return None

    want_statewide = bool(source.get("statewide_offices"))
    records: list[dict] = []
    federal_seen = 0
    withdrawn = 0
    for office_text, raw_name, raw_party in rows:
        if _WITHDRAWN_RE.search(raw_name):
            withdrawn += 1
            continue
        # A certified ballot, not primary results — an independent on it
        # is an ordinary entry, not an unreadable label.
        party = normalize_party(raw_party, ballot_list=True)

        federal = parse_office(office_text)
        if federal:
            office, district = federal
            last = surname(raw_name)
            if not last:
                continue
            federal_seen += 1
            records.append({
                "office": office,
                "district": district,
                "party": party,
                "last_name": last,
            })
            continue

        # Same record shape as the federal rows — the caller tells them
        # apart by office code, and _sync_statewide_nominees reads the
        # display name out of `last_name` exactly as the other adapters
        # leave it. Gated on the state opting in, because that flag means
        # "this feed is the WHOLE truth for statewide offices" and a
        # partial list would retire real nominees.
        if not want_statewide:
            continue
        statewide = parse_statewide_office(office_text)
        if statewide:
            office_key, _seat = statewide
            name = clean_display_name(raw_name)
            if not name:
                continue
            records.append({
                "office": office_key,
                "district": None,
                "party": party,
                "last_name": name,
            })

    if not federal_seen:
        # Ballot order puts the federal contests on page one. None here
        # means the assumption broke (a re-sort, a redesign, a wrong
        # election) — a warning worth seeing, not an empty list that
        # would read as "nobody is running for Congress in South Dakota".
        logger.warning(
            "SD VIP: no federal contest on page 1 of eid=%s (%d rows, %d withdrawn) "
            "— grid order or election id may have changed",
            eid, len(rows), withdrawn,
        )
        return None

    logger.info(
        "SD VIP: %d records from eid=%s (%d federal, %d withdrawn dropped)",
        len(records), eid, federal_seen, withdrawn,
    )
    return records
