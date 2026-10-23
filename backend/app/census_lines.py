"""Which district lines each builder needs, and where the Census Bureau
publishes them, found by rule (AGENTS.md 3a, 6a).

Every per-district table (computed lean, counties, places, state
legislative crosswalk, the browser's district shapes) is built from the
Census Redistricting Data Office's block equivalency files (BEFs): every
2020 block and the district it lies in. Nothing here names a Congress, a
cycle or a URL a person found: the Congresses come from the clock
(settings.CURRENT_CONGRESS, the election the site is on) and each state's
map from redrawn_congressional_maps.json (the copy in use, which
scripts/derive_redrawn_maps.py extends from the BEFs themselves), and the
files from the RDO's own directory listing.

A map is named by the Congress whose BEF drew it ("CD119"): a state that
kept its map is on the same lines in every later Congress, so a table is
needed per map in use, not per Congress.

Standard library plus app.contact and the runtime-path helpers, so the
scripts under backend/scripts can import it without the app's settings.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.atomic_write import runtime_data_path
from app.contact import BOT_USER_AGENT
from app.file_cache import new_reload_lock, read_json_preferring, reload_if_moved

RDO = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files"
MAPS_NAME = "redrawn_congressional_maps.json"
BUNDLED_MAPS = Path(__file__).resolve().parent / "data" / MAPS_NAME
# Every state is on the lines of the Congress it was first elected under
# after the 2020 census unless the maps file lists a later one. The file
# lists no cycle before 2026, so this is the map every unlisted state is
# on: the 119th Congress's (the 2024 election's), a fact of the record.
BASE_MAP = "CD119"

_LISTING_RE = re.compile(
    r'<a href="([^"?/][^"]*)">[^<]*</a></td><td align="right">(\d{4}-\d\d-\d\d \d\d:\d\d)'
)
_HREF_RE = re.compile(r'href="([^"?/:][^":]*)"')


# The Nth Congress is elected in 1786 + 2N (the 1st in 1788).
_ELECTED_BASE = 1786


def seated_by(cycle: int) -> int:
    """The Congress a November election seats (2026 seats the 120th)."""
    return (int(cycle) - _ELECTED_BASE) // 2


def elected_in(congress: int) -> int:
    """The election year that seats `congress`."""
    return _ELECTED_BASE + 2 * int(congress)


def convenes(congress: int) -> int:
    """The year `congress` first meets (Jan 3 after its election)."""
    return elected_in(congress) + 1


def census_year(congress: int) -> int:
    """The decennial census whose blocks and apportionment `congress`'s
    lines are drawn on: a census taken in a year ending in 0 is first used
    for the election two years later (2020's for the 2022 election, the
    118th Congress; 2030's for 2032's, the 123rd)."""
    return 10 * ((elected_in(congress) - 2) // 10)


def map_congress(name: str) -> int:
    """"CD120" -> 120."""
    return int(name.removeprefix("CD"))


def map_in(cycles: dict, cycle: int, state: str) -> str:
    """The map `state` votes on in `cycle`: the newest listed cycle at or
    before it that names the state (a map stands until replaced), else
    BASE_MAP."""
    for year in sorted((int(y) for y in cycles), reverse=True):
        if year <= cycle and state in cycles[str(year)]:
            return cycles[str(year)][state].get("map", BASE_MAP)
    return BASE_MAP


_maps_lock = new_reload_lock()
_maps_cache: dict | None = None
_maps_stamp = None


def maps_cycles() -> dict:
    """Each cycle's maps, from the copy in use: the data refresh's on the
    data volume, else the bundled file (re-read when either changes)."""
    global _maps_cache, _maps_stamp
    paths = [runtime_data_path(MAPS_NAME), BUNDLED_MAPS]
    with _maps_lock:
        _maps_cache, _maps_stamp = reload_if_moved(
            paths, _maps_cache, _maps_stamp,
            lambda: read_json_preferring(*paths, default={}, accept=lambda d: isinstance(d, dict)),
        )
        return dict((_maps_cache or {}).get("cycles") or {})


def listed(cycles: dict, congress: int) -> bool:
    """Whether `congress`'s election has been reviewed: it is listed, or
    no later than the newest listed cycle (an earlier cycle's maps are
    settled). An unlisted later cycle carries nothing (district_lines)."""
    year = elected_in(congress)
    return str(year) in cycles or year <= max((int(c) for c in cycles), default=0)


def state_maps(congress: int, states: Iterable[str], cycles: dict) -> dict[str, int]:
    """{state: the Congress whose BEF holds its lines for `congress`}."""
    year = elected_in(congress)
    return {st: map_congress(map_in(cycles, year, st)) for st in states}


# ---------------------------------------------------------------------------
# Finding the files
# ---------------------------------------------------------------------------


def _get(url: str, timeout: int = 120, attempts: int = 3) -> bytes:
    """GET, tried again on a dropped connection or a 5xx (www2.census.gov
    resets one now and then); a 4xx is an answer and raises at once."""
    req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts - 1:
                raise
        except OSError:
            if attempt == attempts - 1:
                raise
        time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def listing(url: str) -> dict[str, str]:
    """{entry: modified ("" when the page doesn't say)} of a www2.census.gov
    directory page (an Apache index, or the site's own templated one)."""
    page = _get(url).decode("utf-8", "replace")
    found = dict(_LISTING_RE.findall(page))
    for href in _HREF_RE.findall(page):
        if not href.startswith(("css/", "main/")) and not href.endswith((".css", ".html")):
            found.setdefault(href, "")
    return found


def _years() -> list[str]:
    return sorted((y.rstrip("/") for y in listing(RDO + "/") if re.fullmatch(r"\d{4}/", y)), reverse=True)


def find_congress_bef(congress: int) -> tuple[str, str] | None:
    """(URL, modified) of the RDO's BEF zip for `congress`, or None: under
    <year it convenes>/<N>-congressional-district-bef(s)/cd<N>.zip (the
    113th to the 120th all sit there), else any year directory."""
    want = re.compile(rf"^{congress}-congressional-district-befs?/$")
    for year in dict.fromkeys([str(convenes(congress)), *_years()]):
        try:
            entries = listing(f"{RDO}/{year}/")
        except Exception:
            continue
        for sub in entries:
            if want.match(sub):
                for name, modified in listing(f"{RDO}/{year}/{sub}").items():
                    if name.lower() == f"cd{congress}.zip":
                        return f"{RDO}/{year}/{sub}{name}", modified
    return None


def find_sld_bef(cycle: int, chamber: str) -> tuple[str, str, int] | None:
    """(URL, modified, cycle) of the newest state legislative BEF ("sldu" or
    "sldl") for an election at or before `cycle`: <cycle>-state-legislative-
    bef/<chamber><yy>.zip, under the year after it (2026's under 2027/)."""
    best = None
    for year in _years():
        try:
            entries = listing(f"{RDO}/{year}/")
        except Exception:
            continue
        for sub in entries:
            m = re.fullmatch(r"(\d{4})-state-legislative-bef/", sub)
            if not m or int(m[1]) > cycle or (best and int(m[1]) <= best[2]):
                continue
            for name, modified in listing(f"{RDO}/{year}/{sub}").items():
                if name.lower() == f"{chamber}{m[1][2:]}.zip":
                    best = (f"{RDO}/{year}/{sub}{name}", modified, int(m[1]))
    return best


TIGER = "https://www2.census.gov/geo/tiger"


def exists(url: str) -> bool:
    """Whether www2.census.gov serves `url` (a HEAD request; any failure
    but a 404 raises, so an outage is never read as "not published")."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": BOT_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def tiger_years() -> list[int]:
    """The TIGER/Line and cartographic vintages published, newest first."""
    return sorted({int(m[2]) for e in listing(TIGER + "/") if (m := re.fullmatch(r"(TIGER|GENZ)(\d{4})/", e))},
                  reverse=True)


def newest(template: str, years: Iterable[int] | None = None) -> str | None:
    """The newest vintage of a file ("{y}" for the year in `template`) the
    Census Bureau serves, or None."""
    for year in (tiger_years() if years is None else years):
        url = template.format(y=year)
        if exists(url):
            return url
    return None


def tiger_cd_url(congress: int, fips: str) -> str | None:
    """TIGER/Line's full-resolution shapes of one state's districts on
    `congress`'s lines: in the vintage of the year it convenes, else of
    the year it is elected (the 119th's in TIGER2025, the 120th's in
    TIGER2026)."""
    return newest(f"{TIGER}/TIGER{{y}}/CD/tl_{{y}}_{fips}_cd{congress}.zip",
                  (convenes(congress), elected_in(congress)))


def cartographic_cd_url(congress: int) -> str | None:
    """The 1:500,000 cartographic boundary file of `congress`'s districts,
    in the vintage of its election year (cb_2024_us_cd119_500k), or None
    while there is none (the 120th's lines in 2026)."""
    y = elected_in(congress)
    url = f"{TIGER}/GENZ{y}/shp/cb_{y}_us_cd{congress}_500k.zip"
    return url if exists(url) else None


def blocks_url(congress: int, fips: str) -> str:
    """TIGER/Line's census blocks the lines of `congress` are drawn on."""
    y = census_year(congress)
    return f"{TIGER}/TIGER{y}/TABBLOCK{y % 100:02d}/tl_{y}_{fips}_tabblock{y % 100:02d}.zip"


def newest_gazetteer(kind: str) -> str:
    """The newest Census Gazetteer national file for "place" or "cousub"."""
    base = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    years = sorted((int(m[1]) for e in listing(base) if (m := re.fullmatch(r"(\d{4})_Gazetteer/", e))),
                   reverse=True)
    stem = {"place": "place", "cousub": "cousubs"}[kind]
    for year in years:
        name = f"{year}_Gaz_{stem}_national.zip"
        if name in listing(f"{base}{year}_Gazetteer/"):
            return f"{base}{year}_Gazetteer/{name}"
    raise LookupError(f"no Gazetteer {kind} file under {base}")


# ---------------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------------


def national_file(names: list[str]) -> str:
    return next(n for n in names if n.lower().startswith("national"))


def own_file(names: list[str], fips: str) -> str | None:
    """The zip member that is one state's own file, by its FIPS code as a
    name token ("01_AL_CD119.txt", "CD120_01.txt"), or None."""
    for n in names:
        if not n.lower().startswith("national") and re.search(rf"(?:^|_){fips}(?:_|\.)", n):
            return n
    return None


def bef_rows(zip_path: Path, fips: str, state: str) -> Iterator[tuple[str, str]]:
    """(GEOID, district code) for one state's blocks in a BEF zip: from the
    state's own file when the zip has one ("01_AL_CD119.txt", "CD120_48.txt",
    "27_MN_SLDU26.txt": a plan filed after the national file was cut, which
    replaces that state's national rows wholesale; in cd120.zip each is
    identical to the national rows), else from the national file. The code is the file's last column but one (CDFP, SLDUST,
    SLDLST)."""
    with zipfile.ZipFile(zip_path) as zf:
        member = own_file(zf.namelist(), fips) or national_file(zf.namelist())
        with zf.open(member) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            head = next(reader)
            gi = head.index("GEOID")
            ci = next(i for i, h in enumerate(head) if h in ("CDFP", "SLDUST", "SLDLST", "DISTRICT"))
            for row in reader:
                if row and row[gi].startswith(fips):
                    yield row[gi], row[ci]


def bef_all_rows(zip_path: Path) -> Iterator[tuple[str, str]]:
    """(GEOID, district code) for every block in a BEF zip, each state's own
    file (bef_rows) in place of its national rows."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        own = {}
        for n in names:
            m = re.search(r"(?:^|_)(\d{2})(?:_|\.)", n)
            if m and not n.lower().startswith("national"):
                own[m[1]] = n
        for member in [*own.values(), national_file(names)]:
            from_national = member == national_file(names)
            with zf.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                head = next(reader)
                gi = head.index("GEOID")
                ci = next(i for i, h in enumerate(head) if h in ("CDFP", "SLDUST", "SLDLST", "DISTRICT"))
                for row in reader:
                    if row and not (from_national and row[gi][:2] in own):
                        yield row[gi], row[ci]


def block_digest(geoid: str, code: str) -> int:
    """One block's share of an order-independent digest of a state's
    assignments (summed mod 2**64): two files agree on a state exactly
    when its sums and block counts agree."""
    return int.from_bytes(hashlib.blake2b(f"{geoid},{code}".encode(), digest_size=8).digest(), "big")


def bundled_json(name: str) -> dict:
    try:
        return json.loads((Path(__file__).resolve().parent / "data" / name).read_text())
    except (OSError, ValueError):
        return {}
