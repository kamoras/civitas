"""Kentucky's confirmed-general-candidate strategy — the State Board of
Elections' own "Primary Certification of Vote Totals" PDF (one of
potentially many per-state strategies in state_candidates.py; see that
module for the shared contract).

A genuinely different PDF shape from every other state's: this is a
county-by-county precinct tally where each CANDIDATE is a COLUMN and
each COUNTY is a ROW, not the row-per-candidate table every other
strategy expects — so it cannot reuse state_candidates_tabular's format
machinery for reading candidates, only its landing_page URL discovery
(the filename itself is NOT evergreen — verified live: 2024's file was
"/results/2020-2029/Documents/2024 Primary Results.pdf" while 2026's is
"/Documents/2026 Primary Certification of Vote Totals Final.pdf", a
different name AND a different directory — so the link is read off each
year's own results page, never written down here).

Because a candidate is a column, Kentucky often prints that column's
header ROTATED 90 degrees (upright=False chars, reading top-to-bottom)
to fit many names into narrow columns on a crowded primary ballot (the
2026 US Senate GOP primary had 11 candidates); a district with few
candidates gets normal upright headers instead. Both are handled the
same way here: every header word is assigned to its NEAREST vote-total
column by word CENTER, not left edge (verified live: a long surname
like the real "PERRY-ADELMANN" starts far enough left of its own
column's total that left-edge matching wrongly pulled it into the
PREVIOUS candidate's cluster; the center holds steady regardless of
word length, in both the rotated and upright cases), and within that
word cluster, the ALL-CAPS word (not
position, not row/column order within the cluster) is trusted as the
surname — verified against real crowded fields where a given name
itself was multiple words ("Michael James FARIS") or where the
apparent word order look reversed under rotation; ALL-CAPS is the one
signal that held constant in every real case examined, including a
real hyphenated surname (PERRY-ADELMANN) and a real given name that
included the word "Other" (WENZEL's ballot-printed given name is
"Other Donald" — confirmed genuine by checking "Other" never appears
as a distinct vote-total column anywhere in the document, only ever as
an ordinary Title-Case header word).

Never guessed: if a cluster doesn't resolve to EXACTLY one ALL-CAPS
surname word, or the number of resolved candidate clusters doesn't
match the number of real vote-total columns on that page (verified
live on the real 2026 Democratic US Senate primary: 8 header names are
printed for only 7 counted vote columns — one listed candidate
received no tallied votes at all, most likely withdrawn, and nothing
in the document says which — this strategy correctly refuses the whole
contest rather than guess which name to drop), the whole contest is
skipped rather than risk attributing real votes to the wrong name.

Kentucky decides its federal primaries by plain plurality (no runoff,
no convention threshold), so runoff_threshold_pct is null and
pick_nominee (advance_count=1) is the correct shared chooser — it also
already refuses to name a winner on a tie, which matters here since
this module supplies no other tie-breaking signal.
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import pick_nominee
from app.pipeline.fetch.state_candidates_tabular import _discover_urls
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

DISCOVERY = {
    "mode": "landing_page",
    "page_url": "https://elect.ky.gov/results/2020-2029/Pages/{year}.aspx",
    # The directory holding the file is NOT stable across cycles either
    # (2024's link is under /results/2020-2029/Documents/, 2026's under
    # /Documents/ at the site root) — [^"']*Documents/ captures whatever
    # that directory turns out to be so the match resolves (via urljoin)
    # to a correct absolute path rather than one relative to this page.
    "link_regex": (
        r"[^\"']*Documents/{year}%20Primary%20"
        r"(?:Results|Certification%20of%20Vote%20Totals(?:%20Final)?)\.pdf"
    ),
}

_rate_limiter = RateLimiter(rps=1.0)

_SENATE_RE = re.compile(r"United States Senator")
_HOUSE_RE = re.compile(r"United States Representative in Congress|US Representative")
_DISTRICT_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\s+Congressional District")
_PARTY_RE = re.compile(r"(Republican|Democratic)\s+Party")
_SURNAME_RE = re.compile(r"^[A-Z][A-Z'\-]+$")
# The masthead/title vocabulary's only ALL-CAPS multi-letter token — "US"
# in the short-form "US Representative" title, which a page can carry on
# the SAME page as its Total Votes row (a short contest needs no
# continuation page) and which would otherwise land in whichever
# candidate column its x0 happens to be nearest, one false ALL-CAPS
# match away from wrongly forcing that column's real surname into an
# "ambiguous, skip" verdict. Every other masthead/title word is
# Title-Case or carries a digit/punctuation the surname regex excludes.
_INSTITUTIONAL_ALL_CAPS = {"US"}


def _title_on_page(text: str) -> tuple[str, int | None, str] | None:
    """(office, district, party) for a page whose text carries a full
    contest title, or None if this page doesn't start one — a section
    divider ("For the office of United States Senator") has the office
    but no party and must NOT be mistaken for a real contest title."""
    party_m = _PARTY_RE.search(text)
    if not party_m:
        return None
    party = "R" if party_m.group(1) == "Republican" else "D"
    if _SENATE_RE.search(text):
        return "S", None, party
    if _HOUSE_RE.search(text):
        dist_m = _DISTRICT_RE.search(text)
        if not dist_m:
            return None
        return "H", int(dist_m.group(1)), party
    return None


def _rows_by_top(words: list[dict]) -> list[list[dict]]:
    """Words grouped by shared row (exact-matching `top`), each row
    sorted left to right — NOT meaningful for a rotated header, where
    every word keeps its own distinct `top`, only for the upright
    county-data rows and the Total Votes row this function is actually
    used to find."""
    by_top: dict[float, list[dict]] = {}
    for w in words:
        by_top.setdefault(round(w["top"], 1), []).append(w)
    return [sorted(by_top[t], key=lambda w: w["x0"]) for t in sorted(by_top)]


def _parse_total_page(
    words: list[dict], office: str, district: int | None, party: str,
) -> list[dict]:
    """This page's contest, as [{"office", "district", "party",
    "last_name"}] for whoever pick_nominee names the winner — empty if
    nothing can be named safely."""
    rows = _rows_by_top(words)
    total_row = next(
        (r for r in rows if r and r[0]["text"] == "Total"
         and len(r) > 1 and r[1]["text"] == "Votes"), None,
    )
    if not total_row:
        return []
    columns: list[float] = []
    votes: list[int] = []
    for w in total_row[2:]:
        cleaned = w["text"].replace(",", "")
        if not cleaned.isdigit():
            continue
        columns.append(w["x0"])
        votes.append(int(cleaned))
    if not columns:
        return []

    # No need to isolate the header region at all: a rotated header can
    # start almost anywhere on a continuation page (verified live — a
    # repeated header on one page started as high as top=90, well above
    # where it sits on that contest's own title page), so any fixed
    # y-band is fragile. Instead every word on the page is clustered by
    # nearest vote-total column, masthead and county data included — and
    # it's harmless, because nothing else on a real page is an ALL-CAPS
    # multi-letter word: county names and given names are Title-Case,
    # the masthead and "Total Votes" are Title-Case, and vote counts are
    # digits. Only a candidate's surname ever matches the ALL-CAPS check
    # below, so clustering the whole page finds it without needing to
    # know in advance where the header starts or ends.
    total_row_ids = {id(w) for w in total_row}
    clusters: dict[int, list[dict]] = {i: [] for i in range(len(columns))}
    for w in words:
        if id(w) in total_row_ids or w["text"] in _INSTITUTIONAL_ALL_CAPS:
            continue
        # A word's own CENTER, not its left edge (x0), is what tracks its
        # column: a long surname like "PERRY-ADELMANN" starts far enough
        # left of a narrow numeric column's x0 that x0-nearest wrongly
        # pulled it into the PREVIOUS candidate's cluster (verified live
        # on the real 2nd District GOP primary). The center holds steady
        # regardless of word length.
        center = (w["x0"] + w["x1"]) / 2
        nearest = min(range(len(columns)), key=lambda i: abs(columns[i] - center))
        clusters[nearest].append(w)

    surnames: list[str | None] = []
    for i in range(len(columns)):
        caps = [w["text"] for w in clusters[i] if _SURNAME_RE.match(w["text"])]
        surnames.append(caps[0] if len(caps) == 1 else None)

    if any(s is None for s in surnames):
        logger.info(
            "KY %s district %s %s: a candidate column's header didn't "
            "resolve to exactly one surname, skipping this contest",
            office, district, party,
        )
        return []

    choices = list(zip(surnames, votes, strict=True))
    won = pick_nominee(choices, runoff_threshold_pct=None)
    if not won:
        return []
    return [{"office": office, "district": district, "party": party, "last_name": won[0]}]


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is KY-only by construction
) -> list[dict] | None:
    stages = await _discover_urls(client, "KY", year, DISCOVERY)
    urls = [s["url"] for s in stages if s.get("url")]
    if not urls:
        return None

    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", urls[0], timeout=60.0,
        log_label=f"KY primary certification {year}", headers=BROWSER_HEADERS,
    )
    if resp is None:
        return None

    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            results: list[dict] = []
            current: tuple[str, int | None, str] | None = None
            for page in pdf.pages:
                text = page.extract_text() or ""
                title = _title_on_page(text)
                if title:
                    current = title
                if "Total Votes" not in text or current is None:
                    continue
                words = page.extract_words()
                office, district, party = current
                results.extend(_parse_total_page(words, office, district, party))
                current = None  # this contest is fully consumed
    except Exception:
        logger.exception("KY primary certification PDF for %d failed to parse", year)
        return None

    if not results:
        logger.warning("KY primary certification PDF for %d yielded no contests", year)
        return None

    return results
