"""Build backend/app/data/county_district_crosswalk.json — which counties
each U.S. House district covers, for the current election cycle's map.

Civitas never asks a visitor for their address (AGENTS.md §8), so a
reader who doesn't know their district number finds it by the county
they live in. This file is that lookup: "ST-N" -> the county names in the
district, a " (part)" suffix on any county that district shares with
another one. It is served by `app/api/elections.py:_district_counties`.

SOURCE. U.S. Census Bureau Redistricting Data Office block equivalency
files (BEFs): every 2020 Census block -> the congressional district it
lies in. A block's GEOID embeds its state and county FIPS, so joining the
BEF to the Bureau's county-name table gives the county list directly.

  * 119th Congress (`cd119.zip`): NationalCD119.txt plus corrected
    per-state files for Alabama, Georgia, Louisiana, New York and North
    Carolina, whose maps were redrawn after the national file was cut.
    The corrected files replace the national file's rows for those states.
  * 120th Congress (`cd120.zip`): NationalCD120.txt, which carries the
    2025-26 mid-decade redistricting.

WHICH MAP. Districts are not fixed for a decade: nine states redrew for
the 2026 elections. `CYCLE_MAPS` (read from
app/data/redrawn_congressional_maps.json) names, per state, which BEF that
state votes on this cycle and why. The 120th-Congress file is not simply "the
2026 map" — Missouri's CD120 rows are its 2025 map, which the U.S.
Supreme Court stayed on 2026-09-25, so Missouri votes on its 2022 (CD119)
lines in 2026. That is an explicit entry below, not a silent fallback.

Every state *not* listed must be identical block-for-block in both files;
the script refuses to write if one isn't, so a state that redraws later
cannot slip through on stale lines without someone deciding which map it
uses. Rerun this (and build_district_topology.py, which reads the same
choice) whenever a state's map changes.

"(part)". A county is tagged "(part)" when its 2020 Census blocks are
assigned to more than one district of that state (any block, populated
or not — the rule the bundled file has always used; regenerating the
119th-Congress file with it reproduces the earlier file exactly).
Delegate seats (district 98: DC and the territories) and unassigned
water blocks ("ZZ") are left out.

Run from the repo root (network required, ~80MB of downloads):

    python backend/scripts/build_county_district_crosswalk.py [--cache DIR]
    python backend/scripts/build_county_district_crosswalk.py --all-cd119 --out /tmp/x.json
        # the 119th-Congress file, for checking against an older build
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Runs from the repo root on a bare python3: app.contact is standard library only.
sys.path.insert(0, str(REPO / "backend"))
from app.contact import BOT_USER_AGENT  # noqa: E402
OUT_PATH = REPO / "backend" / "app" / "data" / "county_district_crosswalk.json"

_RDO = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files"
CD119_URL = f"{_RDO}/2025/119-congressional-district-befs/cd119.zip"
CD120_URL = f"{_RDO}/2027/120-congressional-district-befs/cd120.zip"
COUNTY_NAMES_URL = (
    "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"
)

# Names Civitas and its contact, as every fetch does (app/contact.py).
USER_AGENT = BOT_USER_AGENT

CD119 = "CD119"
CD120 = "CD120"
CYCLE = "2026"

MAPS_PATH = REPO / "backend" / "app" / "data" / "redrawn_congressional_maps.json"


def load_cycle_maps(cycle: str = CYCLE, path: Path = MAPS_PATH) -> dict[str, tuple[str, str]]:
    """The map each listed state votes on in `cycle`, with the reason —
    app/data/redrawn_congressional_maps.json, the one list the live-results
    sync also reads. States absent there must be identical in CD119 and
    CD120 (checked in `choose_assignment`)."""
    entries = json.loads(path.read_text())["cycles"].get(cycle, {})
    return {st: (e["map"], e["reason"]) for st, e in entries.items()}


CYCLE_MAPS: dict[str, tuple[str, str]] = load_cycle_maps()

# Congressional district codes that are not House seats on a state ballot.
_NOT_A_SEAT = {"98", "ZZ"}


# ---------------------------------------------------------------------------
# Pure functions (unit-tested with inline data)
# ---------------------------------------------------------------------------


def map_for_state(
    state: str, cycle_maps: Mapping[str, tuple[str, str]] = CYCLE_MAPS
) -> str:
    """The BEF a state's blocks come from this cycle (CD119 unless listed)."""
    return cycle_maps[state][0] if state in cycle_maps else CD119


def choose_assignment(
    cd119: Mapping[str, str],
    cd120: Iterable[tuple[str, str]],
    fips_to_state: Mapping[str, str],
    cycle_maps: Mapping[str, tuple[str, str]] = CYCLE_MAPS,
) -> tuple[dict[str, str], list[str]]:
    """Block GEOID -> CDFP for the cycle, plus a list of problems.

    `cd119` is the corrected 119th-Congress assignment; `cd120` streams
    (GEOID, CDFP) rows of the 120th. A state not in `cycle_maps` whose
    blocks disagree between the two is a problem: someone has to decide
    which map it votes on before this file can be written.
    """
    chosen = dict(cd119)
    seen120: set[str] = set()
    unlisted_diffs: dict[str, int] = defaultdict(int)
    for geoid, cd in cd120:
        state = fips_to_state.get(geoid[:2], geoid[:2])
        seen120.add(geoid)
        if map_for_state(state, cycle_maps) == CD120:
            chosen[geoid] = cd
        elif state not in cycle_maps and cd119.get(geoid) != cd:
            unlisted_diffs[state] += 1
    problems = [
        f"{st}: {n} blocks differ between CD119 and CD120 but {st} has no CYCLE_MAPS entry"
        for st, n in sorted(unlisted_diffs.items())
    ]
    # A CD120 state's blocks all come from CD120; one it lacks would keep
    # a stale CD119 district.
    stale = defaultdict(int)
    for geoid in cd119:
        state = fips_to_state.get(geoid[:2], geoid[:2])
        if map_for_state(state, cycle_maps) == CD120 and geoid not in seen120:
            stale[state] += 1
    problems += [
        f"{st}: {n} blocks missing from CD120" for st, n in sorted(stale.items())
    ]
    # An unlisted state's blocks must all be in CD120 too: one missing
    # there says the two files disagree about that state, listed or not.
    unlisted_missing = defaultdict(int)
    for geoid in cd119:
        state = fips_to_state.get(geoid[:2], geoid[:2])
        if state not in cycle_maps and geoid not in seen120:
            unlisted_missing[state] += 1
    problems += [
        f"{st}: {n} blocks in CD119 missing from CD120 but {st} has no CYCLE_MAPS entry"
        for st, n in sorted(unlisted_missing.items())
    ]
    # A state listed as CD120 whose lines didn't change is a stale entry:
    # it would tell the live-results sync its seats have no holder.
    present: set[str] = set()
    changed: set[str] = set()
    for g, cd in chosen.items():
        st = fips_to_state.get(g[:2], g[:2])
        present.add(st)
        if cd119.get(g) != cd:
            changed.add(st)
    listed_same = [
        st for st, (bef, _) in cycle_maps.items() if bef == CD120 and st in present and st not in changed
    ]
    problems += [f"{st}: listed as CD120 but its lines are identical to CD119" for st in sorted(listed_same)]
    return chosen, problems


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


def source_note(
    cycle_maps: Mapping[str, tuple[str, str]], built: dt.date, all_cd119: bool
) -> str:
    base = (
        "U.S. Census Bureau Redistricting Data Office congressional district Block "
        "Equivalency Files (2020 Census blocks -> district) joined to each block's own "
        "county FIPS (embedded in its GEOID) and the Bureau's national_county2020.txt "
        "county-name table. 119th Congress: NationalCD119.txt with Alabama/Georgia/"
        "Louisiana/New York/North Carolina replaced by their post-redistricting corrected "
        f"files ({CD119_URL}). 120th Congress: NationalCD120.txt ({CD120_URL}). "
    )
    if all_cd119:
        plan = "Every state uses its 119th Congress map. "
    else:
        lines = [
            f"{st} {plan} ({reason})"
            for st, (plan, reason) in sorted(cycle_maps.items())
        ]
        plan = (
            f"Map per state for the {CYCLE} elections: " + "; ".join(lines) + ". "
            "Every other state's 119th and 120th Congress files are identical. "
        )
    return (
        base
        + plan
        + "A county tagged '(part)' has 2020 Census blocks in more than one district "
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
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    return dest


def _bef_rows(zf: zipfile.ZipFile, name: str) -> Iterable[tuple[str, str]]:
    with zf.open(name) as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
            yield row["GEOID"], row["CDFP"]


def load_cd119(archive: Path) -> dict[str, str]:
    """NationalCD119 with each corrected per-state file replacing that
    state's national rows wholesale."""
    with zipfile.ZipFile(archive) as zf:
        corrected = sorted(n for n in zf.namelist() if n.endswith("_CD119.txt"))
        replaced = {name[:2] for name in corrected}
        assignment = {
            geoid: cd
            for geoid, cd in _bef_rows(zf, "NationalCD119.txt")
            if geoid[:2] not in replaced
        }
        for name in corrected:
            rows = dict(_bef_rows(zf, name))
            assignment.update(rows)
            print(f"  {name}: {len(rows)} blocks replace the national file's rows")
    return assignment


def cd120_rows(archive: Path) -> Iterable[tuple[str, str]]:
    with zipfile.ZipFile(archive) as zf:
        yield from _bef_rows(zf, "NationalCD120.txt")


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


def build(cache: Path, all_cd119: bool) -> tuple[dict, list[str], dict[str, str]]:
    names, fips_to_state = load_county_names(download(COUNTY_NAMES_URL, cache))
    print("reading CD119")
    cd119 = load_cd119(download(CD119_URL, cache))
    cycle_maps = {} if all_cd119 else CYCLE_MAPS
    if all_cd119:
        assignment, problems = cd119, []
    else:
        print("reading CD120")
        assignment, problems = choose_assignment(
            cd119, cd120_rows(download(CD120_URL, cache)), fips_to_state, cycle_maps
        )
    districts = build_districts(county_districts(assignment), names, fips_to_state)
    doc = {
        "_source": source_note(cycle_maps, dt.date.today(), all_cd119),
        "districts": districts,
    }
    return doc, problems, assignment


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--cache", type=Path, help="keep downloads here (default: a temp dir)"
    )
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument(
        "--all-cd119", action="store_true", help="every state on its 119th Congress map"
    )
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        cache = args.cache or Path(tmp)
        cache.mkdir(parents=True, exist_ok=True)
        doc, problems, _ = build(cache, args.all_cd119)

    if problems:
        print("REFUSING TO WRITE:")
        for p in problems:
            print(f"  {p}")
        return 1
    args.out.write_text(json.dumps(doc, indent=2))
    print(f"wrote {len(doc['districts'])} districts to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
