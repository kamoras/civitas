"""Build the county crosswalks: which counties each U.S. House district
covers, on the lines of one Congress.

Civitas never asks a visitor for their address (AGENTS.md §8), so a
reader who doesn't know their district number finds it by the county
they live in. Each file is that lookup: "ST-N" -> the county names in the
district, a " (part)" suffix on any county that district shares with
another one. Served by `app/api/elections.py:_district_counties` and
/politicians (app/district_places.counties_on_lines_of).

SOURCE. U.S. Census Bureau Redistricting Data Office block equivalency
files (BEFs): every 2020 Census block -> the congressional district it
lies in, one zip per Congress, found by rule (app.census_lines). A
block's GEOID embeds its state and county FIPS, so joining the BEF to the
Bureau's county-name table gives the county list directly. A state's own
file in a zip (a plan filed after the national file was cut, as the
119th's for Alabama, Georgia, Louisiana, New York and North Carolina)
replaces its national rows.

WHICH LINES. `--congress N` builds N's lines: each state's from the BEF
of the map it votes on in the election that seats N
(redrawn_congressional_maps.json, the copy in use; a state never listed
is on its 119th Congress map). Missouri's CD120 rows were its 2025 map
until the 2026-10-07 re-release, and the 2026 entry keeps it on CD119
either way: the listing decides, never the newest file.

Checks (any failure prints why and writes nothing): N's election is
listed (an unreviewed cycle gets no table, as district_lines carries
none into it); where N's own BEF is published, a state on an earlier map
whose blocks differ there must be listed for that election (a stay, as
Missouri's), and a state listed as on N's map must differ from the map it
replaced.

"(part)". A county is tagged "(part)" when its 2020 Census blocks are
assigned to more than one district of that state (any block, populated
or not). Delegate seats (district 98: DC and the territories) and
unassigned water blocks ("ZZ") are left out.

TWO FILES, TWO QUESTIONS. county_district_crosswalk.json holds the lines
of the Congress the coming election seats ("which district do I vote
in"); county_district_crosswalk_sitting.json the sitting Congress's ("who
represents me today": in a redrawn state the district of the same number
is a different district). Each states its `congress`; the reader picks by
Congress and carries a table forward for the states whose map didn't
change (district_lines.table_for_lines). app/data_refresh.py rebuilds
both when a Congress the site needs has a state no table covers.

    python backend/scripts/build_county_district_crosswalk.py --congress N --out PATH [--cache DIR]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import tempfile
import urllib.request
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Runs from the repo root on a bare python3: app.contact is standard library only.
sys.path.insert(0, str(REPO / "backend"))
from app.contact import BOT_USER_AGENT  # noqa: E402
from app import census_lines  # noqa: E402
from app.ordinals import ordinal  # noqa: E402


# Congressional district codes that are not House seats on a state ballot.
_NOT_A_SEAT = {"98", "ZZ"}


# ---------------------------------------------------------------------------
# Pure functions (unit-tested with inline data)
# ---------------------------------------------------------------------------


def check_maps(
    congress: int,
    maps: Mapping[str, int],
    digests: Mapping[int, Mapping[str, tuple[int, int]]],
    listed_now: Iterable[str],
    previous: Mapping[str, int],
) -> list[str]:
    """Problems with building `congress`'s lines from `maps` (state -> the
    Congress whose BEF it is on). `digests`: BEF Congress -> state ->
    (digest, blocks) for every file read; `listed_now`: the states the maps
    file names for the election that seats `congress`; `previous`: each
    state's map in the election before."""
    listed_now = set(listed_now)
    anywhere = {st for d in digests.values() for st in d}  # not the territories
    problems = [f"{st}: no blocks in the {ordinal(m)} Congress's file" for st, m in sorted(maps.items())
                if st in anywhere and st not in digests.get(m, {})]
    own = digests.get(congress)
    if own is not None:
        for st, m in sorted(maps.items()):
            if m != congress and st in own and own[st] != digests.get(m, {}).get(st) and st not in listed_now:
                problems.append(f"{st}: its blocks differ between the {ordinal(m)} and {ordinal(congress)} Congress files "
                                f"but the maps file doesn't list it for the election that seats the {ordinal(congress)}")
            if m == congress and st in listed_now and own.get(st) == digests.get(previous.get(st), {}).get(st):
                problems.append(f"{st}: listed as on the {ordinal(congress)} Congress's map but its lines are identical "
                                f"to the {ordinal(previous.get(st))}'s")
    return problems


def county_districts(assignment: Mapping[str, str]) -> dict[str, dict[str, set[str]]]:
    """County FIPS (5 digits) -> the House districts (CDFP) its blocks
    fall in. Delegate seats and unassigned water blocks are not districts."""
    by_county: dict[str, set[str]] = defaultdict(set)
    for geoid, cd in assignment.items():
        if cd in _NOT_A_SEAT:
            continue
        by_county[geoid[:5]].add(cd)
    return by_county


def build_districts(
    by_county: Mapping[str, set[str]],
    county_names: Mapping[str, str],
    fips_to_state: Mapping[str, str],
) -> dict[str, list[str]]:
    """ "ST-N" -> sorted county names, " (part)" on a county split between
    districts. At-large seats (CDFP 00) are "ST-0"."""
    districts: dict[str, list[str]] = defaultdict(list)
    for county, cds in by_county.items():
        state = fips_to_state[county[:2]]
        name = county_names[county]
        label = f"{name} (part)" if len(cds) > 1 else name
        for cd in cds:
            districts[f"{state}-{int(cd)}"].append(label)
    # Plain string order ("CA-1", "CA-10", "CA-11", ...), as bundled.
    return {k: sorted(v) for k, v in sorted(districts.items())}


def source_note(congress: int, maps: Mapping[str, int], urls: Mapping[int, str],
                reasons: Mapping[str, str], built: dt.date) -> str:
    counts: dict[int, int] = defaultdict(int)
    for m in maps.values():
        counts[m] += 1
    files = "; ".join(f"{ordinal(m)} Congress: {urls[m]}" for m in sorted(urls))
    redrawn = "; ".join(f"{st} on the {ordinal(m)} Congress's map ({reasons[st]})" for st, m in sorted(maps.items())
                        if st in reasons)
    return (
        "U.S. Census Bureau Redistricting Data Office congressional district Block "
        "Equivalency Files (2020 Census blocks -> district), each state's own corrected file "
        "replacing its national rows, joined to each block's own county FIPS (embedded in its "
        f"GEOID) and the Bureau's national county-name table of the census the lines are drawn on. Files: {files}. "
        f"The lines of the {ordinal(congress)} Congress (elected {census_lines.elected_in(congress)}): "
        f"each state on the map it votes on in that election (redrawn_congressional_maps.json)"
        + (f": {redrawn}" if redrawn else "") + "; every other state on its earlier map. "
        "A county tagged '(part)' has 2020 Census blocks in more than one district "
        "within that state. Generated by backend/scripts/build_county_district_crosswalk.py "
        f"-- built {built.isoformat()}."
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def download(url: str, cache: Path) -> Path:
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists():
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    return dest


def load_county_names(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(county FIPS -> name, state FIPS -> postal code). The table is
    UTF-8 (the 2026-08 hand build read it as Latin-1 and bundled
    "DoÃ±a Ana County")."""
    names: dict[str, str] = {}
    states: dict[str, str] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="|"):
            names[row["STATEFP"] + row["COUNTYFP"]] = row["COUNTYNAME"]
            states[row["STATEFP"]] = row["STATE"]
    return names, states


def read_map(path: Path, fips_to_state: Mapping[str, str], mine: set[str],
             by_county: dict[str, set[str]]) -> dict[str, tuple[int, int]]:
    """Stream one BEF: each state's (digest, blocks), and the districts of
    the counties in `mine` (the states on this map) added to `by_county`."""
    sums: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for geoid, cd in census_lines.bef_all_rows(path):
        st = fips_to_state.get(geoid[:2], geoid[:2])
        sums[st] = (sums[st] + census_lines.block_digest(geoid, cd)) % (1 << 64)
        counts[st] += 1
        if st in mine and cd not in _NOT_A_SEAT:
            by_county[geoid[:5]].add(cd)
    return {st: (sums[st], counts[st]) for st in sums}


def build(congress: int, cache: Path, cycles: dict | None = None) -> tuple[dict, list[str]]:
    # County names of the census the Congress's lines are drawn on.
    names, fips_to_state = load_county_names(
        download(census_lines.county_names_url(census_lines.census_year(congress)), cache))
    cycles = census_lines.maps_cycles() if cycles is None else cycles
    if not census_lines.listed(cycles, congress):
        return {}, [f"the election that seats the {ordinal(congress)} Congress ({census_lines.elected_in(congress)}) "
                    "is not in redrawn_congressional_maps.json yet"]
    year = census_lines.elected_in(congress)
    states = sorted(set(fips_to_state.values()))
    maps = census_lines.state_maps(congress, states, cycles)
    previous = census_lines.state_maps(congress - 1, states, cycles)
    by_county: dict[str, set[str]] = defaultdict(set)
    digests: dict[int, dict] = {}
    urls: dict[int, str] = {}
    inputs: dict[str, list[str]] = {}
    for m in sorted(set(maps.values()) | set(previous.values()) | {congress}):
        found = census_lines.find_congress_bef(m)
        if found is None:
            if m in maps.values():
                return {}, [f"no Census block equivalency file for the {ordinal(m)} Congress"]
            continue
        print(f"reading {found[0]}")
        mine = {st for st, x in maps.items() if x == m}
        digests[m] = read_map(download(found[0], cache), fips_to_state, mine, by_county if mine else {})
        if mine:
            urls[m] = found[0]
            inputs[str(m)] = list(found)
    entries = cycles.get(str(year), {})
    problems = check_maps(congress, maps, digests, entries, previous)
    reasons = {st: e["reason"] for st, e in entries.items() if maps.get(st) == congress}
    doc = {
        "_source": source_note(congress, maps, urls, reasons, dt.date.today()),
        # Whose lines these are; the reader picks by Congress.
        "congress": congress,
        # The files read (URL, published): the data refresh rebuilds on a re-release.
        "_inputs": {"bef": inputs},
        "districts": build_districts(by_county, names, fips_to_state),
    }
    return doc, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--congress", type=int, action="append", required=True, help="whose lines (repeatable)")
    ap.add_argument("--out", type=Path, action="append", required=True, help="one per --congress, in order")
    ap.add_argument("--cache", type=Path, help="keep downloads here (default: a temp dir)")
    args = ap.parse_args(argv)
    if len(args.congress) != len(args.out):
        ap.error("give one --out per --congress")
    with tempfile.TemporaryDirectory() as tmp:
        cache = args.cache or Path(tmp)
        cache.mkdir(parents=True, exist_ok=True)
        built = [build(c, cache) for c in args.congress]
    problems = [p for _, ps in built for p in ps]
    if problems:
        print("REFUSING TO WRITE:")
        for p in problems:
            print(f"  {p}")
        return 1
    for (doc, _), out in zip(built, args.out):
        out.write_text(json.dumps(doc, indent=2))
        print(f"wrote {len(doc['districts'])} districts ({ordinal(doc['congress'])} Congress) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
