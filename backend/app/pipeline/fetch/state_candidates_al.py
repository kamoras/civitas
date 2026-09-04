"""Alabama's own election-night-reporting site (www2.alabamavotes.gov), a
single-state deployment covering ONLY the 2026-08-11 special primary — not
Alabama's whole federal slate, and not by choice.

WHY JUST THE SPECIAL PRIMARY: following Louisiana v. Callais (2026-04-29)
and a Alabama Legislature special session, Governor Ivey ordered four of
Alabama's seven US House districts (1, 2, 6, 7) redrawn and re-run under a
new map, decided in a single-round SPECIAL primary on 2026-08-11 with NO
runoff (confirmed reporting: "There will be no runoff election"). The other
three districts (3, 4, 5) and the US Senate seat were decided earlier under
the ORDINARY May 19 primary / June 16 runoff cycle, whose results this
module does NOT read — that data exists (the state parties published real
per-county Excel workbooks — GOP: sos.alabama.gov/sites/default/files/
05-29-2026/GOP%20Results.xlsx; Democratic primary and runoff equivalents
under /election-2026/), but every STATE-CERTIFIED nominee document for that
cycle (the two-column Office/Name PDFs read by the Alabama Republican and
Democratic Parties' own letters to the Secretary of State) is a flat
SCANNED image with no text layer at all (confirmed: pdftotext, pdffonts and
pdfimages against six of these certification PDFs all show zero embedded
fonts, only JPEG/JBIG2 raster pages) — unreadable without OCR, which this
codebase does not build. And the one piece that WOULD close the gap without
OCR — the June 16 Republican Senate runoff's own vote count — has no
published machine-readable file anywhere findable (the Democratic runoff's
Excel exists publicly; the Republican Party's equivalent apparently was
only ever shared with the Secretary of State's office directly, per its own
certification letter: "the documents found in the shared Dropbox folder").
So CD3, CD4, CD5 and the Senate seat stay on the ordinary FEC-filer
fallback rather than being wrongly guessed from certification news
coverage — this module reads only what has a genuine machine-readable
source: the four redrawn districts.

ecode=1001300 is the special primary's own results-page id on Alabama's
ASP.NET election-night system, verified live 2026-09-03 (weeks after the
election) to still be a stable, permanent reference — Alabama does not
appear to recycle an id the way New Mexico's single "always current" URL
does. ponytail: this id is NOT rediscovered each run (no listing/API
endpoint or dated results-announcement page was found that names it
programmatically); the upgrade path for 2028+ is finding one, or hand-
verifying and updating this id when the next redistricting-driven special
primary (or any future AL special congressional primary) occurs.

Verified live 2026-09-03 against the real, certified-by-count special
primary: Jerry Carl (CD1 R, 74.70%), Rhett Marques (CD2 R, 50.03%), Maurice
Mercer (CD6 D, 64.17%), Gary Palmer (CD6 R, 86.98%), Ammie Akin (CD7 R,
73.37%) — no CD1/CD2/CD7 Democratic primary is shown on this page at all,
meaning Democrats fielded no candidate in those three redrawn districts.
"""

import logging
from html.parser import HTMLParser

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    normalize_party, parse_office, pick_nominee, surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Permanent for the 2026 special primary (see module docstring) — not a
# per-cycle template, because nothing on the site names this id
# programmatically.
_SPECIAL_PRIMARY_ECODE = 1001300
_RESULTS_URL = (
    "https://www2.alabamavotes.gov/electionNight/statewideResultsByContest.aspx"
    f"?ecode={_SPECIAL_PRIMARY_ECODE}"
)
_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


def _votes(raw: str) -> int:
    try:
        return int(raw.replace(",", "").strip() or 0)
    except ValueError:
        return 0


class _ContestResultsParser(HTMLParser):
    """Alabama's own results page nests one table per contest, headed by a
    td.enrContestHeader ("UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL
    DISTRICT (REP)"), then one row per candidate whose name/party sit in a
    td.enrCandNameCol ("Jerry Carl                             (REP)") and
    whose vote count sits in a td.enrCandVoteNumCol. Alternating rows carry
    an extra "enrAlt" class prefix (plain zebra striping), so matching is on
    the class SUFFIX, not the exact class string. A totals row's vote cell
    reuses the same class with no matching name cell before it — harmless
    here, since a vote count is only ever recorded alongside a name already
    captured for it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contests: dict[str, list[tuple[str, int]]] = {}
        self._contest: str | None = None
        self._capture: str | None = None
        self._buf: list[str] = []
        self._pending_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "td":
            return
        cls = dict(attrs).get("class") or ""
        if "enrContestHeader" in cls:
            self._capture = "header"
        elif "CandNameCol" in cls:
            self._capture = "name"
        elif "CandVoteNumCol" in cls:
            self._capture = "votes"
        else:
            return
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._capture:
            return
        text = "".join(self._buf).strip()
        if self._capture == "header":
            self._contest = text
            self.contests.setdefault(text, [])
        elif self._capture == "name":
            self._pending_name = text
        elif self._capture == "votes" and self._contest and self._pending_name:
            self.contests[self._contest].append((self._pending_name, _votes(text)))
            self._pending_name = None
        self._capture = None


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is AL-only by construction
) -> list[dict] | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", _RESULTS_URL, timeout=30.0,
        log_label=f"AL special primary results {year}", headers=_HEADERS,
    )
    if resp is None:
        return None

    parser = _ContestResultsParser()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001 - a malformed page is a skip, not a crash
        logger.warning("AL special primary results page was not parsable HTML")
        return None
    if not parser.contests:
        logger.warning("No contests found on AL special primary results page")
        return None

    results: list[dict] = []
    for contest, choices in parser.contests.items():
        office_district = parse_office(contest)
        if office_district is None:
            continue
        office, district = office_district
        # Each contest section is scoped to one party already (Alabama
        # runs separate ballots, not a top-two contest), but the party
        # comes off each candidate's own row — same as every other
        # results-derived strategy — rather than assumed from the header.
        named = [(surname(name), normalize_party(name), votes) for name, votes in choices]
        by_party: dict[str, list[tuple[str, int]]] = {}
        for last_name, party, votes in named:
            if not last_name or not party:
                continue
            by_party.setdefault(party, []).append((last_name, votes))
        for party, party_choices in by_party.items():
            won = pick_nominee(party_choices, runoff_threshold_pct=None)
            if won:
                results.append({
                    "office": office, "district": district,
                    "party": party, "last_name": won[0],
                })

    if not results:
        logger.warning("AL special primary results yielded no confirmed nominees")
        return None
    return results
