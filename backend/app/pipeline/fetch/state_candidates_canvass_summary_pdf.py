"""A state's official canvass of its partisan primary, published as a
summary PDF in which the state itself marks each contest's winner.

Wisconsin is the live case. Every page of elections.wi.gov answers a
server request with a bot challenge, which is why the state sat on
`google_civic` — but the Commission's document files are served from an
open path, and the certified canvass (WEC Canvass Reporting System,
"Canvass Results for 2026 Partisan Primary", generated 2026-08-27) is one
of them. It reads, per page:

    Office REPRESENTATIVE IN CONGRESS DISTRICT 1 Total Votes: 122,554
    Party: Republican Total Votes: 51,119
    Winner 50,915 99.6% Bryan Steil Republican
    204 .4%
    SCATTERING

The state's own "Winner" mark names the nominee, so nothing is derived
from vote totals here — the concern that keeps results PDFs out of this
pipeline (a name separated from its number) does not arise when the one
line that matters carries the winner mark, the count and the name
together. A "Winner" line with no name (text extraction does drop one now
and then) names nobody rather than the next name down the page; a
SCATTERING winner (write-ins totalled, a party with no candidate) is not
a person.

This is primary results, so it names party nominees and cannot see an
independent — the state stays general_ballot_complete: false, and the
page says "nominees", not "confirmed".

STATE OFFICES (`statewide_offices: true`). The same canvass carries the
state's executive contests (GOVERNOR, LIEUTENANT GOVERNOR, ATTORNEY
GENERAL, SECRETARY OF STATE, STATE TREASURER) and both legislative
chambers (STATE SENATOR DISTRICT 7, REPRESENTATIVE TO THE ASSEMBLY
DISTRICT 20), each under the same Office/Party/Winner shape, so they are
read by the same winner mark through parse_statewide_office and
parse_state_leg_office. Three details of the real 2026 file matter there
and did not for the 16 federal nominees:

- A name can sit up to 6pt above or below its own vote count, so
  plain text extraction puts it on a line of its own and the Winner line
  comes out nameless -- ten real nominees vanished that way (Christine M.
  Sinicki 4pt above her count, Elizabeth McCrank 6pt below hers). No
  single line tolerance fixes it: the same page has a row's SCATTERING
  label 7-9pt off its count and the next row's heading 8pt away. So
  lines are rebuilt from word positions (_page_lines): the count and
  heading column on the left anchors each row, and each run of words in
  the candidate and party columns joins the anchor nearest it -- and
  stays a line of its own when two anchors are nearly as near, so an
  ambiguous name is never given to the wrong row.
- The printed party trails the name, and one party is also a surname:
  "Chanz Green Republican". Exactly one trailing party label is
  stripped, never every party-looking word.
- A minor party's heading can wrap: "Party: REPRESENTATIVE TO THE
  ASSEMBLY DISTRICT 3 - Total Votes: 3" with "Constitution" on the next
  line. The next line is taken as the party only when it is a party and
  nothing else.

The file is found from the primary date (the FEC calendar supplies it):
`url_templates` are tried in order with {year}, {primary_month} and
{primary_date} filled in. Wisconsin renamed this file between cycles, so
a template is a best guess for the next one, not a promise; when none
resolves the source returns None and the state's `fallback` source runs.
"""

import logging
import re
from datetime import date
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    federal_record,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
)
from app.pipeline.fetch.state_election_dates import primary_date
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_OFFICE_RE = re.compile(r"^Office\s+(.+?)(?:\s+Total Votes:.*)?$")
_PARTY_RE = re.compile(r"^Party:\s+(.+?)(?:\s+Total Votes:.*|\s+[\d,]+)?$")
_WINNER_RE = re.compile(r"^Winner\s+[\d,]+\s+[\d.]+%\s*(?P<rest>.*)$")
# The candidate's party printed after the name, in the report's own
# words, longest first. "Wisconsin" alone is the Wisconsin Green label
# with "Green" wrapped onto the next line.
_PRINTED_PARTIES = (
    "wisconsin green", "wisconsin", "democratic", "democrat", "republican",
    "libertarian", "constitution", "green", "independent",
)
# The canvass's three column bands, in points (pdfplumber's unit): the
# label/count/percent column starts at the left margin, candidate names
# at x~257 and the printed party at x~461.
_CANDIDATE_X = 250
_PARTY_X = 455
# Words within this of each other vertically are one visual line.
_SAME_LINE = 3
# How far a name may sit from its row's count (the real file: 6pt), and
# how much nearer one row must be than the next for the name to be
# given to it. Rows are 10pt or more apart.
_ROW_REACH = 12
_ROW_MARGIN = 2


def _winner_name(rest: str) -> str | None:
    name = " ".join(rest.split())
    lowered = name.lower()
    for label in _PRINTED_PARTIES:
        if lowered.endswith(" " + label):
            name = name[: -len(label) - 1].rstrip()
            break
    if not name or name.upper() == "SCATTERING" or len(name.split()) < 2:
        return None
    return name


def _contest(label: str, state_offices: bool) -> tuple[str, str | None, str | None] | None:
    """(office, district, seat) for a contest this source may publish:
    federal always, the state's own offices only when opted in."""
    federal = parse_office(label)
    if federal is not None:
        return federal[0], federal[1], None
    if not state_offices:
        return None
    statewide = parse_statewide_office(label)
    if statewide is not None:
        return statewide[0], statewide[1], None
    return parse_state_leg_office(label)


def _party_only(line: str) -> str | None:
    """The party a wrapped heading continues with, when the line is a party
    label and nothing else."""
    if re.search(r"\d", line) or line.lower() not in _PRINTED_PARTIES:
        return None
    return normalize_party(line)


def parse_canvass(lines: list[str], state_offices: bool = False) -> list[dict]:
    """One record per (office, district, party) the state marks a named
    winner for: federal offices always, the state's executive offices and
    legislative seats when `state_offices`."""
    records: dict[tuple, dict] = {}
    office: tuple[str, str | int | None, str | None] | None = None
    party: str | None = None
    wrapped_party = False
    for raw in lines:
        line = raw.strip()
        if wrapped_party:
            wrapped_party = False
            continued = _party_only(line)
            if continued is not None:
                party = continued
                continue
        m = _OFFICE_RE.match(line)
        if m:
            office = _contest(m.group(1), state_offices)
            party = None
            continue
        m = _PARTY_RE.match(line)
        if m:
            # A minor party's heading repeats the office: "Party: REPRESENTATIVE
            # IN CONGRESS DISTRICT 1 - Constitution" -- or wraps before it.
            heading = m.group(1).strip()
            if heading.endswith(" -"):
                party, wrapped_party = None, True
                continue
            party = normalize_party(heading.rsplit(" - ", 1)[-1])
            continue
        m = _WINNER_RE.match(line)
        if not m or office is None or party is None:
            continue
        name = _winner_name(m.group("rest"))
        if name is None:
            continue
        code, district, seat = office
        key = (code, district, seat, party)
        if key in records:
            continue  # a page break repeats the office heading
        if code in ("S", "H"):
            record = federal_record(code, district, party, name)
        else:
            # A state office has no FEC row to match; the printed name is
            # kept whole.
            display = clean_display_name(name)
            record = {"office": code, "district": district, "party": party, "last_name": display} if display else None
            if record and seat is not None:
                record["seat"] = seat
        if record:
            records[key] = record
    return list(records.values())


def _visual_lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: w["top"]):
        if lines and word["top"] - lines[-1][-1]["top"] <= _SAME_LINE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return lines


def _top(line: list[dict]) -> float:
    return sum(w["top"] for w in line) / len(line)


def _page_lines(words: list[dict]) -> list[str]:
    """One text line per row of the page: each visual line of the left
    column is a row, and each visual line of the name/party columns joins
    the row it is unambiguously nearest (see the module docstring)."""
    rows = [
        {"top": _top(line), "left": line, "right": []}
        for line in _visual_lines([w for w in words if w["x0"] < _CANDIDATE_X])
    ]
    for line in _visual_lines([w for w in words if w["x0"] >= _CANDIDATE_X]):
        top = _top(line)
        nearest = sorted(rows, key=lambda r: abs(r["top"] - top))
        gap = abs(nearest[0]["top"] - top) if nearest else None
        if gap is not None and gap <= _ROW_REACH and (
            len(nearest) == 1 or abs(nearest[1]["top"] - top) - gap >= _ROW_MARGIN
        ):
            nearest[0]["right"].append(line)
        else:
            rows.append({"top": top, "left": [], "right": [line]})
    out = []
    for row in sorted(rows, key=lambda r: r["top"]):
        right = [w for line in sorted(row["right"], key=_top) for w in sorted(line, key=lambda w: w["x0"])]
        words_in_order = (
            sorted(row["left"], key=lambda w: w["x0"])
            + [w for w in right if w["x0"] < _PARTY_X]
            + [w for w in right if w["x0"] >= _PARTY_X]
        )
        out.append(" ".join(w["text"] for w in words_in_order))
    return out


def _lines(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return [ln for page in pdf.pages for ln in _page_lines(page.extract_words())]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    templates = source.get("url_templates") or []
    held = primary_date(state, year)
    if not templates or not held:
        logger.info("%s canvass: no url_templates or no primary date for %d", state, year)
        return None
    day = date.fromisoformat(held)
    for template in templates:
        url = template.format(year=year, primary_month=day.strftime("%B"), primary_date=held)
        payload = await fetch_bytes_with_retry(
            client, _rate_limiter, url, f"{state} primary canvass {year}", retry_on_4xx=False,
        )
        if not payload or not payload.startswith(b"%PDF"):
            continue
        records = parse_canvass(_lines(payload), bool(source.get("statewide_offices")))
        if any(r["office"] in ("S", "H") for r in records):
            logger.info("%s canvass %s: %d nominees", state, url, len(records))
            return records
        logger.warning("%s canvass %s parsed no federal winner", state, url)
    return None
