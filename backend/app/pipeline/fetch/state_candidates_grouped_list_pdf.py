"""A state's general-election candidate list printed as a PDF with no
column headings: each office is a heading line, each candidate a line
under it, and a later change of status printed on the candidate's next line.

Illinois is the live case. Its State Board of Elections prints the filed
candidates for an election as a "Website Candidate List":

    1ST CONGRESS                                   <- office heading
    DEMOCRATIC    Jonathan L. Jackson   10/31/2025 10:00 AM
    ...
    INDEPENDENT   Mayra Macias          5/26/2026 5:00 PM
                  8445 S Kostner Ave    REMOVED 7/21/2026   <- off the ballot

so the party is the text left of `name_x`, the name the text between
`name_x` and `date_x`, a line with nothing left of `name_x` is a heading
when parse_office recognises it, and a candidate whose next line matches
`removed_regex` is dropped. Address lines are never read.

With the source's `statewide_offices` opt-in, the same Board prints its
STATE offices as two more office groups of the same shape, fetched from
`state_url_templates` (optional, one per group; unlike `url_templates` a
group with no federal heading is expected there). Each heading goes
through the shared gates (parse_statewide_office, then
parse_state_leg_office), after `heading_labels` -- a list of [regex,
replacement] pairs spelling out a state's own terse headings, since
Illinois numbers its seats "2ND SENATE" / "118TH REPRESENTATIVE", which
no shared parser should be taught to guess. A running mate's line
("Christian Mitchell (Pritzker)") has no party to its left and names its
governor's surname in parentheses; it is joined to that governor's record
as the ticket the ballot prints ("JB Pritzker and Christian Mitchell"). Every candidate not
struck off is on the November ballot, independents included; a party the
shared codes cannot name keeps its printed label (ballot_list_party).

The list's address carries the election id, which the state's own home
page links as its "Next Election"; `id_regex` finds it there each run, and
`year_regex` must find this year's general election in the PDF before a
row is read. Every URL in `url_templates` is required (one per office
group — Illinois prints the House and the Senate separately).
"""

import logging
import re
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows as clustered_rows
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import (
    ballot_list_party,
    clean_display_name,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")

# Points right of the name column a state-office heading must start. The
# shared statewide gate reads "Treasurer Rd" or "123 Governor Dr" as an
# office, and an address line sits exactly where a heading does in every
# other respect (nothing left of the name column, no filing date). What
# tells them apart is position: an address starts at the name column
# (x=155-161 on Illinois's 2026 lists, name_x 150), while every office
# heading is centred over the page (x=188 for the longest, GOVERNOR AND
# LIEUTENANT GOVERNOR, 230-260 for the rest).
_HEADING_INDENT = 20.0


def _state_heading(text: str, fmt: dict) -> tuple[str, str | None, str | None] | None:
    """(office, district, seat) for a state-office heading, or None. The
    state's own terse spelling is expanded by `heading_labels` first; the
    shared gates then make every decision."""
    for pattern, replacement in fmt.get("heading_labels") or []:
        text = re.sub(pattern, replacement, text)
    statewide = parse_statewide_office(text)
    if statewide is not None:
        return statewide[0], statewide[1], None
    return parse_state_leg_office(text)


def parse_grouped_list(pages: list[list[dict]], fmt: dict, state_offices: bool = False) -> list[dict]:
    """Federal candidates from each page's words (pdfplumber extract_words)
    -- and, with `state_offices`, statewide-executive and legislative ones
    under the headings the shared gates recognise."""
    name_x, date_x = float(fmt["name_x"]), float(fmt["date_x"])
    removed = re.compile(fmt["removed_regex"], re.IGNORECASE)
    lines: list[list[dict]] = []
    for words in pages:
        clustered = clustered_rows(words)
        lines += [sorted(clustered[k], key=lambda w: w["x0"]) for k in sorted(clustered)]

    records: list[dict] = []
    office: tuple[str, int | None] | None = None
    state_office: tuple[str, str | None, str | None] | None = None
    for i, line in enumerate(lines):
        party_text = " ".join(w["text"] for w in line if w["x0"] < name_x - 1)
        if not party_text and state_office is not None and _joins_ticket(lines, i, line, records, name_x, date_x, removed):
            continue
        if not party_text:
            # Only a line that names an office moves to it. A page's own
            # header ("ILLINOIS STATE BOARD OF ELECTIONS") and an address
            # line leave the office as it was, so a district that runs onto
            # the next page keeps its candidates; each list holds one office
            # group, so there is no other office to fall into.
            text = " ".join(w["text"] for w in line)
            heading = parse_office(text)
            if heading is not None:
                office, state_office = heading, None
            elif state_offices and line[0]["x0"] > name_x + _HEADING_INDENT:
                state_heading = _state_heading(text, fmt)
                if state_heading is not None:
                    office, state_office = None, state_heading
            continue
        if state_office is not None:
            record = _state_record(lines, i, line, party_text, state_office, name_x, date_x, removed)
            if record is not None:
                records.append(record)
            continue
        # A candidate's line carries its filing date in the date column; a
        # page's own header ("9/26/2026 5:21PM WEBSITE CANDIDATE LIST")
        # has text on the left too, but never there.
        if office is None or not any(
            w["x0"] >= date_x - 1 and _DATE_RE.match(w["text"]) for w in line
        ):
            continue
        name = clean_display_name(" ".join(w["text"] for w in line if name_x - 1 <= w["x0"] < date_x - 1))
        last = surname(name)
        if len(name.split()) < 2 or not last:
            continue
        if _struck(lines, i, name_x, removed):
            continue
        records.append({
            "office": office[0],
            "district": office[1],
            "party": normalize_party(party_text, ballot_list=True),
            "last_name": last,
            "display_name": name,
            "party_label": party_text,
        })
    return records


# A running mate's line: the mate's name, then the governor's surname in
# parentheses -- "Christian Mitchell (Pritzker)", "Aaron B. Del Mar
# (Bailey)" on Illinois's 2026 list.
_MATE_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<governor>[^()]+)\)$")


def _joins_ticket(
    lines: list[list[dict]], i: int, line: list[dict], records: list[dict],
    name_x: float, date_x: float, removed: re.Pattern,
) -> bool:
    """Append the running mate on line `i` to the governor above it, when
    it is one: a dated candidate line with no party, naming in parentheses
    the surname of the governor record just made. Illinois elects the two
    jointly (Ill. Const. art. V sec. 4), so the ticket is shown as the pair.
    A mate of a governor struck from the ballot names someone who was not
    recorded, and is passed over like an address line."""
    if not records or records[-1]["office"] != "governor":
        return False
    if not any(w["x0"] >= date_x - 1 and _DATE_RE.match(w["text"]) for w in line):
        return False
    text = " ".join(w["text"] for w in line if name_x - 1 <= w["x0"] < date_x - 1)
    m = _MATE_RE.match(text)
    governor = records[-1]["last_name"].split()
    if not m or not governor or m.group("governor").strip().casefold() != governor[-1].casefold():
        return False
    mate = clean_display_name(m.group("name"))
    if len(mate.split()) < 2 or " and " in records[-1]["last_name"] or _struck(lines, i, name_x, removed):
        return False
    records[-1]["last_name"] = f"{records[-1]['last_name']} and {mate}"
    return True


def _struck(lines: list[list[dict]], i: int, name_x: float, removed: re.Pattern) -> bool:
    """Whether the candidate on line `i` is marked off the ballot on the
    line below it (an address line, nothing to the left of the name)."""
    following = lines[i + 1] if i + 1 < len(lines) else []
    return bool(following) and not any(w["x0"] < name_x - 1 for w in following) and bool(
        removed.search(" ".join(w["text"] for w in following))
    )


def _state_record(
    lines: list[list[dict]], i: int, line: list[dict], party_text: str,
    state_office: tuple[str, str | None, str | None],
    name_x: float, date_x: float, removed: re.Pattern,
) -> dict | None:
    """The record one candidate line under a state-office heading makes, or
    None for a line that is not a live candidate. Same tests as a federal
    line: a filing date in the date column, a two-word name, not struck."""
    if not any(w["x0"] >= date_x - 1 and _DATE_RE.match(w["text"]) for w in line):
        return None
    name = clean_display_name(" ".join(w["text"] for w in line if name_x - 1 <= w["x0"] < date_x - 1))
    party = ballot_list_party(party_text)
    if len(name.split()) < 2 or party is None or _struck(lines, i, name_x, removed):
        return None
    code, district, seat = state_office
    record = {"office": code, "district": district, "party": party[0], "last_name": name}
    if seat is not None:
        record["seat"] = seat
    if party[1]:
        record["party_label"] = party[1]
    return record


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    needed = [k for k in ("page_url", "id_regex", "url_templates", "year_regex") if not discovery.get(k)]
    needed += [k for k in ("name_x", "date_x", "removed_regex") if not fmt.get(k)]
    if needed:
        logger.warning("%s grouped_list_pdf source is missing %s", state, needed)
        return None

    page = await fetch_text_with_retry(client, _rate_limiter, discovery["page_url"], f"{state} election page")
    if page is None:
        return None
    ids = set(re.findall(discovery["id_regex"], page))
    if len(ids) != 1:
        logger.info("%s election page links %d election ids for the list", state, len(ids))
        return None
    election_id = ids.pop()

    year_re = re.compile(discovery["year_regex"].replace("{year}", str(year)))
    state_offices = bool(source.get("statewide_offices"))
    if state_offices and not discovery.get("state_url_templates"):
        # The federal groups carry no state office: the opt-in would claim
        # a reading that cannot happen.
        logger.warning("%s grouped_list_pdf statewide_offices needs discovery.state_url_templates", state)
        return None
    groups = [(t, True) for t in discovery["url_templates"]]
    if state_offices:
        groups += [(t, False) for t in discovery["state_url_templates"]]
    records: list[dict] = []
    for template, federal_group in groups:
        url = template.replace("{id}", election_id)
        payload = await fetch_bytes_with_retry(
            client, _rate_limiter, url, f"{state} candidate list {year}", headers=BROWSER_HEADERS,
        )
        if payload is None or payload[:5] != b"%PDF-":
            return None
        try:
            with BytesIO(payload) as buf, pdfplumber.open(buf) as pdf:
                pages = [p.extract_words() for p in pdf.pages]
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception:
            logger.exception("%s candidate list PDF failed to parse", state)
            return None
        if not year_re.search(text):
            logger.info("%s candidate list is not for the %d general election", state, year)
            return None
        part = parse_grouped_list(pages, fmt, state_offices)
        if not part:
            # Every group is required, the state ones too: a Governor list
            # without its legislative one is half the state ballot, and the
            # opt-in would publish the other half as a confirmed absence.
            logger.warning(
                "%s candidate list %s had no %s candidate", state, url,
                "federal" if federal_group else "state-office",
            )
            return None
        records += part
    federal = sum(r["office"] in ("S", "H") for r in records)
    logger.info(
        "%s candidate list: %d federal candidates, %d state-office", state, federal, len(records) - federal,
    )
    return records
