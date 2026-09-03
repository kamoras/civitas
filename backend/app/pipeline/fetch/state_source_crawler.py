"""Finds a state's CURRENT results source at run time, so coverage grows
and repairs itself without anyone editing a link every election season.

The split this module rests on:

  * A state's nomination RULES are stable law — a 35% convention threshold
    (Iowa Code 43.52), top-two (CA/WA), a 50% runoff (TX/GA/MS/AL/AR/OK/SC).
    Those live in state_candidate_sources.json, are written once, and never
    need seasonal review. Nothing here tries to infer them, because getting
    one wrong invents a nominee.
  * A state's LOCATIONS are volatile — hosts, paths, election ids, blob
    names that carry a fresh GUID per publish, column headers, the file
    name for this cycle's date. Every one of those is discovered here, on
    every run. A state that redesigns its website between cycles is
    re-found rather than silently broken.

What comes out is a source dict in EXACTLY the shape a hand-written entry
has, so everything downstream — the adapters, the certification gate, the
matcher — treats a discovered state and a configured one identically.
There is no separate code path for crawled states.

Discovery is ordered by how much the source tells us about itself:

  1. Clarity          — a fixed contract (/{ST}/elections.json), so the
                        existing adapter needs nothing but the state code.
  2. Enhanced Voting  — a fixed API contract, but its host differs per
                        state and its report names are free text, so both
                        are probed.
  3. Published files  — no contract at all: crawl the state's own election
                        pages for a results download and INFER the shape
                        from the file itself (see infer_format).

Nothing discovered is trusted on its own. The caller validates a candidate
source by running it and checking the nominees it claims against Civitas's
own FEC-derived candidate rows: a source that can't line up with the
official filer list for the same races is rejected, whatever it parsed
into. That check is what makes automatic discovery safe enough to act on.
"""

import asyncio
import logging
import re
from collections import defaultdict
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)
# Probing is one request each to fifty DIFFERENT hosts, and a rate limit
# exists to be polite to ONE host — serialising the whole sweep through
# the download limiter turns a ten-minute weekly job into an hour of
# mostly-idle waiting. Each host still sees at most a couple of requests.
_probe_limiter = RateLimiter(rps=5.0, name="state_source_crawler_probe")

CLARITY_BASE = "https://results.enr.clarityelections.com"

# Each state's own election authority domain. These are structural, like
# election_calendar.py's statutory rules — a .gov domain a state has used
# for years, not a per-cycle path — and the crawler probes SUBDOMAINS and
# PAGES beneath them rather than any stored deep link, so a redesign under
# the same domain costs nothing. A state that moved domains entirely
# announces itself by every probe failing, which the run report surfaces.
ELECTION_DOMAINS = {
    "AL": ["sos.alabama.gov", "alabamavotes.gov"], "AK": ["elections.alaska.gov"],
    "AZ": ["azsos.gov", "arizona.vote"], "AR": ["sos.arkansas.gov"],
    "CA": ["sos.ca.gov", "electionresults.sos.ca.gov"], "CO": ["sos.state.co.us", "coloradosos.gov"],
    "CT": ["portal.ct.gov", "ct.gov"], "DE": ["elections.delaware.gov"],
    "FL": ["dos.fl.gov", "floridaelectionwatch.gov", "elections.myflorida.com"],
    "GA": ["sos.ga.gov"], "HI": ["elections.hawaii.gov"],
    "ID": ["sos.idaho.gov", "voteidaho.gov"], "IL": ["elections.il.gov"],
    "IN": ["in.gov", "indianavoters.in.gov"], "IA": ["sos.iowa.gov"],
    "KS": ["sos.ks.gov"], "KY": ["sos.ky.gov", "elect.ky.gov"], "LA": ["sos.la.gov"],
    "ME": ["maine.gov"], "MD": ["elections.maryland.gov"], "MA": ["sec.state.ma.us"],
    "MI": ["michigan.gov"], "MN": ["sos.mn.gov"], "MS": ["sos.ms.gov"],
    "MO": ["sos.mo.gov"], "MT": ["sosmt.gov"], "NE": ["sos.nebraska.gov"],
    "NV": ["nvsos.gov"], "NH": ["sos.nh.gov"], "NJ": ["nj.gov"],
    "NM": ["sos.nm.gov"], "NY": ["elections.ny.gov"], "NC": ["ncsbe.gov", "dl.ncsbe.gov"],
    "ND": ["sos.nd.gov", "vote.nd.gov"], "OH": ["ohiosos.gov"],
    "OK": ["oklahoma.gov", "okelections.us"], "OR": ["sos.oregon.gov", "oregonvotes.gov"],
    "PA": ["pa.gov", "electionreturns.pa.gov"], "RI": ["elections.ri.gov", "ri.gov"],
    "SC": ["scvotes.gov"], "SD": ["sdsos.gov"], "TN": ["sos.tn.gov"],
    "TX": ["sos.texas.gov", "sos.state.tx.us"], "UT": ["vote.utah.gov", "utah.gov"],
    "VT": ["sos.vermont.gov"], "VA": ["elections.virginia.gov"],
    "WA": ["sos.wa.gov", "votewa.gov"], "WV": ["sos.wv.gov"],
    "WI": ["elections.wi.gov"], "WY": ["sos.wyo.gov"],
}

# Subdomain shapes an election-results host actually takes. Probed against
# each state's domains, which is how a state that stands up a new results
# portal is found without anyone being told its address.
_RESULTS_PREFIXES = ["results", "electionresults", "enr", "results.enr", "election-results", ""]

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "NewHampshire", "NJ": "NewJersey", "NM": "NewMexico", "NY": "NewYork",
    "NC": "NorthCarolina", "ND": "NorthDakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "RhodeIsland", "SC": "SouthCarolina",
    "SD": "SouthDakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "WestVirginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}

# An election this module will read nominees from, and one it won't. Both
# are about the KIND of election, never its date, so they hold every cycle.
_PRIMARY_RE = re.compile(r"\bprimar(?:y|ies)\b", re.IGNORECASE)
_RUNOFF_RE = re.compile(r"\brun[\s-]?off\b", re.IGNORECASE)
_NOT_STATEWIDE_RE = re.compile(
    r"\b(?:municipal|special|town|city|county|school|recall|referend\w*|presidential\s+preference)\b",
    re.IGNORECASE,
)


async def _get(
    client: httpx.AsyncClient, url: str, label: str, timeout: float = 20.0,
    probe: bool = False,
):
    # ONE attempt (retries counts attempts, not extra tries) and no 4xx
    # retry: most of what a crawler asks for is expected to be absent — a
    # host that doesn't exist, a state not on this vendor — and retrying
    # every miss three times turns a sweep of 50 states into a sweep
    # nobody will wait for.
    return await fetch_with_retry(
        client, _probe_limiter if probe else _rate_limiter, "GET", url,
        timeout=timeout, retries=1, retry_on_4xx=False,
        log_label=label, headers=_HEADERS,
    )


_robots: dict[str, RobotFileParser | None] = {}


async def _allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether this state's site says a robot may read this path.

    A weekly sweep of fifty government sites is exactly the kind of thing
    robots.txt exists to govern, and a crawler that ignores it earns a
    block that takes the whole feature down with it. Fetched once per host
    per process; a site with no robots.txt, or one that can't be read, is
    treated as permitting — that is what the standard says absence means.
    """
    host = urlparse(url).netloc
    if host not in _robots:
        parser = RobotFileParser()
        resp = await _get(
            client, f"https://{host}/robots.txt", f"{host} robots.txt",
            timeout=8.0, probe=True,
        )
        if resp is None:
            _robots[host] = None
        else:
            parser.parse(resp.text.splitlines())
            _robots[host] = parser
    parser = _robots[host]
    return parser is None or parser.can_fetch(_HEADERS["User-Agent"], url)


def _hosts_for(state: str) -> list[str]:
    """Every host worth asking whether it runs a results portal."""
    hosts = []
    for domain in ELECTION_DOMAINS.get(state.upper(), []):
        for prefix in _RESULTS_PREFIXES:
            host = f"{prefix}.{domain}" if prefix else domain
            if host not in hosts:
                hosts.append(host)
    return hosts


async def _probe_clarity(client: httpx.AsyncClient, state: str, cycle: int) -> dict | None:
    """Clarity publishes on one fixed path per state, so the only question
    is whether this cycle is in it yet — Iowa's 2026 primary appeared
    between two probes four days apart."""
    resp = await _get(client, f"{CLARITY_BASE}/{state}/elections.json", f"{state} Clarity")
    if resp is None:
        return None
    try:
        elections = resp.json() or []
    except ValueError:
        return None
    if not isinstance(elections, list):
        return None
    for entry in elections:
        if not isinstance(entry, dict):
            continue
        stamp = f"{entry.get('Date') or ''} {entry.get('ElectionName') or ''}"
        if str(cycle) in stamp and _PRIMARY_RE.search(stamp):
            return {"strategy": "clarity", "_evidence": f"Clarity EID {entry.get('EID')}"}
    return None


_DATEISH_RE = re.compile(
    r"\d+(?:st|nd|rd|th)?|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*"
    r"|sep\w*|oct\w*|nov\w*|dec\w*", re.IGNORECASE,
)


def _name_pattern(label: str) -> str | None:
    r"""A pattern matching THIS election by what it is, with everything
    that dates it removed — so the same pattern finds the equivalent
    election two years from now without anyone revisiting it.

    "May 19, 2026 - General Primary" becomes `General\s+Primary`, and
    "2026 August Democratic Primary" becomes `Democratic\s+Primary`,
    which is also how Virginia's two same-day party primaries end up as
    two distinct patterns without anything here knowing about parties.
    """
    words = [w.strip(".,") for w in re.split(r"[\s,\u2013\u2014-]+", label or "") if w.strip(".,")]
    keep = [w for w in words if not _DATEISH_RE.fullmatch(w)]
    return r"\s+".join(re.escape(w) for w in keep) if keep else None


def _election_patterns(names: list[str]) -> list[str] | str:
    """One pattern per primary the portal is actually running. More than
    one is a real answer, not a fallback: Virginia holds a separate
    Democratic and Republican primary on the same day."""
    patterns = []
    for name in names:
        pattern = _name_pattern(name)
        # A primary's name is a prefix of its runoff's ("General Primary"
        # / "General Primary Runoff"), so without this the runoff can be
        # picked up as the primary and thresholded as one.
        pattern = pattern and pattern + r"(?!.*Run[\s-]?off)"
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    if not patterns:
        return r"Primar(?:y|ies)(?!.*Run[\s-]?off)"
    return patterns if len(patterns) > 1 else patterns[0]


async def _probe_enhanced_voting(
    client: httpx.AsyncClient, state: str, cycle: int,
) -> dict | None:
    """The Enhanced Voting portal (GA, WA, VA, UT and counting) answers a
    fixed API, but every state hosts it somewhere different and names its
    reports in free text, so the host is probed and the report chosen by
    what it looks like rather than by anything written down."""
    name = _STATE_NAMES.get(state.upper(), "")
    hosts = _hosts_for(state)

    async def ask(host: str):
        resp = await _get(
            client, f"https://{host}/results/public/api/jurisdictions/{name}",
            f"{state} portal probe", timeout=8.0, probe=True,
        )
        if resp is None:
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) and body.get("id") else None

    answers = await asyncio.gather(*[ask(h) for h in hosts])
    for host, jurisdiction in zip(hosts, answers):
        if jurisdiction is None:
            continue

        def _label(entry: dict) -> str:
            # ENGLISH only: this portal carries every translation an
            # election has, and Virginia's names run to five languages.
            # Joining them makes a pattern that matches nothing.
            names = entry.get("name") or []
            english = next((n for n in names if n.get("languageId") == "en"), None)
            return (english or (names[0] if names else {})).get("text", "")

        cycle_elections = [
            e for e in jurisdiction.get("elections") or []
            if str(e.get("electionDate") or "").startswith(str(cycle))
        ]
        primaries = [
            e for e in cycle_elections
            if _PRIMARY_RE.search(_label(e))
            and not _RUNOFF_RE.search(_label(e))
            and not _NOT_STATEWIDE_RE.search(_label(e))
        ]
        if not primaries:
            logger.info("%s runs a results portal at %s but has no %d primary in it yet",
                        state, host, cycle)
            continue

        election_url = f"https://{host}/results/public/api/elections/{name}/{{election_id}}"
        discovery = {
            "mode": "sos_api_report",
            "jurisdiction_url": f"https://{host}/results/public/api/jurisdictions/{name}",
            "election_url": election_url,
            "cdn_url": f"https://{host}/cdn/results/{{jurisdiction_id}}/{{blob}}",
            "election_name_regex": _election_patterns([_label(e) for e in primaries]),
            "require_official": True,
        }
        # The runoff that matters is the PRIMARY's runoff. Georgia runs a
        # dozen special-election runoffs in a cycle, and taking one of
        # those for the primary's runoff quietly leaves every
        # runoff-decided seat unconfirmed.
        primary_runoffs = [
            _label(e) for e in cycle_elections
            if _RUNOFF_RE.search(_label(e))
            and _PRIMARY_RE.search(_label(e))
            and not _NOT_STATEWIDE_RE.search(_label(e))
        ]
        if primary_runoffs:
            discovery["runoff_name_regex"] = _name_pattern(primary_runoffs[0])
        # Which report is the statewide export is only knowable from the
        # election document — the jurisdiction listing names elections, not
        # their files.
        documents = []
        for entry in primaries[:2]:
            doc = await _get(
                client, election_url.format(election_id=entry.get("publicElectionId") or ""),
                f"{state} election document", timeout=15.0,
            )
            if doc is None:
                continue
            try:
                documents.append(doc.json() or {})
            except ValueError:
                continue
        return {
            "strategy": "tabular",
            "discovery": discovery,
            "_report_candidates": _report_names(documents),
            "_evidence": f"Enhanced Voting portal at {host}",
        }
    return None


# A full statewide results export, versus the many per-county, audit,
# turnout and cast-vote-record files published beside it.
_REPORT_WANTED = re.compile(r"\b(?:all\s+results|total\s+votes|election\s+results|results)\b", re.I)
_REPORT_UNWANTED = re.compile(
    r"\b(?:precinct|audit|canvass|cvr|cast\s+vote|turnout|statistic|change\s+log|absentee|county)\b",
    re.IGNORECASE,
)


def _report_names(elections: list[dict]) -> list[str]:
    """Report names worth trying, best first — the portal names them in
    free text ("All Results Excel", "Total Votes Excel", "Election
    Results"), so which one is the statewide export is a judgement about
    the name, confirmed by actually parsing it."""
    names = []
    for election in elections:
        for category in election.get("publicReportCategories") or []:
            for report in category.get("reports") or []:
                label = report.get("reportName") or ""
                if label and label not in names:
                    names.append(label)
    ranked = [n for n in names if _REPORT_WANTED.search(n) and not _REPORT_UNWANTED.search(n)]
    return ranked or [n for n in names if not _REPORT_UNWANTED.search(n)]


_FILE_RE = re.compile(r"""href=["']([^"']+\.(?:csv|tsv|txt|xlsx|xls|zip))["']""", re.IGNORECASE)
_RESULT_HINT_RE = re.compile(r"result|canvass|vote|return|tally", re.IGNORECASE)
_PAGE_HINT_RE = re.compile(
    r"result|election|canvass|return|data|download|report|statistic|archive",
    re.IGNORECASE,
)


async def _probe_pages(
    client: httpx.AsyncClient, state: str, cycle: int,
) -> list[tuple[str, str]]:
    """Links to this cycle's results downloads on the state's own pages,
    for a state on no known vendor — which is where Florida, Illinois,
    North Carolina and California all came from.

    Each result is (page it was found on, file url), because a source
    that re-reads the page every run is the one that keeps working when
    next cycle's file appears under a new name.

    Two hops from each of the state's own domains, with a budget PER
    domain: Florida's results files are linked from floridaelectionwatch
    .gov, and a shared budget gets spent wandering dos.fl.gov before ever
    reaching it. Deliberately shallow and hint-directed — a directed look
    at a handful of a state's own URLs, not a spider.
    """
    found: list[tuple[str, str]] = []

    def remember(page_url: str, html: str) -> None:
        for match in _FILE_RE.finditer(html):
            link = urljoin(page_url, match.group(1).replace("\\", "/"))
            if not (_RESULT_HINT_RE.search(link) or str(cycle) in link):
                continue
            # A results archive goes back decades, and a 2004 file parses
            # exactly as well as this year's — so anything that dates
            # itself to another cycle is refused outright rather than left
            # for a later check to notice.
            years = re.findall(r"(?:19|20)\d{2}", link)
            if years and str(cycle) not in years:
                continue
            if link not in [f for _, f in found]:
                found.append((page_url, link))

    async def scan_root(root: str) -> None:
        seen: set[str] = set()

        async def scan(url: str, depth: int) -> None:
            if url in seen or len(seen) >= 10 or len(found) > 60:
                return
            seen.add(url)
            if not await _allowed(client, url):
                logger.info("%s: robots.txt disallows %s — not reading it", state, url)
                return
            resp = await _get(client, url, f"{state} page scan", timeout=15.0)
            if resp is None or "html" not in resp.headers.get("content-type", ""):
                return
            remember(url, resp.text)
            if depth <= 0:
                return
            targets = []
            for match in re.finditer(r"""href=["']([^"']+)["']""", resp.text):
                target = urljoin(url, match.group(1))
                if urlparse(target).netloc != urlparse(url).netloc:
                    continue
                if re.search(r"\.\w{2,4}$", urlparse(target).path):
                    continue
                if _PAGE_HINT_RE.search(target) and target not in targets:
                    targets.append(target)
            # Most promising first, since the budget is small: a page
            # about results beats a page about elections in general.
            targets.sort(key=lambda t: (not _RESULT_HINT_RE.search(t), len(t)))
            for target in targets:
                await scan(target, depth - 1)

        await scan(root, 2)

    for domain in ELECTION_DOMAINS.get(state.upper(), []):
        await scan_root(f"https://{domain}/")
    # This cycle's files first, and results before anything else that
    # happened to be linked beside them.
    found.sort(key=lambda pair: (
        str(cycle) not in pair[1], not _RESULT_HINT_RE.search(pair[1]), pair[1],
    ))
    return found


_VOTE_HEADER_RE = re.compile(r"vote|total|count|tally", re.IGNORECASE)
_VOTE_NOT_HEADER_RE = re.compile(r"registr|precinct|ballot|turnout|percent|over|under|id$", re.I)
_DISTRICT_HEADER_RE = re.compile(r"district|juris|seat|office\s*num", re.IGNORECASE)
# The value a state marks a federal House row with — Virginia writes
# "congressional". Anchored, so a free-text cell that merely mentions
# Congress can't pose as the discriminator.
_TYPE_VALUE_RE = re.compile(r"congress(?:ional)?", re.IGNORECASE)


# A candidate's name: words, no digits. Precinct and ward names — the
# other thing that varies within a contest — almost always carry a number.
# Leading punctuation is common in real ballot names ('"CJ" CHRISTINA
# HERNANDEZ'), so the test is: contains a letter, contains no digit.
_NAME_LIKE_RE = re.compile(r"[^\d]*[^\W\d_][^\d]*", re.UNICODE)


def _numeric_share(values: list[str]) -> float:
    numeric = sum(1 for v in values if re.fullmatch(r"[\d,\s]*\d[\d,\s]*", v or ""))
    return numeric / len(values) if values else 0.0


def _office_columns(sample, text_cols, distinct, values) -> tuple[dict | None, list]:
    """The (marker, district) column pair a state uses to say "this row is
    a federal House seat", plus the rows it covers — for exports whose
    office LABEL can't say it (Virginia's "Member, House of
    Representatives (2nd District)"). Returns (None, []) when the file
    carries no such pair."""
    marker = next(
        (c for c in text_cols if any(_TYPE_VALUE_RE.fullmatch(v) for v in distinct[c])),
        None,
    )
    if not marker:
        return None, []
    marker_value = next(v for v in distinct[marker] if _TYPE_VALUE_RE.fullmatch(v))
    # Judge the district column only on the rows it describes: a column
    # holding "02" for federal rows and "ARLINGTON COUNTY" for local ones
    # is still the right column.
    federal_rows = [r for r in sample if str(r.get(marker) or "").strip() == marker_value]
    district_col = next(
        (c for c in text_cols
         if _DISTRICT_HEADER_RE.search(c)
         and _numeric_share([str(r.get(c) or "").strip() for r in federal_rows]) > 0.9),
        None,
    )
    if not district_col:
        return None, []
    return (
        {"type_column": marker, "type_value": marker_value, "district_column": district_col},
        federal_rows,
    )


def _contest_by_seat(federal_rows, text_cols, distinct, spec) -> str | None:
    """The contest column for a file whose labels don't parse: whatever is
    CONSTANT within one district and differs between districts. That is
    what an office label is, and it is what separates Virginia's
    OfficeTitle from the columns that look similar — ElectionName is
    constant everywhere, CandidateName and LocalityName both vary inside a
    single district."""
    seats: dict[str, list[dict]] = defaultdict(list)
    for row in federal_rows:
        seats[str(row.get(spec["district_column"]) or "")].append(row)

    def per_seat(column: str) -> float:
        counts = [
            len({str(r.get(column) or "").strip() for r in members if r.get(column)})
            for members in seats.values()
        ]
        return sum(counts) / len(counts) if counts else 0.0

    fixed = [
        c for c in text_cols
        if c not in (spec["type_column"], spec["district_column"])
        and len(distinct[c]) > 1 and per_seat(c) == 1
    ]
    # Several columns can identify the seat (Virginia carries both
    # OfficeTitle and an OfficeId of "cc13"); prefer the one a human would
    # recognise as the office.
    return max(
        fixed,
        key=lambda c: sum(len(v.split()) for v in distinct[c]) / len(distinct[c]),
        default=None,
    )


def infer_format(rows: list[dict]) -> dict | None:
    """Work out which columns hold the contest, the choice, the party and
    the votes, from the data itself — so a state's export is readable
    without anyone transcribing its headers, and stays readable when the
    state renames them.

    Roles are decided on evidence, not on header text alone:

      * VOTES is numeric, and its header says so while not saying
        "registration" or "precinct" (Illinois publishes both beside the
        real count).
      * CONTEST is the column whose values parse_office recognises as
        federal offices — the strongest signal available, since it is the
        very question the caller is going to ask of it. Where no label
        parses, it is instead the column that is constant within a seat.
      * CHOICE is the column whose values DON'T repeat from one contest to
        the next. This is the load-bearing one: in a candidate-by-county
        export, the candidate column and the county column are structurally
        identical — same rows, same repetition — and only this tells them
        apart, because every contest lists the same counties and the same
        parties but its own candidates.
      * PARTY is a low-cardinality column whose values normalize_party
        recognises.

    Returns None rather than a guess when nothing identifies a federal
    contest, because a shape this can't read is a shape it must not
    pretend to read.
    """
    if not rows:
        return None
    columns = [c for c in rows[0] if c]
    sample = rows[: min(len(rows), 4000)]
    values = {c: [str(r.get(c) or "").strip() for r in sample] for c in columns}
    distinct = {c: {v for v in values[c] if v} for c in columns}

    def _plausible_votes(column: str) -> bool:
        """A results file's vote column varies and reaches real numbers.
        A candidate FILING list also has a numeric column — Nebraska's
        "Vote For" is the number of seats to elect, 1 in every row — and
        without this it reads as a results file with every candidate
        tied."""
        numbers = [int(re.sub(r"[,\s]", "", v)) for v in values[column] if v.strip().isdigit()]
        return len(set(numbers)) > 1 and max(numbers, default=0) >= 10

    votes_col = max(
        (c for c in columns if _numeric_share(values[c]) > 0.9 and _plausible_votes(c)),
        key=lambda c: (
            bool(_VOTE_HEADER_RE.search(c)) and not _VOTE_NOT_HEADER_RE.search(c),
            sum(int(re.sub(r"[,\s]", "", v) or 0) for v in values[c] if v.strip().isdigit()),
        ),
        default=None,
    )
    if not votes_col:
        return None

    # Single-valued columns stay in play for the CONTEST role: a state that
    # publishes one file per office (Illinois) has exactly one contest name
    # in it, and that is the answer, not a degenerate case.
    text_cols = [c for c in columns if c != votes_col and distinct[c]]
    federal = {c: sum(1 for v in distinct[c] if parse_office(v)) for c in text_cols}
    contest_col = max(federal, key=lambda c: federal[c], default=None)
    labels_parse = bool(contest_col) and federal[contest_col] > 0

    # Looked for even when SOME labels parse: Virginia's file names its
    # Senate contest in a form parse_office accepts and its House contests
    # in one it must refuse, so a single file can need both routes. Where
    # the labels already work this costs nothing, since office_from_columns
    # only answers for rows whose marker matches.
    office_spec, federal_rows = _office_columns(sample, text_cols, distinct, values)
    if not labels_parse:
        if not office_spec:
            return None
        contest_col = _contest_by_seat(federal_rows, text_cols, distinct, office_spec)
        if not contest_col:
            return None

    def _party_score(column: str) -> tuple:
        vals = distinct[column]
        recognised = sum(1 for v in vals if normalize_party(v)) / len(vals)
        # A party column holds party NAMES. Utah's "Contest ID" reads "DEM
        # Sen 5" — every value recognisable, none of them a party — so the
        # tie-break is how little else the value carries.
        brevity = -sum(len(v.split()) for v in vals) / len(vals)
        return recognised, brevity

    party_candidates = [
        c for c in text_cols
        if c != contest_col and 0 < len(distinct[c]) <= 12
        and sum(1 for v in distinct[c] if normalize_party(v)) >= max(1, len(distinct[c]) // 2)
    ]
    party_col = max(party_candidates, key=_party_score, default=None)

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in sample:
        groups[str(row.get(contest_col) or "")].append(row)
    contests = [g for g in groups.values() if g][:8]

    def _repeats_across_contests(column: str) -> float:
        """How much a column's values are the SAME from contest to
        contest. Counties and parties repeat almost exactly; candidates
        barely repeat at all."""
        seen = [{str(r.get(column) or "").strip() for r in g if r.get(column)} for g in contests]
        seen = [v for v in seen if v]
        if len(seen) < 2:
            return 0.0
        scores = [
            len(a & b) / len(a | b)
            for i, a in enumerate(seen) for b in seen[i + 1:]
        ]
        return sum(scores) / len(scores) if scores else 0.0

    def _namelike(column: str) -> float:
        vals = distinct[column]
        return sum(1 for v in vals if _NAME_LIKE_RE.fullmatch(v)) / len(vals)

    contenders = [
        c for c in text_cols
        if c not in (contest_col, party_col) and len(distinct[c]) > 1
    ]
    named = [c for c in contenders if _namelike(c) >= 0.5] or contenders
    choice_col = min(named, key=_repeats_across_contests, default=None)
    if not choice_col:
        return None

    fmt = {"contest_column": contest_col, "choice_column": choice_col, "votes_column": votes_col}
    # A file whose URL has no date can still date itself, and a source
    # that can't be dated can't clear the certification gate at all.
    held_col = next(
        (c for c in text_cols
         if sum(1 for v in list(distinct[c])[:50]
                if re.match(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}", v)) >= 1
         and len(distinct[c]) <= 5),
        None,
    )
    if held_col:
        fmt["held_column"] = held_col
    if party_col:
        fmt["party_column"] = party_col
        # A contest label that carries no party can't tell two same-day
        # party primaries apart, so the party column joins the key — the
        # difference between one nominee per party and the bigger primary
        # taking the seat outright.
        if not any(normalize_party(v) for v in list(distinct[contest_col])[:20]):
            fmt["contest_column"] = [contest_col, party_col]
    if office_spec:
        fmt["house_from_columns"] = office_spec
    # Summary rows sit in the same shape as a candidate in most exports.
    fmt["exclude_choices"] = sorted(
        v for v in distinct[choice_col]
        if re.fullmatch(r"(?:ballots?\s+cast|over\s+votes?|under\s+votes?|total\s+votes?|"
                        r"write[\s-]?in(?:\s+votes?)?|blank\w*|scattering)", v, re.IGNORECASE)
    )
    return fmt


_FILING_HINT_RE = re.compile(r"candidat|filing|qualif|ballot", re.IGNORECASE)
_DATE_VALUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")


def infer_filing_format(rows: list[dict]) -> dict | None:
    """The column roles in a candidate FILING list — which is a results
    file with the results removed, so the one thing infer_format leans on
    hardest (a real vote count) is exactly what isn't there.

    What identifies one instead: contests that parse as federal offices, a
    party column, a name column, and — the reason this is worth reading at
    all — a column of election DATES, which is where a state's primary
    date comes from. Without a date column this returns None: a filing
    list that can't say which election it is for can't be used to say who
    is on a primary ballot rather than a general one.
    """
    if not rows:
        return None
    columns = [c for c in rows[0] if c]
    sample = rows[: min(len(rows), 4000)]
    values = {c: [str(r.get(c) or "").strip() for r in sample] for c in columns}
    distinct = {c: {v for v in values[c] if v} for c in columns}

    contest_col = max(
        columns,
        key=lambda c: sum(1 for v in distinct[c] if parse_office(v)),
        default=None,
    )
    if not contest_col or not any(parse_office(v) for v in distinct[contest_col]):
        return None

    date_col = next(
        (c for c in columns
         if distinct[c] and len(distinct[c]) <= 8
         and all(_DATE_VALUE_RE.match(v) for v in distinct[c])),
        None,
    )
    if not date_col:
        return None

    party_col = max(
        (c for c in columns
         if c != contest_col and 0 < len(distinct[c]) <= 12
         and sum(1 for v in distinct[c] if normalize_party(v)) >= max(1, len(distinct[c]) // 2)),
        key=lambda c: sum(1 for v in distinct[c] if normalize_party(v)) / len(distinct[c]),
        default=None,
    )
    if not party_col:
        return None

    # The name column is the one that varies most WITHIN a contest and
    # reads like a person — same test the results inference uses, and for
    # the same reason: a county column varies too, by hundreds.
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in sample:
        groups[str(row.get(contest_col) or "")].append(row)
    contests = [g for g in groups.values() if g][:8]

    def repeats(column: str) -> float:
        seen = [{str(r.get(column) or "").strip() for r in g if r.get(column)} for g in contests]
        seen = [v for v in seen if v]
        if len(seen) < 2:
            return 0.0
        scores = [len(a & b) / len(a | b) for i, a in enumerate(seen) for b in seen[i + 1:]]
        return sum(scores) / len(scores) if scores else 0.0

    named = [
        c for c in columns
        if c not in (contest_col, party_col, date_col) and len(distinct[c]) > 1
        and sum(1 for v in distinct[c] if _NAME_LIKE_RE.fullmatch(v)) / len(distinct[c]) >= 0.8
    ]
    if not named:
        return None
    # A filing list carries a candidate's home address, phone and email
    # beside their name. Only these four roles are ever read.
    return {
        "contest_column": contest_col,
        "choice_column": min(named, key=repeats),
        "party_column": party_col,
        "election_date_column": date_col,
    }


def _shape_of(payload: bytes) -> dict | None:
    """How to READ the download — workbook, or delimited with which
    delimiter — decided from the bytes rather than from a file extension,
    which states get wrong often enough to matter."""
    if payload[:2] == b"PK":
        return {"format": "xlsx"} if b"xl/worksheets" in payload[:4000] else {}
    head = payload[:8000].decode("utf-8", errors="replace").splitlines()
    if not head:
        return None
    counts = {d: head[0].count(d) for d in ("\t", "|", ",", ";")}
    delimiter = max(counts, key=lambda d: counts[d])
    return {"delimiter": delimiter} if counts[delimiter] else None


def _looks_federal(records: list[dict] | None) -> bool:
    """Nominees this source produced must all be federal seats in a
    plausible range — California's 52 districts is the ceiling any state
    can reach."""
    if not records:
        return False
    return all(
        r["office"] in ("S", "H") and (r["district"] is None or 0 <= r["district"] <= 60)
        for r in records
    )


def _federal_contests(rows: list[dict], fmt: dict) -> int:
    """How many federal contests a file CONTAINS, whatever it says about
    who won them. This is the acceptance test, because the most valuable
    moment to adopt a state's source is BEFORE its election: Florida
    publishes the file already staged with every candidate and no votes at
    all, and a source rejected for having no winners yet would have to
    wait for the next crawl to notice it does."""
    from app.pipeline.fetch.state_candidates_tabular import _tally

    spec = fmt.get("house_from_columns")
    count = 0
    for contest, entry in _tally(rows, fmt).items():
        if entry["office"] or parse_office(contest):
            count += 1
    return count if not spec or count else 0


async def _read(client: httpx.AsyncClient, url: str, label: str):
    """Download and parse a candidate results file into rows plus the
    shape that read it, or (None, None)."""
    from app.pipeline.fetch.state_candidates_tabular import MAX_DOWNLOAD_BYTES, _rows

    if not await _allowed(client, url):
        logger.info("%s: robots.txt disallows %s — not downloading it", label, url)
        return None, None
    resp = await _get(client, url, label, timeout=90.0)
    if resp is None or len(resp.content) > MAX_DOWNLOAD_BYTES:
        return None, None
    shape = _shape_of(resp.content)
    if shape is None:
        return None, None
    try:
        return _rows(resp.content, shape), shape
    except Exception as exc:  # noqa: BLE001
        # A crawler is pointed at files nobody promised were results: a
        # PDF served as .txt, a ragged export, an error page with a .csv
        # name. An unreadable candidate is a candidate to skip, never an
        # exception that ends the sweep for the whole state.
        logger.info("%s: candidate file at %s was unreadable (%s)", label, url, exc)
        return None, None


async def discover_source(
    client: httpx.AsyncClient, state: str, cycle: int, rules: dict | None = None,
) -> dict | None:
    """This state's current results source, found live, in the same shape a
    hand-written entry has — or None when nothing could be found AND
    proved. `rules` carries the state's stable law (threshold, top-two) and
    is passed straight through; nothing here infers it.

    A candidate is only returned once it has actually been parsed into
    plausible federal contests. Discovery that stops at "the URL responded"
    is how a crawler starts confirming nonsense.
    """
    from app.pipeline.fetch import state_candidates_tabular as tabular

    st = state.upper()
    rules = dict(rules or {})

    clarity = await _probe_clarity(client, st, cycle)
    if clarity:
        return {**rules, **{k: v for k, v in clarity.items() if not k.startswith("_")},
                "_evidence": clarity["_evidence"]}

    portal = await _probe_enhanced_voting(client, st, cycle)
    if portal:
        for report in portal["_report_candidates"][:4]:
            discovery = {**portal["discovery"], "report_name": report}
            stages = await tabular._discover_urls(client, st, cycle, discovery)
            urls = [s["url"] for s in stages if s.get("url")]
            if not urls:
                continue
            rows, shape = await _read(client, urls[0], f"{st} {report}")
            if not rows:
                continue
            fmt = infer_format(rows)
            if not fmt:
                continue
            source = {**rules, "strategy": "tabular", "discovery": discovery,
                      "format": {**shape, **fmt}}
            by_seat: dict = {}
            tabular._collect(rows, source["format"], by_seat,
                             rules.get("runoff_threshold_pct"),
                             int(rules.get("advance_count") or 1))
            records = [r for v in by_seat.values() for r in v]
            if _federal_contests(rows, source["format"]) and (
                not records or _looks_federal(records)
            ):
                source["_evidence"] = f"{portal['_evidence']}, report {report!r}"
                return source

    for page, link in (await _probe_pages(client, st, cycle))[:8]:
        rows, shape = await _read(client, link, f"{st} candidate results file")
        if not rows:
            continue
        fmt = infer_format(rows)
        if not fmt:
            continue
        fmt = {**shape, **fmt}
        by_seat: dict = {}
        tabular._collect(rows, fmt, by_seat, rules.get("runoff_threshold_pct"),
                         int(rules.get("advance_count") or 1))
        records = [r for v in by_seat.values() for r in v]
        if not _federal_contests(rows, fmt) or (records and not _looks_federal(records)):
            continue
        discovery = {
            "mode": "landing_page",
            "page_url": page,
            "link_regex": _generalise(link),
            "require_official": True,
        }
        # Prove the state can be re-found from its own page, rather than
        # trusting that it can: if reading the page back doesn't turn up
        # the file just parsed, the pattern is wrong and this source would
        # go dark the moment it was stored.
        stages = await tabular._discover_urls(client, st, cycle, discovery)
        if not any(s.get("url") == link for s in stages):
            logger.info(
                "%s: %s parses, but is not re-findable from %s — not adopting it",
                st, link, page,
            )
            continue
        return {
            **rules, "strategy": "tabular", "discovery": discovery, "format": fmt,
            "_evidence": f"results file linked from {page}",
        }
    return None


async def discover_filings(
    client: httpx.AsyncClient, state: str, cycle: int,
) -> dict | None:
    """A `filings` block for `state` — where its candidate FILING list
    lives and how to read it — or None.

    Worth its own pass because it answers a different question from
    results, at a different time: who is on the primary ballot, months
    before anyone votes, and when that primary is. States publish these
    beside their results, so the same page crawl finds them; what differs
    is the shape (see infer_filing_format).
    """
    from app.pipeline.fetch import state_candidates_tabular as tabular

    st = state.upper()
    pages = await _probe_pages(client, st, cycle)
    candidates = [
        (page, link) for page, link in pages if _FILING_HINT_RE.search(link)
    ]
    for page, link in candidates[:6]:
        rows, shape = await _read(client, link, f"{st} candidate filing list")
        if not rows:
            continue
        fmt = infer_filing_format(rows)
        if not fmt:
            continue
        discovery = {
            "mode": "landing_page",
            "page_url": page,
            "link_regex": _generalise(link),
        }
        stages = await tabular._discover_urls(client, st, cycle, discovery)
        if not any(s.get("url") == link for s in stages):
            continue
        return {
            "discovery": discovery,
            "format": {**shape, **fmt},
            "_evidence": f"candidate filing list linked from {page}",
        }
    return None


def _generalise(link: str) -> str:
    """Turn one cycle's file URL into a pattern that finds NEXT cycle's:
    every run of digits — the election date, the state's internal election
    id, the cycle in the filename — becomes a wildcard, because those are
    exactly the parts that change and the parts nobody should have to come
    back and edit."""
    return re.sub(r"\d+", r"\\d+", re.escape(link))
