"""North Carolina's ballot-measure strategy — the State Board of
Elections' statewide "Referendum Choices List" report.

The Board's "Upcoming Election" page links, as "Statewide referenda",
a one-page report generated from its own ballot database:

    STATE BOARD OF ELECTIONS
    REFERENDUM CHOICES LIST GROUPED BY REFERENDUM
    CRITERIA: Election: 11/03/2026, ..., County: ALL COUNTIES,
              Data Source: STATE ONLY VIEW
    CONSTITUTIONAL AMENDMENT - REQUIRE PHOTO ID FOR VOTING
    For Constitutional amendment to require all voters, ...
    Against Constitutional amendment to require all voters, ...

Its criteria line is checked before anything is read: it must name this
election's date, ALL COUNTIES and the STATE ONLY VIEW, so a county
report (local bonds, sales-tax questions) or another election's report
can't be mistaken for the statewide list. The address is the Board's
own stable convention (Elections/<year>/Candidate Filing/
statewide_referendums_<yyyymmdd>.pdf).

Stored verbatim:
- title: the referendum's name as the report prints it — the short
  caption the Constitutional Amendments Publication Commission prepares
  for the ballot (G.S. 147-54.10), hence title_authority.
- official_summary: the ballot text after "For"/"Against", which the
  enacting session law prescribes and both choices repeat. The two are
  compared; if they ever differ the report is refused rather than one
  picked.

North Carolina's ballot doesn't number amendments and neither does the
report, so `number` is empty and each is keyed on its caption. The
"For"/"Against" lines are ballot choices, not an explanation of the
vote — yes_means/no_means stay null, as does the fiscal statement (none
is published). The Commission's longer "official explanation" is not
published by the Board or on the Secretary of State's site for 2026
(its archive page stops at 2018), so it is not read.

A report that exists and lists no referendum is a checked answer ([]).

Verified live 2026-09-28 against the report generated Sep 02, 2026:
three amendments on the November 3, 2026 ballot (photo ID for all
voting, 3.5% income-tax cap, property-tax levy limit).
"""

import logging
import re

import httpx

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_bytes, pdf_text

logger = logging.getLogger(__name__)

URL_PATTERN = (
    "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/{year}/Candidate%20Filing/"
    "statewide_referendums_{ymd}.pdf"
)
TITLE_AUTHORITY = "North Carolina Constitutional Amendments Publication Commission"
ORIGIN = "North Carolina General Assembly"

# The report's own repeated page furniture — skipped wherever it
# appears so a two-page report can't turn a page header into a
# "referendum".
_FURNITURE_RE = re.compile(
    r"^(STATE BOARD OF ELECTIONS|REFERENDUM CHOICES LIST\b.*|CRITERIA:.*|CHOICE DESCRIPTION|"
    r"CONT_CAND_rpt.*|[A-Z][a-z]{2} \d{2}, \d{4} \d{1,2}:\d{2} [ap]m)$",
)
_CHOICE_RE = re.compile(r"^(For|Against)\s+(.*)$")


def report_url(year: int) -> str:
    return URL_PATTERN.format(year=year, ymd=election_day(year).strftime("%Y%m%d"))


def _is_heading(line: str) -> bool:
    return any(c.isalpha() for c in line) and line == line.upper()


def parse_report(text: str, year: int) -> list[dict] | None:
    day = election_day(year)
    criteria = clean_text(" ".join(ln for ln in text.splitlines() if ln.startswith("CRITERIA:"))) or ""
    if (
        f"Election: {day.strftime('%m/%d/%Y')}" not in criteria
        or "County: ALL COUNTIES" not in criteria
        or "STATE ONLY VIEW" not in criteria
    ):
        return None

    referenda: list[dict] = []
    current = None
    choice = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _FURNITURE_RE.match(line):
            continue
        c = _CHOICE_RE.match(line)
        if c and current is not None:
            choice = c.group(1)
            current[choice] = c.group(2)
        elif _is_heading(line) and not c:
            current = {"title": line}
            referenda.append(current)
            choice = None
        elif current is not None and choice is not None:
            current[choice] += " " + line
        else:
            return None

    results = []
    for r in referenda:
        yes_text, no_text = clean_text(r.get("For")), clean_text(r.get("Against"))
        if not yes_text or yes_text != no_text:
            return None
        results.append({
            "number": "",
            "id_key": r["title"],
            "title": r["title"],
            "origin": ORIGIN,
            "official_summary": yes_text,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = report_url(year)
    raw = await get_bytes(client, url, f"NC statewide referenda {year}")
    if raw is None:
        return None
    try:
        parsed = parse_report(pdf_text(raw), year)
    except Exception:
        logger.exception("NC statewide referenda report for %d parse failed", year)
        return None
    if parsed is None:
        logger.warning("NC statewide referenda report for %d is not the statewide list or is malformed", year)
        return None
    return [(p, url) for p in parsed]
