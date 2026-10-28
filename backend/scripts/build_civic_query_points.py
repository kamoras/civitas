"""Build backend/app/data/civic_query_points.json: for every U.S. House
district on the lines an election seats, and every state legislative
district of that election, the internal points of its most populous Census
blocks, the points Google Civic's voterInfoQuery is asked about to read the
ballot of each district (app/pipeline/fetch/state_candidates_civic.py).

Why points, and why these: voterInfoQuery answers for the one precinct an
address sits in, and takes a "latitude,longitude" in place of an address
(measured 2026-10-28: Google answers with the nearest street address's
ballot; a city's name alone answers nothing). A populated block's internal
point is inside its district by the Census Bureau's own assignment (the
block equivalency file) and is where people live, so it usually has a
street address beside it (see RESIDENTIAL); several per district, because
not every point parses and one near a line can answer for the next district
(the adapter keeps the district Google names, never the one asked about). These
are Census geography, the same for every visitor: never a visitor's
location (AGENTS.md §8).

SOURCE. The 2020 P.L. 94-171 geographic header (each populated block's
population and internal point) and the Redistricting Data Office's block
equivalency files: congressional for the Congress the election seats, each
state on the map it votes on (census_lines.state_maps), and the state
legislative files for the election (census_lines.find_sld_bef), read with
the same functions as scripts/build_district_place_crosswalk.py and
scripts/fetch_state_leg_crosswalk.py.

    python backend/scripts/build_civic_query_points.py --congress 120 --cycle 2026 \\
        --out PATH --cache DIR [STATE ...]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_district_place_crosswalk as pc  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402
from app import census_lines  # noqa: E402

OUT_PATH = REPO / "backend" / "app" / "data" / "civic_query_points.json"
# Google answers "Failed to parse address" for many points: a point whose
# nearest result is not a street address (a large block, often an apartment
# complex, dorm or campus). Measured 2026-10-28 on seven Michigan State House
# districts: the 4 largest blocks' internal points answered 5 times in 28,
# blocks of 15 to 120 residents 27 in 56 (the number format made no
# difference). So a district's points are its ordinary residential blocks,
# spread evenly through them by population, 8 of them, then 8 more from all
# its populated blocks: with the 8 alone, 8 of Michigan's 148 legislative
# districts had none answer (failures cluster by area). The adapter stops at
# the first that does.
POINTS_PER_DISTRICT = 8
RESIDENTIAL = (15, 120)
# About a metre: enough to stay in the block, short enough to keep the file small.
DECIMALS = 5


def typical_blocks(rows, key, blocks: dict, points: dict, n: int = POINTS_PER_DISTRICT) -> dict[str, list]:
    """District key -> the [lat, lon] of `n` of its residential blocks,
    evenly spaced through them by population (any populated blocks where it
    has too few). `rows` streams (block GEOID, BEF code); `key` turns a code
    into the district's key (None for no seat)."""
    by_district: dict[str, list] = collections.defaultdict(list)
    for geoid, code in rows:
        district = key(code)
        block = blocks.get(geoid)
        if district is None or block is None or geoid not in points:
            continue
        by_district[district].append((block[0], geoid))
    def spread(pool: list, k: int) -> list:
        step = len(pool) / k if pool else 1
        return [pool[int(i * step)] for i in range(min(k, len(pool)))]

    out = {}
    for district, members in by_district.items():
        members.sort()
        # The residential blocks first; then, for a district whose every
        # residential point fails (failures cluster: an area Google can't
        # place), as many again from all its populated blocks.
        chosen = spread([m for m in members if RESIDENTIAL[0] <= m[0] <= RESIDENTIAL[1]], n)
        chosen += [m for m in spread(members, 2 * n) if m not in chosen][: 2 * n - len(chosen)]
        out[district] = [[round(points[g][1], DECIMALS), round(points[g][0], DECIMALS)] for _, g in chosen]
    return out


def build_state(state: str, cache: Path, cd_map: int, cd_bef: Path, sld: dict) -> dict[str, list]:
    fips = sl._FIPS[state]
    out: dict[str, list] = {}
    for census in sorted({census_lines.census_year(cd_map), census_lines.census_year(census_lines.seated_by(sld["upper"][2]))}):
        points: dict[str, tuple[float, float]] = {}
        with sl.open_geo(state, census, cache) as raw:
            blocks, _names = sl.read_geo(raw, state in sl._STRONG_MCD_STATES, points)
        if census_lines.census_year(cd_map) == census and state != "DC":
            for n, pts in typical_blocks(census_lines.bef_rows(cd_bef, fips, state), pc.cd_key, blocks, points).items():
                out[f"{state}-H-{n}"] = pts
        if census_lines.census_year(census_lines.seated_by(sld["upper"][2])) == census:
            for chamber, (url, _, _) in sld.items():
                rows = sl.bef_rows(sl.download(url, cache), state, fips)
                for n, pts in typical_blocks(rows, sl.district_key, blocks, points).items():
                    out[f"{state}-{chamber}-{n}"] = pts
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("states", nargs="*", help="the states to build (default: every state)")
    ap.add_argument("--congress", type=int, required=True, help="the Congress the election seats")
    ap.add_argument("--cycle", type=int, required=True, help="the election, for its state legislative lines")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--keep-downloads", action="store_true")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    # DC has no House district on a ballot and no state legislature.
    states = [s.upper() for s in args.states] or [s for s in sl._STATES if s != "DC"]
    maps = census_lines.state_maps(args.congress, states, census_lines.maps_cycles())
    befs: dict[int, Path] = {}
    inputs: dict[str, list] = {}
    for m in sorted(set(maps.values())):
        found = census_lines.find_congress_bef(m)
        if found is None:
            raise SystemExit(f"no Census block equivalency file for Congress {m}")
        befs[m], inputs[str(m)] = sl.download(found[0], args.cache), list(found)
    sld = sl.sld_befs(args.cycle)
    points: dict[str, list] = {}
    for state in states:
        built = build_state(state, args.cache, maps[state], befs[maps[state]], sld)
        if not built:
            raise SystemExit(f"{state}: no districts, refusing to write")
        print(f"   {state}: {len(built)} districts")
        points.update(built)
        if not args.keep_downloads:
            sl._drop_state_download(state, args.cache)
    args.out.write_text(json.dumps({
        "_source": (
            f"U.S. Census Bureau: the internal points of {POINTS_PER_DISTRICT} of each district's residential "
            f"2020 Census blocks ({RESIDENTIAL[0]} to {RESIDENTIAL[1]} residents, evenly spaced by population), "
            f"then up to {POINTS_PER_DISTRICT} more of its populated blocks, from the 2020 P.L. 94-171 "
            "geographic header, each block placed "
            "in its district by the Redistricting Data Office's block equivalency files: congressional "
            f"for the Congress the {args.cycle} election seats (each state on its map, "
            "redrawn_congressional_maps.json), state legislative for that election. The points Google "
            "Civic is asked about for each district's ballot. Generated by "
            f"backend/scripts/build_civic_query_points.py, {dt.date.today().isoformat()}."
        ),
        "congress": args.congress,
        "cycle": args.cycle,
        "_inputs": {"bef": inputs, "sldu": sld["upper"][:2], "sldl": sld["lower"][:2]},
        "points": dict(sorted(points.items())),
    }, separators=(",", ":")) + "\n")
    print(f"wrote {args.out} ({len(points)} districts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
