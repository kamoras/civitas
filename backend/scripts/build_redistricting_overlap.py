"""Build backend/app/data/redistricting_overlap.json — for each state that
votes on new U.S. House lines this cycle, how many 2020 Census residents
moved from each old district into each new one.

It answers "is my district different this year, and how much?" with the
same Census join the district place and county files are built from, so
the three files can never describe two different maps:

  * the congressional district block equivalency files: every 2020 block
    -> its district on the 119th Congress's lines (cd119.zip, corrected
    per-state files replacing their national rows) and on the 120th's
    (NationalCD120.txt) — read exactly as
    build_district_place_crosswalk.py reads them;
  * the 2020 P.L. 94-171 geographic header: each block's population
    (fetch_state_leg_crosswalk.read_geo).

Which states: those `CYCLE_MAPS` (app/data/redrawn_congressional_maps.json)
puts on CD120 for the cycle. Missouri's CD120 rows are its stayed 2025 map,
so Missouri is not here: it votes on the lines its members were elected on.

Every populated block lands in one (old, new) cell, so each state's cells
sum to its 2020 population and nothing is rounded or filtered here. The
file also records the district files' sliver floor (`minResidents`,
fetch_state_leg_crosswalk._MIN_RESIDENTS): a block the line cuts through
is assigned whole to one side, so an overlap smaller than about one block
can be an artifact of that rather than people who moved. The API applies
it when it lists where residents came from; the counts stay exact.

Lean (Cook PVI) is not stored here: the API joins it at read time from the
pinned per-Congress tables (district_pvi_for_congress), so a pin advanced
in district_pvi_sources.json reaches the page without rebuilding this.

Run from the repo root (network on first run: the two district files,
~80MB, plus one P.L. file per redrawn state, 14-100MB, kept in --cache):

    python backend/scripts/build_redistricting_overlap.py [--cache DIR]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import sys
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_county_district_crosswalk as cw  # noqa: E402
import build_district_place_crosswalk as places  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402

OUT_PATH = REPO / "backend" / "app" / "data" / "redistricting_overlap.json"


def redrawn(cycle_maps: Mapping[str, tuple[str, str]] = cw.CYCLE_MAPS) -> list[str]:
    """The states voting on their 120th Congress lines this cycle."""
    return sorted(st for st, (plan, _) in cycle_maps.items() if plan == cw.CD120)


def overlap(
    population: Mapping[str, int],
    old: Iterable[tuple[str, str]],
    new: Iterable[tuple[str, str]],
    state: str,
) -> tuple[list[list], int]:
    """(cells, unmatched): [old "ST-N", new "ST-N", residents] for every
    pair of districts sharing a populated block, largest first, and the
    residents of populated blocks that lack a district on either map (a
    delegate seat or water code, or a block missing from one file — the
    caller refuses to write when this is not zero)."""
    old_of = {g: places.cd_key(cd) for g, cd in old if g in population}
    cells: collections.Counter = collections.Counter()
    unmatched = 0
    seen: set[str] = set()
    for geoid, cd in new:
        pop = population.get(geoid)
        if pop is None:
            continue
        seen.add(geoid)
        before, after = old_of.get(geoid), places.cd_key(cd)
        if before is None or after is None:
            unmatched += pop
            continue
        cells[f"{state}-{before}", f"{state}-{after}"] += pop
    unmatched += sum(pop for g, pop in population.items() if g not in seen)
    rows = [[o, n, pop] for (o, n), pop in cells.items()]
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows, unmatched


def block_population(state: str, cache: Path) -> dict[str, int]:
    """Block GEOID -> 2020 residents, populated blocks only."""
    _fips, folder = sl._STATES[state]
    pl = sl.download(sl._PL.format(folder=folder, st=state.lower()), cache)
    with zipfile.ZipFile(pl) as zf, zf.open(f"{state.lower()}geo2020.pl") as raw:
        blocks, _names = sl.read_geo(io.TextIOWrapper(raw, encoding="latin-1"), strong_mcd=False)
    return {g: pop for g, (pop, _units) in blocks.items()}


def build_state(state: str, cache: Path) -> list[list]:
    fips = sl._FIPS[state]
    population = block_population(state, cache)
    cells, unmatched = overlap(
        population,
        places.cd119_rows(cw.download(cw.CD119_URL, cache), state, fips),
        places.cd120_rows(cw.download(cw.CD120_URL, cache), fips),
        state,
    )
    if unmatched:
        raise SystemExit(f"{state}: {unmatched} residents in blocks without a district on both maps")
    print(f"   {state}: {sum(c[2] for c in cells):,} residents in {len(cells)} old/new overlaps")
    return cells


def source_note(states: list[str], built: dt.date) -> str:
    return (
        "U.S. Census Bureau: the Redistricting Data Office's congressional district Block "
        f"Equivalency Files ({cw.CD119_URL}, NationalCD119.txt with the corrected per-state "
        f"files replacing their rows; {cw.CD120_URL}, NationalCD120.txt), joined on the 2020 "
        "Census block to the 2020 P.L. 94-171 block population. Each cell counts the 2020 "
        "residents of the blocks a 119th Congress district and a 120th Congress district "
        f"share, for the states voting on new lines in {cw.CYCLE} ({', '.join(states)}; "
        "app/data/redrawn_congressional_maps.json). Generated by "
        f"backend/scripts/build_redistricting_overlap.py -- built {built.isoformat()}."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-census")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)

    states = redrawn()
    doc = {
        "_source": source_note(states, dt.date.today()),
        "cycle": cw.CYCLE,
        "fromCongress": cw.SITTING_CONGRESS,
        "toCongress": cw.congress_seated_by(cw.CYCLE),
        "minResidents": sl._MIN_RESIDENTS,
        "states": {st: build_state(st, args.cache) for st in states},
    }
    # One cell per line: readable in a diff, a tenth the size of indent=1.
    args.out.write_text(json.dumps(doc, separators=(",", ":")).replace("],[", "],\n[") + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
