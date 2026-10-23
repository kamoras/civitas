"""Build the district shapes a Congress the site needs is missing, and the
manifest that says what a set of shapes holds (app/district_shapes.py).

    build_district_shapes.py --out MANIFEST --cache DIR --congress S --congress E --cycle C
    build_district_shapes.py --describe frontend/public/data --congress E --out app/data/district_shapes.json

The first form (the data refresh's) builds, into a new directory under the
data volume's district_shapes/, every lookup folder the Congresses use
(build_district_lookup.py: one per map, plus the newest state legislative
lines for cycle C) and the ballot map's outlines on E's lines
(build_district_topology.py), then writes the manifest naming that
directory. Builds the manifest in use doesn't name are removed first, so
the volume holds the build in use and the one being made.

The second form describes the shapes bundled with the frontend (written to
app/data/district_shapes.json, the fallback every reader starts from):
which states each lookup folder holds, and the map each state's outline is
on (E's, by rule, which is what build_district_topology.py drew).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app import census_lines  # noqa: E402
from app.atomic_write import runtime_data_path  # noqa: E402
from app.file_cache import read_json_preferring  # noqa: E402

FILE = "district_shapes.json"
ROOT_NAME = "district_shapes"


def describe(data: Path, congress: int, build: str | None) -> dict:
    """The manifest of the shapes under `data` (lookup/<folder>/<ST>.json,
    cd/<ST>.json, the outlines on `congress`'s lines)."""
    lookup = {}
    for folder in sorted(p for p in (data / "lookup").iterdir() if p.is_dir()):
        states = sorted({f.name.split(".")[0] for f in folder.glob("*.json")})
        lookup[folder.name] = states
    outlines = sorted(f.stem for f in (data / "cd").glob("*.json"))
    maps = census_lines.state_maps(congress, outlines, census_lines.maps_cycles())
    return {
        "_source": ("District shapes: lookup/<folder>/<ST>.json by backend/scripts/build_district_lookup.py "
                    "(one folder per map, named by the Congress whose block equivalency file drew it; "
                    "election-<cycle> the state legislative lines), cd/<ST>.json by "
                    "build_district_topology.py. Described by backend/scripts/build_district_shapes.py."),
        "dir": build,
        "lookup": lookup,
        "cd": {"congress": congress, "maps": maps},
    }


def _run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--congress", type=int, action="append", required=True)
    ap.add_argument("--cycle", type=int)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--describe", type=Path, help="describe the shapes under this directory; build nothing")
    args = ap.parse_args(argv)
    election = max(args.congress)
    if args.describe:
        args.out.write_text(json.dumps(describe(args.describe, election, None), indent=1) + "\n")
        return 0
    if args.cycle is None or args.cache is None:
        ap.error("a build needs --cycle and --cache")
    root = Path(runtime_data_path(ROOT_NAME))
    in_use = read_json_preferring(runtime_data_path(FILE), default={}) or {}
    root.mkdir(parents=True, exist_ok=True)
    for old in root.iterdir():
        if old.name != in_use.get("dir"):
            shutil.rmtree(old, ignore_errors=True)
    build = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    work = root / build
    py = sys.executable
    scripts = BACKEND / "scripts"
    lookup = [py, str(scripts / "build_district_lookup.py"), "--cycle", str(args.cycle), "--cache", str(args.cache),
              "--out", str(work / "lookup")]
    for c in sorted(set(args.congress)):
        lookup += ["--congress", str(c)]
    try:
        _run(lookup)
        _run([py, str(scripts / "build_district_topology.py"), "--congress", str(election),
              "--cache", str(args.cache), "--out", str(work / "cd")])
    except subprocess.CalledProcessError:
        shutil.rmtree(work, ignore_errors=True)
        raise
    args.out.write_text(json.dumps(describe(work, election, build), indent=1) + "\n")
    print(f"built {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
