"""Build frontend/public/data/cd/<ST>.json — each state's congressional
district outlines, for the clickable district map on the state ballot page.

Civitas never asks a visitor for their address, so "which district is
mine" has to be answerable by pointing. Counties do most of that work
(county_district_crosswalk.json) because people know theirs, but 13% of
US counties span more than one district, and a dense city can hold ten
districts inside one county. A map of the districts themselves is the
only view that answers "which one is my neighborhood in" without asking.

SOURCE. U.S. Census Bureau cartographic boundary file for the 119th
Congress, 1:500,000 (cb_2024_us_cd119_500k). Public domain. Cartographic
boundary files are clipped to the shoreline, which is what a reader
recognises; TIGER/Line extends districts over water.

ONE FILE PER STATE, not one national file: a state page loads only its
own districts. The whole country is ~207KB at this simplification and the
largest single state (Alaska, all coastline) is ~20KB.

VALIDATED against county_district_crosswalk.json before anything is
written: every state must come out with exactly the district numbers the
crosswalk lists, and no district may be lost to simplification. A dense
urban district is the thing a 3% simplification is most likely to
collapse, and `keep-shapes` alone is a request, not a guarantee.

Needs Node (for `npx mapshaper`) and network access. Run from the repo
root:

    python backend/scripts/build_district_topology.py
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip"
SHAPEFILE = "cb_2024_us_cd119_500k.shp"
OUT_DIR = REPO / "frontend" / "public" / "data" / "cd"
STATE_CODES_TS = REPO / "frontend" / "src" / "lib" / "stateCodes.ts"
CROSSWALK = REPO / "backend" / "app" / "data" / "county_district_crosswalk.json"

# Enough to keep every district recognisable at state-page size while
# staying small; lower loses urban districts, higher only adds bytes.
SIMPLIFY = "3%"


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


def main() -> int:
    fips = _fips_to_state()
    expected = _expected_districts()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "cd.zip"
        print(f"downloading {SOURCE_URL}")
        urllib.request.urlretrieve(SOURCE_URL, archive)
        zipfile.ZipFile(archive).extractall(tmp_path)

        split_dir = tmp_path / "split"
        split_dir.mkdir()
        subprocess.run(
            [
                "npx",
                "-y",
                "mapshaper",
                str(tmp_path / SHAPEFILE),
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
                "-split",
                "st",
                # bbox: the page fits its own projection to it, so the
                # browser needs no geo library beyond react-simple-maps.
                "-o",
                f"{split_dir}/",
                "format=topojson",
                "singles",
                "quantization=10000",
                "bbox",
            ],
            check=True,
        )

        problems: list[str] = []
        written: dict[str, bytes] = {}
        for path in sorted(split_dir.glob("*.json")):
            state = fips.get(path.stem)
            if not state:
                problems.append(f"no state code for FIPS {path.stem}")
                continue
            topo = json.loads(path.read_text())
            layer = next(iter(topo["objects"]))
            geometries = topo["objects"][layer]["geometries"]
            got = {g["properties"]["district"] for g in geometries}
            lost = [
                g["properties"]["district"] for g in geometries if g.get("type") is None
            ]
            if got != expected.get(state, set()):
                problems.append(
                    f"{state}: {sorted(got)} != crosswalk {sorted(expected.get(state, set()))}"
                )
            if lost:
                problems.append(f"{state}: districts lost to simplification: {lost}")
            # One stable object key and nothing but the district number,
            # so the component reads the same shape for every state.
            if not topo.get("bbox"):
                problems.append(f"{state}: no bbox written")
            topo["objects"] = {"districts": topo["objects"][layer]}
            for g in topo["objects"]["districts"]["geometries"]:
                g["properties"] = {"district": g["properties"]["district"]}
            written[state] = json.dumps(topo, separators=(",", ":")).encode()

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
        f"wrote {len(written)} states, {districts} districts, {total // 1024}KB to {OUT_DIR}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
