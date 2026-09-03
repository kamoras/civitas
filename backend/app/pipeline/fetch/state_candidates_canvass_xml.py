"""A statewide canvass XML export some states' election offices publish
over plain FTP — one <electionResult> document per election, contests
already split one-per-party (contestLongName ends "... (DEM)"/"(REP)"),
each carrying every choice's vote total and write-in flag (one of
potentially many per-state strategies in state_candidates.py; see that
module for the shared contract).

Generic to this FILE SHAPE, not to any one state: confirmed live against
Arizona (ftp.azsos.gov), found specifically because its main results
website sits behind Cloudflare bot-detection this codebase won't attempt
to defeat — the FTP host serving the same official canvass was never
protected in the first place, a different instance of the same "the
listing page is guarded but its file host isn't" pattern MN's adapter
already relies on. A second state publishing this same XML shape over an
open host earns a config entry here, never new code.

No vendor "official" flag exists in this format (unlike Enhanced
Voting's isOfficialResults) — the file the FTP host serves IS the
canvass, generated once per publish. Withholding instead checks that
every precinct has reported (precinctsReportingPercent="100.00" at the
statewide jurisdiction) and that resultsTimestamp is past the state's
own certification-deadline window (settle_days, config, same failsafe
role it plays for every other vendor here) — belt-and-suspenders against
an early or corrected post, not a specific vendor quirk.
"""

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import quote
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, surname

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_DAYS = 30


def _ftp_list(url: str) -> list[str]:
    """Entry names from a plain Unix-style FTP LIST response. A name may
    itself contain spaces ("2026 Primary Election") — str.split's default
    whitespace-collapsing means maxsplit=8 always leaves the 9th field as
    the whole remaining name, regardless of how it's padded."""
    with urlopen(url, timeout=30) as resp:  # noqa: S310 — FTP is the source's own protocol, not a redirect target
        text = resp.read().decode("utf-8", errors="replace")
    names = []
    for line in text.splitlines():
        parts = line.split(maxsplit=8)
        if len(parts) == 9:
            names.append(parts[8])
    return names


def _ftp_get(url: str) -> bytes | None:
    try:
        with urlopen(url, timeout=60) as resp:  # noqa: S310
            return resp.read()
    except OSError:
        logger.warning("FTP fetch failed for %s", url)
        return None


def _settled(root: ET.Element, settle_days: int) -> bool:
    jurisdictions = root.findall("./voterTurnout/jurisdictions/jurisdiction")
    state_jur = next((j for j in jurisdictions if j.get("name") == "State"), None)
    if state_jur is None or state_jur.get("precinctsReportingPercent") != "100.00":
        return False
    raw = root.findtext("./electionInformation/resultsTimestamp") or ""
    try:
        held_on = datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return False
    return (datetime.now(UTC).date() - held_on).days >= settle_days


def _nominee(contest: ET.Element) -> tuple[str, str] | None:
    """(choiceName, partyCode) for the top vote-getter among this
    single-party contest's real choices, or None. Write-ins are excluded
    the same way a blank/placeholder ballot line is elsewhere — a write-in
    total is real turnout, not a candidate anyone can be confirmed as."""
    best: tuple[str, str, int] | None = None
    for choice in contest.findall("./choices/choice"):
        if choice.get("isWriteIn") == "true":
            continue
        name = choice.get("choiceName") or ""
        party = choice.get("party") or ""
        try:
            votes = int(choice.get("totalVotes") or 0)
        except ValueError:
            votes = 0
        if not name or best is None or votes > best[2]:
            best = (name, party, votes)
    return (best[0], best[1]) if best else None


async def _fetch_document(year: int, state: str, source: dict) -> ET.Element | None:
    """The election folder's report, or None on any fetch/discovery
    failure — shared by nominee-confirmation and primary-date reading so
    "find this cycle's folder" exists in exactly one place."""
    discovery = source.get("discovery") or {}
    base = discovery.get("base_url")
    keyword = discovery.get("keyword")
    report_name = discovery.get("report_name", "Results.Summary.xml")
    if not base or not keyword:
        return None

    year_dir = base.format(year=year)
    try:
        entries = await asyncio.to_thread(_ftp_list, year_dir)
    except OSError:
        logger.warning("FTP directory listing failed for %s", year_dir)
        return None
    match = next((e for e in entries if keyword.lower() in e.lower()), None)
    if match is None:
        logger.info("No %r election folder found yet for %s %d", keyword, state, year)
        return None

    file_url = f"{year_dir.rstrip('/')}/{quote(match)}/{report_name}"
    raw = await asyncio.to_thread(_ftp_get, file_url)
    if raw is None:
        return None
    try:
        return ET.fromstring(raw)  # noqa: S314 — the state's own official canvass file, not user-supplied
    except ET.ParseError:
        logger.warning("Canvass XML for %s %d was not well-formed", state, year)
        return None


async def discover_primary_date(client, year: int, state: str, source: dict) -> dict:  # noqa: ARG001 — client unused, same reason as fetch_confirmed_candidates
    """{"primary": iso|None} read straight off the same document
    fetch_confirmed_candidates parses — this format states its own
    election's date plainly (electionInformation/electionDate), unlike a
    results-platform feed that needs a name pattern to pick the primary
    out of a list of elections."""
    root = await _fetch_document(year, state, source)
    if root is None:
        return {}
    raw = (root.findtext("./electionInformation/electionDate") or "")[:10]
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return {}
    return {"primary": raw}


async def fetch_confirmed_candidates(
    client, year: int, state: str, source: dict,  # noqa: ARG001 — client unused; this vendor is FTP, not the shared httpx client
) -> list[dict] | None:
    """Every confirmed federal nominee `state` has produced for `year`, or
    None on a fetch failure or when the cycle's election folder isn't
    posted yet — same tri-state discipline every other adapter here
    follows. Each item: {"office", "district", "party", "last_name"}."""
    root = await _fetch_document(year, state, source)
    if root is None:
        return None

    settle_days = (source.get("discovery") or {}).get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(root, settle_days):
        logger.info("%s %d canvass not yet settled — withholding", state, year)
        return []

    results = []
    for contest in root.findall(".//contests/contest"):
        parsed = parse_office(contest.get("contestLongName") or "")
        if parsed is None:
            continue
        office, district = parsed
        won = _nominee(contest)
        if won is None:
            continue
        choice_name, party_code = won
        party = normalize_party(party_code)
        if party is None:
            continue
        last_name = surname(choice_name, last_first=True)
        if not last_name:
            continue
        results.append({
            "office": office, "district": district, "party": party, "last_name": last_name,
        })
    return results
