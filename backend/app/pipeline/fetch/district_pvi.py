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
- every seat in the House's apportionment (the House Clerk's member list,
  read each refresh — house_seats) parses exactly once, with each state's
  seat count right, values in range and a plausible R/D split;
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

The sitting Congress is settings.CURRENT_CONGRESS — the same value the
scored windows read — held for the whole of a pipeline job and advanced to
the Congress in office (noon ET on Jan 3 of an odd year starts the next
one, per the 20th Amendment) at the start of each job, unless an operator
pinned it (app.config.scoring_congress). Every House run, nightly or
triggered, goes through run_house_on_sitting_lines: under the
DISTRICT_LINES lease (app.pipeline.lease), held until the run's scoring is
done, it settles the lines first (_ensure_sitting_lines) — so the first
House run in a job started after that noon copies the new Congress's table up from the
tables already on disk (no restart, no fetch) once that Congress has an
entry in the sources file, and a pin advanced or a Congress added there is
fetched by the next House run rather than the next weekly refresh. If the
sitting Congress has no entry, member scoring stays on the newest pinned
lines before it, nothing is fetched for it, and one ops alert per Congress
asks for the entry. No writer of the file (a refresh, another House
run's check) can change the lines while a House run is scoring.

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
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from app.atomic_write import write_text_atomic
from app.contact import BOT_USER_AGENT
from app.file_cache import new_reload_lock, reload_if_moved
from app.ordinals import ordinal
from app.pipeline.fetch.house_clerk import fetch_house_apportionment
from app.pipeline.lease import STALE_S as LEASE_STALE_S
from app.http_client import make_async_client
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.state_names import STATE_NAME_TO_CODE, STATE_NAMES
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Which districts exist comes from the House Clerk's seat list
# (house_clerk.fetch_house_apportionment), read each refresh: a
# reapportionment after a census, or a state's seat count changing, is
# picked up with no edit here.

API = "https://en.wikipedia.org/w/api.php"
# Names Civitas and how to reach us, as Wikimedia's User-Agent policy asks
# (app.contact).
HEADERS = {"User-Agent": BOT_USER_AGENT}
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


_STATE_CODES = {name.lower(): code for name, code in STATE_NAME_TO_CODE.items()}


def _state_code(raw: str) -> str | None:
    raw = raw.strip()
    if raw.upper() in STATE_NAMES:
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

def ingestion_gates(result: dict[str, int], seats: dict[str, int]) -> list[str]:
    """Structural sanity checks on one table — guard the ingestion (sign
    convention, coverage, parse drift), not the scores. `seats`: {state:
    voting seats}, the House's apportionment (house_seats)."""
    failures = []
    expected = sum(seats.values())
    if len(result) != expected:
        failures.append(f"expected {expected} districts, got {len(result)}")
    per_state: dict[str, int] = {}
    for k in result:
        st = k.split("-")[0]
        per_state[st] = per_state.get(st, 0) + 1
    if set(per_state) != set(seats):
        failures.append(f"state coverage mismatch: {sorted(set(seats) ^ set(per_state))}")
    wrong = sorted(st for st in seats if st in per_state and per_state[st] != seats[st])
    if wrong:
        failures.append(f"seat count mismatch in {wrong}")
    for k in result:
        st, _, dn = k.partition("-")
        n = seats.get(st)
        if n is not None and not (dn == "0" if n == 1 else dn.isdigit() and 1 <= int(dn) <= n):
            failures.append(f"district {k} does not exist in the apportionment")
    vals = list(result.values())
    if not all(-45 <= v <= 45 for v in vals):
        failures.append("PVI outside plausible +/-45 range — parse drift?")
    # Sign convention: the national map has both leans in the hundreds; a
    # parser that flipped D/R or dropped a sign can't produce that.
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    # Neither side under about a third of the House (the bound was 150 of
    # 435 when it was a count): a sign flip or a parse that reads every
    # district one way lands far outside it.
    low, high = round(expected * 0.345), round(expected * 0.655)
    if not (low <= r_lean <= high and low <= d_lean <= high):
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
    table: dict[str, int], base: dict[str, int], redrawn_states: list[str], seats: dict[str, int],
    *, window: str | None = None, base_window: str | None = None,
) -> list[str]:
    """A Congress whose lines were redrawn from `base`'s.

    - Both tables must be from the same Cook window (the elections the
      index averages, `window` in the sources file). Every other check
      here rests on that: a new window moves every district, redrawn or
      not, so comparing across one would report hundreds of "unchanged"
      states as differing. A Congress on a new window is not a redraw of
      the old one — it gets no redrawn_from, and is checked on its own.
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
    if window is not None and base_window is not None and window != base_window:
        return [
            f"Cook window {window} differs from the base Congress's {base_window}: a redraw is "
            f"compared on the same window only (drop redrawn_from for an entry on a new window)"
        ]
    failures = []
    redrawn = set(redrawn_states)
    unchanged_diff = sorted(k for k in table if k.split("-")[0] not in redrawn and table.get(k) != base.get(k))
    if unchanged_diff:
        failures.append(f"districts differ from the base Congress in states that did not redraw: {unchanged_diff[:10]}")
    same, incomplete, shifted = [], [], []
    for st in sorted(redrawn):
        n = seats.get(st, 0)
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
                base: dict[str, int] | None = None,
                base_source: dict | None = None, *,
                seats: dict[str, int]) -> tuple[dict[str, int], list[str]]:
    """Every gate for one Congress's pinned revision. Returns (table,
    failures); the table is only usable when failures is empty.
    `base_source` is the redrawn_from Congress's sources entry (for its
    Cook window); `seats` the House's apportionment (house_seats)."""
    failures = provenance_gates(source, revision, page)
    if revision is None:
        return {}, failures
    content = revision.get("content") or ""
    table, problems = parse_district_table(content)
    failures += problems
    failures += ingestion_gates(table, seats)
    tolerance = int(source.get("median_tolerance", 0))
    if tolerance and not source.get("_why_median_tolerance"):
        failures.append("median_tolerance is set without a _why_median_tolerance saying what moved the median")
    failures += self_consistency_gates(table, parse_stated_summary(content), tolerance)
    if source.get("redrawn_from"):
        if base is None:
            failures.append(f"base Congress {source['redrawn_from']} table unavailable for comparison")
        else:
            failures += cross_congress_gates(
                table, base, source.get("redrawn_states", []), seats,
                window=source.get("window"), base_window=(base_source or {}).get("window"),
            )
    return table, failures


# ── Sources and fetch ──────────────────────────────────────────────────

# The sources file ships in the image and nothing rewrites it at runtime,
# but it is read on the elections GET path (district_pvi_for_congress), so
# it is parsed once per version of the file — keyed by path and stamp (one
# stat), which also keeps an edited checkout honest under a dev server.
_sources_cache: dict[pathlib.Path, tuple] = {}
_sources_lock = new_reload_lock()


def load_sources(path: pathlib.Path | None = None) -> dict:
    """The parsed sources file. Shared: callers read it, never change it.
    Raises when it can't be read or parsed (not cached)."""
    path = path or SOURCES_PATH
    with _sources_lock:
        cached, stamp = _sources_cache.get(path, (None, None))
        cached, stamp = reload_if_moved([path], cached, stamp, lambda: json.loads(path.read_text()))
        _sources_cache[path] = (cached, stamp)
        return cached


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


async def house_seats() -> dict[str, int]:
    """{state: voting seats} from the House Clerk's member list
    (house_clerk.fetch_house_apportionment), or {} when it can't be read.
    Every pinned table is gated against it, so a reapportionment reaches
    the gates with no edit here; the pins from before one describe the
    old apportionment and are retired from the sources file with it."""
    async with make_async_client(follow_redirects=True) as client:
        apportionment = await fetch_house_apportionment(client)
    return {st: a["seats"] for st, a in apportionment.items()}


async def build_payload(
    sources: dict, sitting_congress: int, seats: dict[str, int] | None = None,
) -> tuple[dict | None, list[str]]:
    """Fetch and gate every configured Congress's pinned table. Returns
    (payload, failures); payload is None unless every table passed.
    `seats` defaults to the House Clerk's apportionment (house_seats); an
    unreadable one is a failure, never a guess.

    A sitting Congress with no entry in the sources file is NOT a failure
    here: the configured tables are still written (so the weekly refresh
    and the live-drift check keep working), with the newest table at or
    below the sitting Congress as the top-level one — the latest lines
    known. _ensure_sitting_lines raises the ops alert for the missing
    entry, once per Congress."""
    if seats is None:
        seats = await house_seats()
    if not seats:
        return None, ["no apportionment from the House Clerk's member list"]
    page = sources["page"]
    tables: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    blocks: dict[str, dict] = {}
    for c in _ordered_congresses(sources):
        src = sources["congresses"][c]
        rev = await _fetch_revision(revid=src["revid"])
        base = tables.get(src["redrawn_from"]) if src.get("redrawn_from") else None
        base_src = sources["congresses"].get(src["redrawn_from"]) if src.get("redrawn_from") else None
        table, fails = check_table(src, rev, page, base, base_src, seats=seats)
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
    """Give this process a fresh read of the file now: member scoring's
    SeatLines (score_calculator._reload_district_pvi, under that cache's
    lock) and the elections' copy. Other processes — the API's, beside the
    pipeline's — notice the rewrite by the file's stamp on their next read
    (_district_pvi, _pvi_file); this only spares the writer's own process
    waiting on a stat, and lets a House run pin what the file holds as it
    starts. A lines_of()/current_lines() block already open in any thread
    keeps the table it pinned."""
    global _file_cache, _file_stamp
    with _file_lock:
        _file_cache, _file_stamp = None, None
    from app.pipeline.analyze import score_calculator
    score_calculator._reload_district_pvi()


# ── Which Congress's lines a stored House score is on ──────────────────
#
# Every House score is stored with the Congress whose lines it was computed
# on (Representative.district_lines_congress, written by
# upsert_representative from lines_congress(), or by the startup
# Constituent Alignment rescore from current_lines() in the same commit as
# the scores it rewrites), and the API's "show the
# math" breakdown recomputes Constituent Alignment on THOSE lines
# (lines_of). Without it the breakdown read whatever the file's top-level
# table was: from the moment a House run switched the lines until it had
# rescored a member — hours, or until the next successful run if it failed
# — and, for a member who left at the change of Congress, for as long as
# their record stays up, the breakdown's number was on lines their stored
# score never used.
#
# This makes the district table agree and nothing else. The breakdown also
# reads the Constituent Alignment reference (/data/constituent_reference.json)
# and /data/member_ideal_points.json as they are now; a House run rewrites
# both before its scoring loop, so for a member it hasn't rescored (mid-run,
# after a failed run, or departed) the breakdown can still differ from the
# stored score. That drift predates the per-Congress lines.

# (table, its Congress) pinned by lines_of()/current_lines() in this context.
_OTHER_LINES: ContextVar[tuple[dict[str, int], int | None] | None] = ContextVar(
    "district_pvi_other_lines", default=None,
)


class SeatLines(dict):
    """score_calculator's district table (its _district_pvi_cache) as its
    loader builds it (seat_lines): the file's top-level table, plus
    `congress` — the Congress whose pinned table it is, None for a
    pre-pinning file — and every pinned table (`tables`), all from one read
    of the file. So the Congress a House run records for a score is the
    table that score was computed from.

    A plain dict to every reader except inside lines_of() or
    current_lines(), which, in that call's context only (a ContextVar: other threads and tasks — a House
    run scoring beside an API request — never see it), answers from the
    table the block pinned as it opened: another Congress's, or one read of
    this one. Whichever SeatLines is score_calculator's cache by then (the
    file may be re-read meanwhile, in this process or on its stamp moving)
    answers the same.
    score_calculator reads it through .get; the other lookups by key ([]
    and `in`) are covered too; nothing that runs inside either block
    iterates the table.

    It works only while it IS score_calculator's cache — a plain dict put
    there instead ignores the override — so score_calculator's loader
    builds one (seat_lines) on every read of the file, and only tests set
    that cache to a plain dict."""

    congress: int | None = None
    tables: dict = {}

    def _table(self) -> dict | None:
        pinned = _OTHER_LINES.get()
        return pinned[0] if pinned is not None else None

    def get(self, key, default=None):
        other = self._table()
        return other.get(key, default) if other is not None else super().get(key, default)

    def __getitem__(self, key):
        other = self._table()
        return other[key] if other is not None else super().__getitem__(key)

    def __contains__(self, key) -> bool:
        other = self._table()
        return key in other if other is not None else super().__contains__(key)

    # Every other read of the table answers the same way, so no reader —
    # dict(lines), len(), iteration, get_district_pvi_map — can bypass a
    # block's table. (dict(x) of a dict subclass copies its storage
    # directly unless the subclass has its own __iter__; with this one it
    # goes through keys() and __getitem__.)
    def __iter__(self):
        other = self._table()
        return iter(other) if other is not None else super().__iter__()

    def __len__(self) -> int:
        other = self._table()
        return len(other) if other is not None else super().__len__()

    def keys(self):
        other = self._table()
        return other.keys() if other is not None else super().keys()

    def values(self):
        other = self._table()
        return other.values() if other is not None else super().values()

    def items(self):
        other = self._table()
        return other.items() if other is not None else super().items()

    def copy(self) -> dict:
        return dict(self.items())

    # The remaining dict operations that would read the storage directly —
    # comparison, reversed(), copy.copy/deepcopy, `lines | other`, repr.
    def __eq__(self, other) -> bool:
        return dict(self.items()) == other

    def __ne__(self, other) -> bool:
        return not self == other

    __hash__ = None  # a dict: unhashable, like its base

    def __reversed__(self):
        return reversed(list(self.keys()))

    def __copy__(self) -> dict:
        return self.copy()

    def __deepcopy__(self, memo) -> dict:
        import copy

        return copy.deepcopy(self.copy(), memo)

    def __or__(self, other):
        return self.copy() | other if isinstance(other, dict) else NotImplemented

    def __repr__(self) -> str:
        return f"SeatLines({self.copy()!r}, congress={self.congress!r})"

    def own_table(self) -> dict[str, int]:
        """This read's own (top-level) table, whatever block is open."""
        return dict(dict.items(self))


def seat_lines(raw: dict) -> SeatLines:
    """One read of the file (its parsed JSON), as the SeatLines member
    scoring reads — score_calculator._district_pvi's loader."""
    lines = SeatLines({k: int(v) for k, v in (raw.get("districts") or {}).items()})
    if raw.get("congresses") and isinstance(raw.get("congress"), int):
        lines.congress = raw["congress"]
        lines.tables = raw["congresses"]
    if not lines:
        logger.warning("district_pvi.json unavailable — falling back to state PVI")
    return lines


def _scoring_lines() -> SeatLines:
    """The district table member scoring reads: score_calculator's cache,
    re-read when the file's stamp has moved (another process rewrote it).
    A plain dict there (only a test puts one) is replaced by a read of the
    file."""
    from app.pipeline.analyze import score_calculator as sc

    lines = sc._district_pvi()
    if isinstance(lines, SeatLines):
        return lines
    return sc._reload_district_pvi()


def lines_congress() -> int | None:
    """The Congress whose district lines member scoring reads now — what a
    House run records beside each score it stores (upsert_representative):
    inside a lines_of()/current_lines() block, the Congress of the table the
    block pinned; otherwise the file's as last read. None for a pre-pinning
    file, whose one table is of unknown lines."""
    pinned = _OTHER_LINES.get()
    if pinned is not None:
        return pinned[1]
    return _scoring_lines().congress


@contextmanager
def lines_of(congress: int | None) -> Iterator[int | None]:
    """Within the block, in this context only, member scoring reads
    `congress`'s pinned table instead of the sitting one — for recomputing
    a stored score (the API's breakdown) on the lines it was computed on.
    Yields the Congress whose lines are in effect. A score with no recorded
    Congress (stored before it was recorded), or one whose table is no
    longer on file, is read on the current lines, as it always was — the
    table as the block opened, like current_lines, so a rewrite of the file
    meanwhile can't change lines under the computation. Inside another
    such block, the current lines are that block's table: a request for
    its Congress (or for none, or for one not on file) keeps it."""
    enclosing = _OTHER_LINES.get()
    lines = _scoring_lines()
    current = enclosing or (lines.own_table(), lines.congress)
    pinned = None
    if congress is not None and current[1] is not None and congress != current[1]:
        block = (lines.tables or {}).get(str(congress))
        if block and block.get("districts"):
            pinned = ({k: int(v) for k, v in block["districts"].items()}, congress)
        else:
            logger.info(
                "district-pvi: no %s-Congress table on file for a score stored on its lines — "
                "reading the current lines", ordinal(congress),
            )
    if pinned is None:
        pinned = current
    token = _OTHER_LINES.set(pinned)
    try:
        yield pinned[1]
    finally:
        _OTHER_LINES.reset(token)


@contextmanager
def current_lines() -> Iterator[int | None]:
    """Within the block, in this context only, member scoring reads the
    table in effect as the block opened — one read, whatever the file (or
    this process's cache) becomes meanwhile. Yields that table's Congress:
    what to record beside scores computed inside the block (main's startup
    rescore passes it to constituent_rescore, which records it on each
    rescored representative in the same commit as the score). Reading the
    Congress separately afterwards could name lines another process wrote
    in between — a Swarm start-first rollout runs two backends on one
    volume. Inside another such block, keeps that block's table."""
    pinned = _OTHER_LINES.get()
    if pinned is None:
        lines = _scoring_lines()
        pinned = (lines.own_table(), lines.congress)
    token = _OTHER_LINES.set(pinned)
    try:
        yield pinned[1]
    finally:
        _OTHER_LINES.reset(token)


def _sitting_congress() -> int:
    """The Congress member scoring is on: settings.CURRENT_CONGRESS, the
    same value the scored windows (roll-call sessions, bills, Voteview
    ideal points) read — the run's held Congress inside a pipeline run
    (app.config.scoring_congress), advanced to the Congress in office at
    the start of each run unless an operator pinned it. One value, so a
    House run can never score one Congress's votes on another's lines."""
    from app.config import settings
    return settings.CURRENT_CONGRESS


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


# What each DISTRICT_LINES holder calls itself on the lease row
# (lease.holder): skip messages name it, and a House run tells a refresh or
# the startup rescore (short, worth waiting for) from another House run
# (not).
REFRESH_WHO = "District PVI refresh"
HOUSE_RUN_WHO = "House run"
# main's startup Constituent Alignment rescore holds the lines while it
# rewrites House scores and records their lines, so a House run can't
# switch the lines and upsert members between its read and its commit.
RESCORE_WHO = "startup Constituent Alignment rescore"
WAITED_FOR = (REFRESH_WHO, RESCORE_WHO)


# How long a House run waits for a District PVI refresh that holds the
# lines. A refresh is a handful of requests (one per pinned Congress, plus
# the live revision), each retried with backoff (_RETRIES, _BACKOFF_S) —
# minutes, not hours; a refresh still holding the lease after this is stuck,
# and the House run is skipped with a message that says so.
REFRESH_WAIT_S = 30 * 60
REFRESH_POLL_S = 30.0
# A refusal with nobody holding the lease (lease.REFUSED_BUSY: the holder
# released between the take and the read) is retried this soon, within the
# same wait.
BUSY_RETRY_S = 1.0


# How long a leftover DISTRICT_LINES lease must go without a beat before the
# pipeline process's startup releases it (release_orphaned_holds). A live
# holder's beats can stall behind another SQLite writer — each attempt waits
# up to database.SQLITE_BUSY_TIMEOUT_S, and a writer can hold the database
# for minutes (lease.py) — so a beat interval and a half would release a live
# holder whose beats had merely stalled. lease.STALE_S is lease.py's own
# measure for that ("ten missed beats ride out a SQLite writer holding the
# database for minutes"), used by every tier with no longer window of its
# own. The cost is on the other side: a House run, refresh or rescore killed
# by the deploy that starts this process holds the lines up to this long
# after the restart, rather than the lease's hour-long stale window. Nothing
# is refused meanwhile — a House run (nightly, triggered) or the startup
# rescore refused by a lease under this re-check waits for it (waits_for),
# and each of those waits (REFRESH_WAIT_S) outlasts it
# (test_district_pvi_fetch checks both bounds).
ORPHAN_RECHECK_S = float(LEASE_STALE_S)


def release_orphaned_holds(*, recheck_after_s: float | None = None) -> "threading.Timer | None":
    """At pipeline-process startup only (main._invalidate_orphaned_pipelines,
    beside run_tracker.sweep_orphaned_runs; never from the API process):
    release the DISTRICT_LINES lease a killed House run, refresh or startup
    rescore left behind. Without it the dead holder's lease stood for its
    hour-long stale window: House triggers refused, the nightly House step
    skipped, and the startup rescore skipped the House.

    What makes a leftover lease dead is the pipeline service's stop-first
    update order (docker-compose.swarm.yml) and check-and-deploy.sh's busy
    check: when this process starts, no other pipeline process should be
    running. That is the guarantee — not the role lock, which is per
    container (/dev/shm). The missed-beat check here is a second guard, for
    the case that guarantee is broken (a start-first change, a second
    worker container on the volume): a lease is released only once it has
    gone ORPHAN_RECHECK_S without a beat — at once when its last beat is
    already that old, otherwise by a re-check that long after this call,
    which deletes it only if it still carries the beat it had now. The
    window is long enough to ride out a live holder's beats stalling behind
    another writer (see ORPHAN_RECHECK_S); a holder stalled longer than
    that, in a second process the update order should have ruled out, is
    what this guard can't tell from a dead one. Returns the re-check's
    timer (None when nothing waits on one); logs, never raises."""
    from datetime import timedelta

    from app.time_utils import utcnow

    wait = ORPHAN_RECHECK_S if recheck_after_s is None else recheck_after_s
    cutoff = utcnow() - timedelta(seconds=wait)
    fresh = _release_ours(lambda row: row.cached_at < cutoff)
    if not fresh:
        return None
    seen = {(data, beat) for data, beat, _ in fresh}
    whos = {who for _, _, who in fresh}
    _UNDER_RECHECK.update(whos)

    def recheck() -> None:
        try:
            _release_ours(lambda row: (row.data_json, row.cached_at) in seen)
        finally:
            _UNDER_RECHECK.difference_update(whos)

    timer = threading.Timer(wait, recheck)
    timer.daemon = True
    timer.start()
    return timer


# The holders whose lease release_orphaned_holds is still re-checking: most
# likely dead (killed moments before this process started, by the deploy
# that started it), so a House run or the startup rescore refused by one
# waits for the re-check rather than giving up on a holder that is gone.
_UNDER_RECHECK: set[str] = set()


def waits_for(holder: str | None) -> bool:
    """Whether a House run (or the startup rescore) refused the district
    lines by `holder` waits and retries: a refresh or the startup rescore
    (WAITED_FOR), or a leftover lease release_orphaned_holds is re-checking."""
    return holder in WAITED_FOR or holder in _UNDER_RECHECK


def _release_ours(dead) -> list[tuple[str, object, str]]:
    """Delete our DISTRICT_LINES lease rows that `dead(row)` says are dead —
    each only while it still carries the beat it was read with. Returns the
    (data, beat, who) of our rows it left."""
    from app.database import SessionLocal
    from app.models import ApiCache
    from app.pipeline import lease

    ours = {HOUSE_RUN_WHO, REFRESH_WHO, RESCORE_WHO}
    kept: list[tuple[str, object, str]] = []
    db = SessionLocal()
    try:
        for row in db.query(ApiCache).filter(
            ApiCache.tier == lease.DISTRICT_LINES, ApiCache.cache_key == "lock",
        ).all():
            try:
                who = json.loads(row.data_json).get("who")
            except (TypeError, ValueError, AttributeError):
                who = None
            if who not in ours:
                continue
            if not dead(row):
                kept.append((row.data_json, row.cached_at, who))
                continue
            gone = db.query(ApiCache).filter(
                ApiCache.tier == lease.DISTRICT_LINES, ApiCache.cache_key == "lock",
                ApiCache.data_json == row.data_json, ApiCache.cached_at == row.cached_at,
            ).delete(synchronize_session=False)
            if gone:
                logger.warning("Released the district lines a %s left behind in a process that is gone", who)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Releasing orphaned district-lines leases failed")
    finally:
        db.close()
    return kept


async def refresh_district_pvi() -> bool:
    """Fetch, gate, and persist every configured Congress's table. Returns
    True on a successful write, False otherwise.

    Under the DISTRICT_LINES lease (app.pipeline.lease), which every House
    run holds for its whole run (run_house_on_sitting_lines): while one is
    scoring, or another refresh is going, this writes nothing and returns
    False — the pins can't drift, so the next weekly refresh loses nothing.

    NEVER raises and never writes gated-bad data: any failure keeps the
    previous run's /data/district_pvi.json, logs why, and lets the
    pipeline run continue.
    """
    from app.pipeline import lease

    try:
        async with lease.job_async(lease.DISTRICT_LINES, who=REFRESH_WHO) as granted:
            if not granted:
                return False
            return await _refresh()
    except Exception:
        logger.warning("district-pvi refresh could not take its lease — keeping previous data", exc_info=True)
        return False


async def _refresh() -> bool:
    """refresh_district_pvi's work, for a caller already holding the
    DISTRICT_LINES lease."""
    try:
        sources = load_sources()
        sitting = _sitting_congress()
        newer = _superseded(_file_data())
        if newer is not None:
            logger.warning(
                "district-pvi refresh skipped: this job holds the %s Congress, but the %s has taken "
                "office — writing its lines would switch member scoring back", ordinal(sitting), ordinal(newer),
            )
            return False
        configured = sorted(int(c) for c in sources["congresses"])
        if configured and sitting < configured[0]:
            return await _check_without_writing(sources, sitting, configured[0])
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


async def _check_without_writing(sources: dict, sitting: int, earliest: int) -> bool:
    """A refresh while the sitting Congress is older than every configured
    one (only an environment pin puts it there): no table describes its
    lines, so nothing is written — but the pinned tables are still fetched
    and gated, and the live-drift check still runs on them, so a pin that
    needs advancing isn't hidden for as long as the pin stands. Returns
    False (nothing written); _ensure_sitting_lines's alert says why."""
    payload, failures = await build_payload(sources, earliest)
    for f in failures:
        logger.warning("district-pvi ingestion gate failed: %s", f)
    logger.warning(
        "district-pvi: the sitting %s Congress is older than every pinned table — refresh wrote "
        "nothing (the tables %s)", ordinal(sitting), "failed their gates" if failures else "passed their gates",
    )
    if payload is not None:
        try:
            await _check_live_drift(sources, payload)
        except Exception:
            logger.warning("district-pvi live drift check failed", exc_info=True)
    return False


def _pins_current(data: dict, sources: dict | None) -> bool:
    """Whether the file's tables are exactly the sources file's pins: every
    configured Congress present at its pinned revision, and none that the
    sources file no longer names. An advanced pin (a correction, a court
    ruling) or a newly added Congress is picked up by the next run that
    checks, not the next weekly refresh. With the sources file unreadable
    there is nothing to compare, so the file's own tables stand."""
    if sources is None:
        return True
    blocks = data.get("congresses") or {}
    pinned = sources.get("congresses") or {}
    if set(blocks) != set(pinned):
        return False
    return all((blocks[c].get("_revision") or {}).get("revid") == pinned[c].get("revid") for c in pinned)


def _ensure_sitting_lines() -> str:
    """Before a House run scores: make sure /data/district_pvi.json's
    top-level table is the sitting Congress's, from the pins the sources
    file names now. Returns what it did. The caller holds DISTRICT_LINES.

    The sitting Congress is the run's held Congress (_sitting_congress,
    app.config.scoring_congress), so the first run to start after noon ET
    on Jan 3 of an odd year switches the lines and the scored windows
    together, with no restart and no fetch.

    - "current": already the sitting Congress's table, and every table on
      file is at the revision the sources file pins now (_pins_current).
    - "reselected": the file already holds the sitting Congress's table
      under "congresses", at the current pins (the Jan 3 switch) — copied
      up locally, no fetch.
    - "refreshed": the file is missing, predates pinned sources (the
      infobox-scrape format), lacks the sitting Congress, or its tables
      are not the current pins (a pin advanced, a Congress added) — a
      full refresh is attempted, and succeeded.
    - "restored from bundle": the refresh failed, and the bundled copy
      (same pinned sources, same gates, checked in) has the sitting
      Congress's table — written in place of the file, since a
      pre-pinning file is known to mix maps.
    - "refresh failed": neither worked; an ops alert says so (once a day).
    - "superseded": this job holds an older Congress than the process or
      the file has moved to (_superseded) — nothing is written.
    - "no source configured": app/data/district_pvi_sources.json has no
      entry for the sitting Congress. Nothing can be fetched for it until
      someone adds one, so nothing is fetched for it (the configured
      tables are, when the file is missing, pre-pinning or not at the
      current pins); member scoring stays on the newest pinned lines
      before it, and one ops alert per Congress says so.
    """
    import asyncio

    sitting = _sitting_congress()
    path = pathlib.Path(_PVI_PATH)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    newer = _superseded(data if isinstance(data, dict) else {})
    if newer is not None:
        logger.warning(
            "district-pvi: this job holds the %s Congress, but the %s has taken office — its lines "
            "left alone", ordinal(sitting), ordinal(newer),
        )
        return "superseded"
    try:
        sources = load_sources()
        configured = str(sitting) in sources["congresses"]
    except Exception:
        logger.warning("district-pvi: sources file unreadable", exc_info=True)
        sources = None
        configured = True  # take the fetch path; its own failure alerts
    if not configured:
        return _no_source_configured(sitting, data, path, sources)
    pins_current = bool(data.get("congresses")) and _pins_current(data, sources)
    if pins_current and data.get("congress") == sitting:
        return "current"
    reselected = _reselect(data, sitting) if pins_current else None
    if reselected is not None:
        _write(path, reselected)
        logger.info("district-pvi: sitting Congress is now the %s — switched member lines", ordinal(sitting))
        return "reselected"
    if data.get("congresses") and not pins_current:
        logger.info("district-pvi: the file's tables are not the current pins — refreshing")
    if asyncio.run(_refresh()):
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
        f"/data/district_pvi.json has no table at the current pins for the {ordinal(sitting)} "
        f"Congress, the refresh failed, and the bundled copy has none either. Member scoring is "
        f"reading whatever table the file holds (format: "
        f"{'pinned' if data.get('congresses') else 'missing or pre-pinning'}). The sources "
        f"file names this Congress (or could not be read), so this is a fetch, gate or "
        f"sources-file failure — see the district-pvi warnings in the log.",
        dedupe_key=f"district-pvi-no-sitting-{sitting}-{date.today():%Y-%m-%d}",
    )
    return "refresh failed"


async def run_house_on_sitting_lines(
    run_house, *, refresh_wait_s: float = REFRESH_WAIT_S, poll_s: float = REFRESH_POLL_S,
) -> dict:
    """Every House run, nightly or triggered: take the DISTRICT_LINES
    lease, settle the sitting Congress's district lines
    (_ensure_sitting_lines), then run `run_house()` (run_house_pipeline,
    which takes its own run lock) — all under the lease, released only
    when the run returns. So the lines are settled before the House
    scores, can't be rewritten while it does (by a refresh, or by another
    trigger's check), and a second House trigger is refused at once
    instead of waiting out a first one's network refresh.

    A District PVI refresh (or main's startup rescore, WAITED_FOR) holding
    the lease is waited for (up to
    `refresh_wait_s`, re-trying every `poll_s`): it is minutes of work, and
    skipping would cost a night of House scores — and, in the nightly
    chain, used to end the chain before Stock trades and Election. So is
    a refusal whose holder had already gone when it was read
    (lease.REFUSED_BUSY — a refresh releasing mid-take), retried within
    `BUSY_RETRY_S`.

    Returns run_house()'s result, or a skip in the shape the nightly
    chain's skip alert reads ({"status": "skipped", "reason": code,
    "holder": who}): the lease held elsewhere (by another House run, or a
    refresh past the wait — `holder` names which, from the same read as
    the code) or a data reset (lease refusal codes), or a House run already
    going (run_tracker.ALREADY_RUNNING) — one a process started without
    this lease, e.g. an older image mid-rollout; its lines are left alone.
    A failing check is logged and the run goes ahead on whatever lines the
    file holds, as it always could."""
    import asyncio

    from app.pipeline import lease

    deadline = time.monotonic() + refresh_wait_s
    while True:
        async with lease.job_async(lease.DISTRICT_LINES, who=HOUSE_RUN_WHO) as granted:
            if granted:
                return await _house_run_holding_the_lines(run_house)
        if time.monotonic() >= deadline:
            break
        if granted.code == lease.REFUSED_BUSY and granted.holder is None:
            # Refused, but nobody holds it by the time the holder was read:
            # the refresh released in between (or a writer held SQLite's
            # lock through the take). Try again at once rather than skip
            # the House run — and with it the rest of the nightly chain.
            await asyncio.sleep(min(poll_s, BUSY_RETRY_S))
            continue
        if not waits_for(granted.holder):
            break
        logger.info("House run waiting for the %s that holds the district lines", granted.holder)
        await asyncio.sleep(poll_s)
    if waits_for(granted.holder):
        logger.warning(
            "House pipeline not started: the %s has held the district lines for over %d minutes",
            granted.holder, refresh_wait_s // 60,
        )
    return {"status": "skipped", "reason": granted.code, "holder": granted.holder}


async def _house_run_holding_the_lines(run_house) -> dict:
    """run_house_on_sitting_lines's work, under the DISTRICT_LINES lease,
    on one Congress (app.config.scoring_congress — the enclosing job's
    when there is one): the lines it settles and the windows it scores."""
    from app.config import scoring_congress

    with scoring_congress():
        return await _house_run_on_held_congress(run_house)


def _file_data() -> dict:
    try:
        data = json.loads(pathlib.Path(_PVI_PATH).read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _superseded(data: dict) -> int | None:
    """The newer Congress this job's held one is behind, or None. A job that
    started before noon ET on Jan 3 still holds the outgoing Congress after
    a job started later has advanced the process (settings.__dict__, not
    this context's hold) or switched the file's lines (`data`, the file as
    read) to the new one. Letting it settle ITS lines would switch the file
    back and rescore every House member on the old map — and the next job
    would switch them forward again. Never with an environment pin, which
    may deliberately put the lines on an older Congress.

    The file's Congress counts only while that Congress is actually in
    office by the clock (congress_in_session): a file AHEAD of the clock
    was written under an environment pin since removed (an operator
    pinning the next Congress early), not by a newer job — treating it as
    newer would skip every House run until that Congress took office. It
    is logged, and the House run reselects the held Congress's lines."""
    from app.config import settings
    from app.time_utils import congress_in_session

    if settings.current_congress_pinned:
        return None
    held = _sitting_congress()
    on_file = data.get("congress") if data.get("congresses") and isinstance(data.get("congress"), int) else 0
    in_office = congress_in_session()
    if on_file > in_office:
        logger.warning(
            "district-pvi: the file is on the %s Congress's lines, ahead of the %s in office — written "
            "under an environment pin since removed; the %s's lines are settled over it",
            ordinal(on_file), ordinal(in_office), ordinal(held),
        )
        on_file = 0
    newer = max(settings.__dict__["CURRENT_CONGRESS"], on_file)
    return newer if newer > held else None


async def _house_run_on_held_congress(run_house) -> dict:
    import asyncio

    from app.database import SessionLocal
    from app.models import HousePipelineRun
    from app.pipeline.run_tracker import ALREADY_RUNNING, SUPERSEDED, run_in_progress

    def _read(fn):
        db = SessionLocal()
        try:
            return fn(db)
        finally:
            db.close()

    if await asyncio.to_thread(_read, lambda db: run_in_progress(db, HousePipelineRun)):
        logger.warning("House pipeline not started: a House run is already going; its district lines left alone")
        return {"status": "skipped", "reason": ALREADY_RUNNING}
    newer = _superseded(await asyncio.to_thread(_file_data))
    if newer is not None:
        logger.warning(
            "House pipeline not started: this job holds the %s Congress, but the %s has taken office "
            "and been moved to — the lines and scores stay on it", ordinal(_sitting_congress()), ordinal(newer),
        )
        return {"status": "skipped", "reason": SUPERSEDED}
    try:
        outcome = await asyncio.to_thread(_ensure_sitting_lines)
        logger.info("district-pvi before the House run: %s", outcome)
    except Exception:
        logger.exception("district-pvi: sitting-lines check before the House run failed; running on the file as it is")
    # Re-read the file now, under the lease, and hold that read for the
    # whole run (current_lines): the run scores on it, and records its
    # Congress beside each score (lines_congress). Another process may have
    # switched the lines since this one last read them — and one on an
    # older image, mid-rollout, could rewrite them during the run, which
    # this process's stamp check would otherwise pick up part-way through.
    _reset_caches()
    with current_lines() as congress:
        logger.info(
            "House run scoring on %s", f"the {ordinal(congress)} Congress's district lines"
            if congress else "a pre-pinning district table (lines unknown)",
        )
        return await run_house()


def _served_lines(path: pathlib.Path) -> str:
    """What member scoring reads now, in words — read back from the table
    it is served (after a fresh read), not inferred from what was written:
    with no file at `path`, scoring falls back to the bundled copy."""
    _reset_caches()
    lines = _scoring_lines()
    where = "the file's top-level table" if path.exists() else "the bundled copy's top-level table, as no file is on the volume"
    if lines.congress is not None:
        return f"the {ordinal(lines.congress)} Congress's lines ({where})"
    if lines:
        return f"a pre-pinning table of unknown lines ({where})"
    return "no district table — every House seat falls back to its state's lean"


def _no_source_configured(sitting: int, data: dict, path: pathlib.Path, sources: dict) -> str:
    import asyncio

    from app.config import settings
    from app.ops_alerts import send_ops_alert

    if data.get("congresses") and _pins_current(data, sources):
        reselected = _reselect(data, sitting, exact=False)
        if reselected is not None and reselected["congress"] != data.get("congress"):
            _write(path, reselected)
        lines = reselected["congress"] if reselected is not None else None
    else:
        # Missing, pre-pinning, or not at the current pins: the configured
        # tables still beat a file known to mix maps or carry a stale pin.
        lines = None
        if asyncio.run(_refresh()):
            lines = json.loads(path.read_text()).get("congress")
        else:
            try:
                bundled = _reselect(json.loads(BUNDLED_PATH.read_text()), sitting, exact=False)
            except Exception:
                bundled = None
            if bundled is not None:
                _write(path, bundled)
                lines = bundled["congress"]
    configured = sorted(int(c) for c in sources["congresses"])
    older_than_every_pin = bool(configured) and sitting < configured[0]
    served = _served_lines(path)
    if older_than_every_pin:
        # Only an environment pin can put the sitting Congress before
        # every configured one (the clock only moves forward past them).
        on = served + (
            f", which are NOT the {ordinal(sitting)}'s" if _scoring_lines().congress is not None else ""
        )
        why = (
            f"CURRENT_CONGRESS is pinned in the environment to {sitting}"
            if settings.current_congress_pinned else f"The sitting Congress is the {ordinal(sitting)}"
        )
        text = (
            f"{why}, older than every Congress in app/data/district_pvi_sources.json (the earliest "
            f"is the {ordinal(configured[0])}), so no pinned table describes its district lines. "
            f"Member scoring is on {on}. Until this is fixed, every weekly District PVI refresh "
            f"fetches and checks the pinned tables but writes none of them (none describes the "
            f"{ordinal(sitting)}'s lines); its live-drift check still runs. For an archived-DB "
            f"re-run of the {ordinal(sitting)} Congress, add an entry for it (see the file's "
            f"_contract); otherwise remove or correct the CURRENT_CONGRESS pin."
        )
    else:
        on = (
            f"{served}, the latest pinned" if lines is not None and _scoring_lines().congress == lines
            else served
        )
        text = (
            f"The {ordinal(sitting)} Congress is in office, and app/data/district_pvi_sources.json "
            f"has no entry for it. Member scoring is on {on}. That is right only if no state "
            f"redrew its districts for the election that seated this Congress; add an entry "
            f"(see the file's _contract) either way, listing any redrawn states. Nothing is "
            f"fetched for it until then."
        )
    logger.warning("district-pvi: no source for the sitting %s Congress — member scoring on %s", ordinal(sitting), on)
    send_ops_alert(
        "District PVI has no source for the sitting Congress", text,
        dedupe_key=f"district-pvi-no-source-{sitting}",
    )
    return "no source configured"


# ── Readers for surfaces that are not member scoring ───────────────────

_file_cache: dict | None = None
# The file's stamp when _file_cache was read: the elections API reads it in
# the API process, and the pipeline process rewrites it (new pins, the
# sitting-Congress switch) — see app/file_cache.py.
_file_stamp = None
_file_lock = new_reload_lock()


def _pvi_file() -> dict:
    global _file_cache, _file_stamp
    from app.pipeline.analyze import score_calculator as sc

    with _file_lock:
        _file_cache, _file_stamp = reload_if_moved(
            [pathlib.Path(sc._PVI_PERSISTENT_DIR) / "district_pvi.json"], _file_cache, _file_stamp,
            lambda: sc._read_pvi_json("district_pvi.json", report_unreadable=True),
        )
        return _file_cache


# district_pvi_for_congress's answers, per Congress, for the file and the
# sources as they were read: rebuilt when either is re-read (a new object
# from _pvi_file / load_sources — their stamps moved), so the elections GET
# path does no parsing or table building per request.
_for_congress_cache: dict[int, tuple] = {}
_for_congress_lock = new_reload_lock()


def district_pvi_for_congress(congress: int) -> tuple[dict[str, int], dict | None]:
    """See _district_pvi_for_congress; cached per version of the file and
    the sources file. The table and provenance are shared — read them,
    don't change them."""
    data = _pvi_file()
    try:
        sources = load_sources()
    except Exception:
        logger.warning("district-pvi: sources file unreadable", exc_info=True)
        return _district_pvi_for_congress(congress, data, {})
    with _for_congress_lock:
        hit = _for_congress_cache.get(congress)
        if hit is not None and hit[0] is data and hit[1] is sources:
            return hit[2]
        answer = _district_pvi_for_congress(congress, data, sources.get("congresses") or {})
        _for_congress_cache[congress] = (data, sources, answer)
        return answer


def _district_pvi_for_congress(congress: int, data: dict, sources: dict) -> tuple[dict[str, int], dict | None]:
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
    blocks = data.get("congresses") or {}
    if blocks:
        key = _lines_congress(blocks, congress)
        if key is None:
            return {}, None  # older than every pinned table: no honest answer
        block = blocks[key]
        table = {k: int(v) for k, v in block["districts"].items()}
        low = int(key)
    else:
        # Pre-pinning file (replaced before the next House run scores —
        # run_house_on_sitting_lines): its one table, of unknown lines.
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
