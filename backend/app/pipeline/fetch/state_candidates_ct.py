"""Connecticut's confirmed-general-candidate strategy — the Secretary of
State's own election-night-reporting vendor ("TGS ENR", an AngularJS
single-page app at ctemspublic.tgstg.net backed by a set of static,
version-numbered JSON files, not a queried API), reached with no login
and no bot-detection friction at all (a plain unauthenticated GET
succeeds — verified live 2026-09-04). Not found on any other state
probed (guessed subdomains for a dozen other uncovered states all failed
DNS resolution), so this is written as a single-state module for now,
the same way KY/IN/AL/MS/AR/KS each started.

FOUR calls, nothing cycle-specific hardcoded:

1. GET .../ng-app/data/Elections.json lists every election back to ~2021
   with a name and an id, newest first — the year's Democratic and
   Republican statewide primaries are found by matching each entry's
   "MM/DD/YYYY -- ..." name against the target year, requiring the month
   be AUGUST (Connecticut's regular state primary is fixed by statute —
   Conn. Gen. Stat. Sec. 9-423 — as the second Tuesday of August every
   even year, the same class of legally-grounded assumption this system
   already relies on elsewhere, e.g. "no state legislature is called
   Congress"), and requiring "Democratic Primary"/"Republican Primary"
   appear in the name while "special" does not — real named SPECIAL
   primaries for a single district (a Bridgeport special primary, a
   legislative-vacancy primary) share the exact same "-- ... Primary"
   suffix shape and would otherwise collide. The exact wording around
   the party name isn't stable year to year (2026: "-- Democratic
   Primary"; 2024: "-- August 2024 Democratic Primary") — verified live
   against both — so the match is a substring check, not an exact
   pattern.
2. GET .../ng-app/data/election/{id}/Version.json gives the dataset's
   current version number — this vendor updates results IN PLACE at the
   same URL as the count changes (confirmed live: the version number for
   the SAME election advanced between two fetches minutes apart), so the
   version is read fresh every call rather than cached.
3. GET .../ng-app/data/election/{id}/{version}/Lookupdata.json carries
   office and candidate NAMES, keyed by opaque numeric ids. A federal
   House contest's office entry has OT (office type) "C" for Congress —
   a literal type CODE, not a text label to parse — with its district
   number already isolated in its own "D" field ("Connecticut 01" in DT,
   "1" in D). No Senate seat ever appears here because Connecticut's two
   Senate seats are on staggered 6-year terms and neither is up in an
   even year that isn't 2028/2030 — a real structural absence, not a
   parsing gap.
4. GET .../ng-app/data/election/{id}/{version}/stateVotes_Electiondata.json
   carries the actual vote totals, keyed by the SAME office/candidate ids
   Lookupdata.json uses.

An entire election (one Democratic, one Republican) is scoped to ONE
party — Connecticut's ballot never mixes them the way a state that
prints "D-Name"/"R-Name" prefixes does — so party comes from the
election's own Lookupdata.election.P field ("Democratic Party" /
"Republican Party"), read once per election rather than per candidate.

Connecticut nominates on a PLURALITY — Conn. Gen. Stat. Sec. 9-433 sets
no runoff or majority requirement for a party primary — so
`runoff_threshold_pct: null`.

The vendor publishes no certification flag anywhere (confirmed empty
across every real response captured, and the version-number churn
observed live during this same research session is itself evidence
results are still being updated after polls close) — the same shape as
Arkansas/Tennessee/Florida in this system, so a nominee is confirmed
only once `settle_days` has passed since the matched election's own
date (reusing `_settled` from state_candidates_tabular.py rather than
re-deriving the same freshness rule a fourth time).

STATEWIDE OFFICES (with `statewide_offices` set) come from two places,
because a Connecticut nominee usually never faces a primary at all: the
party-endorsed candidate is the nominee unless a challenger with 15% of
the convention vote forces one. An office a party PRIMARIED is read from
that primary's own "SW" contest; every other office from the Secretary
of the State's certificates of party endorsement (see parse_endorsements
below). Legislative seats are not read: the primary results carry only
the handful of primaried seats and the endorsements are one PDF per
district.

The August requirement is empirically load-bearing today, not
decoration: checked against the real, unfiltered election list back to
2016, year + party-phrase + non-special alone is NOT enough to
disambiguate in every year -- 2021 also carries a real "Judge of Probate
8th Democratic Primary" and 2026 a real "September 1st Democratic
Primary", both of which share the exact "-- ... Democratic Primary"
substring shape and would otherwise collide with the actual statewide
primary. That said, Connecticut HAS moved this date by statute before
(from September to August, 2013, for MOVE Act compliance) and could
again -- a future such change would make this filter silently reject
the real primary and report the cycle as "not yet published" with no
error. A disclosed, real limitation, not a proven-impossible one.
"""

import asyncio
import html
import logging
import re
from io import BytesIO
from urllib.parse import urljoin

import httpx
import pdfplumber
from pdfplumber.utils import extract_words

from app.pipeline.fetch.http_utils import fetch_json_with_retry, fetch_text_with_retry, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    federal_only,
    federal_record,
    normalize_party,
    office_from_columns,
    parse_statewide_office,
    pick_nominee,
)
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://ctemspublic.tgstg.net/ng-app/data"

_ELECTION_NAME_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+--\s+(.*)$")
_OFFICE_SPEC = {"type_column": "OT", "type_value": "C", "district_column": "D"}

_rate_limiter = RateLimiter(rps=1.0)


def _find_primary(elections: list[dict], year: int, party_phrase: str) -> dict | None:
    """The one real statewide {party} primary this `year` -- August only,
    never a named special -- or None if it isn't listed (not yet
    scheduled, or a cycle with no such primary)."""
    for e in elections:
        m = _ELECTION_NAME_RE.match(e.get("Name") or "")
        if not m:
            continue
        month, _day, y, rest = m.groups()
        if int(y) != year or int(month) != 8:
            continue
        if "special" in rest.lower():
            continue
        if party_phrase.lower() not in rest.lower():
            continue
        return {"id": e.get("ID"), "date": f"{y}-{month}-{_day}"}
    return None


async def _party_results(
    client: httpx.AsyncClient, election_id: str, year: int, statewide: bool = False,
) -> tuple[list[dict], set[str]] | None:
    """Every confirmed nominee ONE party's primary decides, plus the
    statewide offices that party's primary CONTESTED (whether or not a
    winner could be named), or None on a real fetch failure.

    Statewide contests are read only when `statewide` is set. Their office
    type is the vendor's own code "SW" (Governor, verified live on the
    2026 Democratic primary), and the label is still put through
    parse_statewide_office rather than trusted from the code alone. The
    contested set matters more than the winners: an office this party
    primaried is decided HERE, so its convention endorsement is no longer
    the nominee -- even when a tie leaves this module unable to name one.
    """
    version = await fetch_json_with_retry(
        client, _rate_limiter, f"{BASE}/election/{election_id}/Version.json", f"CT election version {year}",
    )
    if not isinstance(version, dict) or not version.get("Version"):
        return None
    v = version["Version"]

    lookup = await fetch_json_with_retry(
        client, _rate_limiter, f"{BASE}/election/{election_id}/{v}/Lookupdata.json", f"CT lookup data {year}",
    )
    if not isinstance(lookup, dict):
        return None
    party = normalize_party((lookup.get("election") or {}).get("P") or "")
    if party is None:
        return None
    congress_races: dict[str, int | None] = {}
    statewide_races: dict[str, tuple[str, str | None]] = {}
    for entry in lookup.get("officeList") or []:
        for oid, office in entry.items():
            office_district = office_from_columns(office, _OFFICE_SPEC)
            if office_district is not None:
                congress_races[oid] = office_district[1]
                continue
            if statewide and office.get("OT") == _STATEWIDE_TYPE:
                parsed = parse_statewide_office(office.get("NM") or "")
                if parsed is not None:
                    statewide_races[oid] = parsed
    if not congress_races and not statewide_races:
        return [], set()  # nothing this module reads on this party's ballot
    candidates = lookup.get("candidateIds") or {}

    votes = await fetch_json_with_retry(
        client, _rate_limiter, f"{BASE}/election/{election_id}/{v}/stateVotes_Electiondata.json", f"CT vote totals {year}",
    )
    if not isinstance(votes, dict):
        return None

    def _choices(office_id: str) -> list[tuple[str, int]]:
        choices = []
        for choice in votes.get(office_id) or []:
            for choice_id, vote in choice.items():
                try:
                    vote_count = int(vote.get("V"))
                except (TypeError, ValueError):
                    continue
                choices.append((candidates.get(choice_id, {}).get("NM") or "", vote_count))
        return choices

    records = []
    for office_id, district in congress_races.items():
        won = pick_nominee(_choices(office_id), runoff_threshold_pct=None)
        record = federal_record("H", district, party, won[0]) if won else None
        if record:
            records.append(record)
    contested: set[str] = set()
    for office_id, (code, seat) in statewide_races.items():
        contested.add(code)
        won = pick_nominee(_choices(office_id), runoff_threshold_pct=None)
        name = clean_display_name(won[0]) if won else ""
        if name:
            records.append({"office": code, "district": seat, "party": party, "last_name": name})
    return records, contested


async def _party_nominees(
    client: httpx.AsyncClient, election_id: str, year: int,
) -> list[dict] | None:
    """Every confirmed federal House nominee ONE party's primary
    decides, or None on a real fetch failure."""
    result = await _party_results(client, election_id, year)
    return None if result is None else result[0]


# ── Convention endorsements (statewide offices) ─────────────────────
#
# A Connecticut party nominates its statewide ticket at a CONVENTION. The
# endorsed candidate is the nominee unless someone who won 15% of the
# convention vote forces a primary, and most years nobody does: in 2026
# the Democratic primary contested only Governor and the Republican
# primary no statewide office at all. So primary results alone would show
# one statewide contest out of six. The rest of the ballot is in the
# Secretary of the State's own "Certificate of Party Endorsement, 15%
# Eligibility, or Nomination" filings (form ED-1639), which it posts as
# one combined PDF per party.
#
# The form is filled electronically, so its answers are real text -- but
# printed OVER the form's own labels and tab-stop spaces, which split
# "Treasurer" into "Tre asurer" when read as one line. The answers are
# instead read by FONT: every character in the form's own typeface is
# the form, everything else is what the filer typed. The checkboxes are
# drawn marks, not form fields (the PDF has been flattened), so a box is
# checked when a drawn mark sits inside its square, and it means the
# word printed to its right.

_ENDORSED = "Endorsed"
_OTHER_CERTIFICATES = {"15%", "Nominated"}
_STATEWIDE_TYPE = "SW"


def _row(words: list[dict], text: str) -> dict | None:
    return next((w for w in words if w["text"] == text), None)


def _typed_between(page, words: list[dict], upper: str, lower: str) -> str | None:
    """What the filer typed between the form's `upper` and `lower` label
    rows, or None when this page does not carry both labels (a
    continuation or signature page)."""
    top, bottom = _row(words, upper), _row(words, lower)
    if top is None or bottom is None or top["top"] >= bottom["top"]:
        return None
    form_fonts = {
        c["fontname"] for c in page.chars
        if c["text"].strip() and any(abs(c["top"] - r["top"]) < 1 for r in (top, bottom))
    }
    typed = [
        c for c in page.chars
        if top["bottom"] <= c["top"] < bottom["top"] and c["fontname"] not in form_fonts
    ]
    return " ".join(w["text"] for w in extract_words(typed)).strip()


def _checked_labels(page, words: list[dict]) -> set[str]:
    """The word printed beside every checkbox that carries a drawn mark."""
    marks = page.curves + page.lines
    checked = set()
    for box in page.rects:
        if not (8 <= box["width"] <= 18 and 8 <= box["height"] <= 18):
            continue
        # A mark smaller than the box in both directions, so that the
        # box's own edges (drawn as lines on some forms) never count.
        inside = any(
            m["x0"] >= box["x0"] - 2 and m["x1"] <= box["x1"] + 2
            and m["top"] >= box["top"] - 2 and m["bottom"] <= box["bottom"] + 2
            and m["width"] <= box["width"] - 2 and m["height"] <= box["height"] - 2
            for m in marks
        )
        if not inside:
            continue
        middle = (box["top"] + box["bottom"]) / 2
        beside = sorted(
            (w for w in words
             if abs((w["top"] + w["bottom"]) / 2 - middle) < 6 and w["x0"] >= box["x1"] - 1),
            key=lambda w: w["x0"],
        )
        if beside:
            checked.add(beside[0]["text"])
    return checked


def parse_endorsements(pdf_bytes: bytes) -> list[dict]:
    """Every ENDORSED statewide nominee in one certificate PDF, as
    {"office", "district", "party", "last_name"}. A 15%-eligibility
    certificate is not a nomination (it is the right to force a primary)
    and is skipped; so is any page whose party or office can't be read,
    or whose office isn't statewide."""
    records = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            office_text = _typed_between(page, words, "Party:", "Registrar")
            name = _typed_between(page, words, "Information:", "authorize")
            if not office_text or not name:
                continue
            checked = _checked_labels(page, words)
            # Exactly one kind of certificate: "Endorsed" alone. A form
            # with "15%" or "Nominated" also marked says two things.
            if _ENDORSED not in checked or checked & _OTHER_CERTIFICATES:
                continue
            parties = {p for p in (normalize_party(label) for label in checked) if p}
            if len(parties) != 1:
                continue
            office = parse_statewide_office(office_text)
            name = clean_display_name(name)
            if office is None or not name:
                continue
            records.append({
                "office": office[0], "district": office[1],
                "party": parties.pop(), "last_name": name,
            })
    return records


def _links(page: str, pattern: str, year: int) -> set[str]:
    regex = pattern.replace("{year}", str(year))
    return {html.unescape(m.group(1)) for m in re.finditer(regex, page)}


async def _endorsed_nominees(client: httpx.AsyncClient, year: int, spec: dict) -> list[dict] | None:
    """Every endorsed statewide nominee for `year`, or None when the
    certificates can't be found or read. Two hops, nothing pinned: the
    endorsement index links one page per year (its slug is spelled
    differently from year to year), and that page links the combined
    statewide certificates."""
    index_url = spec.get("index_url")
    if not index_url:
        return None
    index = await fetch_text_with_retry(client, _rate_limiter, index_url, f"CT endorsement index {year}")
    if index is None:
        return None
    year_pages = _links(index, spec.get("year_page_regex") or "", year)
    if len(year_pages) != 1:
        logger.info("CT endorsement index links %d pages for %d", len(year_pages), year)
        return None
    year_url = urljoin(index_url, year_pages.pop())
    page = await fetch_text_with_retry(client, _rate_limiter, year_url, f"CT endorsements {year}")
    if page is None:
        return None
    pdf_urls = sorted(_links(page, spec.get("statewide_link_regex") or "", year))
    if not pdf_urls:
        logger.info("CT endorsement page for %d links no statewide certificates", year)
        return None

    records: list[dict] = []
    for url in pdf_urls:
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", urljoin(year_url, url), timeout=60.0,
            log_label=f"CT statewide endorsements {year}",
        )
        if resp is None:
            return None
        try:
            records.extend(await asyncio.to_thread(parse_endorsements, resp.content))
        except Exception:  # noqa: BLE001 - an unreadable PDF is a skip, not a crash
            logger.warning("CT endorsement certificate %s was not a readable PDF", url)
            return None
    return records


def _merge_statewide(
    primaried: dict[str, set[str]],
    primary_records: list[dict], endorsed: list[dict],
) -> list[dict]:
    """The statewide ballot: an office a party PRIMARIED is decided by that
    primary's result; any other office goes to the party's endorsed
    candidate. Only called once every primary that was held has settled
    (before then the state offices are marked incomplete), and two
    endorsements for one party's office publish neither."""
    by_seat: dict[tuple[str, str | None, str], list[str]] = {}
    for rec in endorsed:
        by_seat.setdefault((rec["office"], rec["district"], rec["party"]), []).append(rec["last_name"])
    records = list(primary_records)
    for (office, district, party), names in by_seat.items():
        if office in primaried.get(party, set()):
            continue
        if len(set(names)) != 1:
            logger.warning("CT: %d endorsements for the %s %s nomination", len(set(names)), party, office)
            continue
        records.append({"office": office, "district": district, "party": party, "last_name": names[0]})
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is CT-only by construction
) -> list[dict] | None:
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    statewide = bool(source.get("statewide_offices"))
    elections = await fetch_json_with_retry(
        client, _rate_limiter, f"{BASE}/Elections.json", f"CT election list {year}",
    )
    if not isinstance(elections, list):
        return None

    dem = _find_primary(elections, year, "Democratic Primary")
    rep = _find_primary(elections, year, "Republican Primary")
    if dem is None and rep is None:
        return []  # not published yet this cycle — healthy unknown

    pending = {
        party for primary, party in ((dem, "D"), (rep, "R"))
        if primary is not None and not _settled(primary["date"], settle_days)
    }

    results: list[dict] = []
    statewide_results: list[dict] = []
    primaried: dict[str, set[str]] = {}
    for primary, party in ((dem, "D"), (rep, "R")):
        if primary is None:
            continue  # this party held no primary: every endorsement stands
        if party in pending:
            continue  # too soon to trust the count
        party_results = await _party_results(client, primary["id"], year, statewide=statewide and not pending)
        if party_results is None:
            return None
        records, contested = party_results
        results.extend(r for r in records if r["office"] == "H")
        statewide_results.extend(r for r in records if r["office"] != "H")
        primaried[party] = contested

    if pending:
        # A primary that was held is still inside its settle window. The
        # settled party's House nominees stand, but its statewide list
        # alone would be taken for the whole ballot -- the sync deletes
        # every stored nominee it does not name and records the state as
        # checked -- so the pending party's nominees (endorsed or
        # primaried) would vanish. The state offices are marked
        # incomplete instead, the same rule Alabama follows while a
        # runoff is owed.
        logger.info("CT: the %d %s primary has not settled yet", year, "/".join(sorted(pending)))
        return federal_only(results) if statewide else results

    if statewide:
        endorsed = await _endorsed_nominees(client, year, source.get("endorsements") or {})
        if endorsed is None:
            # Without the certificates the statewide list would hold only
            # the primaried offices, and the page would read the rest as
            # absent. Fail the run rather than publish that.
            logger.warning("CT statewide endorsements for %d could not be read", year)
            return None
        results.extend(_merge_statewide(primaried, statewide_results, endorsed))
    return results
