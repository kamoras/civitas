"""Build frontend/public/data/cd/<ST>.json — each state's congressional
district outlines, for the clickable district map on the state ballot page.

Civitas never asks a visitor for their address, so "which district is
mine" has to be answerable by pointing. Counties do most of that work
(county_district_crosswalk.json) because people know theirs, but 13% of
US counties span more than one district, and a dense city can hold ten
districts inside one county. A map of the districts themselves is the
only view that answers "which one is my neighborhood in" without asking.

WHICH MAP. District lines change whenever a state redistricts, not once
a decade: nine states redrew for the 2026 elections. The per-state choice
lives in one place, app/data/redrawn_congressional_maps.json (read by
build_county_district_crosswalk.py as `CYCLE_MAPS`),
and this script follows it, so the map and the county list can never
describe different districts. Rerun both scripts when a state's map
changes.

SOURCES. Public domain, U.S. Census Bureau.
  * States on their 119th Congress map: the cartographic boundary file
    for the 119th Congress, 1:500,000 (cb_2024_us_cd119_500k). Cartographic
    boundary files are clipped to the shoreline, which is what a reader
    recognises; TIGER/Line extends districts over water.
  * States voting on their 120th Congress map (CYCLE_MAPS): no cartographic
    file exists yet, so each district is built from its blocks — the
    Redistricting Data Office's CD120 block equivalency file joined to
    TIGER/Line 2020 blocks (tl_2020_<FIPS>_tabblock20), dissolved by
    district, then clipped to the state's cb_2024_us_state_500k outline so
    the coast matches the cartographic style of every other state.

SIMPLIFICATION. The national 119th file is simplified to 3% of its
vertices, as it always has been; mapshaper reports the distance threshold
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

Needs Node (for `npx mapshaper`) and network access; the block files are
large (Texas alone is ~750MB), so pass --cache to keep them between runs.
Run from the repo root:

    python backend/scripts/build_district_topology.py [--cache DIR]
"""

from __future__ import annotations

import argparse
import csv
import io
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
from build_county_district_crosswalk import (  # noqa: E402
    CD120,
    CD120_URL,
    download,
    map_for_state,
)

REPO = Path(__file__).resolve().parents[2]
CD119_CB_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip"
)
CD119_CB_SHAPEFILE = "cb_2024_us_cd119_500k.shp"
STATE_CB_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
)
STATE_CB_SHAPEFILE = "cb_2024_us_state_500k.shp"
BLOCKS_URL = "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{fips}_tabblock20.zip"
OUT_DIR = REPO / "frontend" / "public" / "data" / "cd"
STATE_CODES_TS = REPO / "frontend" / "src" / "lib" / "stateCodes.ts"
CROSSWALK = REPO / "backend" / "app" / "data" / "county_district_crosswalk.json"

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
    # The frontend's own table, so the file names cannot drift from the
    # codes the map component looks them up by.
    return dict(re.findall(r'"(\d{2})":\s*"([A-Z]{2})"', STATE_CODES_TS.read_text()))


def _expected_districts() -> dict[str, set[int]]:
    expected: dict[str, set[int]] = {}
    for key in json.loads(CROSSWALK.read_text())["districts"]:
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
        ["npx", "-y", "mapshaper", *args],
        check=True,
        capture_output=True,
        text=True,
        env=_NODE_ENV,
    )
    return result.stdout + result.stderr


def _build_cd119(cache: Path, work: Path) -> tuple[dict[str, Path], str]:
    """Every state from the 119th Congress cartographic file, plus the
    distance threshold its 3% simplification came to."""
    zipfile.ZipFile(download(CD119_CB_URL, cache)).extractall(work)
    split_dir = work / "split"
    split_dir.mkdir()
    out = _mapshaper(
        [
            str(work / CD119_CB_SHAPEFILE),
            # 98 = non-voting delegates (DC and the territories), which
            # have no House race on a state ballot page.
            "-filter",
            'CD119FP !== "98" && CD119FP !== "ZZ"',
            "-each",
            "district = +CD119FP, st = STATEFP",
            "-filter-fields",
            "district,st",
            "-simplify",
            SIMPLIFY,
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
    return {p.stem: p for p in split_dir.glob("*.json")}, parse_threshold(out)


def _write_cd120_assignments(
    cache: Path, work: Path, fips_list: set[str]
) -> dict[str, Path]:
    """Per-state GEOID,CDFP CSVs from NationalCD120.txt."""
    paths = {fips: work / f"cd120_{fips}.csv" for fips in fips_list}
    handles = {fips: open(path, "w", newline="") for fips, path in paths.items()}
    try:
        for fh in handles.values():
            fh.write("GEOID,CDFP\n")
        with (
            zipfile.ZipFile(download(CD120_URL, cache)) as zf,
            zf.open("NationalCD120.txt") as raw,
        ):
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                fh = handles.get(row["STATEFP"])
                if fh:
                    fh.write(f"{row['GEOID']},{row['CDFP']}\n")
    finally:
        for fh in handles.values():
            fh.close()
    return paths


def _build_from_blocks(
    fips: str, assignment_csv: Path, cache: Path, work: Path, interval: str
) -> Path:
    """One state's districts dissolved from its 2020 blocks, clipped to the
    cartographic shoreline and simplified to the national threshold."""
    outline = work / f"state_{fips}.shp"
    if not outline.exists():
        zipfile.ZipFile(download(STATE_CB_URL, cache)).extractall(work)
        _mapshaper(
            [
                str(work / STATE_CB_SHAPEFILE),
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
            str(download(BLOCKS_URL.format(fips=fips), cache)),
            "-join",
            str(assignment_csv),
            "keys=GEOID20,GEOID",
            "string-fields=GEOID,CDFP",
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
            f"{fips}: blocks and CD120 rows do not match one-to-one:\n{log}"
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-state district TopoJSON")
    ap.add_argument(
        "--cache", type=Path, help="keep downloads here (default: a temp dir)"
    )
    args = ap.parse_args()

    fips = _fips_to_state()
    expected = _expected_districts()
    redrawn = {f for f, st in fips.items() if map_for_state(st) == CD120}

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        cache = args.cache or work / "downloads"
        cache.mkdir(parents=True, exist_ok=True)

        sources, interval = _build_cd119(cache, work)
        print(f"119th Congress file simplified at {interval}")
        csvs = _write_cd120_assignments(cache, work, redrawn)
        for f in sorted(redrawn):
            sources[f] = _build_from_blocks(f, csvs[f], cache, work, interval)
            if not args.cache:
                # ~1-2GB per big state; don't hold every one at once.
                (cache / BLOCKS_URL.format(fips=f).rsplit("/", 1)[1]).unlink()

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

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    for state, blob in written.items():
        (OUT_DIR / f"{state}.json").write_bytes(blob)
    total = sum(len(b) for b in written.values())
    districts = sum(
        len(json.loads(b)["objects"]["districts"]["geometries"])
        for b in written.values()
    )
    print(
        f"wrote {len(written)} states ({len(redrawn)} from 120th Congress blocks), "
        f"{districts} districts, {total // 1024}KB to {OUT_DIR}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
