"""Build frontend/public/data/cd/<ST>.json — each state's congressional
district outlines, for the clickable district map on the state ballot page.

Civitas never asks a visitor for their address, so "which district is
mine" has to be answerable by pointing. Counties do most of that work
(county_district_crosswalk.json) because people know theirs, but 13% of
US counties span more than one district, and a dense city can hold ten
districts inside one county. A map of the districts themselves is the
only view that answers "which one is my neighborhood in" without asking.

WHICH MAP. District lines change whenever a state redistricts, not once
a decade: nine states redrew for the 2026 elections. `--congress N` draws
N's lines (the Congress the coming election seats): each state on the map
it votes on in that election (app/data/redrawn_congressional_maps.json,
the copy in use, via app.census_lines), the same choice the county and
place crosswalks make, so the map and the county list can never describe
different districts. The data refresh's choice of N and of when to
rebuild is the crosswalks' (app/data_refresh.py).

SOURCES. Public domain, U.S. Census Bureau, found by rule (app.census_lines).
  * States on a map with a cartographic boundary file (1:500,000, the
    vintage of the map's election year: cb_2024_us_cd119_500k for the
    119th Congress): that file. Cartographic boundary files are clipped to
    the shoreline, which is what a reader recognises; TIGER/Line extends
    districts over water.
  * States on a map with none yet (the 120th's in 2026): each district is
    built from its blocks — the Redistricting Data Office's block
    equivalency file for that map joined to TIGER/Line's blocks of the
    census the lines are drawn on (tl_2020_<FIPS>_tabblock20), dissolved by
    district, then clipped to the state's 1:500,000 outline (the vintage
    of the file the simplification is measured on) so the coast matches
    the cartographic style of every other state.

SIMPLIFICATION. The newest national cartographic file of a Congress up
to N (the 119th's, for the 120th) is simplified to 3% of its vertices, as it always has been; mapshaper reports the distance threshold
that works out to, and the block-built states are simplified with that
same threshold (`interval=`) so every state is drawn at the same level of
detail. A percentage would not transfer: dissolved blocks carry
full-resolution TIGER lines, many times the vertices of a 1:500k file.

ONE FILE PER STATE, not one national file: a state page loads only its
own districts. The whole country is ~207KB at this simplification and the
largest single state (Alaska, all coastline) is ~20KB.

VALIDATED against county_district_crosswalk.json before anything is
written: every state must come out with exactly the district numbers the
crosswalk lists, and no district may be lost to simplification. A dense
urban district is the thing simplification is most likely to collapse,
and `keep-shapes` alone is a request, not a guarantee.

Needs the pinned mapshaper (scripts/mapshaper/: in the backend image, else
npx at that version) and network access; the block files are large (Texas
alone is ~750MB, about 3 GB of memory to dissolve), so pass --cache to keep
them between runs. The data refresh runs it through
scripts/build_district_shapes.py when a map the ballot needs has no outlines.
Run from the repo root:

    python backend/scripts/build_district_topology.py --congress N [--cache DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_state_leg_crosswalk as sl  # noqa: E402
from build_county_district_crosswalk import download  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from app import census_lines  # noqa: E402
from app.ordinals import ordinal  # noqa: E402

OUT_DIR = REPO / "frontend" / "public" / "data" / "cd"
CROSSWALK_NAME = "county_district_crosswalk.json"

# Enough to keep every district recognisable at state-page size while
# staying small; lower loses urban districts, higher only adds bytes.
SIMPLIFY = "3%"

# Output options shared by both builds, so every file has the same shape.
# bbox: the page fits its own projection to it, so the browser needs no
# geo library beyond react-simple-maps.
_TOPOJSON_OUT = ["format=topojson", "quantization=10000", "bbox"]

# Dissolving a big state's blocks needs more than node's default heap.
_NODE_ENV = {**os.environ, "NODE_OPTIONS": "--max-old-space-size=12000"}


def _fips_to_state() -> dict[str, str]:
    """The states' FIPS codes -> postal codes (DC included: it has no
    district, so it is left out by the delegate filter)."""
    return {fips: st for st, fips in sl._FIPS.items()}


# The pinned mapshaper (scripts/mapshaper/package.json): installed in the
# backend image (the Dockerfile's mapshaper stage sets MAPSHAPER), npx at
# the same version elsewhere.
MAPSHAPER_VERSION = json.loads((Path(__file__).resolve().parent / "mapshaper" / "package.json").read_text())[
    "dependencies"]["mapshaper"]
MAPSHAPER = os.environ.get("MAPSHAPER", "").split() or ["npx", "-y", f"mapshaper@{MAPSHAPER_VERSION}"]


def _expected_districts(congress: int) -> dict[str, set[int]]:
    """The district numbers of the county crosswalk on `congress`'s lines
    (the copy in use, carried by district_lines.table_for_lines)."""
    from app.district_places import counties_on_lines_of

    expected: dict[str, set[int]] = {}
    for key in counties_on_lines_of(congress) or {}:
        state, district = key.split("-")
        expected.setdefault(state, set()).add(int(district))
    return expected


def parse_threshold(mapshaper_output: str) -> str:
    """The distance threshold mapshaper's `-simplify ... stats` reports,
    as an `interval=` value ("2293.9253m")."""
    match = re.search(
        r"Simplification threshold:\s*([\d.]+)\s*meters", mapshaper_output
    )
    if not match:
        raise ValueError("mapshaper did not report a simplification threshold")
    return f"{match.group(1)}m"


def validate_and_normalize(
    topo: dict, state: str, expected: dict[str, set[int]]
) -> tuple[bytes, list[str]]:
    """One stable object key and nothing but the district number, so the
    component reads the same shape for every state; plus any problems."""
    problems: list[str] = []
    layer = next(iter(topo["objects"]))
    geometries = topo["objects"][layer]["geometries"]
    got = {g["properties"]["district"] for g in geometries}
    lost = [g["properties"]["district"] for g in geometries if g.get("type") is None]
    if got != expected.get(state, set()):
        problems.append(
            f"{state}: {sorted(got)} != crosswalk {sorted(expected.get(state, set()))}"
        )
    if lost:
        problems.append(f"{state}: districts lost to simplification: {lost}")
    if not topo.get("bbox"):
        problems.append(f"{state}: no bbox written")
    topo["objects"] = {"districts": topo["objects"][layer]}
    for g in topo["objects"]["districts"]["geometries"]:
        g["properties"] = {"district": g["properties"]["district"]}
    return json.dumps(topo, separators=(",", ":")).encode(), problems


def _mapshaper(args: list[str]) -> str:
    result = subprocess.run(
        [*MAPSHAPER, *args],
        check=True,
        capture_output=True,
        text=True,
        env=_NODE_ENV,
    )
    return result.stdout + result.stderr


def _build_cartographic(url: str, congress: int, keep: set[str], cache: Path, work: Path,
                        interval: str | None = None) -> tuple[dict[str, Path], str]:
    """The states in `keep` (FIPS) from a national cartographic file of
    `congress`'s districts, simplified to SIMPLIFY of the whole file's
    vertices (or at `interval`), and the distance threshold that came to."""
    folder = work / f"cb{congress}"
    zipfile.ZipFile(download(url, cache)).extractall(folder)
    shp = next(folder.glob("*.shp"))
    split_dir = folder / "split"
    split_dir.mkdir()
    field = f"CD{congress}FP"
    out = _mapshaper(
        [
            str(shp),
            # 98 = non-voting delegates (DC and the territories), which
            # have no House race on a state ballot page.
            "-filter",
            f'{field} !== "98" && {field} !== "ZZ"',
            "-each",
            f"district = +{field}, st = STATEFP",
            "-filter-fields",
            "district,st",
            "-simplify",
            *([SIMPLIFY] if interval is None else [f"interval={interval}"]),
            "keep-shapes",
            "stats",
            "-split",
            "st",
            "-o",
            f"{split_dir}/",
            "singles",
            *_TOPOJSON_OUT,
        ]
    )
    return {p.stem: p for p in split_dir.glob("*.json") if p.stem in keep}, parse_threshold(out)


def _write_assignments(bef: Path, work: Path, fips_list: set[str]) -> dict[str, Path]:
    """Per-state GEOID,CDFP CSVs from a block equivalency file."""
    paths = {}
    for fips in fips_list:
        paths[fips] = work / f"bef_{fips}.csv"
        with open(paths[fips], "w", newline="") as fh:
            fh.write("GEOID,CDFP\n")
            for geoid, cd in census_lines.bef_rows(bef, fips, ""):
                fh.write(f"{geoid},{cd}\n")
    return paths


def _build_from_blocks(
    fips: str, assignment_csv: Path, cache: Path, work: Path, interval: str,
    blocks_url: str, state_cb_url: str,
) -> Path:
    """One state's districts dissolved from its 2020 blocks, clipped to the
    cartographic shoreline and simplified to the national threshold."""
    outline = work / f"state_{fips}.shp"
    if not outline.exists():
        zipfile.ZipFile(download(state_cb_url, cache)).extractall(work)
        _mapshaper(
            [
                str(work / state_cb_url.rsplit("/", 1)[1].replace(".zip", ".shp")),
                "-filter",
                f'STATEFP === "{fips}"',
                "-o",
                str(outline),
            ]
        )
    dissolved = work / f"dissolved_{fips}.shp"
    print(f"  {fips}: dissolving blocks")
    log = _mapshaper(
        [
            # mapshaper reads the shapefile straight out of the zip.
            str(download(blocks_url, cache)),
            "-join",
            str(assignment_csv),
            "keys=GEOID20,GEOID",
            "string-fields=GEOID,CDFP",
            # Same exclusions as the cartographic path: delegate seats
            # and unassigned water are not House districts on a ballot.
            "-filter",
            'CDFP !== "98" && CDFP !== "ZZ"',
            "-dissolve",
            "CDFP",
            "-o",
            str(dissolved),
        ]
    )
    joined = re.search(
        r"Join: ([\d,]+)/([\d,]+) targets matched, ([\d,]+)/([\d,]+) sources used", log
    )
    if (
        not joined
        or joined.group(1) != joined.group(2)
        or joined.group(3) != joined.group(4)
    ):
        raise RuntimeError(
            f"{fips}: blocks and block-file rows do not match one-to-one:\n{log}"
        )
    # A separate pass on purpose: in memory the dissolved layer still
    # carries every block edge in its arc table, and simplifying against
    # that drops small coastal islands (Hatteras) that a fresh read keeps.
    out_path = work / f"blocks_{fips}.json"
    _mapshaper(
        [
            str(dissolved),
            "-clip",
            str(outline),
            # TIGER's land borders and the 1:500k outline disagree by metres
            # all along the state line; without this the edge becomes a
            # fringe of slivers that bloats the file (Texas: 25KB -> 16KB)
            # and fragments the outline into hundreds of tiny arcs.
            "remove-slivers",
            "-each",
            f'district = +CDFP, st = "{fips}"',
            "-filter-fields",
            "district,st",
            "-sort",
            "district",
            "-simplify",
            f"interval={interval}",
            "keep-shapes",
            "-o",
            str(out_path),
            *_TOPOJSON_OUT,
        ]
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build per-state district TopoJSON")
    ap.add_argument("--congress", type=int, required=True, help="whose lines (the Congress the election seats)")
    ap.add_argument("--cache", type=Path, help="keep downloads here (default: a temp dir)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    fips = _fips_to_state()
    expected = _expected_districts(args.congress)
    cycles = census_lines.maps_cycles()
    if not census_lines.listed(cycles, args.congress):
        raise SystemExit(f"the election that seats the {ordinal(args.congress)} Congress is not listed yet")
    by_state = census_lines.state_maps(args.congress, fips.values(), cycles)
    maps = {f: by_state[st] for f, st in fips.items()}
    # The simplification is measured on the newest national cartographic
    # file of a Congress up to N, and the block-built coasts are clipped to
    # the state outline of the same vintage.
    base = next(((c, u) for c in range(args.congress, 0, -1)
                 if (u := census_lines.cartographic_cd_url(c))), None)
    if base is None:
        raise SystemExit("no cartographic boundary file of any Congress's districts")
    vintage = base[1].split("/GENZ", 1)[1][:4]
    state_cb_url = f"{census_lines.TIGER}/GENZ{vintage}/shp/cb_{vintage}_us_state_500k.zip"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        cache = args.cache or work / "downloads"
        cache.mkdir(parents=True, exist_ok=True)

        on = {m: {f for f, x in maps.items() if x == m} for m in set(maps.values())}
        sources, interval = _build_cartographic(base[1], base[0], on.get(base[0], set()), cache, work)
        print(f"{ordinal(base[0])} Congress file simplified at {interval}")
        from_blocks = 0
        for m, members in sorted(on.items()):
            if m == base[0]:
                continue
            cart = census_lines.cartographic_cd_url(m)
            if cart:
                sources.update(_build_cartographic(cart, m, members, cache, work, interval)[0])
                continue
            found = census_lines.find_congress_bef(m)
            if found is None:
                raise SystemExit(f"no block equivalency file for the {ordinal(m)} Congress")
            csvs = _write_assignments(download(found[0], cache), work, members)
            for f in sorted(members):
                url = census_lines.blocks_url(m, f)
                sources[f] = _build_from_blocks(f, csvs[f], cache, work, interval, url, state_cb_url)
                from_blocks += 1
                if not args.cache:
                    # ~1-2GB per big state; don't hold every one at once.
                    (cache / url.rsplit("/", 1)[1]).unlink()

        problems: list[str] = []
        written: dict[str, bytes] = {}
        for f, path in sorted(sources.items()):
            state = fips.get(f)
            if not state:
                problems.append(f"no state code for FIPS {f}")
                continue
            written[state], found = validate_and_normalize(
                json.loads(path.read_text()), state, expected
            )
            problems += found

    if problems:
        print("REFUSING TO WRITE — validation failed:")
        for p in problems:
            print(f"  {p}")
        return 1

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    for state, blob in written.items():
        (args.out / f"{state}.json").write_bytes(blob)
    total = sum(len(b) for b in written.values())
    districts = sum(
        len(json.loads(b)["objects"]["districts"]["geometries"])
        for b in written.values()
    )
    print(
        f"wrote {len(written)} states ({from_blocks} from blocks), "
        f"{districts} districts, {total // 1024}KB to {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
