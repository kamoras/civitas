"""Build backend/app/data/district_lean.json — a Cook-style partisan lean
for every U.S. House district, computed by Civitas from open precinct
returns, beside (never instead of) Cook's published PVI.

WHAT IT IS. Cook's formula, the same one scripts/fetch_state_pvi.py uses
for states: the district's Democratic share of the two-party presidential
vote minus the nation's, 75% on 2024 and 25% on 2020, negated so positive
is a Republican lean.

    lean(d) = -100 * (0.25 * (D20_d / (D20_d + R20_d) - N20)
                     + 0.75 * (D24_d / (D24_d + R24_d) - N24))

N20 / N24 are the national two-party Democratic shares from MEDSL's
official state returns, read from app/data/state_pvi.json's stored inputs
(the same counts the state PVIs are computed from).

INPUTS (every one open-licensed or public domain; nothing share-alike):

  * 2020 — Voting and Election Science Team (VEST), "2020 Precinct-Level
    Election Results", Harvard Dataverse doi:10.7910/DVN/K7760H, CC BY 4.0:
    each state's precinct shapefile with its presidential results.
  * 2024 — MIT Election Data & Science Lab (MEDSL),
    github.com/MEDSL/2024-elections-official, per-state precinct returns,
    CC0 (as declared on Dataverse), pinned to a commit.
  * U.S. Census Bureau (public domain): 2020 P.L. 94-171 block population
    and internal points; the 119th- and 120th-Congress block equivalency
    files (BEFs).
  * Each state's or county's own certified canvass, for the MEDSL errors
    listed in app/data/district_lean_corrections.json (one entry per fix,
    with its URL).

METHOD.

  2020: every populated Census block's internal point is placed in the
  VEST precinct that contains it (even-odd point in polygon, in the
  shapefile's own projection), and the precinct's Biden and Trump votes
  are shared among its blocks by population. A precinct that contains no
  block point (a sliver) gives its votes to the block point nearest its
  vertices' centre. Blocks then sum to districts through the BEFs, on
  both maps.

  2024, 119th lines: MEDSL's precincts carry the U.S. House district each
  voted in (the 2024 election was held on the 119th Congress's lines), so
  a precinct's Harris and Trump votes go to its own district. A reporting
  unit that spans several districts (a county-wide early-vote or mail
  row, a vote centre) is split by the House votes it cast in each district
  times the party's share in that district among the county's
  single-district precincts. A precinct with no House contest on its
  ballot (an unopposed seat) goes to the districts of its county that no
  other precinct is labelled with, the same way.

  2024, 120th lines (only the states that redrew; every other state's
  lines are the same): swing transfer. Each block's 2024 vote is its 2020
  vote times the 2024/2020 ratio of the smallest cell it shares with
  labelled 2024 precincts — county x 119th district x state house
  district where the precincts' labels make that cell exact, else county x
  119th district — rescaled so every county x 119th district cell keeps
  its exact 2024 total, then summed on the new lines.

CORRECTIONS. app/data/district_lean_corrections.json lists every MEDSL
error found and how it is fixed, always from the state's or county's own
certified canvass, fetched from the source the entry names: drop summary
or duplicate rows, replace a county that lies in one district with its
official total, scale a county's precincts to its official municipal
totals, add county-wide official votes (Louisiana's early and provisional
ballots), replace a county's precincts with its own precinct file (King
County), or take a state's official county x district or district counts
(California, Maine).

GATES (any failure: the script prints every failure and writes nothing):

  * each state's two-party totals within 0.2% of the official state
    figures: the FEC's for 2020, MEDSL's state file (state_pvi.json inputs)
    for 2024;
  * every county whose official canvass was fetched (all of Indiana's, New
    Jersey's and Louisiana's, and each corrected county) within 0.2% of it;
  * every district's 2024/2020 two-party vote ratio between 0.85 and 1.15,
    unless the corrections file records it as an evidenced exception, which
    then holds only at its recorded ratio;
  * every district of both maps present exactly once;
  * Texas's (Red-206, both plans, both years) and California's official
    per-district presidential tables reproduced: within 0.1 point of
    two-party share where the votes were cast on those lines, within 1
    point where both sides are estimates (2020, and the 120th lines).

  Districts more than 1.5 points from the pinned Cook value are listed as
  an alert, not a failure: the two are different measurements.

A district whose input could only be completed from a source behind a
wall is written as unavailable (`unavailable`, with the reason), never
filled from a scrape.

Memory: one state at a time; nothing national is held in memory.

Run from the repo (network required on the first run; about 1.5 GB of
downloads, cached):

    pip install -r requirements.txt -r scripts/requirements-research.txt
    python backend/scripts/build_district_lean.py [--cache DIR] [--states AL,AK,...]

`--states` builds a subset for inspection and never writes the data file.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import difflib
import hashlib
import io
import json
import re
import statistics
import struct
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pdfplumber
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_county_district_crosswalk as cw  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402
import fetch_state_pvi as sp  # noqa: E402
from app.contact import BOT_USER_AGENT  # noqa: E402  (cw put backend/ on sys.path)
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows  # noqa: E402

try:  # research-only dependency (scripts/requirements-research.txt)
    from pyproj import CRS, Transformer
except ImportError:  # pragma: no cover - the unit tests never project
    CRS = Transformer = None

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
OUTPUT = DATA / "district_lean.json"
CORRECTIONS = DATA / "district_lean_corrections.json"
STATE_PVI = DATA / "state_pvi.json"
DISTRICT_PVI = DATA / "district_pvi.json"
REGENERATE = "backend/scripts/build_district_lean.py"

# VEST 2020, pinned to one immutable Dataverse version; each state's file
# is found by name in that version's own file list and checked by its MD5.
VEST_DOI = "doi:10.7910/DVN/K7760H"
VEST_VERSION = "49.0"
VEST_VERSION_URL = (
    "https://dataverse.harvard.edu/api/datasets/:persistentId/versions/"
    f"{VEST_VERSION}?persistentId={VEST_DOI}"
)
VEST_FILE_URL = "https://dataverse.harvard.edu/api/access/datafile/{id}"
VEST_D, VEST_R = "G20PREDBID", "G20PRERTRU"  # Biden, Trump (VEST's field naming)

MEDSL_COMMIT = "df531089c78e6d0098db1a6bfb3849a066a06995"  # 2026-05-04, the repository's head
MEDSL_PRECINCTS_URL = (
    f"https://raw.githubusercontent.com/MEDSL/2024-elections-official/{MEDSL_COMMIT}"
    "/individual_states/{st}24.zip"
)

# Official per-district presidential tables, the independent cross-check.
TX_RED206 = {  # Texas Legislative Council Red-206, (Congress, year) -> PDF
    (119, "2024"): "https://data.capitol.texas.gov/dataset/b806b39a-4bab-4103-a66a-9c99bcaba490/resource/"
                   "f033e126-e7ee-43d7-95c5-c9fe3a2079d7/download/planc2193_r206_election24g.pdf",
    (119, "2020"): "https://data.capitol.texas.gov/dataset/b806b39a-4bab-4103-a66a-9c99bcaba490/resource/"
                   "05ead8c8-9184-4b95-8574-c3594b08d9a4/download/planc2193r206_20g.pdf",
    (120, "2024"): "https://data.capitol.texas.gov/dataset/748c952b-e926-4f44-8d01-a738884b3ec8/resource/"
                   "94a2fff0-39ca-46f9-9e40-dd72addde245/download/planc2333_r206_election24g.pdf",
    (120, "2020"): "https://data.capitol.texas.gov/dataset/748c952b-e926-4f44-8d01-a738884b3ec8/resource/"
                   "d3af6493-38d0-4f92-89d8-cc858784e6ae/download/planc2333_r206_election20g.pdf",
}
CA_SSOV_2024 = "https://elections.cdn.sos.ca.gov/sov/2024-general/ssov/pres-by-congress.xlsx"  # 119th lines

# Gates (module docstring).
STATE_TOLERANCE = 0.002
COUNTY_TOLERANCE = 0.002
TURNOUT_RATIO = (0.85, 1.15)
EXCEPTION_SLACK = 0.005  # a recorded turnout exception holds only at its own ratio
# A precinct labelled with a district none of its county's populated
# blocks are in (a split precinct's odd label) can't be carried to the new
# lines; a handful of votes is noise, a share this size would not be.
STRAY_SHARE = 1e-4
COOK_ALERT = 1.5
# The official tables' 2024 counts are the same certified canvass MEDSL
# transcribes, so a 2024 figure on the lines the votes were cast on must
# agree closely. 2020 and the 120th lines are estimates on both sides
# (Red-206 too disaggregates precinct votes to blocks), so they are
# reported, and gated only against a gross error.
CROSS_TOLERANCE = {"exact": 0.1, "estimate": 1.0}

_NOT_A_SEAT = {"98", "ZZ", ""}

# ---------------------------------------------------------------------------
# Shapefiles (ESRI technical description, 1998): just enough to read VEST's
# polygon layers — no new dependency for a format this small.
# ---------------------------------------------------------------------------

_POLYGON_TYPES = {5, 15, 25}  # Polygon, PolygonZ, PolygonM: x/y come first in all three


def read_dbf(raw: bytes, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """The named columns of every non-deleted record, as stripped text."""
    count, header_len, record_len = struct.unpack("<IHH", raw[4:12])
    layout, offset = {}, 1  # byte 0 of a record is its deletion flag
    for i in range(32, header_len - 1, 32):
        name = raw[i:i + 11].split(b"\0")[0].decode("ascii")
        width = raw[i + 16]
        layout[name] = (offset, width)
        offset += width
    missing = [f for f in fields if f not in layout]
    if missing:
        raise ValueError(f"DBF has no column {missing}; columns: {sorted(layout)}")
    out = []
    for n in range(count):
        rec = raw[header_len + n * record_len: header_len + (n + 1) * record_len]
        out.append({f: rec[layout[f][0]: layout[f][0] + layout[f][1]].decode("latin-1").strip(" \x00")
                    for f in fields} | {"_deleted": rec[:1] == b"*"})
    return out


def read_shp(raw: bytes) -> list[list[np.ndarray] | None]:
    """Each record's rings as (n, 2) float arrays; None for a null shape."""
    shapes: list[list[np.ndarray] | None] = []
    pos = 100
    while pos < len(raw):
        length = struct.unpack(">i", raw[pos + 4:pos + 8])[0] * 2
        body = raw[pos + 8:pos + 8 + length]
        pos += 8 + length
        kind = struct.unpack("<i", body[:4])[0]
        if kind == 0:
            shapes.append(None)
            continue
        if kind not in _POLYGON_TYPES:
            raise ValueError(f"shape type {kind} is not a polygon")
        n_parts, n_points = struct.unpack("<ii", body[36:44])
        parts = list(np.frombuffer(body, "<i4", n_parts, 44)) + [n_points]
        pts = np.frombuffer(body, "<f8", n_points * 2, 44 + 4 * n_parts).reshape(-1, 2)
        shapes.append([pts[parts[i]:parts[i + 1]] for i in range(n_parts)])
    return shapes


def read_shapefile(zip_path, fields: tuple[str, ...]) -> tuple[list, list[dict], str]:
    """(shapes, records, .prj WKT) from a zipped shapefile with one layer."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

        def member(ext: str) -> bytes:
            hits = [n for n in names if n.lower().endswith(ext)]
            if len(hits) != 1:
                raise ValueError(f"{zip_path}: expected one {ext}, found {hits}")
            return zf.read(hits[0])

        shapes, records, prj = read_shp(member(".shp")), read_dbf(member(".dbf"), fields), member(".prj")
    if len(shapes) != len(records):
        raise ValueError(f"{zip_path}: {len(shapes)} shapes but {len(records)} records")
    return shapes, records, prj.decode("latin-1")


# ---------------------------------------------------------------------------
# Point in polygon, vectorised
# ---------------------------------------------------------------------------

# Points tested against one polygon's edges at a time, so a big rural
# precinct's edges x its points never needs more than a few MB.
_PIP_CELLS = 4_000_000


def locate(points: np.ndarray, shapes: list) -> np.ndarray:
    """Index of the first shape containing each point (-1 for none).

    Even-odd ray cast over every ring, so holes and multi-part polygons
    need no special case. Candidates come from each shape's bounding box
    against the points sorted by x."""
    owner = np.full(len(points), -1, dtype=np.int64)
    order = np.argsort(points[:, 0], kind="stable")
    xs = points[order, 0]
    for idx, rings in enumerate(shapes):
        if not rings:
            continue
        allpts = np.concatenate(rings)
        x0, y0 = allpts.min(0)
        x1, y1 = allpts.max(0)
        lo, hi = np.searchsorted(xs, x0, "left"), np.searchsorted(xs, x1, "right")
        if lo == hi:
            continue
        cand = order[lo:hi]
        cand = cand[(points[cand, 1] >= y0) & (points[cand, 1] <= y1) & (owner[cand] < 0)]
        if not len(cand):
            continue
        a = np.concatenate([r[:-1] if len(r) > 1 and (r[0] == r[-1]).all() else r for r in rings])
        b = np.concatenate([np.roll(r[:-1] if len(r) > 1 and (r[0] == r[-1]).all() else r, -1, axis=0)
                            for r in rings])
        step = max(1, _PIP_CELLS // len(a))
        for s in range(0, len(cand), step):
            c = cand[s:s + step]
            px, py = points[c, 0], points[c, 1]
            ya, yb = a[:, 1:2], b[:, 1:2]
            straddle = (ya > py) != (yb > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                xcross = a[:, 0:1] + (py - ya) * (b[:, 0:1] - a[:, 0:1]) / (yb - ya)
            hit = (straddle & (px < xcross)).sum(0) % 2 == 1
            owner[c[hit]] = idx
    return owner


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def fetch(url: str, dest: Path, md5: str | None = None) -> Path:
    """Download once into the cache, retrying dropped connections; a
    pinned MD5, when given, must match."""
    if not dest.exists():
        print(f"  downloading {url}", flush=True)
        part = dest.with_name(dest.name + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as resp, open(part, "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        part.rename(dest)
    if md5 and hashlib.md5(dest.read_bytes()).hexdigest() != md5:
        raise SystemExit(f"{dest}: MD5 does not match the pinned source")
    return dest


def vest_files(cache: Path) -> dict[str, tuple[int, str]]:
    """{"al_2020.zip": (datafile id, md5)} from the pinned VEST version."""
    meta = json.loads(fetch(VEST_VERSION_URL, cache / f"vest_v{VEST_VERSION}.json").read_text())["data"]
    return {f["dataFile"]["filename"]: (f["dataFile"]["id"], f["dataFile"]["md5"]) for f in meta["files"]}


# ---------------------------------------------------------------------------
# Blocks and districts
# ---------------------------------------------------------------------------


def seat(code: str) -> str | None:
    code = code.strip()
    return None if code in _NOT_A_SEAT else str(int(code))


def state_blocks(st: str, cache: Path) -> tuple[list[str], np.ndarray, np.ndarray, dict[str, str]]:
    """(GEOIDs, population, (lon, lat) internal points) of the populated
    2020 blocks, and county base name -> county FIPS — the P.L. 94-171
    header, through the crosswalk script's own reader."""
    fips, folder = sl._STATES[st]
    pl = fetch(sl._PL.format(folder=folder, st=st.lower()), cache / f"{st.lower()}2020.pl.zip")
    points: dict[str, tuple[float, float]] = {}
    with zipfile.ZipFile(pl) as zf, zf.open(f"{st.lower()}geo2020.pl") as raw:
        blocks, _names = sl.read_geo(io.TextIOWrapper(raw, encoding="latin-1"), False, points)
    geoids = list(points)
    counties = {base: fips + code for (kind, code), (_name, base) in _names.items() if kind == "county"}
    return geoids, np.array([blocks[g][0] for g in geoids], float), np.array([points[g] for g in geoids]), counties


def _bef_member(zf: zipfile.ZipFile, member: str, fips: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with zf.open(member) as raw:
        reader = io.TextIOWrapper(raw, encoding="utf-8-sig")
        next(reader)
        for line in reader:
            if line.startswith(fips):
                out[line[:15]] = line.rstrip("\r\n").rsplit(",", 1)[1]
            elif out:
                break  # the national files are sorted by GEOID
    return out


def state_befs(st: str, cache: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(block -> 119th district, block -> 120th-election district) for one
    state: the corrected per-state 119th file where Census issued one, and
    the map the state votes on in 2026 (cw.map_for_state — Missouri's
    stayed 2025 map is not it)."""
    fips = sl._FIPS[st]
    with zipfile.ZipFile(fetch(cw.CD119_URL, cache / "cd119.zip")) as zf:
        corrected = f"{fips}_{st}_CD119.txt"
        b119 = _bef_member(zf, corrected if corrected in zf.namelist() else "NationalCD119.txt", fips)
    if cw.map_for_state(st) == cw.CD119:
        return b119, b119
    with zipfile.ZipFile(fetch(cw.CD120_URL, cache / "cd120.zip")) as zf:
        return b119, _bef_member(zf, "NationalCD120.txt", fips)


# ---------------------------------------------------------------------------
# 2020: VEST precincts -> blocks
# ---------------------------------------------------------------------------


def votes_2020(st: str, cache: Path, files: dict, lonlat: np.ndarray, pop: np.ndarray) -> tuple[np.ndarray, dict]:
    """(blocks x [D, R] 2020 votes, stats)."""
    name = f"{st.lower()}_2020.zip"
    fid, md5 = files[name]
    shapes, records, prj = read_shapefile(fetch(VEST_FILE_URL.format(id=fid), cache / name, md5), (VEST_D, VEST_R))
    votes = np.array([[float(r[VEST_D] or 0), float(r[VEST_R] or 0)] for r in records])
    to_shape = Transformer.from_crs(CRS.from_epsg(4269), CRS.from_wkt(prj), always_xy=True)
    x, y = to_shape.transform(lonlat[:, 0], lonlat[:, 1])
    pts = np.column_stack([x, y])
    owner = locate(pts, shapes)
    out = np.zeros((len(pts), 2))
    inside = owner >= 0
    share = np.zeros(len(pts))
    totals = np.bincount(owner[inside], weights=pop[inside], minlength=len(shapes))
    share[inside] = pop[inside] / totals[owner[inside]]
    out[inside] = votes[owner[inside]] * share[inside, None]
    # A precinct no block point falls in (a sliver, a water precinct)
    # gives its votes to the block point nearest the centre of its vertices.
    has = np.zeros(len(shapes), bool)
    has[owner[inside]] = True
    tree = cKDTree(pts)
    nearest_votes = null_votes = 0.0
    for i in np.flatnonzero(~has):
        if not votes[i].any():
            continue
        if not shapes[i]:
            null_votes += votes[i].sum()
            continue
        _, j = tree.query(np.concatenate(shapes[i]).mean(0))
        out[j] += votes[i]
        nearest_votes += votes[i].sum()
    return out, {"precincts": len(shapes), "votesToNearestBlock": round(nearest_votes),
                 "votesInNullShapes": round(null_votes), "total": votes.sum(0).tolist()}


# ---------------------------------------------------------------------------
# 2024: MEDSL precincts (+ official corrections) -> reporting units
# ---------------------------------------------------------------------------


class Unit:
    """One 2024 reporting unit: its county, Harris/Trump votes, the U.S.
    House votes it cast per district, its state house district when it
    has exactly one, and whether it is a county-wide row to be spread over
    the county's districts (`spread`)."""

    __slots__ = ("county", "votes", "house", "sld", "spread", "name")

    def __init__(self, county, votes, house=None, sld=None, spread=False, name=""):
        self.county, self.votes, self.house, self.sld, self.spread = county, votes, house or {}, sld, spread
        self.name = name


def _norm(precinct: str) -> str:
    return " ".join(precinct.upper().split())


def _collapse(modes: dict) -> dict:
    """A precinct reported by mode AND as a TOTAL row counts the total
    only; otherwise its modes add up."""
    if "TOTAL" in modes and len(modes) > 1:
        return modes["TOTAL"]
    acc: dict = collections.Counter()
    for m in modes.values():
        acc.update(m)
    return acc


def _candidate_party(row: dict) -> str | None:
    """"D" for Harris, "R" for Trump, by the candidate's name: some rows
    leave party_simplified blank and mark the candidate a write-in (New
    Jersey's Warren County), though both were printed on every state's
    ballot; a name is the one field every row fills."""
    name = row["candidate"].upper()
    return "D" if "HARRIS" in name else "R" if "TRUMP" in name else None


def medsl_units(st: str, cache: Path, fixes: list[dict], official: "Official") -> tuple[list[Unit], list[str]]:
    """Reporting units for one state, MEDSL's precincts with the state's
    corrections applied. Returns (units, notes)."""
    zpath = fetch(MEDSL_PRECINCTS_URL.format(st=st.lower()), cache / f"{st.lower()}24.zip")
    drop = collections.defaultdict(list)
    replaced, county_totals, municipal, residual = set(), {}, {}, None
    notes = []
    for f in fixes:
        if f["action"] == "drop_rows":
            drop[f["county"]].append(re.compile(f["precinct_regex"]))
        elif f["action"] == "replace_precincts":
            replaced.add(f["county"])
        elif f["action"] == "county_total":
            county_totals[f["county"]] = f
        elif f["action"] == "municipal_totals":
            municipal[f["county"]] = f
        elif f["action"] == "county_residual":
            residual = f
    sld_office = {"STATE HOUSE"} | ({"STATE SENATE"} if st == "NE" else set())  # Nebraska is unicameral
    pres = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    house = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    sld = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    names = {}
    dropped = collections.Counter()
    with zipfile.ZipFile(zpath) as zf:
        member = next(n for n in zf.namelist() if n.endswith(".csv"))
        for r in csv.DictReader(io.TextIOWrapper(zf.open(member), encoding="utf-8", errors="replace")):
            office = r["office"]
            if office not in ("US PRESIDENT", "US HOUSE") and office not in sld_office:
                continue
            county = r["county_fips"]
            if county in replaced:
                continue
            party = _candidate_party(r) if office == "US PRESIDENT" else None
            if any(rx.search(r["precinct"]) for rx in drop.get(county, ())):
                if party:
                    dropped[(county, party)] += float(r["votes"] or 0)
                continue
            try:
                v = float(r["votes"])
            except ValueError:
                continue
            key = (county, r["jurisdiction_fips"], _norm(r["precinct"]))
            names[key] = r["precinct"]
            if office == "US PRESIDENT":
                if party:
                    pres[key][r["mode"]][party] += v
            elif office == "US HOUSE":
                house[key][r["mode"]][r["district"]] += v
            else:
                sld[key][r["mode"]][r["district"]] += v
    for (county, party), v in sorted(dropped.items()):
        notes.append(f"{county}: dropped {v:,.0f} {party} votes in rows matching the correction")
    keys = list(pres)
    units = []
    for key in keys:
        p = _collapse(pres[key])
        h = {d: n for d, n in _collapse(house[key]).items() if n > 0} if key in house else {}
        s = {d for d, n in _collapse(sld[key]).items() if n > 0} if key in sld else set()
        units.append(Unit(key[0], [p["D"], p["R"]], h, next(iter(s)) if len(s) == 1 else None, name=names[key]))
    for county, f in municipal.items():
        units = _scale_to_municipalities(units, county, official.municipal(f), notes)
    for county, f in county_totals.items():
        d, r = official.county(f)
        units = [u for u in units if u.county != county] + [Unit(county, [d, r], spread=True)]
        notes.append(f"{county}: precincts replaced by the official county total {d:,.0f} D / {r:,.0f} R")
    for county in replaced:
        units += official.precinct_units(st, county)
    if residual:
        units += _county_residuals(units, official.louisiana_parishes(), notes)
    return units, notes


def _official_cells(cell24: dict, official_cells: dict, notes: list[str]) -> tuple[dict, dict]:
    """Every county x district cell set to the state's own official count
    of it, and the districts summed from those."""
    moved = sum(abs(official_cells.get(k, (0, 0))[i] - cell24.get(k, (0, 0))[i])
                for k in set(cell24) | set(official_cells) for i in (0, 1))
    d24 = collections.defaultdict(lambda: [0.0, 0.0])
    for (c, d), v in official_cells.items():
        d24[d][0] += v[0]
        d24[d][1] += v[1]
    notes.append(f"county x district cells set to the official counts ({moved:,.0f} votes moved in all)")
    return {k: list(v) for k, v in official_cells.items()}, dict(d24)


def _county_residuals(units: list[Unit], table: dict[str, tuple[float, float]], notes: list[str]) -> list[Unit]:
    """Votes the official county totals hold beyond the precincts (rows
    the source reports only county-wide: Louisiana's early and provisional
    votes), as one spread unit per county."""
    have = collections.defaultdict(lambda: [0.0, 0.0])
    for u in units:
        have[u.county][0] += u.votes[0]
        have[u.county][1] += u.votes[1]
    out, added = [], [0.0, 0.0]
    for county, (d, r) in sorted(table.items()):
        rest = [d - have[county][0], r - have[county][1]]
        if min(rest) < -0.5:
            raise SystemExit(f"{county}: precincts exceed the official county total by {rest}")
        out.append(Unit(county, [max(rest[0], 0.0), max(rest[1], 0.0)], spread=True))
        added = [added[0] + rest[0], added[1] + rest[1]]
    notes.append(f"county-wide official votes added: {added[0]:,.0f} D / {added[1]:,.0f} R")
    return out


# Municipality-name suffixes on New Jersey's official tables and MEDSL's
# precinct names, folded to one spelling so "Hopewell Boro" and "Hopewell
# Twp." stay two places and "Ewing Twp" matches "Ewing Twp.".
_SUFFIX = {"twp": "twp", "township": "twp", "boro": "boro", "borough": "boro",
           "city": "city", "town": "town", "village": "village"}


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9-]+", name.lower().replace("fallstwp", "falls twp")) if w and w != "of"]


def match_municipality(precinct: str, municipalities: list[str]) -> str | None:
    """The official municipality a precinct name starts with: the longest
    name (without its legal-type suffix) that prefixes the precinct's
    words, and among equal names the one whose legal type the precinct
    repeats ("Hopewell Boro 2" is not Hopewell Twp.; "Borough of Alpha
    District 1" is Alpha Boro). A one-letter slip in the official table
    ("Phillpsburg Town") still matches (SequenceMatcher ratio >= 0.9) when
    no name matches exactly.
    None when nothing, or more than one place, fits."""
    words = _words(precinct)
    lead = _SUFFIX.get(words[0]) if words else None
    if lead:
        words = words[1:]
    best: list[tuple[int, int, int, str]] = []
    for m in municipalities:
        mw = _words(m)
        suffix = _SUFFIX.get(mw[-1]) if mw else None
        base = mw[:-1] if suffix else mw
        head = words[:len(base)]
        if not base or (head != base and difflib.SequenceMatcher(None, " ".join(head), " ".join(base)).ratio() < 0.9):
            continue
        nxt = _SUFFIX.get(words[len(base)]) if len(words) > len(base) else None
        said = nxt or lead
        if said and suffix and said != suffix:
            continue
        best.append((int(head == base), len(base), int(bool(said)), m))
    if not best:
        return None
    best.sort(reverse=True)
    top = [b for b in best if b[:3] == best[0][:3]]
    return top[0][3] if len(top) == 1 else None


# Rows of an official municipal table that belong to no municipality:
# ballots counted county-wide (overseas, hand counts).
_COUNTY_WIDE = re.compile(r"overseas|\bfed\b|hand count", re.I)


def _scale_to_municipalities(units: list[Unit], county: str,
                             table: dict[str, tuple[float, float]], notes: list[str]) -> list[Unit]:
    """Scale a county's precincts so each municipality matches its
    official total, candidate by candidate (the missing votes are assumed
    to fall like the reported ones within the municipality). A county-wide
    official row (overseas ballots) becomes a spread unit. Municipalities
    with no precinct, and precincts with no municipality, fail loudly."""
    rows = [m for m in table if m != "_county"]
    county_wide = [m for m in rows if _COUNTY_WIDE.search(m)]
    munis = [m for m in rows if m not in county_wide]
    groups: dict[str, list[int]] = collections.defaultdict(list)
    out = []
    for i, u in enumerate(units):
        name = u.name
        if u.county != county:
            out.append(u)
            continue
        m = match_municipality(name, munis)
        if m is None and _COUNTY_WIDE.search(name):
            continue  # replaced by the official county-wide rows below
        if m is None:
            raise SystemExit(f"{county}: precinct {name!r} matches no official municipality")
        groups[m].append(i)
    missing = [m for m in munis if m not in groups]
    if missing:
        raise SystemExit(f"{county}: official municipalities with no MEDSL precinct: {missing}")
    for m, idx in groups.items():
        target = table[m]
        have = [sum(units[i].votes[k] for i in idx) for k in (0, 1)]
        scale = [target[k] / have[k] if have[k] else 0.0 for k in (0, 1)]
        for i in idx:
            u = units[i]
            out.append(Unit(u.county, [u.votes[0] * scale[0], u.votes[1] * scale[1]], u.house, u.sld, name=u.name))
        notes.append(f"{county} {m}: precincts scaled x{scale[0]:.3f} D, x{scale[1]:.3f} R to the official total")
    for m in county_wide:
        out.append(Unit(county, list(table[m]), spread=True))
        notes.append(f"{county} {m}: official county-wide row spread over the county's districts")
    return out


def _as_first_sheet(xlsx: bytes, sheet: str) -> bytes:
    """The workbook with `sheet` moved into sheet1's place, for the
    first-sheet reader the app already has."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            if name == "xl/worksheets/sheet1.xml":
                dst.writestr(name, src.read(f"xl/worksheets/{sheet}.xml"))
            else:
                dst.writestr(name, src.read(name))
    return buf.getvalue()


class Official:
    """The certified canvasses the corrections cite, fetched from each
    source's own published file (corrections file `sources`)."""

    def __init__(self, sources: dict, cache: Path):
        self.sources, self.cache = sources, cache / "official"
        self.cache.mkdir(exist_ok=True)
        self._memo: dict = {}

    def _get(self, key: str, url: str, name: str) -> Path:
        return fetch(url, self.cache / name)

    # Indiana: the Election Division's certified ENR archive, county rows.
    def indiana_counties(self) -> dict[str, tuple[float, float]]:
        if "in" not in self._memo:
            src = self.sources["in_enr"]
            root = json.loads(self._get("in", src["url"], "in_2024_president.json").read_text())["Root"]
            out = {}
            for region in root["OfficeCategory"]["Regions"]["Region"]:
                cands = region["RegionSummary"]["Race"]["Candidates"]["Candidate"]
                tot = {c["PARTY"]: float(c["TOTAL"]) for c in cands}
                out[region["MAP_FIPS"]] = (tot["D"], tot["R"])
            self._memo["in"] = out
        return self._memo["in"]

    # New Jersey: the Division of Elections' official per-county PDFs.
    def nj_county_table(self, county_name: str) -> dict[str, tuple[float, float]]:
        key = ("nj", county_name)
        if key not in self._memo:
            src = self.sources["nj_dos"]
            slug = county_name.lower().replace(" ", "")
            path = self._get(key, src["url"].format(county=slug), f"nj_2024_president_{slug}.pdf")
            table = {}
            with pdfplumber.open(path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            for line in text.splitlines():
                m = re.match(r"^(.*?\D)\s+([\d,]+)\s+([\d,]+)(\s+[\d,]+)+\s*$", line)
                if m and not line.startswith(("Presidential", "November")):
                    label = m.group(1).strip()
                    table["_county" if label == "COUNTY TOTAL" else label] = (
                        float(m.group(2).replace(",", "")), float(m.group(3).replace(",", "")))
            if "_county" not in table:
                raise SystemExit(f"NJ {county_name}: no COUNTY TOTAL row in {path}")
            self._memo[key] = table
        return self._memo[key]

    def nj_counties(self) -> dict[str, tuple[float, float]]:
        """Every county's official total, from the statewide report
        ("BERGEN DEMOCRATIC 232,660"). Counties are listed by name; New
        Jersey's FIPS codes run 34001, 34003, ... in alphabetical order."""
        if "nj" not in self._memo:
            path = self._get("nj", self.sources["nj_dos"]["state_url"], "nj_2024_president_counties.pdf")
            with pdfplumber.open(path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            got = collections.defaultdict(dict)
            for line in text.splitlines():
                m = re.match(r"^([A-Z ]+?) (DEMOCRATIC|REPUBLICAN) ([\d,]+)$", line.strip())
                if m:
                    got[m.group(1)][m.group(2)[0]] = float(m.group(3).replace(",", ""))
            if len(got) != 21:
                raise SystemExit(f"NJ statewide report: expected 21 counties, read {len(got)}")
            self._memo["nj"] = {f"34{2 * i + 1:03d}": (got[n]["D"], got[n]["R"]) for i, n in enumerate(sorted(got))}
        return self._memo["nj"]

    def municipal(self, fix: dict) -> dict[str, tuple[float, float]]:
        return self.nj_county_table(fix["county_name"])

    def county(self, fix: dict) -> tuple[float, float]:
        if fix["official"] == "in_enr":
            return self.indiana_counties()[fix["county"]]
        if fix["official"] == "nj_dos":
            return self.nj_county_table(fix["county_name"])["_county"]
        raise SystemExit(f"no county total reader for {fix['official']}")

    # Louisiana: the Secretary of State's official results, by parish.
    def louisiana_parishes(self) -> dict[str, tuple[float, float]]:
        if "la" not in self._memo:
            src = self.sources["la_sos"]
            races = json.loads(self._get("la", src["races_url"], "la_2024_races.json").read_text())
            race = next(r for r in races["Races"]["Race"] if r["ID"] == src["race_id"])
            party = {}
            for c in race["Choice"]:
                if c["Desc"].endswith(" Democratic"):
                    party[c["ID"]] = 0
                elif c["Desc"].endswith(" Republican"):
                    party[c["ID"]] = 1
            if sorted(party.values()) != [0, 1]:
                raise SystemExit("LA: could not find one Democratic and one Republican choice")
            data = json.loads(self._get("la", src["url"], "la_2024_president_parishes.json").read_text())
            out = {}
            for p in data["Parishes"]["Parish"]:
                v = [0.0, 0.0]
                for c in p["Choice"]:
                    if c["ID"] in party:
                        v[party[c["ID"]]] += float(c["VoteTotal"])
                # The portal numbers parishes 01-64 alphabetically, which
                # is the order of their odd FIPS codes (Acadia 22001 ...).
                out[f"22{2 * int(p['ParishValue']) - 1:03d}"] = tuple(v)
            self._memo["la"] = out
        return self._memo["la"]

    # California: the Supplement to the Statement of Vote, president by
    # congressional district, each district's counties listed under it.
    def california_cells(self, county_fips: dict[str, str]) -> dict[tuple[str, str], tuple[float, float]]:
        rows = _xlsx_rows(self._get("ca", self.sources["ca_sos"]["url"], "ca_ssov_pres_by_congress.xlsx").read_bytes())
        cols = list(rows[0])
        d_col = next(c for c in cols if "Harris" in c)
        r_col = next(c for c in cols if "Trump" in c)
        out, district = {}, 1
        for r in rows[2:]:
            label, d, rr = r[cols[0]], r[d_col], r[r_col]
            if not label and d.replace(".", "").isdigit():
                district += 1  # the district's total row closes its county rows
            elif label and label != "Percent" and d.isdigit():
                out[(county_fips[label], str(district))] = (float(d), float(rr))
        if district - 1 != 52:
            raise SystemExit(f"CA SSOV: read {district - 1} districts, expected 52")
        return out

    # The FEC's official 2020 results by state (Federal Elections 2020,
    # Table 2): the 2020 state-total reference. MEDSL's 1976-2020 file
    # predates New York's certified count (13,901 Biden and 7,199 Trump
    # votes short), which VEST and the FEC both have.
    def fec_2020_states(self) -> dict[str, tuple[float, float]]:
        raw = self._get("fec", self.sources["fec_2020"]["url"], "fec_federalelections2020.xlsx").read_bytes()
        rows = _xlsx_rows(_as_first_sheet(raw, self.sources["fec_2020"]["sheet"]), skip=3)
        table = [list(rows[0])] + [list(r.values()) for r in rows]
        out = {}
        for v in table:
            st = (v[0] or "").rstrip("*")  # "ME*", "NE*": electoral votes split by district
            if len(v) > 4 and re.fullmatch(r"[A-Z]{2}", st) and v[3].isdigit():
                out[st] = (float(v[3]), float(v[4]))
        if len(out) != 51:
            raise SystemExit(f"FEC 2020 table: read {len(out)} jurisdictions, expected 51")
        return out

    # Maine: the Secretary of State's results by congressional district,
    # one sheet per district, each closed by a "CGn Total" row.
    def maine_districts(self) -> dict[str, list[float]]:
        raw = self._get("me", self.sources["me_sos"]["url"], "me_pres_by_cd.xlsx").read_bytes()
        out = {}
        for sheet in ("sheet1", "sheet2"):
            rows = _xlsx_rows(_as_first_sheet(raw, sheet))
            cols = list(rows[0])
            d_col = next(c for c in cols if c.startswith("Harris"))
            r_col = next(c for c in cols if c.startswith("Trump"))
            for r in rows:
                m = re.fullmatch(r"CG ?(\d) Total", r.get("MUNICIPALITY", "").strip())
                if m:
                    out[m.group(1)] = [float(r[d_col]), float(r[r_col])]
        if sorted(out) != ["1", "2"]:
            raise SystemExit(f"ME: read district totals {sorted(out)}, expected 1 and 2")
        return out

    # King County, Washington: its own final precinct results.
    def precinct_units(self, st: str, county: str) -> list[Unit]:
        src = self.sources["king_county"]
        path = self._get("king", src["url"], "king_2024_precincts.csv")
        pres = collections.defaultdict(lambda: [0.0, 0.0])
        cd = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r["Race"].startswith("President and Vice President"):
                    continue
                k = 0 if r["CounterType"].startswith("Kamala D. Harris") else 1 if r["CounterType"].startswith("Donald J. Trump") else None
                if k is not None:
                    pres[r["Precinct"]][k] += float(r["SumOfCount"])
                    cd[r["Precinct"]] = r["CongressionalDistrict"]
        return [Unit(county, v, {cd[p]: 1.0}, None) for p, v in pres.items()]


# ---------------------------------------------------------------------------
# 2024 units -> 119th districts
# ---------------------------------------------------------------------------


def allocate_2024(units: list[Unit], seats: set[str], county_20: dict) -> tuple[dict, dict, dict, dict]:
    """(district -> [D, R], (county, district) -> [D, R], (county, district,
    state house district) -> [D, R] for the exactly-labelled units, stats).

    county_20: (county, district) -> [D, R] 2020 votes on the 119th lines,
    the fallback weight where 2024 offers none."""
    single = len(seats) == 1
    lab = collections.defaultdict(lambda: [0.0, 0.0])  # (county, d), single-district units
    labelled = collections.defaultdict(set)
    county_seats = collections.defaultdict(set)
    for (c, d) in county_20:
        county_seats[c].add(d)

    def districts(u: Unit) -> dict[str, float]:
        return {seat(d) if d.strip().isdigit() else d: v for d, v in u.house.items()
                if (d.strip().isdigit() and seat(d) in seats)}

    for u in units:
        h = districts(u)
        if single or (len(h) == 1 and not u.spread):
            d = next(iter(seats)) if single else next(iter(h))
            lab[(u.county, d)][0] += u.votes[0]
            lab[(u.county, d)][1] += u.votes[1]
            labelled[u.county].add(d)
    d24 = collections.defaultdict(lambda: [0.0, 0.0])
    cell = collections.defaultdict(lambda: [0.0, 0.0])
    fine = collections.defaultdict(lambda: [0.0, 0.0])  # (county, d, state house district), exact units only
    stats = collections.Counter()
    for u in units:
        h = districts(u)
        if single or (len(h) == 1 and not u.spread):
            d = next(iter(seats)) if single else next(iter(h))
            weights = {d: (1.0, 1.0)}
            stats["exact"] += sum(u.votes)
            sld = sl.district_key(u.sld) if u.sld else None
            if sld:
                fine[(u.county, d, sld)][0] += u.votes[0]
                fine[(u.county, d, sld)][1] += u.votes[1]
        else:
            if len(h) > 1 and not u.spread:
                turnout = h  # the House votes it cast in each district
                stats["multiDistrict"] += sum(u.votes)
            else:
                pool = county_seats.get(u.county) or seats
                if not u.spread:
                    # No House contest on this ballot: an unopposed seat,
                    # so one of the county's districts no precinct names.
                    pool = (pool - labelled[u.county]) or pool
                    stats["unlabelled"] += sum(u.votes)
                else:
                    stats["countyWide"] += sum(u.votes)
                turnout = {d: (sum(lab[(u.county, d)]) or sum(county_20.get((u.county, d), (0, 0)))) for d in pool}
                if not any(turnout.values()):
                    turnout = {d: 1.0 for d in pool}
                    stats["evenSplit"] += sum(u.votes)
            weights = {}
            for d, t in turnout.items():
                ref = lab.get((u.county, d)) if sum(lab.get((u.county, d), (0, 0))) else county_20.get((u.county, d))
                tot = sum(ref) if ref else 0
                weights[d] = (t * ref[0] / tot, t * ref[1] / tot) if tot else (t, t)
        for k in (0, 1):
            norm = sum(w[k] for w in weights.values())
            for d, w in weights.items():
                share = u.votes[k] * w[k] / norm if norm else u.votes[k] / len(weights)
                d24[d][k] += share
                cell[(u.county, d)][k] += share
    return dict(d24), dict(cell), dict(fine), dict(stats)


# ---------------------------------------------------------------------------
# 2024 on the 120th lines: swing transfer
# ---------------------------------------------------------------------------


def swing_transfer(block_county: list[str], b119: list[str | None], b120: list[str | None],
                   block_sld: list[str | None], v20: np.ndarray, pop: np.ndarray,
                   cell24: dict, fine24: dict) -> tuple[dict, dict]:
    """2024 on the 120th lines.

    Each block's 2024 vote is its 2020 vote times a 2024/2020 ratio, party
    by party: its county x 119th district x state house district cell's,
    where labelled precincts give that cell an exact 2024 count
    (`fine24`), else its county x 119th district cell's. Each county x
    119th district cell is then rescaled to its exact 2024 total
    (`cell24`) and the blocks summed on the 120th lines, so the result is
    exact for every cell the new map keeps whole. A cell with no 2020 vote
    falls back to block population. Returns (district -> [D, R], stats)."""
    cells = collections.defaultdict(list)
    for i, (c, d) in enumerate(zip(block_county, b119)):
        if d is not None:
            cells[(c, d)].append(i)
    out = collections.defaultdict(lambda: [0.0, 0.0])
    lost = 0.0
    fine_blocks = 0
    for key, idx in cells.items():
        idx = np.array(idx)
        target = cell24.get(key, (0.0, 0.0))
        sub = collections.defaultdict(list)
        for n, i in enumerate(idx):
            sub[block_sld[i]].append(n)
        for k in (0, 1):
            base = v20[idx, k].copy()
            if base.sum() <= 0:
                base = pop[idx].astype(float)
            est = base.copy()
            for sld, members in sub.items():
                f = fine24.get((key[0], key[1], sld)) if sld is not None else None
                b20 = base[members].sum()
                if f is not None and b20 > 0:
                    est[members] = base[members] * f[k] / b20
                    fine_blocks += len(members) if k == 0 else 0
            share = target[k] * est / est.sum() if est.sum() > 0 else np.zeros(len(idx))
            for i, v in zip(idx, share):
                if b120[i] is not None:
                    out[b120[i]][k] += v
                else:
                    lost += v
    unplaced = {k: v for k, v in cell24.items() if k not in cells}
    return dict(out), {"votesInCellsWithNoBlock": round(sum(sum(v) for v in unplaced.values())),
                       "votesOnNoSeatBlocks": round(lost), "blocksInFineCells": fine_blocks,
                       "blocks": int(sum(len(v) for v in cells.values()))}


# ---------------------------------------------------------------------------
# One state
# ---------------------------------------------------------------------------


def build_state(st: str, cache: Path, files: dict, fixes: list[dict], official: Official) -> dict:
    t0 = time.time()
    geoids, pop, lonlat, county_fips = state_blocks(st, cache)
    bef119, bef120 = state_befs(st, cache)
    b119 = [seat(bef119.get(g, "")) for g in geoids]
    b120 = [seat(bef120.get(g, "")) for g in geoids]
    missing_bef = sum(1 for g in geoids if g not in bef119 or g not in bef120)
    county = [g[:5] for g in geoids]
    v20, stats20 = votes_2020(st, cache, files, lonlat, pop)
    d20 = {119: collections.defaultdict(lambda: [0.0, 0.0]), 120: collections.defaultdict(lambda: [0.0, 0.0])}
    county_20 = collections.defaultdict(lambda: [0.0, 0.0])
    for i in range(len(geoids)):
        for cong, b in ((119, b119), (120, b120)):
            if b[i] is not None:
                d20[cong][b[i]][0] += v20[i, 0]
                d20[cong][b[i]][1] += v20[i, 1]
        if b119[i] is not None:
            county_20[(county[i], b119[i])][0] += v20[i, 0]
            county_20[(county[i], b119[i])][1] += v20[i, 1]
    units, notes = medsl_units(st, cache, fixes, official)
    for f in fixes:
        if f["action"] == "county_total":
            for cong, b in ((119, b119), (120, b120)):
                touched = {d for c, d in zip(county, b) if c == f["county"] and d is not None}
                if len(touched) != 1:
                    raise SystemExit(f"{st} {f['county']}: a county total is exact only for a county in one "
                                     f"district; it is in {sorted(touched)} on the {cong}th lines")
    seats119 = {d for d in b119 if d is not None}
    d24_119, cell24, fine24, stats24 = allocate_2024(units, seats119, dict(county_20))
    if any(f["action"] == "district_cells" for f in fixes):
        cell24, d24_119 = _official_cells(cell24, official.california_cells(county_fips), notes)
    if any(f["action"] == "district_totals" for f in fixes):
        d24_119 = official.maine_districts()
        notes.append(f"district totals set to the official counts {d24_119}")
    redrawn = bef120 is not bef119
    if redrawn:
        sld_bef = dict(sl.bef_rows(fetch(sl._BEF.format(chamber="sldl"), cache / "sldl26.zip"), st, sl._FIPS[st]))
        block_sld = [sl.district_key(sld_bef[g]) if g in sld_bef else None for g in geoids]
        d24_120, stats120 = swing_transfer(county, b119, b120, block_sld, v20, pop, cell24, fine24)
    else:
        d24_120, stats120 = d24_119, {}
    tot24 = [sum(v[k] for v in d24_119.values()) for k in (0, 1)]
    county24 = collections.defaultdict(lambda: [0.0, 0.0])
    for u in units:
        county24[u.county][0] += u.votes[0]
        county24[u.county][1] += u.votes[1]
    print(f"{st}: {len(geoids)} blocks, {stats20['precincts']} VEST precincts, {len(units)} 2024 units, "
          f"{time.time() - t0:.0f}s", flush=True)
    return {
        "state": st, "redrawn": redrawn, "blocksWithoutBef": missing_bef,
        "d20": {c: {d: v for d, v in t.items()} for c, t in d20.items()},
        "d24": {119: d24_119, 120: d24_120},
        "total20": [float(v20[:, 0].sum()), float(v20[:, 1].sum())], "total24": tot24,
        "county24": dict(county24), "stats20": stats20, "stats24": stats24, "stats120": stats120, "notes": notes,
    }


# ---------------------------------------------------------------------------
# Lean, gates, cross-checks
# ---------------------------------------------------------------------------


def two_party(v) -> float:
    return v[0] / (v[0] + v[1])


def lean(d20, d24, n20: float, n24: float) -> float:
    w = sp.CYCLE_WEIGHTS
    return -100 * (w["2020"] * (two_party(d20) - n20) + w["2024"] * (two_party(d24) - n24))


def national_shares() -> tuple[dict, float, float]:
    inputs = json.loads(STATE_PVI.read_text())["inputs"]
    nat = {y: [sum(v["D"] for v in inputs[y].values()), sum(v["R"] for v in inputs[y].values())] for y in sp.CYCLES}
    return inputs, two_party(nat["2020"]), two_party(nat["2024"])


def red206(path: Path) -> dict[str, list[float]]:
    """District -> [D, R] presidential votes from a Texas Legislative
    Council Red-206 report."""
    with pdfplumber.open(path) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    out: dict[str, list[float]] = {}
    for text in pages:
        lines = text.splitlines()
        head = next((i for i, ln in enumerate(lines) if ln.startswith("District ") and "-D " in ln), None)
        if head is None or "PRESIDENT" not in lines[head - 1]:
            continue
        names = lines[head].split()[1:]
        d_col = next(i for i, n in enumerate(names) if n.endswith("-D"))
        r_col = next(i for i, n in enumerate(names) if n.endswith("-R"))
        for ln in lines[head + 1:]:
            parts = ln.split()
            if not parts or not parts[0].isdigit():
                continue
            counts = [p for p in parts[1:] if re.fullmatch(r"[\d,]+", p)]
            if len(counts) <= max(d_col, r_col):
                continue  # a page heading or footer that starts with a number
            out[str(int(parts[0]))] = [float(counts[d_col].replace(",", "")), float(counts[r_col].replace(",", ""))]
    return out


def ca_ssov(path: Path) -> dict[str, list[float]]:
    """District -> [D, R] from California's Supplement to the Statement
    of Vote, president by congressional district (district totals are the
    unlabelled rows after each district's counties, in district order)."""
    rows = _xlsx_rows(path.read_bytes())
    cols = list(rows[0])
    d_col = next(c for c in cols if "Harris" in c)
    r_col = next(c for c in cols if "Trump" in c)
    totals = [[float(r[d_col]), float(r[r_col])] for r in rows[2:]
              if not r[cols[0]] and r[d_col].replace(".", "").isdigit()]
    return {str(i + 1): t for i, t in enumerate(totals)}


def cross_checks(results: dict, cache: Path) -> tuple[dict, list[str]]:
    report, failures = {}, []
    tables = []
    if "TX" in results:
        for (cong, year), url in TX_RED206.items():
            tables.append(("TX", cong, year, red206(fetch(url, cache / "official" / url.rsplit("/", 1)[1])),
                           "exact" if (cong, year) == (119, "2024") else "estimate", url))
    if "CA" in results:
        tables.append(("CA", 119, "2024", ca_ssov(fetch(CA_SSOV_2024, cache / "official" / "ca_ssov_pres_by_congress.xlsx")),
                       "exact", CA_SSOV_2024))  # also CA's correction source: a check that it was applied
    for st, cong, year, table, kind, url in tables:
        ours = results[st]["d24" if year == "2024" else "d20"][cong]
        diffs = {d: 100 * (two_party(ours[d]) - two_party(v)) for d, v in table.items() if d in ours}
        if set(diffs) != set(ours):
            failures.append(f"cross-check {st} {cong} {year}: districts differ {sorted(set(ours) ^ set(table))}")
        worst = max(diffs, key=lambda d: abs(diffs[d]))
        report[f"{st}-{cong}-{year}"] = {
            "source": url, "districts": len(diffs), "kind": kind,
            "meanAbsDiffPts": round(statistics.mean(abs(v) for v in diffs.values()), 3),
            "maxAbsDiffPts": round(abs(diffs[worst]), 3), "worst": f"{st}-{worst}",
        }
        if abs(diffs[worst]) > CROSS_TOLERANCE[kind]:
            failures.append(f"cross-check {st}-{worst} ({cong}th lines, {year}): two-party D share off the official "
                            f"table by {diffs[worst]:+.2f} pts (> {CROSS_TOLERANCE[kind]})")
    return report, failures


def gates(results: dict, inputs: dict, official: Official, fixes: list[dict],
          turnout_exceptions: list[dict]) -> list[str]:
    failures = []
    excused = {(str(e["congress"]), e["district"]): e["ratio"] for e in turnout_exceptions}
    cook = json.loads(DISTRICT_PVI.read_text())["congresses"]
    fec20 = official.fec_2020_states()
    for st, r in results.items():
        refs = (("2020", r["total20"], fec20[st], "FEC official"),
                ("2024", r["total24"], (inputs["2024"][st]["D"], inputs["2024"][st]["R"]), "MEDSL state file"))
        for year, got, ref, label in refs:
            for k, p in ((0, "D"), (1, "R")):
                if abs(got[k] / ref[k] - 1) > STATE_TOLERANCE:
                    failures.append(f"{st} {year} {p}: {got[k]:,.0f} vs {label} {ref[k]:,.0f} "
                                    f"({100 * (got[k] / ref[k] - 1):+.2f}%)")
        if r["blocksWithoutBef"]:
            failures.append(f"{st}: {r['blocksWithoutBef']} populated blocks missing from a block equivalency file")
        stray = r["stats120"].get("votesInCellsWithNoBlock", 0) if r["redrawn"] else 0
        if stray > STRAY_SHARE * sum(r["total24"]):
            failures.append(f"{st}: {stray:,} 2024 votes are in county x district cells no populated block is in, "
                            "so the 120th-line transfer cannot place them")
        for cong in (119, 120):
            want = {k.split("-", 1)[1] for k in cook[str(cong)]["districts"] if k.startswith(st + "-")}
            have = set(r["d24"][cong]) & set(r["d20"][cong])
            if have != want:
                failures.append(f"{st} {cong}th lines: districts {sorted(have)} vs Cook's {sorted(want)}")
            for d in sorted(have):
                ratio = sum(r["d24"][cong][d]) / sum(r["d20"][cong][d])
                pinned = excused.get((str(cong), f"{st}-{d}"))
                if pinned is not None:
                    if abs(ratio - pinned) > EXCEPTION_SLACK:
                        failures.append(f"{st}-{d} ({cong}th lines): ratio {ratio:.3f} moved from its recorded "
                                        f"exception {pinned:.3f}")
                elif not TURNOUT_RATIO[0] <= ratio <= TURNOUT_RATIO[1]:
                    failures.append(f"{st}-{d} ({cong}th lines): 2024/2020 two-party vote ratio {ratio:.3f}")
    # Official county canvasses, wherever one was fetched.
    checks = {}
    if "IN" in results:
        checks.update({c: v for c, v in official.indiana_counties().items()})
    if "NJ" in results:
        checks.update(official.nj_counties())
    if "LA" in results:
        checks.update(official.louisiana_parishes())
    for f in fixes:  # a county's own canvass, where it and the state's differ
        if f["state"] in results and "official_totals" in f:
            checks[f["county"]] = tuple(f["official_totals"])
    for st, r in results.items():
        for c, (d, rr) in checks.items():
            if c[:2] != sl._FIPS[st]:
                continue
            got = r["county24"].get(c, [0.0, 0.0])
            if abs(got[0] - d) > COUNTY_TOLERANCE * d or abs(got[1] - rr) > COUNTY_TOLERANCE * rr:
                failures.append(f"{st} county {c}: {got[0]:,.0f} D / {got[1]:,.0f} R vs official {d:,.0f} / {rr:,.0f}")
    return failures


def compare_with_cook(leans: dict) -> tuple[dict, list[str]]:
    cook = json.loads(DISTRICT_PVI.read_text())["congresses"]
    summary, alerts = {}, []
    for cong, table in leans.items():
        ref = cook[str(cong)]["districts"]
        keys = sorted(k for k in table if k in ref)
        ours = [table[k] for k in keys]
        theirs = [float(ref[k]) for k in keys]
        rounded = [int(x + 0.5) if x >= 0 else -int(-x + 0.5) for x in ours]
        summary[str(cong)] = {
            "districts": len(keys),
            "r": round(statistics.correlation(ours, theirs), 5),
            "meanAbsDiff": round(statistics.mean(abs(a - b) for a, b in zip(ours, theirs)), 3),
            "roundedEqual": sum(a == b for a, b in zip(rounded, theirs)),
            "withinOne": sum(abs(a - b) <= 1 for a, b in zip(rounded, theirs)),
            "maxAbsDiff": round(max(abs(a - b) for a, b in zip(ours, theirs)), 2),
        }
        for k, a, b in zip(keys, ours, theirs):
            if abs(a - b) > COOK_ALERT:
                alerts.append(f"{k} ({cong}th lines): computed {a:+.2f}, Cook {b:+.0f}")
    return summary, alerts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SOURCE = (
    "Computed by Civitas with Cook's formula from open precinct returns: 2020, the Voting and Election "
    "Science Team's precinct results and shapefiles (Harvard Dataverse doi:10.7910/DVN/K7760H, CC BY 4.0); "
    "2024, the MIT Election Data and Science Lab's precinct returns (github.com/MEDSL/2024-elections-official, "
    "CC0), with listed errors corrected from state and county certified canvasses; U.S. Census Bureau 2020 "
    "blocks and block equivalency files."
)
METHOD = (
    "lean = -100 * (0.25 * (D20/(D20+R20) - N20) + 0.75 * (D24/(D24+R24) - N24)), positive = R lean; N = the "
    "national two-party Democratic share (MEDSL state returns). 2020 precinct votes shared among the Census "
    "blocks inside each precinct by population; 2024 precincts counted in the House district each voted in "
    "(2024 was held on the 119th Congress's lines); the 120th lines in states that redrew by swing transfer "
    "within county x 119th-district cells."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-district-lean")
    ap.add_argument("--states", help="comma-separated subset to build and report; never writes the data file")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    corrections = json.loads(CORRECTIONS.read_text())
    official = Official(corrections["sources"], args.cache)
    inputs, n20, n24 = national_shares()
    states = args.states.split(",") if args.states else [s for s in sl._STATES if s != "DC"]
    files = vest_files(args.cache)
    results = {}
    for st in states:
        fixes = [f for f in corrections["fixes"] if f["state"] == st]
        results[st] = build_state(st, args.cache, files, fixes, official)
    failures = gates(results, inputs, official, corrections["fixes"], corrections.get("turnout_exceptions", []))
    xcheck, xfail = cross_checks(results, args.cache)
    failures += xfail
    unavailable = {str(c): {} for c in (119, 120)}
    for u in corrections.get("unavailable", []):
        unavailable[str(u["congress"])][u["district"]] = u["reason"]
    leans = {}
    for cong in (119, 120):
        leans[cong] = {f"{st}-{d}": lean(r["d20"][cong][d], r["d24"][cong][d], n20, n24)
                       for st, r in results.items() for d in r["d24"][cong] if d in r["d20"][cong]
                       and f"{st}-{d}" not in unavailable[str(cong)]}
    summary, alerts = compare_with_cook(leans)
    for st, r in results.items():
        print(f"  {st}: 2024 allocation {r['stats24']}; 2020 {r['stats20']}; 120th {r['stats120']}")
        for n in r["notes"]:
            print(f"     {n}")
    print("cross-checks:", json.dumps(xcheck, indent=1))
    print("against Cook:", json.dumps(summary, indent=1))
    for a in alerts:
        print("ALERT (not a failure):", a)
    for f in failures:
        print("GATE FAILED:", f)
    if failures:
        print("NOT writing output — gates failed")
        return 1
    if args.states:
        print("subset run: not writing", OUTPUT)
        return 0
    OUTPUT.write_text(json.dumps({
        "_source": SOURCE,
        "_method": METHOD,
        "_sign": "positive = R lean, negative = D lean (matches district_pvi.json)",
        "_window": "2020+2024",
        "_as_of": dt.date.today().isoformat(),
        "_regenerate": REGENERATE,
        "inputs": corrections["inputs"],
        "corrections": [{k: f[k] for k in ("state", "county", "county_name", "action", "why", "official") if k in f}
                        for f in corrections["fixes"]],
        "correctionSources": corrections["sources"],
        "turnoutExceptions": corrections.get("turnout_exceptions", []),
        "validation": {"againstCook": summary, "crossChecks": xcheck, "cookAlerts": alerts},
        "unavailable": unavailable,
        "congresses": {str(c): {"districts": {k: round(v, 1) for k, v in sorted(t.items())}} for c, t in leans.items()},
    }, indent=1, sort_keys=False) + "\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
