"""Build frontend/public/data/lookup/, the district shapes the browser
places a reader in, on the device, when they press "Use my location" or
tap their part of a split town (AGENTS.md §8).

Civitas never asks for an address, and a town name stops short where a
town is split between districts. These files answer the rest without
anything leaving the browser: the page fetches its state's file from this
site and runs the point-in-polygon itself (frontend/src/lib/districtAt.ts).
The coordinates are never sent.

NAMED BY THE LINES THEY HOLD, so a file can never answer for other lines:

  * congress-<N>/<ST>.json (`house`: district number `d`, 0 at large) and
    <ST>.towns.json (`towns`: every city, town and community the place
    crosswalk lists under more than one of those districts, `n` its name
    as the crosswalk writes it; `roads`: TIGER's primary and secondary
    roads inside them, `n` the road's name), for the split-town map.
    One folder per map: congress-119 for every state on its 119th
    Congress lines, congress-120 for the states voting on new lines this
    cycle (`map_for_state`, as build_county_district_crosswalk chooses
    them; Missouri's stayed map is not one). The API names the folder for
    a state and a Congress (district_lines.lookup_lines, the map that
    state is on), so a state that kept its map is found in its earlier
    folder when a new Congress sits, with nothing rebuilt.
  * election-<cycle>/<ST>.legislature.json: `sldu` and `sldl`, the state
    senate and house districts the cycle's election is on, `d` the key the
    ballot pages use (fetch_state_leg_crosswalk.district_key; a district
    the ballots don't number, as in Vermont, has none). Nebraska has no
    lower house.

SOURCES. Public domain, U.S. Census Bureau, each found by rule
(app.census_lines): TIGER/Line (the full-resolution lines the block
equivalency files are drawn from: a Congress's in the vintage of the year
it convenes, else the year it is elected; the state legislative districts
in the vintage of the election the newest legislative block file is for;
the newest roads) and the newest cartographic boundary files for the
towns. TIGER
districts are not clipped to the shore: a position on a beach or a pier is
still in its district. Unassigned water ("ZZ") is left out.

SIMPLIFICATION AND THE ACCURACY GATE. The House layer is simplified at
`--interval`, the legislative ones at `--legislature-interval` (Douglas-
Peucker with keep-shapes: no line moves further than that; shared arcs keep
neighbouring districts gapless), and quantized. Before anything is written,
every populated 2020 block's internal point (the P.L. 94-171 header) is
placed, with the same even-odd ray cast the browser uses, in the simplified
shapes and in the same lines unsimplified, and compared with the district
the Census block equivalency file assigns the block (each map's zip with
its states' own files, and the newest SLDU/SLDL files).
Blocks are what the lines are made of, and many a block's point is a few
metres from one, so this measures exactly what simplification could break;
the residents within NEAR_LINE_M of a line are reported on their own. The
unsimplified lines are the floor: where TIGER and a block file disagree (a
few states' legislative files), that is not the simplification's doing. The
script refuses to write if simplifying loses more than `--max-drop` of a
layer's residents, or `--max-near-line-drop` of its near-line ones, and
writes every figure to lookup/_accuracy.json beside the files.

Needs the pinned mapshaper (scripts/mapshaper/: in the backend image, else
npx at that version) and build_district_lean's reader; network on the first
run (~5-20MB per state plus the P.L. headers), cached in --cache. The data
refresh runs it through scripts/build_district_shapes.py when a map the
site needs has no shapes. From the repo root:

    python backend/scripts/build_district_lookup.py --congress 119 --congress 120 --cycle 2026 \\
        [--cache DIR] [--out DIR] [--interval 10] [ST ...]

`--congress` (repeatable): the Congresses whose House lines are needed (the
sitting one and the one the coming election seats); a folder is built for
each map they use (congress-<the map's Congress>). `--cycle`: the election
the legislature folder is for.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_district_lean as lean  # noqa: E402
import build_district_place_crosswalk as places_cw  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402
from build_district_topology import MAPSHAPER  # noqa: E402

from app import census_lines  # noqa: E402
from app.ordinals import ordinal  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "frontend" / "public" / "data" / "lookup"
PLACES = REPO / "backend" / "app" / "data" / "congressional_district_places.json"

# The simplification distances in metres, chosen by measurement (the PR
# that added this script reports the table). Douglas-Peucker, so no
# simplified line is more than this far from the TIGER line it replaces;
# Visvalingam (mapshaper's default) made bigger files that misplaced 1-5%
# of residents within 100m of a line at 25m. The House, which every finder
# reads, at 3m: Arkansas and Maine misplaced 21 and 100 near-line residents
# at 10m and none at 3m beyond what the unsimplified lines do; the state
# legislative districts too (at 10m Delaware's lost a 42-resident block).
INTERVAL_M = 3
# The split towns' outlines and roads are only drawn: 20m is under a pixel
# on a town-sized map, and a third of the bytes.
TOWNS_INTERVAL_M = 20
# "Near a line": the residents a simplified line is most likely to put on
# the wrong side, reported on their own so the overall share (most blocks
# are nowhere near a line) can't hide them.
NEAR_LINE_M = 100
# The gates, against the same lines unsimplified (whose own disagreements
# with the block files, where TIGER and the block files differ, are not the
# simplification's): refuse to write if simplifying loses more than these
# shares of residents placed in their block's district, overall and near a
# line. What simplifying loses at 3m doesn't shrink at finer intervals: it
# is the odd block whose point sits within a metre or two of a line or in a
# sliver (Delaware's whole loss is one 42-resident block). Measured on all
# fifty states, the worst were 0.0042% of a layer's residents and 0.25% of
# its near-line ones; the gates sit just above, so a change in the source or
# the method that loses more is refused.
MAX_DROP = 0.0001
MAX_NEAR_LINE_DROP = 0.005
# A grid fine enough not to add error on top of the simplification: 1e6
# steps across even Texas (~1,300 km) is ~1.3m. At 1e5 (~10m steps across a
# wide state) Washington misplaced 413 near-line residents that 1e6 places.
QUANTIZATION = 1_000_000
# Near-line points written to the frontend's test fixture
# (frontend/src/lib/districtAt.fixture.json), so the browser's decoder is
# checked against this measurement on the hardest points: a small state, a
# town-based one, and one on its redrawn 2026 map.
FIXTURE_STATES = ("RI", "MA", "UT")
FIXTURE_POINTS = 25
FIXTURE = REPO / "frontend" / "src" / "lib" / "districtAt.fixture.json"

_NODE_ENV = {**os.environ, "NODE_OPTIONS": "--max-old-space-size=8000"}


def fetch_zip(url: str, cache: Path, attempts: int = 3) -> Path:
    """A cached download that is really a zip. www2.census.gov's firewall
    answers some requests with a 200 HTML "Request Rejected" page (every
    time, for Utah's 2026 state house file), which must not be cached as
    the file; the Bureau's FTP mirror of the same path serves it."""
    mirror = url.replace("https://www2.census.gov/", "ftp://ftp2.census.gov/")
    for attempt in range(attempts):
        for source in (url, mirror):
            path = sl.download(source, cache)
            if zipfile.is_zipfile(path):
                return path
            path.unlink()
        time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"{url}: not a zip after {attempts} tries, nor from {mirror}")


def _mapshaper(args: list[str]) -> str:
    result = subprocess.run(
        [*MAPSHAPER, *args], capture_output=True, text=True, env=_NODE_ENV
    )
    if result.returncode:
        raise RuntimeError(f"mapshaper failed:\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# TopoJSON decoding and placement (the browser's districtAt.ts, in numpy)
# ---------------------------------------------------------------------------


def decode_layer(topo: dict, layer: str) -> tuple[list, list]:
    """(shapes, properties) of one object: each shape a list of rings as
    (n, 2) arrays of [lon, lat], every ring of every part together, which
    an even-odd test reads correctly (lean.locate)."""
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        a = np.cumsum(np.array(arc, dtype=float), axis=0)
        arcs.append(np.column_stack([a[:, 0] * sx + tx, a[:, 1] * sy + ty]))

    def ring(indexes: list[int]) -> np.ndarray:
        parts = []
        for i in indexes:
            arc = arcs[~i][::-1] if i < 0 else arcs[i]
            parts.append(arc if not parts else arc[1:])
        return np.concatenate(parts)

    shapes, props = [], []
    for g in topo["objects"][layer]["geometries"]:
        if g.get("type") == "Polygon":
            rings = [ring(r) for r in g["arcs"]]
        elif g.get("type") == "MultiPolygon":
            rings = [ring(r) for poly in g["arcs"] for r in poly]
        else:
            continue
        shapes.append(rings)
        props.append(g.get("properties") or {})
    return shapes, props


def near_line(topo: dict, layer: str, points: np.ndarray, within_m: float) -> np.ndarray:
    """Which points lie within `within_m` metres of a line between two of
    the layer's districts (an arc two of its shapes share), where a
    simplified line is most likely to put them on the wrong side."""
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    uses: Counter = Counter()
    for g in topo["objects"][layer]["geometries"]:
        stack = [g.get("arcs") or []]
        while stack:
            x = stack.pop()
            if isinstance(x, int):
                uses[x if x >= 0 else ~x] += 1
            else:
                stack.extend(x)
    lat0 = np.radians(points[:, 1].mean()) if len(points) else 0.0
    to_m = np.array([111_320.0 * np.cos(lat0), 110_540.0])
    samples = []
    for i, n in uses.items():
        if n < 2:
            continue
        a = np.cumsum(np.array(topo["arcs"][i], dtype=float), axis=0) * (sx, sy) + (tx, ty)
        a = a * to_m
        for p, q in zip(a[:-1], a[1:]):
            # Densified to a tenth of the distance asked about.
            steps = max(1, int(np.hypot(*(q - p)) / (within_m / 10)))
            samples.append(p + np.linspace(0, 1, steps + 1)[:, None] * (q - p))
    if not samples:
        return np.zeros(len(points), dtype=bool)
    dist, _ = cKDTree(np.concatenate(samples)).query(points * to_m, distance_upper_bound=within_m)
    return np.isfinite(dist)


def accuracy(
    topo: dict, layer: str, points: np.ndarray, pop: np.ndarray, truth: list, sample: list | None = None
) -> dict:
    """How many residents' block points land in the district their block
    is assigned to, overall and within NEAR_LINE_M of a district line.
    `truth` is the district per point (None = water or no seat, skipped)."""
    shapes, props = decode_layer(topo, layer)
    owner = lean.locate(points, shapes)
    got = [str(props[i]["d"]) if i >= 0 else None for i in owner]
    keep = np.array([t is not None for t in truth])
    right = np.array([g == t for g, t in zip(got, truth)])
    unplaced = np.array([g is None for g in got])
    near = near_line(topo, layer, points, NEAR_LINE_M) & keep
    total, near_total = pop[keep].sum(), pop[near].sum()
    if sample is not None and near.any():
        # Near-line blocks for the browser's test, with the block's own
        # district and where this measurement put it.
        picks = np.random.default_rng(0).choice(np.flatnonzero(near), min(FIXTURE_POINTS, near.sum()), replace=False)
        sample += [
            [round(float(points[i, 0]), 7), round(float(points[i, 1]), 7), layer, truth[i], got[i]]
            for i in sorted(picks)
        ]
    return {
        "residents": int(total),
        "blocks": int(keep.sum()),
        "placedRight": round(float(pop[keep & right].sum() / total), 6) if total else 1.0,
        "residentsMisplaced": int(pop[keep & ~right & ~unplaced].sum()),
        "residentsUnplaced": int(pop[keep & unplaced].sum()),
        "residentsNearLine": int(near_total),
        "nearLinePlacedRight": round(float(pop[near & right].sum() / near_total), 6) if near_total else 1.0,
    }


# ---------------------------------------------------------------------------
# Building one state
# ---------------------------------------------------------------------------


class Sources:
    """The files of one build, found by rule (app.census_lines) once."""

    def __init__(self, cycle: int):
        self.towns = (
            census_lines.newest(f"{census_lines.TIGER}/GENZ{{y}}/shp/cb_{{y}}_us_place_500k.zip"),
            census_lines.newest(f"{census_lines.TIGER}/GENZ{{y}}/shp/cb_{{y}}_us_cousub_500k.zip"),
        )
        self.sld_bef = {kind: census_lines.find_sld_bef(cycle, kind) for kind in ("sldu", "sldl")}
        if not all(self.towns) or not all(self.sld_bef.values()):
            raise SystemExit(f"missing a source: towns {self.towns}, legislative files {self.sld_bef}")
        self.sld_cycle = self.sld_bef["sldu"][2]
        roads = census_lines.newest(f"{census_lines.TIGER}/TIGER{{y}}/PRISECROADS/tl_{{y}}_01_prisecroads.zip")
        self.roads_year = int(roads.split("/TIGER", 1)[1][:4])
        self.cd_bef: dict[int, Path] = {}

    def cd_url(self, congress: int, fips: str) -> str:
        url = census_lines.tiger_cd_url(congress, fips)
        if url is None:
            raise SystemExit(f"no TIGER/Line districts for the {ordinal(congress)} Congress ({fips})")
        return url

    def sld_url(self, kind: str, fips: str) -> str:
        y = self.sld_cycle
        return f"{census_lines.TIGER}/TIGER{y}/{kind.upper()}/tl_{y}_{fips}_{kind}.zip"

    def roads_url(self, fips: str) -> str:
        y = self.roads_year
        return f"{census_lines.TIGER}/TIGER{y}/PRISECROADS/tl_{y}_{fips}_prisecroads.zip"

    def bef(self, congress: int, cache: Path) -> Path:
        if congress not in self.cd_bef:
            found = census_lines.find_congress_bef(congress)
            if found is None:
                raise SystemExit(f"no block equivalency file for the {ordinal(congress)} Congress")
            self.cd_bef[congress] = sl.download(found[0], cache)
        return self.cd_bef[congress]


def split_towns(congress_key: str, state: str) -> set[str]:
    """Places the crosswalk lists under more than one of the state's House
    districts on this map: the towns a name alone can't settle."""
    seen: Counter = Counter()
    places = census_lines.read_json_preferring(census_lines.runtime_data_path(PLACES.name), PLACES, default={})
    for key, names in places["congresses"][congress_key].items():
        if key.split("-")[0] == state:
            seen.update(set(names))
    return {n for n, c in seen.items() if c > 1}


_COUSUB_NAMES: dict[str, set[str]] = {}


def town_names(cache: Path, fips: str, src: Sources) -> set[str]:
    """The county subdivision names in a state, from the cartographic file."""
    if not _COUSUB_NAMES:
        with zipfile.ZipFile(fetch_zip(src.towns[1], cache)) as zf:
            dbf = zf.read(next(n for n in zf.namelist() if n.endswith(".dbf")))
        for row in lean.read_dbf(dbf, ("STATEFP", "NAMELSAD")):
            # read_dbf decodes Latin-1; the cartographic files are UTF-8.
            name = row["NAMELSAD"].encode("latin-1").decode("utf-8")
            _COUSUB_NAMES.setdefault(row["STATEFP"], set()).add(name)
    return _COUSUB_NAMES.get(fips, set())


def _layer_args(path: Path, name: str, expr: str, where: str) -> list[str]:
    return ["-i", str(path), "name=" + name, "-filter", where, "-each", expr, "-filter-fields", "d"]


def build(
    state: str,
    cache: Path,
    work: Path,
    interval: float,
    src: Sources,
    house_congress: int | None = None,
    towns_key: str | None = None,
    legislature: bool = False,
) -> dict[str, dict]:
    """One state's topologies, simplified at `interval` metres (0: not at
    all): "house" (one Congress's House districts) and "towns" (the towns
    split between them, with their roads) when `house_congress` is given,
    "legislature" (state senate and house) when asked."""
    fips = sl._FIPS[state]
    args: list[str] = []
    outs: dict[str, list[str]] = {}
    if house_congress:
        cd_field = f"CD{house_congress}FP"
        args += _layer_args(
            fetch_zip(src.cd_url(house_congress, fips), cache),
            "house",
            f"d = +{cd_field}",
            f'{cd_field} !== "ZZ" && {cd_field} !== "98"',
        )
        outs["house"] = ["house"]
        towns = sorted(split_towns(towns_key or str(house_congress), state))
        if towns:
            # A strong-MCD state's towns are county subdivisions, and some
            # are also places of the same name ("Providence city"): the town
            # file first, then the places it doesn't already name.
            in_cousub = sorted(set(towns) & town_names(cache, fips, src))
            rest = sorted(set(towns) - set(in_cousub))
            sources = [(u, n) for u, n in ((src.towns[1], in_cousub), (src.towns[0], rest)) if n]
            for i, (url, names) in enumerate(sources):
                args += [
                    "-i", str(fetch_zip(url, cache)), f"name=towns{i}",
                    "-filter", f'STATEFP === "{fips}" && {json.dumps(names)}.indexOf(NAMELSAD) > -1',
                    "-each", "n = NAMELSAD", "-filter-fields", "n",
                ]
            if len(sources) == 2:
                args += ["-merge-layers", "target=towns0,towns1", "name=towns", "force"]
            else:
                args += ["-rename-layers", "towns", "target=towns0"]
            args += [
                "-i", str(fetch_zip(src.roads_url(fips), cache)), "name=roads",
                "-clip", "towns", "target=roads",
                "-filter", "!!FULLNAME", "-each", "n = FULLNAME", "-filter-fields", "n",
            ]
            outs["towns"] = ["towns", "roads"]
    if legislature:
        layers = []
        for kind in ("sldu", "sldl"):
            url = src.sld_url(kind, fips)
            try:
                path = fetch_zip(url, cache)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:  # Nebraska has no lower house file
                    raise
                print(f"   {state}: no {kind} file")
                continue
            args += _layer_args(path, kind, f"d = {kind.upper()}ST", "true")
            layers.append(kind)
        if layers:
            outs["legislature"] = layers
    paths = {kind: work / f"{state}.{kind}.json" for kind in outs}
    lookup = [layer for kind, targets in outs.items() if kind != "towns" for layer in targets]
    simplify = [("-simplify", "dp", f"interval={interval}", "keep-shapes", "target=" + ",".join(lookup))]
    if "towns" in outs:
        # Drawn, never used to place anyone (a tap is placed in the House
        # shapes), so coarser.
        simplify.append(("-simplify", "dp", f"interval={TOWNS_INTERVAL_M}", "keep-shapes", "target=towns,roads"))
    _mapshaper(
        [
            *args,
            *(arg for step in (simplify if interval else []) for arg in step),
            *[
                arg
                for kind, targets in outs.items()
                for arg in (
                    "-o", str(paths[kind]), "format=topojson", f"quantization={QUANTIZATION}", "bbox",
                    "target=" + ",".join(targets),
                )
            ],
        ]
    )
    topos = {kind: json.loads(path.read_text()) for kind, path in paths.items()}
    topo = topos.get("legislature")
    for kind in ("sldu", "sldl"):
        if topo and kind in topo["objects"]:
            # The key the ballot pages use; a code that names no seat the
            # ballots identify by number (unassigned water) is dropped.
            kept = []
            for g in topo["objects"][kind]["geometries"]:
                g["properties"]["d"] = sl.district_key(str(g["properties"]["d"]))
                if g["properties"]["d"] is not None:
                    kept.append(g)
            topo["objects"][kind]["geometries"] = kept
    return topos


def truth_house(state: str, congress: int, cache: Path, geoids: list[str], src: Sources) -> list:
    fips = sl._FIPS[state]
    assigned = {g: places_cw.cd_key(cd) for g, cd in census_lines.bef_rows(src.bef(congress, cache), fips, state)}
    return [None if assigned.get(g) is None else assigned[g] for g in geoids]


def truth_sld(state: str, kind: str, cache: Path, geoids: list[str], src: Sources) -> list:
    fips = sl._FIPS[state]
    path = sl.download(src.sld_bef[kind][0], cache)
    assigned = {g: sl.district_key(code) for g, code in sl.bef_rows(path, state, fips)}
    return [assigned.get(g) for g in geoids]


def main(argv: list[str] | None = None) -> int:
    global QUANTIZATION
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="*", help="postal codes (default: all 50)")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-census")
    ap.add_argument("--interval", type=float, default=INTERVAL_M, help="metres")
    ap.add_argument("--quantization", type=int, default=QUANTIZATION)
    ap.add_argument("--max-drop", type=float, default=MAX_DROP)
    ap.add_argument("--max-near-line-drop", type=float, default=MAX_NEAR_LINE_DROP)
    ap.add_argument("--congress", type=int, action="append", required=True,
                    help="a Congress whose House lines are needed (repeatable)")
    ap.add_argument("--cycle", type=int, required=True, help="the election the legislature folder is for")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="the lookup folder to write")
    ap.add_argument("--dry-run", action="store_true", help="measure only; write nothing")
    args = ap.parse_args(argv)
    QUANTIZATION = args.quantization
    args.cache.mkdir(parents=True, exist_ok=True)
    states = args.states or sorted(s for s in sl._STATES if s != "DC")
    src = Sources(args.cycle)
    cycles = census_lines.maps_cycles()
    unlisted = [c for c in args.congress if not census_lines.listed(cycles, c)]
    if unlisted:
        raise SystemExit(f"the elections that seat {unlisted} are not in redrawn_congressional_maps.json yet")
    maps = {c: census_lines.state_maps(c, states, cycles) for c in sorted(set(args.congress))}

    written: dict[str, bytes] = {}
    report: dict[str, dict] = {}
    fixture: dict[str, list] = {}
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for st in states:
            print(f"{st}:")
            # Each map is measured on the blocks of the census it is drawn
            # on (census_lines.census_year).
            blocks: dict[int, tuple] = {}

            def on(census: int) -> tuple:
                if census not in blocks:
                    blocks[census] = lean.state_blocks(st, args.cache, census)[:3]
                return blocks[census]

            # (folder, build arguments, {layer: the block files' answer}):
            # one House folder per map the Congresses use (named by the
            # map, so a state that kept its map stays in its earlier
            # folder: district_lines.lookup_lines), its towns from a
            # Congress on that map; and the legislature.
            on_map = {}
            for c, by_state in maps.items():
                on_map.setdefault(by_state[st], c)
            jobs = [
                (f"congress-{m}", {"house_congress": m, "towns_key": str(c)}, census_lines.census_year(m),
                 lambda m=m: {"house": truth_house(st, m, args.cache, on(census_lines.census_year(m))[0], src)})
                for m, c in sorted(on_map.items())
            ]
            sld_census = census_lines.census_year(census_lines.seated_by(src.sld_cycle))
            jobs.append((
                f"election-{src.sld_cycle}",
                {"legislature": True},
                sld_census,
                lambda: {kind: truth_sld(st, kind, args.cache, on(sld_census)[0], src) for kind in ("sldu", "sldl")},
            ))
            for folder, how, census, truths_of in jobs:
                geoids, pop, points = on(census)
                truths = truths_of()
                simplified = build(st, args.cache, work, args.interval, src, **how)
                unsimplified = build(st, args.cache, work, 0, src, **how)
                for layer, truth in truths.items():
                    kind = "house" if layer == "house" else "legislature"
                    if kind not in simplified or layer not in simplified[kind]["objects"] or not any(truth):
                        continue
                    suffix = f".{kind}" if kind != "house" else ""
                    sample = fixture.setdefault(f"{folder}/{st}{suffix}.json", []) if st in FIXTURE_STATES else None
                    m = accuracy(simplified[kind], layer, points, pop, truth, sample)
                    base = accuracy(unsimplified[kind], layer, points, pop, truth)
                    m["unsimplified"] = {k: base[k] for k in ("placedRight", "nearLinePlacedRight")}
                    print(f"   {folder} {layer}: {m}")
                    report.setdefault(folder, {}).setdefault(st, {})[layer] = m
                    drop = base["placedRight"] - m["placedRight"]
                    near_drop = base["nearLinePlacedRight"] - m["nearLinePlacedRight"]
                    if drop > args.max_drop or near_drop > args.max_near_line_drop:
                        problems.append(
                            f"{st} {folder} {layer}: simplifying cost {drop:.4%} of residents, "
                            f"{near_drop:.4%} of those near a line"
                        )
                for kind, topo in simplified.items():
                    for layer, obj in topo["objects"].items():
                        for g in obj["geometries"]:
                            if layer != "roads" and g.get("type") is None:
                                problems.append(f"{st} {folder} {layer}: {g['properties']} lost")
                    suffix = f".{kind}" if kind != "house" else ""
                    written[f"{folder}/{st}{suffix}.json"] = json.dumps(topo, separators=(",", ":")).encode()

    if problems:
        print("REFUSING TO WRITE: accuracy gate failed:")
        for p in problems:
            print(f"  {p}")
        return 1
    sizes = {k: len(v) for k, v in written.items()}
    groups: dict[str, list[int]] = {}
    for name, size in sizes.items():
        folder, file = name.split("/")
        groups.setdefault(f"{folder} {file.split('.', 1)[1]}", []).append(size)
    for group, files in sorted(groups.items()):
        print(f"{group}: {len(files)} states, {sum(files) // 1024}KB, largest {max(files) // 1024}KB")
    if args.dry_run:
        return 0
    for name, blob in written.items():
        path = args.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    # The browser decoder's test fixture is the repository's: written by a
    # build into the repository's own folder, never by the data refresh's.
    if fixture and args.out == OUT_DIR:
        FIXTURE.write_text(
            json.dumps(
                {
                    "_source": (
                        f"Census blocks whose internal point is within {NEAR_LINE_M}m of a district line, as "
                        "[lon, lat, layer, the block's district (block equivalency file), where the build's "
                        "measurement placed it]. Written by backend/scripts/build_district_lookup.py."
                    ),
                    "files": [{"file": f, "points": p} for f, p in sorted(fixture.items())],
                },
                indent=1,
            )
            + "\n"
        )
    accuracy_path = args.out / "_accuracy.json"
    existing = json.loads(accuracy_path.read_text()) if accuracy_path.exists() else {}
    for folder, by_state in report.items():
        existing.setdefault(folder, {}).update(by_state)
    existing["_source"] = (
        "Share of 2020 residents whose census block's internal point (P.L. 94-171 header) falls, in "
        "these simplified shapes, in the district the Census block equivalency file assigns the "
        f"block. Simplified at {args.interval:g}m, quantized to {QUANTIZATION} steps. Generated "
        f"{dt.date.today().isoformat()} by backend/scripts/build_district_lookup.py."
    )
    accuracy_path.write_text(json.dumps(existing, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
