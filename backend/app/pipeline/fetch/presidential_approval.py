"""Fetch + parse presidential approval polling from UCSB's American
Presidency Project (presidency.ucsb.edu).

Replaces the Gallup dependency this platform's Public Mandate dimension
used to lean on for its "modern presidents" claim — Gallup itself ended
presidential approval tracking entirely in Feb 2026 after 88 years.
FiveThirtyEight's approval CSV (the obvious alternative) is also dead —
its dedicated data site now 302s to abcnews.go.com/politics. UCSB's
American Presidency Project is the one still-live, reputable source found
(2026-07 research): each president gets a per-term page with a real HTML
data table, still updated for the sitting president (most recent Trump
2nd-term row observed during development: poll dated 07/08-07/13/2026,
sourced "ipsos-wapo" — UCSB has already adapted to aggregate AP-NORC/
CNN-SSRS/Marist/Verasight/Pew/etc. in place of the discontinued Gallup
feed, rather than going stale).

No API or CSV/JSON export exists — this scrapes the rendered HTML table,
so a redesign of UCSB's pages breaks it without warning. Table markup is real but not perfectly
uniform across the ~90 years of pages this project maintains: some rows
wrap cell values in a <p> tag, others put text directly in the <td> —
.text_content() handles both. A trailing all-blank row (template
artifact) is also observed on at least one live page and must be skipped.

URL slugs are NOT reliably derivable from a president's name (middle
initials, "2nd-term" suffixes for repeat presidents, inconsistent
formatting) — listed per-president below, each verified live against
presidency.ucsb.edu during development (2026-07). A president sworn in
after that list is found in UCSB's own index of approval pages, by name
(approval_slugs), so the list never needs a new entry. Only presidents with
real polling-era coverage are included (Truman #33 onward, matching this
platform's existing "modern presidents" framing) — pre-Truman presidents
have no live source; their Public Mandate uses the election-margin
historical proxy instead (see presidential_elections.py), never a seed
value.

The pages also publish Gallup's by-party breakdown (Democrats,
Independents and Republicans approving) for every president from Truman.
It is parsed for one use (president v9): telling a president's approval
apart from the polarization of the era they served in. Approval among the
other party fell from about 49% under Eisenhower to 5% under Biden while
approval among the president's own party rose, so raw approval ranked
presidents partly by era (r = -0.40 with the parties' distance in
Congress, 14 completed presidencies). Each party group's approval is
compared with what that group gave presidents under the same polarization
(president_scorer.partisan_reference). A partisan *gap* is never scored or
shown as the president's doing: the 2026-07 objection to that (a gap can
reflect opposition that was coming regardless of merit) stands, and this
use removes that environment rather than attributing it.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

BASE_URL = "https://www.presidency.ucsb.edu/statistics/data"

# Verified live (200 OK, real approval table present) against
# presidency.ucsb.edu, 2026-07. Presidents not listed here (pre-Truman)
# have no UCSB polling-era page and keep their seed Public Mandate value.
PRESIDENT_APPROVAL_SLUGS: dict[str, str] = {
    "truman-33": "harry-s-truman-public-approval",
    "eisenhower-34": "dwight-d-eisenhower-public-approval",
    "jfk-35": "john-f-kennedy-public-approval",
    "lbj-36": "lyndon-b-johnson-public-approval",
    "nixon-37": "richard-m-nixon-public-approval",
    "ford-38": "gerald-r-ford-public-approval",
    "carter-39": "jimmy-carter-public-approval",
    "reagan-40": "ronald-reagan-public-approval",
    "ghwbush-41": "george-bush-public-approval",
    "clinton-42": "william-j-clinton-public-approval",
    "gwbush-43": "george-w-bush-public-approval",
    "obama-44": "barack-obama-public-approval",
    "trump-45": "donald-j-trump-public-approval",
    "biden-46": "joseph-r-biden-public-approval",
    "trump-47": "donald-j-trump-2nd-term-public-approval",
}

# UCSB's index of every approval page, newest president first, each link
# named for the president ("Donald J. Trump", twice: newest term first).
# A president sworn in after the table above was written is found here by
# name, so their Public Mandate gets their own polling without anyone
# typing a slug; the table stays as the verified set for the rest.
INDEX_URL = f"{BASE_URL}/presidential-job-approval-all-data"
_INDEX_CACHE_KEY = "approval-index"

# UCSB doesn't publish a documented rate limit; this is a courteous
# default for a nonprofit academic site rather than a fitted value (same
# posture as this file's peers when no documented limit exists).
_RATE_LIMITER = RateLimiter(rps=1.0)

_CACHE_TIER = "presidential-approval"
_CACHE_MAX_AGE_HOURS = 20  # a bit under the ~24h pipeline cadence


@dataclass
class ApprovalPoll:
    start_date: str  # MM/DD/YYYY, as published
    end_date: str
    approving: float | None
    disapproving: float | None
    unsure: float | None
    source: str | None
    # Approval among Democrats, independents and Republicans (Gallup's
    # breakdown); None where the page gives none for the poll.
    dem: float | None = None
    ind: float | None = None
    rep: float | None = None


def _cell_text(td) -> str:
    return (td.text_content() or "").strip()


def _cell_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_approval_table(html: str) -> list[ApprovalPoll]:
    """Parse UCSB's approval-data table into poll records, oldest first.

    Column order (verified against several live pages, 2026-07): Start
    date, End Date, Approving, Disapproving, Unsure/NoData, [blank
    spacer], then the by-party breakdown and/or Source. The breakdown's
    order differs between pages (Democrats first on most, Republicans
    first on the 2nd-term Trump page, whose header also spells
    "Opproving") and so do its labels ("Democrats Approving",
    "Democratic Approval", "DemocraticApproval"), so its columns are found
    by the label's party stem, never by position.
    """
    doc = lxml_html.fromstring(html)
    tables = doc.cssselect("table")
    if not tables:
        return []
    rows = tables[0].cssselect("tr")

    party_cols: dict[str, int] = {}
    polls: list[ApprovalPoll] = []
    for row in rows:
        labels = [_cell_text(c) for c in row.cssselect("th, td")]
        if any(label.startswith("Democrat") for label in labels):
            # "Democrats Approving", "Democratic Approval", "DemocraticApproval"
            party_cols = {
                key: i for i, label in enumerate(labels)
                for key, prefix in (("dem", "Democrat"), ("ind", "Independent"), ("rep", "Republican"))
                if label.startswith(prefix)
            }
            continue
        cells = row.cssselect("td")
        if len(cells) < 5:
            continue
        start = _cell_text(cells[0])
        end = _cell_text(cells[1])
        if not start or not end:
            continue  # trailing blank template row, or a malformed one
        approving = _cell_float(_cell_text(cells[2]))
        disapproving = _cell_float(_cell_text(cells[3]))
        unsure = _cell_float(_cell_text(cells[4]))
        if approving is None and disapproving is None:
            continue  # header-ish or otherwise unusable row
        source_text = _cell_text(cells[-1])
        source = source_text if source_text and _cell_float(source_text) is None else None
        by_party = {
            key: _cell_float(_cell_text(cells[i])) if i < len(cells) else None
            for key, i in party_cols.items()
        }
        polls.append(ApprovalPoll(
            start_date=start, end_date=end,
            approving=approving, disapproving=disapproving, unsure=unsure,
            source=source, **by_party,
        ))

    # Row order is NOT consistent across UCSB's pages — verified live,
    # 2026-07: Trump's 1st-term page lists newest-first, his 2nd-term page
    # lists oldest-first. Sorting by parsed start_date (rather than
    # trusting either assumption or blindly reversing) is correct
    # regardless of which convention a given page happens to use. Any row
    # with an unparseable date sorts first (datetime.min) rather than
    # raising and discarding the whole page.
    def _sort_key(p: ApprovalPoll) -> datetime:
        try:
            return datetime.strptime(p.start_date, "%m/%d/%Y")
        except ValueError:
            return datetime.min

    polls.sort(key=_sort_key)
    return polls


async def fetch_president_approval_history(
    db: Session, president_id: str, slug: str | None = None,
) -> list[ApprovalPoll] | None:
    """Fetch + parse a president's full approval-poll history from UCSB.

    Returns None if this president has no known UCSB page (pre-Truman) or
    the fetch/parse failed — never an empty-but-successful list conflated
    with "no data source", so callers can tell "not applicable" from
    "temporarily unavailable"."""
    slug = slug or PRESIDENT_APPROVAL_SLUGS.get(president_id)
    if slug is None:
        return None

    # v2: the cached rows carry the by-party columns.
    cache_key = f"approval-v2-{president_id}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return [ApprovalPoll(**p) for p in cached["polls"]]

    url = f"{BASE_URL}/{slug}"
    resp = await fetch_with_retry_requests(
        _RATE_LIMITER, "GET", url,
        log_label="UCSB approval",
    )
    if resp is None or resp.status_code != 200:
        # url (not president_id) identifies which president in these
        # messages — a bare id like "obama-44" reads as a person
        # identifier to CodeQL's clear-text-logging heuristic even though
        # it's a public internal slug; the URL conveys the same info
        # without that false-positive class (see error_utils.py's
        # docstring on this codebase's prior fights with the same query).
        logger.warning("Failed to fetch UCSB approval data (%s)", url)
        return None

    try:
        polls = _parse_approval_table(resp.text)
    except Exception:
        logger.exception("Failed to parse UCSB approval table (%s)", url)
        return None

    if not polls:
        logger.warning("UCSB approval page parsed to zero rows — page structure may have changed (%s)", url)
        return None

    api_cache_set(db, _CACHE_TIER, cache_key, {
        "polls": [
            {
                "start_date": p.start_date, "end_date": p.end_date,
                "approving": p.approving, "disapproving": p.disapproving,
                "unsure": p.unsure, "source": p.source,
                "dem": p.dem, "ind": p.ind, "rep": p.rep,
            }
            for p in polls
        ],
    })
    return polls


def dated_approvals(polls: list[ApprovalPoll]) -> list[tuple[date, float]]:
    """(start date, approve %) for every poll with both, in date order. A
    poll with an unparseable date is left out rather than guessed into
    place."""
    out = []
    for p in polls:
        if p.approving is None:
            continue
        try:
            out.append((datetime.strptime(p.start_date, "%m/%d/%Y").date(), p.approving))
        except ValueError:
            continue
    return sorted(out)


def dated_by_party(polls: list[ApprovalPoll]) -> list[tuple[date, dict[str, float]]]:
    """(start date, {"D", "I", "R": approve %}) for every poll with all
    three groups, in date order; polls without the breakdown are left out."""
    out = []
    for p in polls:
        if p.dem is None or p.ind is None or p.rep is None:
            continue
        try:
            day = datetime.strptime(p.start_date, "%m/%d/%Y").date()
        except ValueError:
            continue
        out.append((day, {"D": p.dem, "I": p.ind, "R": p.rep}))
    return sorted(out, key=lambda pair: pair[0])


def recent_polls(polls: list[ApprovalPoll], days: int = 90, as_of: datetime | None = None) -> list[ApprovalPoll]:
    """Polls whose start_date falls within the last `days` days of `as_of`
    (defaults to now) — a rolling recent window distinct from Public
    Mandate's own full-term average, for a "how is this changing lately"
    view of the currently-serving president specifically. Any poll with
    an unparseable date is excluded rather than guessed into the window."""
    as_of = as_of or utcnow()
    cutoff = as_of - timedelta(days=days)
    result = []
    for p in polls:
        try:
            d = datetime.strptime(p.start_date, "%m/%d/%Y")
        except ValueError:
            continue
        if d >= cutoff:
            result.append(p)
    return result


def parse_approval_index(html: str) -> list[tuple[str, str]]:
    """(president's name, page slug) for every approval page UCSB's index
    links, in its order (newest first), one per page."""
    doc = lxml_html.fromstring(html)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in doc.xpath('//a[contains(@href, "-public-approval")]'):
        slug = (a.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
        name = a.text_content().strip()
        # The index splits "Joseph R. Biden, Jr." over two links to one
        # page; the first carries the name.
        if not slug or slug in seen or not name or name.startswith(","):
            continue
        seen.add(slug)
        out.append((name, slug))
    return out


async def approval_slugs(db: Session, presidents: list) -> dict[str, str]:
    """{president id: page slug}: the verified table, plus every president
    numbered after its newest entry whose name the index links. A name the
    index lists more than once (a president with two terms) pairs its links,
    newest first, with that president's terms, newest first."""
    from app.pipeline.fetch.historical_executive_orders import name_key

    slugs = dict(PRESIDENT_APPROVAL_SLUGS)
    numbered = {p.id: p.number for p in presidents}
    newest = max((numbered[pid] for pid in slugs if pid in numbered), default=0)
    later = sorted((p for p in presidents if p.number > newest), key=lambda p: -p.number)
    if not later:
        return slugs

    index = api_cache_get(db, _CACHE_TIER, _INDEX_CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if index is None:
        resp = await fetch_with_retry_requests(_RATE_LIMITER, "GET", INDEX_URL, log_label="UCSB approval index")
        if resp is None or resp.status_code != 200:
            logger.warning("Failed to fetch UCSB approval index (%s)", INDEX_URL)
            return slugs
        index = {"pages": parse_approval_index(resp.text)}
        api_cache_set(db, _CACHE_TIER, _INDEX_CACHE_KEY, index)

    known = set(slugs.values())
    by_name: dict[str, list[str]] = {}
    for name, slug in index["pages"]:
        by_name.setdefault(name_key(name), []).append(slug)
    for president in later:
        pages = by_name.get(name_key(president.name), [])
        # Pages are newest first, as are the later presidents; the first
        # page of this name not already claimed is this term's.
        page = next((s for s in pages if s not in known), None)
        if page:
            slugs[president.id] = page
            known.add(page)
    return slugs
