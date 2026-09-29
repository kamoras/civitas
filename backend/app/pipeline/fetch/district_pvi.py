"""Per-district Cook PVI, one table per Congress's district lines.

Member scoring needs the partisan lean of the district a member was
ELECTED on. That is a fixed fact for the life of a Congress, and it stops
being what Wikipedia's district infoboxes show the moment a state redraws:
editors replace each infobox's PVI with the value for the district's new
lines. This module used to scrape those infoboxes weekly, and by
2026-09 the file was a silent mix of 119th-Congress and 2026-map values
across the nine states that redrew for 2026 (TN-9 read R+9 on the new map
for a member elected in a D+23 seat; MO-5 read R+9 on a map the Supreme
Court then blocked).

So the source is now pinned, per Congress, in app/data/
district_pvi_sources.json: one immutable revision of Wikipedia's "Cook
Partisan Voting Index" article, whose per-district table transcribes a
named Cook release and whose own citation says which map it describes.
A pinned revision cannot drift. The ingest then refuses a table unless:

- the revision fetched is the one pinned, and its text carries the pinned
  label (the Cook release / map the citation names);
- every one of the 435 seats parses exactly once, with each state's seat
  count right, values in range and a plausible R/D split;
- the revision's own prose summary agrees with its table (districts more
  R / more D / EVEN, and the stated median). An editor part-way through
  swapping in a new release leaves the prose on the old one — the
  2026-07-29 revision that started the 2026 edits fails this check;
- a Congress whose lines were redrawn from an earlier one is identical to
  it in every state that did not redraw, and in every state that did has
  all its seats, differs somewhere, and keeps the state's mean district
  lean (voters move between a state's districts, not out of the state;
  see REDRAW_MEAN_SHIFT_MAX).

Output (/data/district_pvi.json; bundled fallback app/data/
district_pvi.json via scripts/fetch_district_pvi.py): every configured
Congress's table under "congresses", and the sitting Congress's table
copied to the top-level "districts" that score_calculator._district_pvi()
and fetch/voteview.py read ("congress" names whose table it is).

The sitting Congress is read from the clock on every call
(app.config.sitting_congress: noon ET on Jan 3 of an odd year starts the
next one, per the 20th Amendment), not from settings.CURRENT_CONGRESS,
which is computed once when the process starts. ensure_sitting_lines()
runs before every nightly chain, so the first nightly run after that noon
copies the new Congress's table up from the tables already on disk — no
restart, no fetch — once that Congress has an entry in the sources file.
If it has none, member scoring stays on the newest pinned lines before
it, nothing is fetched for it, and one ops alert per Congress asks for
the entry.

Supplementary re-runs the fetch weekly (idempotent — the pins don't move)
and compares the article's CURRENT revision with the newest pinned table;
a difference raises an ops alert asking someone to review and, if it is a
correction or a court-ordered map change, advance the pin. It is never
ingested on its own. Any fetch or gate failure keeps the previous file
(never punitive), same contract as every other ingest in this package.
"""

import json
import logging
import pathlib
import re
import urllib.parse
from datetime import date

from app.atomic_write import write_text_atomic
from app.ordinals import ordinal
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_CODES = {v.lower(): k for k, v in STATE_NAMES.items()}

# Post-2020-census apportionment (118th Congress onward), 435 seats. Will
# need updating after the 2030 census reapportions seats between states —
# same "unavoidable one-time human step after a real-world event" class as
# adding a new president, not a decay path this module can self-correct.
SEATS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5,
    "DE": 1, "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9,
    "IA": 4, "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9,
    "MI": 13, "MN": 8, "MS": 4, "MO": 8, "MT": 2, "NE": 3, "NV": 4,
    "NH": 2, "NJ": 12, "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15,
    "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7, "SD": 1, "TN": 9,
    "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2, "WI": 8,
    "WY": 1,
}

API = "https://en.wikipedia.org/w/api.php"
# Generic, and deliberately carries no personal contact detail.
HEADERS = {"User-Agent": "CivitasCivicPlatform/1.0 (district PVI ingestion)"}
_PVI_PATH = "/data/district_pvi.json"
SOURCES_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "district_pvi_sources.json"
BUNDLED_PATH = SOURCES_PATH.parent / "district_pvi.json"

# A weekly handful of requests to a large public site needs no aggressive
# pacing; Wikimedia does answer bursts with 429, which the retry backs off
# from (10s, 20s, ...).
_rate_limiter = RateLimiter(rps=1.0)
_RETRIES = 5
_BACKOFF_S = 10.0

_SIGN = "positive = R lean, negative = D lean (matches state_pvi.json)"


# ── Parsing ────────────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"(?m)^==\s*By congressional district\s*==\s*$")
_NEXT_SECTION_RE = re.compile(r"(?m)^==[^=].*==\s*$")
# One table row: the district cell, then the PVI cell on the next line.
_ROW_RE = re.compile(
    r"\{\{\s*ushr\s*\|([^|}]+)\|([^|}]+)(?:\|[^}]*)?\}\}\s*\n\|\s*"
    r"\{\{\s*Shading PVI\s*\|([^}]*)\}\}",
    re.IGNORECASE,
)
_DISTRICT_CELL_RE = re.compile(r"\{\{\s*ushr\s*\|", re.IGNORECASE)
_NUMBER_WORDS = {
    w: i for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_COUNTS_RE = re.compile(
    r"(\w+) districts are more Republican than the national average, "
    r"(\w+) districts are more Democratic than the national average, "
    r"and (\w+) districts? matche?s? the national average",
    re.IGNORECASE,
)
_MEDIAN_RE = re.compile(
    r"With a PVI of (EVEN|[DR]\+\d+),\s*\{\{\s*ushr\s*\|([^|}]+)\|([^|}]+)[^}]*\}\}"
    r"(?:\{\{[^}]*\}\})*\s*was determined to be the median",
    re.IGNORECASE,
)


def district_title(state: str, district: int) -> str:
    """Wikipedia's article title for a district (used by callers that link
    to it; the ingest itself reads the PVI article's table)."""
    name = STATE_NAMES[state]
    possessive = f"{name}'s"
    if district == 0:
        return f"{possessive} at-large congressional district"
    return f"{possessive} {ordinal(district)} congressional district"


def _state_code(raw: str) -> str | None:
    raw = raw.strip()
    if raw.upper() in SEATS:
        return raw.upper()
    return _STATE_CODES.get(raw.lower())


def _district_number(raw: str) -> int | None:
    raw = raw.strip()
    if raw.upper() in ("AL", "AT-LARGE", "AT LARGE"):
        return 0
    return int(raw) if raw.isdigit() else None


def _pvi_value(text: str) -> int | None:
    """"R+7" / "D+12" / "EVEN" -> signed int (positive = R)."""
    t = text.strip().upper().replace(" ", "")
    if t == "EVEN":
        return 0
    m = re.fullmatch(r"([DR])\+(\d+)", t)
    if not m:
        return None
    return int(m.group(2)) * (1 if m.group(1) == "R" else -1)


def _shading_value(args: str) -> int | None:
    """Arguments of {{Shading PVI|...}}: "R|7", "D|value=12", "EVEN",
    "EVEN|0"."""
    parts = [p.strip() for p in args.split("|")]
    party = parts[0].upper()
    if party == "EVEN":
        return 0
    if party not in ("R", "D") or len(parts) < 2:
        return None
    num = parts[1].split("=")[-1].strip()
    if not num.isdigit():
        return None
    return int(num) * (1 if party == "R" else -1)


def _district_section(wikitext: str) -> str | None:
    m = _SECTION_RE.search(wikitext)
    if not m:
        return None
    rest = wikitext[m.end():]
    nxt = _NEXT_SECTION_RE.search(rest)
    return rest[:nxt.start()] if nxt else rest


def parse_district_table(wikitext: str) -> tuple[dict[str, int], list[str]]:
    """The article's "By congressional district" table -> ("ST-N" -> signed
    PVI, problems). Every row that names a district must parse; a row that
    doesn't, a duplicate, or an unknown state is a problem, never skipped
    silently. At-large seats are keyed "ST-0"."""
    section = _district_section(wikitext)
    if section is None:
        return {}, ["no 'By congressional district' section"]
    start = section.find("{|")
    if start < 0:
        return {}, ["no table in the 'By congressional district' section"]
    section = section[start:]  # the table only, not the prose above it
    table: dict[str, int] = {}
    problems: list[str] = []
    rows = _ROW_RE.findall(section)
    for state_raw, district_raw, shading in rows:
        st = _state_code(state_raw)
        dn = _district_number(district_raw)
        val = _shading_value(shading)
        if st is None or dn is None or val is None:
            problems.append(f"unparseable row: {state_raw}|{district_raw}|{shading}")
            continue
        key = f"{st}-{dn}"
        if key in table:
            problems.append(f"duplicate row for {key}")
            continue
        table[key] = val
    # Rows the row pattern didn't match at all (a new cell layout) would
    # otherwise vanish; the district-cell count exposes them.
    cells = len(_DISTRICT_CELL_RE.findall(section))
    if cells != len(rows):
        problems.append(f"{cells} district cells in the table but {len(rows)} parsed rows — layout drift?")
    return table, problems


def _count(word: str) -> int | None:
    w = word.strip().lower()
    if w.isdigit():
        return int(w)
    return _NUMBER_WORDS.get(w)


def parse_stated_summary(wikitext: str) -> dict:
    """What the revision's own prose says about its table: counts of
    districts more R / more D / EVEN, and the named median district. Keys
    are absent when the sentence isn't there."""
    section = _district_section(wikitext) or ""
    out: dict = {}
    m = _COUNTS_RE.search(section)
    if m:
        counts = [_count(g) for g in m.groups()]
        if None not in counts:
            out["r"], out["d"], out["even"] = counts
    m = _MEDIAN_RE.search(section)
    if m:
        st, dn = _state_code(m.group(2)), _district_number(m.group(3))
        val = _pvi_value(m.group(1))
        if st is not None and dn is not None and val is not None:
            out["median_key"] = f"{st}-{dn}"
            out["median_pvi"] = val
    return out


# ── Gates ──────────────────────────────────────────────────────────────

def ingestion_gates(result: dict[str, int]) -> list[str]:
    """Structural sanity checks on one table — guard the ingestion (sign
    convention, coverage, parse drift), not the scores."""
    failures = []
    if len(result) != 435:
        failures.append(f"expected 435 districts, got {len(result)}")
    per_state: dict[str, int] = {}
    for k in result:
        st = k.split("-")[0]
        per_state[st] = per_state.get(st, 0) + 1
    if set(per_state) != set(SEATS):
        failures.append(f"state coverage mismatch: {sorted(set(SEATS) ^ set(per_state))}")
    wrong = sorted(st for st in SEATS if st in per_state and per_state[st] != SEATS[st])
    if wrong:
        failures.append(f"seat count mismatch in {wrong}")
    for k in result:
        st, _, dn = k.partition("-")
        n = SEATS.get(st)
        if n is not None and not (dn == "0" if n == 1 else dn.isdigit() and 1 <= int(dn) <= n):
            failures.append(f"district {k} does not exist in the apportionment")
    vals = list(result.values())
    if not all(-45 <= v <= 45 for v in vals):
        failures.append("PVI outside plausible +/-45 range — parse drift?")
    # Sign convention: the national map has both leans in the hundreds; a
    # parser that flipped D/R or dropped a sign can't produce that.
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    if not (150 <= r_lean <= 285 and 150 <= d_lean <= 285):
        failures.append(f"implausible lean split R={r_lean} D={d_lean}")
    return failures


def self_consistency_gates(table: dict[str, int], summary: dict, median_tolerance: int = 0) -> list[str]:
    """The revision's prose must describe its own table. Counts are exact.
    The stated median district must hold the stated value, and the table's
    median (the 218th of 435) must equal the stated median exactly — Cook
    computes it from the same table, so a transcription agrees to the
    point. `median_tolerance` is not a fudge factor: a source entry sets it
    only with a written reason (`_why_median_tolerance`, enforced by
    check_table) naming the change made after Cook's release that moved
    the median while the release's sentence stayed as written — the 120th
    Congress's pin carries Missouri's 2026-09 reversion to its 2022 map,
    which moved the median from R+3 to R+2."""
    failures = []
    if not {"r", "d", "even"} <= summary.keys():
        failures.append("revision states no district counts to check its table against")
    else:
        vals = list(table.values())
        got = (sum(v > 0 for v in vals), sum(v < 0 for v in vals), sum(v == 0 for v in vals))
        want = (summary["r"], summary["d"], summary["even"])
        if got != want:
            failures.append(
                f"table counts R/D/EVEN {got} disagree with the revision's own prose {want} "
                "— a table part-way through being replaced?"
            )
    if "median_key" not in summary:
        failures.append("revision names no median district to check its table against")
    else:
        key, stated = summary["median_key"], summary["median_pvi"]
        if table.get(key) != stated:
            failures.append(f"stated median {key} at {stated:+d} but the table has {table.get(key)}")
        vals = sorted(table.values())
        if vals and abs(vals[len(vals) // 2] - stated) > median_tolerance:
            failures.append(
                f"table median {vals[len(vals) // 2]:+d} is not within {median_tolerance} of stated {stated:+d}"
            )
    return failures


# How far a redrawn state's mean district lean may move between the base
# table and the new one. A redraw moves voters between a state's districts
# but not in or out of the state, and districts are equal-population, so
# with the same Cook window on both sides the unweighted mean of the
# state's district PVIs is (nearly) conserved. Not exactly: each district
# is rounded to a whole point (the two means can differ by up to 1.0 from
# rounding alone), and districts are equal in population, not in votes
# cast, so turnout differences between the old and new districts move the
# unweighted mean a little. Measured over the nine 2026 redraws (119th ->
# 120th Congress pins): |shift| <= 1.11 (TN), every other state <= 0.75.
# A table that updated only some districts of a redrawn state breaks the
# conservation, because the voters the updated districts gained or lost
# are still counted in the old values of the rest: TN with only TN-9
# updated shifts +3.6, UT with only UT-1 -5.5.
REDRAW_MEAN_SHIFT_MAX = 1.5


def cross_congress_gates(
    table: dict[str, int], base: dict[str, int], redrawn_states: list[str],
) -> list[str]:
    """A Congress whose lines were redrawn from `base`'s.

    - Every state that didn't redraw must be identical: the same Cook
      window on the same lines gives the same number.
    - Every state that did must have all its seats in both tables, differ
      in at least one of them, and keep its mean district lean within
      REDRAW_MEAN_SHIFT_MAX of the base's (see that constant) — which a
      half-updated state fails.

    Deliberately NOT a minimum share of changed districts: a mid-decade
    redraw can legitimately move one or two seats (North Carolina's 2025
    map changed 2 of its 14 districts' PVI), so any share threshold either
    refuses that or is too low to mean anything."""
    failures = []
    redrawn = set(redrawn_states)
    unchanged_diff = sorted(k for k in table if k.split("-")[0] not in redrawn and table.get(k) != base.get(k))
    if unchanged_diff:
        failures.append(f"districts differ from the base Congress in states that did not redraw: {unchanged_diff[:10]}")
    same, incomplete, shifted = [], [], []
    for st in sorted(redrawn):
        n = SEATS.get(st, 0)
        keys = [f"{st}-0"] if n == 1 else [f"{st}-{i}" for i in range(1, n + 1)]
        if not keys or any(k not in table or k not in base for k in keys):
            incomplete.append(st)
            continue
        if all(table[k] == base[k] for k in keys):
            same.append(st)
            continue
        shift = (sum(table[k] for k in keys) - sum(base[k] for k in keys)) / len(keys)
        if abs(shift) > REDRAW_MEAN_SHIFT_MAX:
            shifted.append(f"{st} {shift:+.2f}")
    if incomplete:
        failures.append(f"redrawn states missing seats in this or the base table: {incomplete}")
    if same:
        failures.append(f"redrawn states identical to the base Congress (old lines?): {same}")
    if shifted:
        failures.append(
            f"redrawn states whose mean district lean moved more than {REDRAW_MEAN_SHIFT_MAX} "
            f"from the base (only some districts updated?): {shifted}"
        )
    return failures


def provenance_gates(source: dict, revision: dict | None, page: str) -> list[str]:
    if revision is None:
        return ["pinned revision could not be fetched"]
    failures = []
    if revision.get("revid") != source["revid"]:
        failures.append(f"asked for revision {source['revid']}, got {revision.get('revid')}")
    if revision.get("title") != page:
        failures.append(f"revision belongs to {revision.get('title')!r}, not {page!r}")
    if source.get("revision_timestamp") and revision.get("timestamp") != source["revision_timestamp"]:
        failures.append(
            f"revision timestamp {revision.get('timestamp')} != pinned {source['revision_timestamp']}"
        )
    if source["label"] not in (revision.get("content") or ""):
        failures.append(f"revision does not carry its label {source['label']!r}")
    return failures


def check_table(source: dict, revision: dict | None, page: str,
                base: dict[str, int] | None = None) -> tuple[dict[str, int], list[str]]:
    """Every gate for one Congress's pinned revision. Returns (table,
    failures); the table is only usable when failures is empty."""
    failures = provenance_gates(source, revision, page)
    if revision is None:
        return {}, failures
    content = revision.get("content") or ""
    table, problems = parse_district_table(content)
    failures += problems
    failures += ingestion_gates(table)
    tolerance = int(source.get("median_tolerance", 0))
    if tolerance and not source.get("_why_median_tolerance"):
        failures.append("median_tolerance is set without a _why_median_tolerance saying what moved the median")
    failures += self_consistency_gates(table, parse_stated_summary(content), tolerance)
    if source.get("redrawn_from"):
        if base is None:
            failures.append(f"base Congress {source['redrawn_from']} table unavailable for comparison")
        else:
            failures += cross_congress_gates(table, base, source.get("redrawn_states", []))
    return table, failures


# ── Sources and fetch ──────────────────────────────────────────────────

def load_sources(path: pathlib.Path | None = None) -> dict:
    return json.loads((path or SOURCES_PATH).read_text())


def congress_for_election(year: int) -> int:
    """The Congress a November general election in `year` seats (2026 ->
    120th, convening Jan 3, 2027)."""
    return 1 + (year + 1 - 1789) // 2


def _revision_url(page: str, revid: int) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={urllib.parse.quote(page.replace(' ', '_'))}&oldid={revid}"


async def _fetch_revision(*, revid: int | None = None, page: str | None = None) -> dict | None:
    """One revision's wikitext + identity: the pinned `revid`, or the
    page's current revision when only `page` is given.

    Via `requests`, not httpx: measured 2026-09-28, the MediaWiki API
    answered httpx with 403 "Please respect our robot policy" for every
    header set tried (same User-Agent, Accept-Encoding: identity, a bare
    Accept, a project URL) while `requests`, urllib and curl sending the
    same User-Agent from the same host were served — the same shape as the
    presidency.ucsb.edu block fetch_with_retry_requests exists for. The
    old infobox scrape used httpx, so its weekly refresh may have been
    failing closed ("kept previous data") for as long as that has held."""
    params = {
        "action": "query", "prop": "revisions", "rvprop": "ids|timestamp|content",
        "rvslots": "main", "format": "json", "formatversion": "2",
    }
    if revid is not None:
        params["revids"] = str(revid)
    else:
        params["titles"] = page
    url = API + "?" + urllib.parse.urlencode(params)
    resp = await fetch_with_retry_requests(
        _rate_limiter, "GET", url, retries=_RETRIES, backoff_s=_BACKOFF_S,
        log_label="district-pvi wikipedia revision", headers=HEADERS,
    )
    if resp is None:
        return None
    pages = resp.json().get("query", {}).get("pages", [])
    if not pages or not pages[0].get("revisions"):
        return None
    rev = pages[0]["revisions"][0]
    return {
        "title": pages[0].get("title"),
        "revid": rev.get("revid"),
        "timestamp": rev.get("timestamp"),
        "content": rev.get("slots", {}).get("main", {}).get("content"),
    }


def _ordered_congresses(sources: dict) -> list[str]:
    """Base Congresses before the ones redrawn from them."""
    return sorted(sources["congresses"], key=int)


async def build_payload(sources: dict, sitting_congress: int) -> tuple[dict | None, list[str]]:
    """Fetch and gate every configured Congress's pinned table. Returns
    (payload, failures); payload is None unless every table passed.

    A sitting Congress with no entry in the sources file is NOT a failure
    here: the configured tables are still written (so the weekly refresh
    and the live-drift check keep working), with the newest table at or
    below the sitting Congress as the top-level one — the latest lines
    known. ensure_sitting_lines raises the ops alert for the missing
    entry, once per Congress."""
    page = sources["page"]
    tables: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    blocks: dict[str, dict] = {}
    for c in _ordered_congresses(sources):
        src = sources["congresses"][c]
        rev = await _fetch_revision(revid=src["revid"])
        base = tables.get(src["redrawn_from"]) if src.get("redrawn_from") else None
        table, fails = check_table(src, rev, page, base)
        if fails:
            failures += [f"{ordinal(int(c))} Congress: {f}" for f in fails]
            continue
        tables[c] = table
        blocks[c] = _congress_block(page, src, table)
    if failures:
        return None, failures
    if _lines_congress(blocks, sitting_congress) is None:
        return None, [f"no pinned table at or below the sitting {ordinal(sitting_congress)} Congress"]
    return _payload(blocks, sitting_congress), []


def _congress_block(page: str, src: dict, table: dict[str, int]) -> dict:
    return {
        "_source": (
            f"{src['cook_release']}, as transcribed in Wikipedia's \"{page}\" article, "
            f"revision {src['revid']} ({src['revision_timestamp']}): {_revision_url(page, src['revid'])}"
        ),
        "_lines": src["lines"],
        "_window": src["window"],
        "_revision": {"page": page, "revid": src["revid"], "timestamp": src["revision_timestamp"]},
        "districts": dict(sorted(table.items())),
    }


def _lines_congress(blocks: dict, congress: int) -> str | None:
    """The key of the newest table at or below `congress` — its own when
    it has one, otherwise the latest lines known before it (a Congress
    whose lines nobody has pinned yet is, until someone does, on the
    latest lines anyone has)."""
    at_or_below = [int(c) for c in blocks if int(c) <= congress]
    return str(max(at_or_below)) if at_or_below else None


def _payload(blocks: dict[str, dict], sitting_congress: int) -> dict:
    """The file: every table under "congresses", and the sitting
    Congress's (or, with none pinned, the newest before it) at the top
    level. "congress" is the Congress whose pinned table that is."""
    key = _lines_congress(blocks, sitting_congress)
    sitting = blocks[key]
    return {
        "_source": sitting["_source"] + (
            ". Pinned per Congress in app/data/district_pvi_sources.json; ingested by "
            "app/pipeline/fetch/district_pvi.py (regenerate the bundled copy with "
            "backend/scripts/fetch_district_pvi.py)."
        ),
        "_sign": _SIGN,
        "_window": sitting["_window"],
        "_lines": sitting["_lines"],
        "_as_of": date.today().isoformat(),
        "congress": int(key),
        "districts": sitting["districts"],
        "congresses": blocks,
    }


def _reselect(payload: dict, sitting_congress: int, *, exact: bool = True) -> dict | None:
    """The same tables with a different sitting Congress, or None when the
    payload has no table for it (exact) / none at or below it (not exact)."""
    blocks = payload.get("congresses") or {}
    if (str(sitting_congress) not in blocks) if exact else _lines_congress(blocks, sitting_congress) is None:
        return None
    out = _payload(blocks, sitting_congress)
    out["_as_of"] = payload.get("_as_of", out["_as_of"])
    return out


def _write(path: pathlib.Path, payload: dict) -> None:
    write_text_atomic(path, json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    _reset_caches()


def _reset_caches() -> None:
    global _file_cache
    _file_cache = None
    from app.pipeline.analyze import score_calculator
    score_calculator._district_pvi_cache = None


def _sitting_congress() -> int:
    """The Congress in office now, read from the clock on every call (noon
    ET on Jan 3 of an odd year starts the next one) unless CURRENT_CONGRESS
    is pinned in the environment — see app.config.sitting_congress. NOT
    settings.CURRENT_CONGRESS, which is computed once at process start and
    would hold the old Congress's lines until a restart."""
    from app.config import sitting_congress
    return sitting_congress()


def _diff_digest(changed: list[str], pinned: dict[str, int], live: dict[str, int]) -> str:
    """A short stable hash of which districts differ and how."""
    import hashlib
    text = ";".join(f"{k}:{pinned.get(k)}>{live.get(k)}" for k in sorted(changed))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _check_live_drift(sources: dict, payload: dict) -> list[str]:
    """Compare the article's current table with the newest pinned one.
    Informational only — a difference is reported for someone to review
    (a correction, or a court-ordered map change, means advancing the pin);
    nothing from the live revision is ever written."""
    page = sources["page"]
    newest = max(sources["congresses"], key=int)
    pinned_revid = sources["congresses"][newest]["revid"]
    live = await _fetch_revision(page=page)
    if live is None or live.get("revid") == pinned_revid:
        return []
    table, problems = parse_district_table(live.get("content") or "")
    if problems:
        logger.info("district-pvi: live revision %s did not parse cleanly (%s)", live.get("revid"), problems[0])
        return []
    pinned = payload["congresses"][newest]["districts"]
    changed = sorted(k for k in set(table) | set(pinned) if table.get(k) != pinned.get(k))
    if changed:
        from app.ops_alerts import send_ops_alert
        sample = ", ".join(f"{k} {pinned.get(k)}->{table.get(k)}" for k in changed[:12])
        logger.warning(
            "district-pvi: live article revision %s differs from the pinned %s-Congress table in %d districts (%s)",
            live.get("revid"), ordinal(int(newest)), len(changed), sample,
        )
        send_ops_alert(
            "District PVI source changed since its pin",
            f"Wikipedia's \"{page}\" table (revision {live.get('revid')}) differs from the "
            f"pinned {ordinal(int(newest))}-Congress table (revision {pinned_revid}) in {len(changed)} "
            f"districts: {sample}. Nothing was ingested. If this is a correction or a map "
            f"change for that Congress, review the diff and advance its revid in "
            f"app/data/district_pvi_sources.json.",
            # Keyed on the difference itself, not the live revid: every
            # unrelated edit to the article (prose, references, another
            # section) makes a new revid with the same table difference.
            dedupe_key=f"district-pvi-live-drift-{pinned_revid}-{_diff_digest(changed, pinned, table)}",
        )
    return changed


async def refresh_district_pvi() -> bool:
    """Fetch, gate, and persist every configured Congress's table. Returns
    True on a successful write, False otherwise.

    NEVER raises and never writes gated-bad data: any failure keeps the
    previous run's /data/district_pvi.json, logs why, and lets the
    pipeline run continue.
    """
    try:
        sources = load_sources()
        sitting = _sitting_congress()
        payload, failures = await build_payload(sources, sitting)
        if failures:
            for f in failures:
                logger.warning("district-pvi ingestion gate failed: %s", f)
            return False
        _write(pathlib.Path(_PVI_PATH), payload)
        logger.info(
            "district-pvi refreshed: %d Congresses, sitting %s, member lines the %s's (%d districts)",
            len(payload["congresses"]), ordinal(sitting), ordinal(payload["congress"]),
            len(payload["districts"]),
        )
        if str(sitting) not in sources["congresses"]:
            logger.warning(
                "district-pvi: no source pinned for the sitting %s Congress — member scoring is on "
                "the %s Congress's lines, the latest pinned", ordinal(sitting), ordinal(payload["congress"]),
            )
        try:
            await _check_live_drift(sources, payload)
        except Exception:
            logger.warning("district-pvi live drift check failed", exc_info=True)
        return True
    except Exception:
        logger.warning(
            "district-pvi refresh failed — keeping previous data; run continues",
            exc_info=True,
        )
        return False


def ensure_sitting_lines() -> str:
    """Nightly, before any scoring: make sure /data/district_pvi.json's
    top-level table is the sitting Congress's. Returns what it did.

    The sitting Congress comes from the clock (_sitting_congress), so the
    first nightly run after noon ET on Jan 3 of an odd year switches the
    lines with no restart and no fetch.

    - "current": already the sitting Congress's pinned table.
    - "reselected": the file already holds the sitting Congress's table
      under "congresses" (the Jan 3 switch) — copied up locally, no fetch.
    - "refreshed": the file is missing, predates pinned sources (the
      infobox-scrape format), or lacks the sitting Congress — a full
      refresh is attempted, and succeeded.
    - "restored from bundle": the refresh failed, and the bundled copy
      (same pinned sources, same gates, checked in) has the sitting
      Congress's table — written in place of the file, since a
      pre-pinning file is known to mix maps.
    - "refresh failed": neither worked; an ops alert says so (once a day).
    - "no source configured": app/data/district_pvi_sources.json has no
      entry for the sitting Congress. Nothing can be fetched for it until
      someone adds one, so nothing is fetched (unless the file is missing
      or pre-pinning, when the configured tables are still worth
      restoring); member scoring stays on the newest pinned lines before
      it, and one ops alert per Congress says so.
    """
    import asyncio

    sitting = _sitting_congress()
    path = pathlib.Path(_PVI_PATH)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    try:
        configured = str(sitting) in load_sources()["congresses"]
    except Exception:
        logger.warning("district-pvi: sources file unreadable", exc_info=True)
        configured = True  # take the fetch path; its own failure alerts
    if not configured:
        return _no_source_configured(sitting, data, path)
    if data.get("congress") == sitting and data.get("congresses"):
        return "current"
    reselected = _reselect(data, sitting) if data.get("congresses") else None
    if reselected is not None:
        _write(path, reselected)
        logger.info("district-pvi: sitting Congress is now the %s — switched member lines", ordinal(sitting))
        return "reselected"
    if asyncio.run(refresh_district_pvi()):
        return "refreshed"
    try:
        bundled = _reselect(json.loads(BUNDLED_PATH.read_text()), sitting)
    except Exception:
        bundled = None
    if bundled is not None:
        _write(path, bundled)
        logger.warning(
            "district-pvi: refresh failed — restored the bundled %s-Congress tables", ordinal(sitting),
        )
        return "restored from bundle"
    from app.ops_alerts import send_ops_alert
    send_ops_alert(
        "District PVI has no table for the sitting Congress",
        f"/data/district_pvi.json has no pinned table for the {ordinal(sitting)} Congress, the "
        f"refresh failed, and the bundled copy has none either. Member scoring is reading "
        f"whatever table the file holds (format: "
        f"{'pinned' if data.get('congresses') else 'missing or pre-pinning'}). The sources "
        f"file names this Congress (or could not be read), so this is a fetch, gate or "
        f"sources-file failure — see the district-pvi warnings in the log.",
        dedupe_key=f"district-pvi-no-sitting-{sitting}-{date.today():%Y-%m-%d}",
    )
    return "refresh failed"


async def ensure_sitting_lines_before_run() -> None:
    """ensure_sitting_lines for a manually triggered run (the admin and
    token trigger endpoints), which skip the nightly pre-checks: without
    it, a House run triggered after the Jan 3 switch but before the next
    nightly run would score on the outgoing Congress's lines. Runs in a
    worker thread (ensure_sitting_lines may start its own event loop) and
    never raises — a failure logs and the run goes ahead, as it would
    nightly."""
    import asyncio

    try:
        await asyncio.to_thread(ensure_sitting_lines)
    except Exception:
        logger.exception("district-pvi: sitting-lines check before a triggered run failed")


def _no_source_configured(sitting: int, data: dict, path: pathlib.Path) -> str:
    import asyncio

    from app.ops_alerts import send_ops_alert

    if data.get("congresses"):
        reselected = _reselect(data, sitting, exact=False)
        if reselected is not None and reselected["congress"] != data.get("congress"):
            _write(path, reselected)
        lines = reselected["congress"] if reselected is not None else data.get("congress")
    else:
        # Missing or pre-pinning file: the configured tables still beat a
        # file known to mix maps.
        lines = None
        if asyncio.run(refresh_district_pvi()):
            lines = json.loads(path.read_text()).get("congress")
        else:
            try:
                bundled = _reselect(json.loads(BUNDLED_PATH.read_text()), sitting, exact=False)
            except Exception:
                bundled = None
            if bundled is not None:
                _write(path, bundled)
                lines = bundled["congress"]
    on = f"the {ordinal(lines)} Congress's lines, the latest pinned" if lines else "no pinned table"
    logger.warning("district-pvi: no source for the sitting %s Congress — member scoring on %s", ordinal(sitting), on)
    send_ops_alert(
        "District PVI has no source for the sitting Congress",
        f"The {ordinal(sitting)} Congress is in office, and app/data/district_pvi_sources.json "
        f"has no entry for it. Member scoring is on {on}. That is right only if no state "
        f"redrew its districts for the election that seated this Congress; add an entry "
        f"(see the file's _contract) either way, listing any redrawn states. Nothing is "
        f"fetched for it until then.",
        dedupe_key=f"district-pvi-no-source-{sitting}",
    )
    return "no source configured"


# ── Readers for surfaces that are not member scoring ───────────────────

_file_cache: dict | None = None


def _pvi_file() -> dict:
    global _file_cache
    if _file_cache is None:
        from app.pipeline.analyze.score_calculator import _read_pvi_json
        _file_cache = _read_pvi_json("district_pvi.json")
    return _file_cache


def district_pvi_for_congress(congress: int) -> tuple[dict[str, int], dict | None]:
    """(table, provenance) for the district lines a given Congress is
    elected on — what an election page should show for that cycle's House
    races (congress_for_election). Member scoring does not use this; it
    reads the sitting Congress's table via score_calculator._district_pvi().

    A Congress with no pinned table of its own gets the newest one before
    it — the latest lines known. From the day after a November election
    the elections pages ask for the NEXT cycle's Congress (2028's 121st on
    2026-11-04), which nobody has pinned yet; its races are on the lines
    just used unless a state redraws again, and falling back to the
    sitting Congress's table instead would put the nine states that redrew
    for 2026 back on their old lines. Any state the sources file lists as
    redrawn for a Congress in between whose table is not on file is
    dropped — a number for its old lines would describe a different
    district — and those seats fall back to the state lean, which the
    elections API labels as such. Provenance says which Congress's table
    it is ("congress") and for which Congress it was asked ("forCongress").
    """
    data = _pvi_file()
    blocks = data.get("congresses") or {}
    try:
        sources = load_sources()["congresses"]
    except Exception:
        sources = {}
    if blocks:
        key = _lines_congress(blocks, congress)
        if key is None:
            return {}, None  # older than every pinned table: no honest answer
        block = blocks[key]
        table = {k: int(v) for k, v in block["districts"].items()}
        low = int(key)
    else:
        # Pre-pinning file (replaced by ensure_sitting_lines on the next
        # nightly run): its one table, of unknown lines.
        key, block, low = None, None, None
        table = {k: int(v) for k, v in (data.get("districts") or {}).items()}
    redrawn = {
        st
        for c, src in sources.items()
        if (int(c) == congress if low is None else low < int(c) <= congress)
        for st in src.get("redrawn_states", [])
    }
    if redrawn:
        table = {k: v for k, v in table.items() if k.split("-")[0] not in redrawn}
    if block is None:
        return table, None
    revision = block.get("_revision") or {}
    meta = {
        "source": block.get("_source"),
        "lines": block.get("_lines"),
        "window": block.get("_window"),
        "congress": int(key),
        "forCongress": congress,
        # The table is as of the pinned revision — not the day it was
        # fetched, which is "fetchedOn".
        "asOf": revision.get("timestamp"),
        "revision": revision or None,
        "fetchedOn": data.get("_as_of"),
    }
    if redrawn:
        meta["omittedRedrawnStates"] = sorted(redrawn)
    return table, meta
