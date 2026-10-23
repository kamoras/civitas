"""Build backend/app/data/congressional_district_places.json — the cities,
towns and communities in each U.S. House district.

county_district_crosswalk.json lets a reader find their House district by
county, but about one county in eight is split between districts, and the
name a voter reaches for first is their city or town, not their county
(AGENTS.md §8: Civitas never asks where they live, so the filter has to
work from a place name they already know). This file is the place list
the House district filters match against, one map per Congress given
(`--congress`, which app/data_refresh.py passes by rule: the sitting
Congress and the one the coming election seats), keyed by that Congress.
Each state's lines for a Congress are the map it votes on in the
election that seats it (redrawn_congressional_maps.json, the copy in use,
via app.census_lines), so Missouri's stayed map and every state that kept
its map are chosen exactly as build_county_district_crosswalk.py chooses
them. The state ballot pages read the election's; finding who represents
a reader now reads the sitting Congress's.

SOURCE. The same join as scripts/fetch_state_leg_crosswalk.py, which
reads the state legislative districts this way, with the congressional
block equivalency files in place of the state legislative ones:

  * Census Redistricting Data Office congressional district BEFs: every
    2020 block -> its district, the zip of each map's Congress found by
    rule (census_lines.find_congress_bef), a state's own file in place of
    its national rows.
  * The 2020 P.L. 94-171 geographic header: each block's population and
    the place (incorporated place or Census Designated Place) and, in the
    strong-MCD states, the town or township it lies in, named as in the
    newest Gazetteer (or --gazetteer-year), with places created since 2020 drawn from TIGERweb
    (fetch_state_leg_crosswalk.state_units).

A district lists every town (strong-MCD states only) and place with at
least 50 of its 2020 residents inside the district, or half the residents
of a smaller place — the state legislative file's floor, for the reason
given there (a block the district line cuts through is assigned whole to
one side). Towns first, then places, each largest first. Counties are
left out: county_district_crosswalk.json has them, "(part)" tags and all.
A place listed under two districts is split between them; the filter
says so.

    python backend/scripts/build_district_place_crosswalk.py --congress 119 --congress 120 \\
        --out PATH --cache DIR [--gazetteer-year YEAR] [--keep-downloads]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_county_district_crosswalk as cw  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402

from app import census_lines  # noqa: E402
from app.ordinals import ordinal  # noqa: E402

OUT_PATH = REPO / "backend" / "app" / "data" / "congressional_district_places.json"


def cd_key(code: str) -> str | None:
    """A BEF district code as the county file keys it: "05" -> "5", an
    at-large "00" -> "0"; delegate seats and unassigned water are no
    House seat on a state ballot."""
    return None if code in cw._NOT_A_SEAT else str(int(code))


def places_only(blocks: dict) -> dict:
    """The blocks with their county dropped: counties come from the county
    file, which also says which ones are split."""
    return {g: (pop, tuple(u for u in units if u[0] != "county")) for g, (pop, units) in blocks.items()}


def build_state(state: str, cache: Path, maps: dict[int, dict[str, int]], befs: dict[int, Path],
                gazetteer: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """{Congress: {"ST-N": places}} for one state on each Congress in
    `maps` (Congress -> state -> the Congress whose BEF holds its lines)."""
    fips = sl._FIPS[state]
    by_map: dict[int, dict] = {}
    needed = sorted({maps[c][state] for c in maps})
    # Each map on the blocks of the census it is drawn on (a new census's
    # maps on its own blocks, census_lines.census_year).
    for census in sorted({census_lines.census_year(m) for m in needed}):
        blocks, names = sl.state_units(state, cache, gazetteer, census)
        blocks = places_only(blocks)
        for m in (m for m in needed if census_lines.census_year(m) == census):
            by_map[m] = sl.crosswalk(blocks, names, census_lines.bef_rows(befs[m], fips, state), key=cd_key)
    print(f"   {state}: " + ", ".join(f"{ordinal(m)} map {len(seats)} districts" for m, seats in by_map.items()))
    return {str(c): {f"{state}-{n}": listed for n, listed in by_map[maps[c][state]].items()} for c in maps}


def source_note(maps: dict[int, dict[str, int]], urls: dict[int, str], gazetteer: dict[str, str],
                built: dt.date) -> str:
    per = []
    for c, by_state in sorted(maps.items()):
        moved = [f"{st} {ordinal(m)}" for st, m in sorted(by_state.items()) if m != min(by_state.values())]
        per.append(f"'{c}': the {ordinal(c)} Congress's lines (elected {census_lines.elected_in(c)})"
                   + (f", each state on its map that election: {', '.join(moved)}, every other state on the "
                      f"{ordinal(min(by_state.values()))}'s" if moved else f", every state on the {ordinal(min(by_state.values()))} "
                      "Congress's map"))
    return (
        "U.S. Census Bureau: the Redistricting Data Office's congressional district Block "
        "Equivalency Files (" + "; ".join(urls[m] for m in sorted(urls)) + ", each state's own corrected "
        "file replacing its national rows), joined on the 2020 Census block to the 2020 P.L. 94-171 "
        "geographic header (block population, place, and county subdivision in the strong-MCD "
        "states). " + "; ".join(per) + " (redrawn_congressional_maps.json). A district lists the county "
        "subdivisions (strong-MCD states only) and places (incorporated places and Census Designated "
        f"Places) with at least {sl._MIN_RESIDENTS} 2020 residents inside it (or half the residents of a "
        "smaller place), largest first, named as in the Census Bureau's Gazetteer files "
        f"({gazetteer['place'].rsplit('/', 1)[1]}). Generated by "
        f"backend/scripts/build_district_place_crosswalk.py -- built {built.isoformat()}."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--congress", type=int, action="append", required=True, help="whose lines (repeatable)")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--gazetteer-year", type=int, help="names from this year's Gazetteer (default: the newest)")
    ap.add_argument("--keep-downloads", action="store_true", help="keep each state's P.L. file in --cache")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)

    cycles = census_lines.maps_cycles()
    unlisted = [c for c in args.congress if not census_lines.listed(cycles, c)]
    if unlisted:
        raise SystemExit(f"the elections that seat {unlisted} are not in redrawn_congressional_maps.json yet")
    # DC's delegate seat is no district on a ballot (cd_key).
    states = [s for s in sl._STATES if s != "DC"]
    maps = {c: census_lines.state_maps(c, states, cycles) for c in sorted(set(args.congress))}
    urls, befs, inputs = {}, {}, {}
    for m in sorted({m for by_state in maps.values() for m in by_state.values()}):
        found = census_lines.find_congress_bef(m)
        if found is None:
            raise SystemExit(f"no Census block equivalency file for the {ordinal(m)} Congress")
        urls[m] = found[0]
        inputs[str(m)] = list(found)
        befs[m] = sl.download(found[0], args.cache)
    gazetteer = sl.gazetteer_urls(args.gazetteer_year)
    congresses: dict[str, dict] = {str(c): {} for c in maps}
    for state in states:
        built = build_state(state, args.cache, maps, befs, gazetteer)
        if not all(built.values()):
            raise SystemExit(f"{state}: no districts — refusing to write")
        for c, seats in built.items():
            congresses[c].update(seats)
        if not args.keep_downloads:
            sl._drop_state_download(state, args.cache)
    congresses = {c: dict(sorted(t.items())) for c, t in congresses.items()}
    args.out.write_text(json.dumps({
        "_source": source_note(maps, urls, gazetteer, dt.date.today()),
        # The files read: the data refresh rebuilds on a re-release or a newer Gazetteer.
        "_inputs": {"bef": inputs, "gazetteer": gazetteer},
        "congresses": congresses,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}: " + ", ".join(f"{c}: {len(v)} districts" for c, v in congresses.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
