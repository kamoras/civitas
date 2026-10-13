"""Build backend/app/data/congressional_district_places.json — the cities,
towns and communities in each U.S. House district.

county_district_crosswalk.json lets a reader find their House district by
county, but about one county in eight is split between districts, and the
name a voter reaches for first is their city or town, not their county
(AGENTS.md §8: Civitas never asks where they live, so the filter has to
work from a place name they already know). This file is the place list
the House district filters match against, on two maps:

  * "120": the lines each state votes on in the 2026 elections, chosen
    exactly as build_county_district_crosswalk.py chooses them
    (`CYCLE_MAPS`: the 120th Congress's lines in the states that redrew,
    the 119th's elsewhere, Missouri's stayed 2025 map included). The
    state ballot pages read it.
  * "119": the 119th Congress's lines, the districts today's members were
    elected on, for finding who represents a reader now.

SOURCE. The same join as scripts/fetch_state_leg_crosswalk.py, which
reads the state legislative districts this way, with the congressional
block equivalency files in place of the state legislative ones:

  * Census Redistricting Data Office congressional district BEFs: every
    2020 block -> its district (cd119.zip with its corrected per-state
    files; NationalCD120.txt).
  * The 2020 P.L. 94-171 geographic header: each block's population and
    the place (incorporated place or Census Designated Place) and, in the
    strong-MCD states, the town or township it lies in, named as in the
    2025 Gazetteer, with places created since 2020 drawn from TIGERweb
    (fetch_state_leg_crosswalk.state_units).

A district lists every town (strong-MCD states only) and place with at
least 50 of its 2020 residents inside the district, or half the residents
of a smaller place — the state legislative file's floor, for the reason
given there (a block the district line cuts through is assigned whole to
one side). Towns first, then places, each largest first. Counties are
left out: county_district_crosswalk.json has them, "(part)" tags and all.
A place listed under two districts is split between them; the filter
says so.

Run from the repo root (network required: the two district files, ~80MB,
plus 4-100MB per state, cached in --cache):

    python backend/scripts/build_district_place_crosswalk.py [--cache DIR] [ST ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_county_district_crosswalk as cw  # noqa: E402
import fetch_state_leg_crosswalk as sl  # noqa: E402

OUT_PATH = REPO / "backend" / "app" / "data" / "congressional_district_places.json"

# The bundled county file's at-large seats are "ST-0", and so are these.
SITTING, ELECTION = "119", "120"


def cd_key(code: str) -> str | None:
    """A BEF district code as the county file keys it: "05" -> "5", an
    at-large "00" -> "0"; delegate seats and unassigned water are no
    House seat on a state ballot."""
    return None if code in cw._NOT_A_SEAT else str(int(code))


def places_only(blocks: dict) -> dict:
    """The blocks with their county dropped: counties come from the county
    file, which also says which ones are split."""
    return {g: (pop, tuple(u for u in units if u[0] != "county")) for g, (pop, units) in blocks.items()}


def _rows(zf: zipfile.ZipFile, member: str, fips: str) -> Iterator[tuple[str, str]]:
    """(GEOID, CDFP) for one state's blocks. The national files are not
    sorted by state, so the whole file is read."""
    with zf.open(member) as raw:
        reader = io.TextIOWrapper(raw, encoding="utf-8-sig")
        header = next(reader).rstrip("\r\n").split(",")
        cd = header.index("CDFP")
        for line in reader:
            if line.startswith(fips):
                parts = line.rstrip("\r\n").split(",")
                yield parts[0], parts[cd]


def cd119_rows(archive: Path, state: str, fips: str) -> Iterator[tuple[str, str]]:
    """The 119th Congress, a corrected per-state file replacing the national
    file's rows for its state (cw.load_cd119's rule)."""
    with zipfile.ZipFile(archive) as zf:
        corrected = [n for n in zf.namelist() if n == f"{fips}_{state}_CD119.txt"]
        yield from _rows(zf, corrected[0] if corrected else "NationalCD119.txt", fips)


def cd120_rows(archive: Path, fips: str) -> Iterator[tuple[str, str]]:
    with zipfile.ZipFile(archive) as zf:
        yield from _rows(zf, "NationalCD120.txt", fips)


def build_state(state: str, cache: Path) -> dict[str, dict[str, list[str]]]:
    fips = sl._FIPS[state]
    blocks, names = sl.state_units(state, cache)
    blocks = places_only(blocks)
    cd119 = cw.download(cw.CD119_URL, cache)
    sitting = sl.crosswalk(blocks, names, cd119_rows(cd119, state, fips), key=cd_key)
    if cw.map_for_state(state) == cw.CD120:
        election = sl.crosswalk(blocks, names, cd120_rows(cw.download(cw.CD120_URL, cache), fips), key=cd_key)
    else:
        election = sitting
    print(f"   {state}: {len(sitting)} sitting / {len(election)} election districts")
    return {
        congress: {f"{state}-{n}": listed for n, listed in seats.items()}
        for congress, seats in ((SITTING, sitting), (ELECTION, election))
    }


def source_note(built: dt.date) -> str:
    plans = "; ".join(f"{st} {plan} ({reason})" for st, (plan, reason) in sorted(cw.CYCLE_MAPS.items()))
    return (
        "U.S. Census Bureau: the Redistricting Data Office's congressional district Block "
        f"Equivalency Files ({cw.CD119_URL}, NationalCD119.txt with the corrected per-state "
        f"files replacing their rows; {cw.CD120_URL}, NationalCD120.txt), joined on the 2020 "
        "Census block to the 2020 P.L. 94-171 geographic header (block population, place, and "
        "county subdivision in the strong-MCD states). '119': the 119th Congress's lines. "
        f"'120': the lines each state votes on in the {cw.CYCLE} elections: {plans}; every other "
        "state on its 119th Congress map. A district lists the county subdivisions (strong-MCD "
        "states only) and places (incorporated places and Census Designated Places) with at "
        f"least {sl._MIN_RESIDENTS} 2020 residents inside it (or half the residents of a smaller "
        "place), largest first, named as in the Census Bureau's 2025 Gazetteer files. Generated "
        f"by backend/scripts/build_district_place_crosswalk.py -- built {built.isoformat()}."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("states", nargs="*", help="rebuild only these (default: every state)")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-census")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)

    # DC's delegate seat is no district on a ballot (cd_key).
    states = [s.upper() for s in args.states] or [s for s in sl._STATES if s != "DC"]
    doc = json.loads(args.out.read_text()) if args.states and args.out.exists() else {"congresses": {}}
    congresses = {c: doc["congresses"].get(c, {}) for c in (SITTING, ELECTION)}
    for state in states:
        built = build_state(state, args.cache)
        if not built[SITTING]:
            raise SystemExit(f"{state}: no districts — refusing to write")
        for congress, seats in built.items():
            # A state rebuilt from scratch, so a district a remap removed goes.
            kept = {k: v for k, v in congresses[congress].items() if not k.startswith(f"{state}-")}
            congresses[congress] = dict(sorted({**kept, **seats}.items()))

    args.out.write_text(json.dumps({
        "_source": source_note(dt.date.today()),
        "congresses": congresses,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}: " + ", ".join(f"{c}: {len(v)} districts" for c, v in congresses.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
