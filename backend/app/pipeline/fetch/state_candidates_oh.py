"""A Secretary of State's official primary canvass published as one
summary workbook PER PARTY, listed in a JSON index of its public files --
Ohio's "Summary Level Official Results" (publicfiles.ohiosos.gov). A
single-state publication, so a vendor module in the same sense
state_candidates_wy.py is; everything that locates it is configuration:

    "index_url":    the files index (JSON: pastElectionResults -> year ->
                    elections -> fileGroups -> files{displayName, blobPath})
    "files_base":   what each blobPath is relative to
    "election_regex": which of the year's elections is the primary
                    ("Primary/Special Election - May 5, 2026")
    "file_regex":   which files are the per-party summaries, with {year}
                    and ONE capture group naming the party ("... 2026
                    Primary Election - Republican"); the county-officials-
                    only and precinct-level files do not match
    "title_regex":  what each workbook's own first cell must say before a
                    row is read ("May 5, 2026 Primary/Special Election
                    Official Canvass" -- {year} slot), so a mislabelled or
                    unofficial file is refused rather than trusted from its
                    index entry
    "sheets":       the sheets read, by name ("U.S. Congress", "Statewide
                    Offices", "General Assembly"); the rest (judges, party
                    central committees, the Master sheet that repeats them
                    all) are never opened

Found 2026-09-28 by reading the Secretary of State's data portal
(data.ohiosos.gov/portal), whose script names this index; the portal page
itself and www.ohiosos.gov serve the same files' links. Verified live from
a plain fetch that day, standard request headers: the May 5, 2026 primary's
three summary workbooks (Democratic, Libertarian, Republican).

THE SHAPE. Each sheet is a wide cross-tab: row 1 names each contest once,
in the first column of its group (blank across the rest of the group --
forward-filled here); the "County Name" row names every candidate as
"Jon Husted (R)", a write-in as "Linda Matthews (WI)* (R)"; then one row
per county, and one "Total" row -- read directly, exactly one required,
as Wyoming's is. The header row is found by its content ("County Name" in
the first cell), never a fixed index.

PARTY comes from the file (each workbook is one party's primary), and a
candidate whose own "(X)" disagrees with the file's party is skipped
rather than guessed. The winner of each contest is the shared, tie-safe
pick_nominee over the Total row; Ohio nominates by plurality (no runoff).
A write-in who led a contest where nobody was printed is withheld: Ohio
nominates them only with as many votes as the office's petition
signatures (R.C. 3513.23), which the canvass does not print. A write-in
who beat a printed candidate is nominated like anyone else.
Uncontested nominees are printed with their votes (Jim Jordan's
district 4), so they are read like any other.

Every matched workbook is required: one party's file failing would drop
that party's nominees, and an empty party read as "nobody filed".

With `statewide_offices` the same sheets' executive contests ("Governor
and Lieutenant Governor", printed as one ticket, "Attorney General",
"Auditor of State", "Secretary of State", "Treasurer of State") and
General Assembly seats are read through the shared gates. Primary results
name each party's winner only: independents, and a nominee a party
committee named after the primary (ORC 3513.31), are not in these files.
"""

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from urllib.parse import quote

import httpx

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    federal_record,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    pick_nominee,
    runoff_threshold,
)
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_HEADER_LABEL = "county name"
_TOTAL_LABEL = "total"
# "Jon Husted (R)", "Linda Matthews (WI)* (R)": the trailing code is the
# candidate's party; "(WI)*" marks a certified write-in.
_CANDIDATE_RE = re.compile(r"^(?P<name>.+?)\s*(?:\(WI\)\*?\s*)?\((?P<party>[A-Z]{1,3})\)\s*$")
_TITLE_DATE_RE = re.compile(r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def sheet_rows(workbook: bytes, names: list[str]) -> dict[str, list[list[str]]] | None:
    """Each named sheet's rows as lists of cell text, placed by each cell's
    own reference so a blank cell the writer omitted never shifts a vote
    total into the next candidate's column. A configured sheet the
    workbook lacks is simply absent (the Libertarian workbook has no
    judges' sheet). None when the file is not a readable workbook."""
    try:
        book = zipfile.ZipFile(io.BytesIO(workbook))
        shared = [
            "".join(t.text or "" for t in si.iter(f"{_XL}t"))
            for si in ET.fromstring(book.read("xl/sharedStrings.xml"))
        ] if "xl/sharedStrings.xml" in book.namelist() else []
        targets = {
            rel.get("Id"): rel.get("Target") or ""
            for rel in ET.fromstring(book.read("xl/_rels/workbook.xml.rels")).iter(f"{_REL}Relationship")
        }
        found = {}
        for sheet in ET.fromstring(book.read("xl/workbook.xml")).iter(f"{_XL}sheet"):
            if sheet.get("name") not in names:
                continue
            target = targets.get(sheet.get(f"{_R}id"), "").lstrip("/")
            path = target if target.startswith("xl/") else f"xl/{target}"
            found[sheet.get("name")] = ET.fromstring(book.read(path))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        logger.warning("Primary canvass download was not a readable workbook")
        return None

    def text(c) -> str:
        if c.get("t") == "inlineStr":
            return "".join(t.text or "" for t in c.iter(f"{_XL}t"))
        v = c.find(f"{_XL}v")
        if v is None or v.text is None:
            return ""
        if c.get("t") == "s":
            try:
                return shared[int(v.text)]
            except (ValueError, IndexError):
                return ""
        return v.text

    out = {}
    for name, root in found.items():
        rows = []
        for row in root.iter(f"{_XL}row"):
            cells: list[str] = []
            for c in row.iter(f"{_XL}c"):
                at = _col(c.get("r"))
                cells.extend([""] * (at + 1 - len(cells)))
                cells[at] = text(c)
            rows.append(cells)
        out[name] = rows
    return out


def contest_totals(rows: list[list[str]], party: str) -> list[tuple[str, str, int, bool]] | None:
    """(contest label, printed name, votes, write-in) for every candidate column of
    one sheet of `party`'s workbook. None when the sheet's header or Total
    row cannot be found (a changed layout, never a quiet empty answer)."""
    at = next((i for i, r in enumerate(rows) if r and r[0].strip().lower() == _HEADER_LABEL), None)
    totals = [r for r in rows if r and r[0].strip().lower() == _TOTAL_LABEL]
    if not at or len(totals) != 1:
        logger.warning("Primary canvass sheet has no header row above a single Total row")
        return None
    contests, names, total = rows[at - 1], rows[at], totals[0]
    out, contest = [], ""
    for i in range(1, len(names)):
        if i < len(contests) and contests[i].strip():
            contest = " ".join(contests[i].split())
        m = _CANDIDATE_RE.match(" ".join(names[i].split()))
        if not contest or not m:
            continue
        if normalize_party(m.group("party")) != party:
            logger.info("Skipping %r: printed party is not the workbook's", names[i])
            continue
        votes = (total[i] if i < len(total) else "").strip()
        if not votes.isdigit():
            continue
        out.append((contest, m.group("name"), int(votes), "(WI)" in names[i]))
    return out


def _records(
    by_contest: dict[tuple[str, str], list[tuple[str, int]]], write_ins: set[tuple[str, str, str]],
    threshold, state_offices: bool,
) -> list[dict]:
    records = []
    for (contest, party), choices in by_contest.items():
        won = pick_nominee(choices, threshold)
        if not won:
            continue
        if all((contest, party, name) in write_ins for name, _ in choices):
            # Nobody was printed for this nomination, so a write-in is
            # nominated only with at least as many votes as the petition
            # signatures the office requires (R.C. 3513.23) -- a number
            # the canvass does not print. Withheld rather than guessed:
            # the Libertarian "nominee" for one legislative seat had 3.
            continue
        name = clean_display_name(won[0])
        federal = parse_office(contest)
        if federal is not None:
            record = federal_record(federal[0], federal[1], party, name)
            if record:
                records.append(record)
            continue
        if not state_offices:
            continue
        statewide = parse_statewide_office(contest)
        if statewide is not None:
            records.append({"office": statewide[0], "district": statewide[1], "party": party, "last_name": name})
            continue
        seat = parse_state_leg_office(contest)
        if seat is not None:
            record = {"office": seat[0], "district": seat[1], "party": party, "last_name": name}
            if seat[2] is not None:
                record["seat"] = seat[2]
            records.append(record)
    return records


def _files(index: dict, year: int, election_regex: str, file_regex: str) -> list[tuple[str, str]] | None:
    """(party code, blobPath) for this year's primary per-party summaries."""
    pattern = re.compile(file_regex.replace("{year}", str(year)))
    for entry in index.get("pastElectionResults") or []:
        if entry.get("year") != year:
            continue
        for election in entry.get("elections") or []:
            if not re.search(election_regex, str(election.get("type") or "")):
                continue
            found = []
            for group in election.get("fileGroups") or []:
                for f in group.get("files") or []:
                    m = pattern.search(str(f.get("displayName") or ""))
                    if m and f.get("blobPath"):
                        party = normalize_party(m.group(1))
                        if party is None:
                            logger.warning("Primary canvass file %r names no party read here", f.get("displayName"))
                            return None
                        found.append((party, f["blobPath"]))
            return found
    return []


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    needed = ("index_url", "files_base", "election_regex", "file_regex", "title_regex", "sheets")
    missing = [k for k in needed if not source.get(k)]
    if missing:
        logger.warning("%s primary canvass source is missing %s", state, missing)
        return None
    index = await fetch_json_with_retry(client, _rate_limiter, source["index_url"], f"{state} public files index")
    if not isinstance(index, dict):
        return None
    files = _files(index, year, source["election_regex"], source["file_regex"])
    if files is None:
        return None
    if not files:
        logger.info("%s has no %d primary canvass in its files index yet", state, year)
        return []

    title_re = re.compile(source["title_regex"].replace("{year}", str(year)))
    by_contest: dict[tuple[str, str], list[tuple[str, int]]] = {}
    write_ins: set[tuple[str, str, str]] = set()
    held = None
    for party, blob in files:
        url = source["files_base"].rstrip("/") + "/" + quote(blob, safe="/+,")
        payload = await fetch_bytes_with_retry(client, _rate_limiter, url, f"{state} {year} primary canvass ({party})")
        if payload is None:
            return None
        sheets = sheet_rows(payload, list(source["sheets"]))
        if not sheets:
            return None
        for rows in sheets.values():
            title = rows[0][0] if rows and rows[0] else ""
            if not title_re.search(title):
                logger.warning("%s primary canvass sheet is titled %r, not this year's official canvass", state, title[:80])
                return None
            date = _TITLE_DATE_RE.search(title)
            if date:
                held = datetime.strptime(date.group(1), "%B %d, %Y").date().isoformat()
            totals = contest_totals(rows, party)
            if totals is None:
                return None
            for contest, name, votes, write_in in totals:
                by_contest.setdefault((contest, party), []).append((name, votes))
                if write_in:
                    write_ins.add((contest, party, name))

    if not _settled(held, source.get("settle_days", DEFAULT_SETTLE_DAYS)):
        return []
    return _records(by_contest, write_ins, runoff_threshold(source), bool(source.get("statewide_offices")))
