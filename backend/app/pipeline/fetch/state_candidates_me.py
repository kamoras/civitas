"""Maine's own results page (maine.gov/sos/elections-voting/election-
results-data) — a single-state deployment (see state_candidates.py for
why that earns a new module, not a config entry), and the only state in
this system whose federal primary can be decided by an actual multi-
round Ranked Choice Voting tabulation rather than a plain vote count
(Me. Rev. Stat. tit. 21-A, the "RCV law", applies to any primary or
general federal/gubernatorial contest with more than two candidates).

DISCOVERY is a single plain, unauthenticated GET (verified live
2026-09-09, no bot-detection friction at all) of one evergreen landing
page, which Maine's own CMS republishes in place for each cycle — no
per-year URL segment anywhere. The page lists results under two `<h2>`
sections, "... Ranked Choice Offices" and "... Non-Ranked Choice
Offices", each `<h2>`'s own text carrying the cycle's year and date
("June 9, 2026 - Primary Election - ...") — checked against the `year`
argument before trusting anything under it, the only real cycle guard
this module needs, since a future cycle's page will simply carry a
different year in that same text. A race with no results posted yet
carries no `<h3>`/link at all (the page's own down-ballot sections
literally say "Election results will be linked on this page upon
completion") — so an early-cycle probe naturally reads as the empty
list, no separate polling/staging gate needed the way a live-count
portal (WA/VA/UT's Enhanced Voting, FL's election-night file) needs one.

Under "Non-Ranked Choice Offices", each `<h3>` names an OFFICE only (no
party — "U.S. Senate", "Representative to Congress, District 1") with a
`<ul>` of per-party `<li><a>` links to a plain "FINAL" xlsx export —
these are the offices where Maine's own RCV law never even triggered
(one or two total candidates for that party's primary makes a majority
automatic, so the state just posts a final count with no tabulation
round needed). Under "Ranked Choice Offices", each `<h3>` names an
office AND a party together ("Representative to Congress, District 2 -
Democratic" — the one real 2026 federal race that needed it), followed
directly by that office's own first-choice xlsx export and its official
"RCV Summary Report" PDF, before an `<h4>Cast Vote Records</h4>` begins
a long list of raw per-batch exports this module never descends into
(the `<h4>` is what ends `_ResultsPageReader`'s own link collection for
that heading — verified this boundary holds by reading the real page's
raw HTML, not assumed from its rendered layout).

THE WIDE XLSX SHAPE is a genuinely new format for this system: one row
per MUNICIPALITY (not per candidate — the transpose of every other
tabular state's export), one column per candidate, the column header
itself the candidate's raw FEC-style "LAST, FIRST MIDDLE" name. A
county's own subtotal row and the file's final "State Totals" row both
carry a blank Municipality-adjacent DIS/CTY pair with their real numbers
shifted into the Municipality/candidate columns (Maine's export omits a
wholly-blank cell from the row's XML rather than writing an empty one,
which is what motivated fixing `_xlsx_rows` to place cells by their own
column reference — see that module). `_municipality_choices` sums only
real per-town/UOCAVA rows (a non-blank Municipality that doesn't end in
"Total"/"Totals"), never a subtotal or the header's own hometown/write-
in-status annotation row (both blank-Municipality) — summing either
would double-count or corrupt a candidate's real statewide total.

THE RCV SUMMARY REPORT is Maine's own official record of an actual,
in-person, publicly-livestreamed round-by-round elimination tabulation
(confirmed via the Department's real 2026-06-10 press release: the count
happens at a fixed date/location, open to the public and press, with a
formal recount right attached to any top-three finisher) — the same
trust this system already gives NJ's/MS's/KY's own official post-
primary certification documents, not a live convenience flag. Real
shape (verified live against the actual 2026 CD2 Democratic primary,
plain embedded text, pdfplumber's bare `extract_text()` reads it
cleanly): a flat `Label value` line per metadata field (`Winner(s)
Dunlap, Matthew G.`), then one line per round-by-round candidate row
(name, then one vote total per round, an eliminated candidate trailing
zeros in every later round). `_parse_rcv_summary` never trusts the
`Winner(s)` line alone — it also finds whichever candidate carries the
single highest vote count in the FINAL round column and refuses unless
that's the SAME person the document itself named winner (a tie for the
top final-round spot, or a name mismatch, refuses rather than guessing
which of the two figures is right). A federal House/Senate seat is
always single-winner, so "Winner(s)" naming exactly one person is the
only real shape this module needs to handle.

No settle_days gate: unlike a live-updating portal, nothing under either
`<h2>` section exists on this page in a draft/unofficial form to begin
with — a race's own files (all real-verified: "...FINAL.xlsx", the RCV
tabulation PDF) appear only once genuinely done, weeks after the
election in the real 2026 case, which is why an early probe reads as
"no h3 yet" rather than a half-finished document needing a time-based
release rule.

Maine's federal primaries nominate by PLURALITY once RCV resolves a
majority winner (or a two-candidate field's own natural majority) — no
separate runoff mechanism exists, so `runoff_threshold_pct` stays null
for both the ordinary vote-count path and the RCV path (whose own 50%
majority bar is enforced by Maine's tabulation itself, not by this
module re-deriving it).

Verified live 2026-09-09 against the real, certified 2026 primary:
Matthew Dunlap (CD2 D, real RCV winner — trailed Joseph Baldacci in
first-choice votes, won on the third and final elimination round) and
Chellie Pingree (CD1 D, real incumbent, plain plurality, no RCV needed).
"""

import io
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import (
    DiscoveryFailed,
    normalize_party,
    parse_office,
    resolve_confirmed_nominees,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)
_LANDING_URL = "https://www.maine.gov/sos/elections-voting/election-results-data"

_NON_CANDIDATE_COLUMNS = {"DIS", "CTY", "Municipality", "BLANK", "TBC"}

_RCV_LABEL_PREFIXES = (
    "Contest", "Jurisdiction", "Office", "Date", "Winner(s)",
    "Threshold", "Rounds", "Eliminated", "Elected", "Exhausted Ballots",
)


class _ResultsPageReader(HTMLParser):
    """Every (h2 section, h3 heading) -> [(link text, href), ...] the
    results page's own markup lists, tracked by nesting rather than any
    assumption about surrounding tags -- see module docstring for why an
    `<h4>` (which always means "Cast Vote Records" on this page) has to
    end a heading's own link collection before its long tail of raw
    per-batch exports begins."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self._h2 = ""
        self._h3 = ""
        self._collecting = False
        self._current_href: str | None = None
        self._text_buf = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3", "h4"):
            self._collecting = False
            self._text_buf = ""
        elif tag == "a" and self._collecting:
            self._current_href = dict(attrs).get("href")
            self._text_buf = ""

    def handle_endtag(self, tag):
        if tag == "h2":
            self._h2 = self._text_buf.strip()
            self._h3 = ""
        elif tag == "h3":
            self._h3 = self._text_buf.strip()
            self._collecting = True
        elif tag == "a" and self._current_href is not None:
            text = self._text_buf.strip()
            if self._h2 and self._h3:
                self.sections.setdefault((self._h2, self._h3), []).append((text, self._current_href))
            self._current_href = None

    def handle_data(self, data):
        self._text_buf += data


async def _get_text(client: httpx.AsyncClient, url: str, label: str) -> str:
    text = await fetch_text_with_retry(client, _rate_limiter, url, label)
    if text is None:
        raise DiscoveryFailed(f"fetch failed: {label}")
    return text


async def _get_bytes(client: httpx.AsyncClient, url: str, label: str) -> bytes:
    content = await fetch_bytes_with_retry(client, _rate_limiter, url, label)
    if content is None:
        raise DiscoveryFailed(f"fetch failed: {label}")
    return content


def _discover_entries(html: str, year: int) -> list[tuple[str, tuple[str, int | None], str, str]]:
    """[(kind, (office, district), party, url), ...] for every real
    federal entry the page currently lists for `year` — kind is "xlsx"
    for a plain vote-count export or "rcv" for a ranked-choice summary
    PDF. Empty is a real, healthy answer (nothing posted yet this cycle,
    or the page is still showing an older year)."""
    reader = _ResultsPageReader()
    reader.feed(html)

    entries: list[tuple[str, tuple[str, int | None], str, str]] = []
    for (h2, h3), links in reader.sections.items():
        if str(year) not in h2:
            continue
        office_district = parse_office(h3)
        if office_district is None:
            continue
        if "Non-Ranked Choice Offices" in h2:
            for text, href in links:
                party = normalize_party(text)
                if party is None:
                    continue
                entries.append(("xlsx", office_district, party, urljoin(_LANDING_URL, href)))
        elif "Ranked Choice Offices" in h2:
            party = normalize_party(h3)
            if party is None:
                continue
            pdf_href = next((href for text, href in links if "RCV Summary Report" in text), None)
            if pdf_href is None:
                continue  # tabulation not posted yet -- healthy
            entries.append(("rcv", office_district, party, urljoin(_LANDING_URL, pdf_href)))
    return entries


def _municipality_choices(rows: list[dict]) -> list[tuple[str, int]]:
    """Sum each candidate column across every real per-town (or statewide
    UOCAVA/overseas-ballot) row — see module docstring for why a blank
    Municipality cell (a header annotation row) or a subtotal row (a
    county's own "YOR Totals", or the state's own grand total) must be
    skipped rather than summed.

    The subtotal check is a case-insensitive substring match on "total"
    rather than a fixed suffix: real Maine exports were found to spell
    this differently ACROSS FILES for the exact same kind of row —
    "State Totals" (Representative to Congress District 1) vs. "STATE
    TOTAL" (U.S. Senate, no trailing "s") — live-verified 2026-09-09.
    Matching only one exact casing/suffix let the Senate file's own
    all-caps grand-total row silently get summed in as if it were a real
    municipality, doubling every candidate's true statewide total —
    caught only by cross-checking against that file's own row-level
    arithmetic (candidate votes + blanks == total ballots cast), not by
    inspection. No real Maine municipality name contains the word
    "total", so this is safe as a general rule, not just for this file."""
    totals: dict[str, int] = {}
    for row in rows:
        municipality = (row.get("Municipality") or "").strip()
        if not municipality or "total" in municipality.lower():
            continue
        for column, value in row.items():
            if column in _NON_CANDIDATE_COLUMNS:
                continue
            value = (value or "").strip()
            if value.isdigit():
                totals[column] = totals.get(column, 0) + int(value)
    return list(totals.items())


def _pdf_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _parse_rcv_summary(text: str) -> str | None:
    """The certified winner's own display name, cross-checked against the
    document's own round-by-round table — see module docstring for why
    neither the `Winner(s)` line nor the vote table is trusted alone.
    None when the two disagree, or either can't be read at all."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    winner_line = next((ln for ln in lines if ln.startswith("Winner(s)")), None)
    rounds_line = next((ln for ln in lines if ln.startswith("Rounds ")), None)
    if not winner_line or not rounds_line:
        return None
    winner = winner_line[len("Winner(s)"):].strip()
    num_rounds = rounds_line.count("Round ")
    if not winner or num_rounds < 1:
        return None

    finals: dict[str, int] = {}
    for ln in lines:
        if ln.startswith(_RCV_LABEL_PREFIXES):
            continue
        # A candidate row is "Name ... <round 1> <round 2> ...": split on
        # whitespace and peel off the trailing run of purely-numeric
        # tokens (never a single trailing regex group, which is greedy
        # toward the NAME half and would only ever capture the row's
        # LAST round instead of every round).
        tokens = ln.split()
        split_at = len(tokens)
        while split_at > 0 and tokens[split_at - 1].isdigit():
            split_at -= 1
        votes = tokens[split_at:]
        if split_at == 0 or not votes or len(votes) != num_rounds:
            continue
        finals[" ".join(tokens[:split_at])] = int(votes[-1])
    if not finals:
        return None

    top_votes = max(finals.values())
    if top_votes <= 0:
        return None
    top_names = [name for name, v in finals.items() if v == top_votes]
    if len(top_names) != 1:
        return None  # a tie for the final round's top spot -- refuse, don't guess
    if surname(top_names[0], last_first=True) != surname(winner, last_first=True):
        return None
    return winner


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — source unused, this strategy is ME-only by construction
) -> list[dict] | None:
    try:
        html = await _get_text(client, _LANDING_URL, f"ME results page {year}")
        entries = _discover_entries(html, year)
        if not entries:
            return []

        by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
        results: list[dict] = []
        for kind, (office, district), party, url in entries:
            label = f"ME {office}{district or 0} {party} {kind} {year}"
            content = await _get_bytes(client, url, label)
            if kind == "xlsx":
                rows = _xlsx_rows(content)
                if rows is None:
                    raise DiscoveryFailed(f"{label}: download was not a readable xlsx workbook")
                by_seat.setdefault((office, district, party), []).extend(_municipality_choices(rows))
            else:
                try:
                    text = _pdf_text(content)
                except Exception as exc:
                    raise DiscoveryFailed(f"{label}: PDF failed to parse") from exc
                winner = _parse_rcv_summary(text)
                if winner is None:
                    raise DiscoveryFailed(f"{label}: RCV summary did not yield a cross-checked winner")
                last_name = surname(winner, last_first=True)
                if last_name:
                    results.append({"office": office, "district": district, "party": party, "last_name": last_name})
    except DiscoveryFailed as exc:
        logger.warning("ME results: discovery failed: %s", exc)
        return None

    results.extend(
        resolve_confirmed_nominees(
            by_seat, runoff_threshold_pct=None,
            name_transform=lambda n: surname(n, last_first=True),
        ),
    )
    return results
