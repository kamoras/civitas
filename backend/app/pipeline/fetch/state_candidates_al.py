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
programmatically, and state_source_crawler.py's own landing-page probes
only ever match a downloadable file extension, never an ASP.NET query-
string results page like this one); the upgrade path for 2028+ is finding
one, or hand-verifying and updating this id when the next redistricting-
driven special primary (or any future AL special congressional primary)
occurs. Kept in state_candidate_sources.json's "AL" entry rather than
hardcoded here, so that update is a config edit, not a code change.

Because that id has no cycle of its own baked into the URL, this module
refuses to serve it for any cycle but the one it was verified against —
`YEAR` below — rather than silently re-confirming 2026's winners against a
LATER cycle's real FEC candidates on nothing but a surname match (Jerry
Carl and Gary Palmer, among real 2026 winners here, are exactly the kind
of repeat incumbent who could otherwise coincidentally "confirm" a false
positive in 2028).

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
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import _votes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# The only cycle ecode=1001300 (see below) is verified to mean — see the
# module docstring for why an off-cycle call refuses rather than reuses it.
YEAR = 2026

_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


class _ContestResultsParser(HTMLParser):
    """Alabama's own results page nests one table per contest, headed by a
    td.enrContestHeader ("UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL
    DISTRICT (REP)"), then one row per candidate whose name/party sit in a
    td.enrCandNameCol ("Jerry Carl                             (REP)") and
    whose vote count sits in a td.enrCandVoteNumCol. Alternating rows carry
    an extra "enrAlt" class prefix (plain zebra striping), so matching is on
    the class SUFFIX, not the exact class string — but a plain "CandNameCol"
    substring also matches the (different) column-labels row above the real
    candidates ("enrCandidatesHeader enrCandNameCol", holding only &nbsp;),
    so `CandidateListItemCol` — present only on a real candidate row's own
    class, singular "Candidate" — is required too, or that label row would
    be captured as a same-named "candidate" with an empty name.

    Capture is entered only when nothing is already being captured, and
    exited only by that SAME td's own close: a matching-class td can only
    ever directly hold text (never a candidate/header td nested inside
    another one on this page), so once inside a capture, a nested td
    starttag only tracks a depth counter rather than hijacking the buffer
    or ending the capture on ITS close instead of the outer td's.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contests: dict[str, list[tuple[str, int]]] = {}
        self._contest: str | None = None
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._pending_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "td":
            return
        if self._capture:
            self._capture_depth += 1
            return
        cls = dict(attrs).get("class") or ""
        if "enrContestHeader" in cls:
            self._capture = "header"
        elif "CandidateListItemCol" in cls and "CandNameCol" in cls:
            self._capture = "name"
        elif "CandidateListItemCol" in cls and "CandVoteNumCol" in cls:
            self._capture = "votes"
        else:
            return
        self._capture_depth = 1
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._capture:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        text = "".join(self._buf).strip()
        if self._capture == "header":
            self._contest = text
            self.contests.setdefault(text, [])
            # A stray name with no votes row after it (page truncated
            # mid-fetch, a still-tabulating precinct) must not survive
            # into the NEXT contest and get attributed to its first
            # unrelated vote count.
            self._pending_name = None
        elif self._capture == "name":
            self._pending_name = text
        elif self._capture == "votes" and self._contest and self._pending_name:
            self.contests[self._contest].append((self._pending_name, _votes(text)))
            self._pending_name = None
        self._capture = None


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is AL-only by construction
) -> list[dict] | None:
    if year != YEAR:
        # See module docstring — ecode=1001300 names one specific 2026
        # election with no date of its own; reusing it for any other
        # cycle would confirm that cycle's candidates off a stale surname
        # match rather than that cycle's real result.
        return []

    results_url = (
        "https://www2.alabamavotes.gov/electionNight/statewideResultsByContest.aspx"
        f"?ecode={source.get('ecode')}"
    )
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", results_url, timeout=30.0,
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
        # Alabama runs separate per-party ballots, not a top-two contest,
        # so every candidate under one contest header shares one party —
        # taken from the header (same source parse_office already reads),
        # not re-derived per candidate: normalize_party on a full "Name
        # (PARTY)" cell is the only place in this codebase that runs it
        # against a name rather than a party-only column, and a name that
        # happened to contain a party word would misfile that candidate
        # into a fabricated second party for an otherwise single-party
        # contest.
        party = normalize_party(contest)
        if party is None:
            continue
        choices_by_name = [(surname(name), votes) for name, votes in choices]
        choices_by_name = [(n, v) for n, v in choices_by_name if n]
        won = pick_nominee(choices_by_name, runoff_threshold_pct=None)
        if won:
            results.append({
                "office": office, "district": district,
                "party": party, "last_name": won[0],
            })

    if not results:
        logger.warning("AL special primary results yielded no confirmed nominees")
        return None
    return results
