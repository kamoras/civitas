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
- a Congress whose lines were redrawn from an earlier one differs from it
  in every redrawn state and in no other state.

Output (/data/district_pvi.json; bundled fallback app/data/
district_pvi.json via scripts/fetch_district_pvi.py): every configured
Congress's table under "congresses", and the table for
settings.CURRENT_CONGRESS copied to the top-level "districts" that
score_calculator._district_pvi() and fetch/voteview.py read. Switching to
the next Congress's lines when its members are seated (Jan 3 of an odd
year) is automatic once that Congress has an entry in the sources file:
ensure_sitting_lines() runs before every nightly chain and re-selects
from the tables already on disk.

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


def self_consistency_gates(table: dict[str, int], summary: dict) -> list[str]:
    """The revision's prose must describe its own table. Counts are exact.
    The stated median district must hold the stated value, and the table's
    median may sit at most one point from it: a single map change adopted
    after Cook's release (Missouri's 2026-09 reversion) moves the median a
    step while the release's published median sentence stays as written."""
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
        if vals and abs(vals[len(vals) // 2] - stated) > 1:
            failures.append(f"table median {vals[len(vals) // 2]:+d} is not within 1 of stated {stated:+d}")
    return failures


def cross_congress_gates(
    table: dict[str, int], base: dict[str, int], redrawn_states: list[str],
) -> list[str]:
    """A Congress whose lines were redrawn from `base`'s: the same Cook
    window on the same lines gives the same number, so every state that
    didn't redraw must be identical, and every state that did must differ
    somewhere. Catches a pin whose table mixes the two maps, or a redrawn
    state that was never updated."""
    failures = []
    redrawn = set(redrawn_states)
    unchanged_diff = sorted(k for k in table if k.split("-")[0] not in redrawn and table.get(k) != base.get(k))
    if unchanged_diff:
        failures.append(f"districts differ from the base Congress in states that did not redraw: {unchanged_diff[:10]}")
    same = sorted(
        st for st in redrawn
        if all(table.get(k) == base.get(k) for k in table if k.split("-")[0] == st)
    )
    if same:
        failures.append(f"redrawn states identical to the base Congress (old lines?): {same}")
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
    failures += self_consistency_gates(table, parse_stated_summary(content))
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
    (payload, failures); payload is None unless every table passed and the
    sitting Congress has one."""
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
            failures += [f"{c}th Congress: {f}" for f in fails]
            continue
        tables[c] = table
        blocks[c] = _congress_block(page, src, table)
    if str(sitting_congress) not in sources["congresses"]:
        failures.append(
            f"no source configured for the sitting {sitting_congress}th Congress in "
            "app/data/district_pvi_sources.json"
        )
    if failures:
        return None, failures
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


def _payload(blocks: dict[str, dict], sitting_congress: int) -> dict:
    sitting = blocks[str(sitting_congress)]
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
        "congress": sitting_congress,
        "districts": sitting["districts"],
        "congresses": blocks,
    }


def _reselect(payload: dict, sitting_congress: int) -> dict | None:
    """The same tables with a different sitting Congress, or None when the
    payload has no table for it."""
    blocks = payload.get("congresses") or {}
    if str(sitting_congress) not in blocks:
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
    from app.config import settings
    return settings.CURRENT_CONGRESS


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
            "district-pvi: live article revision %s differs from the pinned %sth-Congress table in %d districts (%s)",
            live.get("revid"), newest, len(changed), sample,
        )
        send_ops_alert(
            "District PVI source changed since its pin",
            f"Wikipedia's \"{page}\" table (revision {live.get('revid')}) differs from the "
            f"pinned {newest}th-Congress table (revision {pinned_revid}) in {len(changed)} "
            f"districts: {sample}. Nothing was ingested. If this is a correction or a map "
            f"change for that Congress, review the diff and advance its revid in "
            f"app/data/district_pvi_sources.json.",
            dedupe_key=f"district-pvi-live-drift-{live.get('revid')}",
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
        payload, failures = await build_payload(sources, _sitting_congress())
        if failures:
            for f in failures:
                logger.warning("district-pvi ingestion gate failed: %s", f)
            return False
        _write(pathlib.Path(_PVI_PATH), payload)
        logger.info(
            "district-pvi refreshed: %d Congresses, sitting %dth (%d districts)",
            len(payload["congresses"]), payload["congress"], len(payload["districts"]),
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
    - "refresh failed": neither worked; an ops alert says so.
    """
    import asyncio

    sitting = _sitting_congress()
    path = pathlib.Path(_PVI_PATH)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    if data.get("congress") == sitting and data.get("congresses"):
        return "current"
    reselected = _reselect(data, sitting) if data.get("congresses") else None
    if reselected is not None:
        _write(path, reselected)
        logger.info("district-pvi: sitting Congress is now the %dth — switched member lines", sitting)
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
            "district-pvi: refresh failed — restored the bundled %dth-Congress tables", sitting,
        )
        return "restored from bundle"
    from app.ops_alerts import send_ops_alert
    send_ops_alert(
        "District PVI has no table for the sitting Congress",
        f"/data/district_pvi.json has no pinned table for the {sitting}th Congress, the "
        f"refresh failed, and the bundled copy has none either. Member scoring is reading "
        f"whatever table the file holds (format: "
        f"{'pinned' if data.get('congresses') else 'missing or pre-pinning'}). If "
        f"app/data/district_pvi_sources.json has no entry for the {sitting}th Congress, "
        f"add one (see its _contract).",
        dedupe_key=f"district-pvi-no-sitting-{sitting}-{date.today():%Y-%m-%d}",
    )
    return "refresh failed"


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

    When that Congress has no pinned table, the sitting table is returned
    minus any state the sources file lists as redrawn for it: a number
    for the old lines of a redrawn district would describe a different
    district, so those seats fall back to the state lean, which the
    elections API labels as such."""
    data = _pvi_file()
    blocks = data.get("congresses") or {}
    block = blocks.get(str(congress))
    if block:
        meta = {k.lstrip("_"): block.get(k) for k in ("_source", "_lines", "_window")}
        meta["asOf"] = data.get("_as_of")
        return {k: int(v) for k, v in block["districts"].items()}, meta
    table = {k: int(v) for k, v in (data.get("districts") or {}).items()}
    try:
        redrawn = set(load_sources()["congresses"].get(str(congress), {}).get("redrawn_states", []))
    except Exception:
        redrawn = set()
    if redrawn:
        table = {k: v for k, v in table.items() if k.split("-")[0] not in redrawn}
    return table, None
