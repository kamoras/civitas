"""Build app/data/state_leg_district_crosswalk.json — which places and
counties each state legislative district covers.

This is the state-legislative analog of county_district_crosswalk.json,
and it exists for the same reason: Civitas never asks a visitor for their
address (AGENTS.md core design principle 8), so "which of these 75
districts is mine" has to be answerable from something a person already
knows. For a U.S. House district that is the county. State legislative
districts are far smaller than a county — Rhode Island fits 75 lower-
chamber seats into 5 of them — so the useful unit is the town, city or
community, with the county listed too.

SOURCE. Two Census Bureau files, joined on the 2020 Census block:

  * The Redistricting Data Office's 2026 state legislative Block
    Equivalency Files (sldu26.zip / sldl26.zip, August 2026): every 2020
    block -> the upper and lower chamber district it lies in, on the lines
    drawn for the 2026 elections. The national files are corrected by
    per-state files for the states that redrew after they were cut
    (Michigan's senate, Minnesota and Mississippi); a corrected file
    replaces the national file's rows for that state and chamber.
  * The 2020 P.L. 94-171 redistricting file's geographic header, one per
    state: every block's population and the county, county subdivision
    and place (incorporated place or Census Designated Place) it lies in,
    plus those units' names. Place and county subdivision names are
    taken from the 2025 Gazetteer files where the code still exists, so
    a CDP that has incorporated since 2020 carries its new name. A place
    or town the Gazetteer has and the 2020 file doesn't (a merger, a new
    city, a CDP given a new code) is drawn from TIGERweb's current
    boundaries, and the blocks whose internal points fall inside it are
    moved into it (`reassign`).

Until August 2026 no block file carried the current districts (the 2020
Block Assignment Files have the 2011-12 lines), and this script sampled a
grid of points inside each town against TIGERweb's district polygons
instead. The block join replaces that: it is exact, it weighs an overlap
by the people living in it rather than by its area, and it needs no
geometry. It reproduces the grid method's verified Rhode Island answers
(Jamestown -> 74, Barrington -> 66 and 67, Cranston -> 9 districts,
Providence -> all 14).

WHICH PLACES. A district lists, in this order:

  * In the "strong MCD" states (Census's own term), the county
    subdivisions it covers. There they are real units of government that
    people live in and vote in: Rhode Island has 39 towns and only 8
    incorporated places. Everywhere else county subdivisions are
    statistical inventions ("Fairburn-Union City CCD") and are not used.
  * Every place it covers: incorporated places ("Takoma Park city") AND
    Census Designated Places, the Bureau's name for a recognised community
    with no municipal government. Incorporated places alone missed where
    most suburban Americans live — Silver Spring, Bethesda, The Woodlands
    and Hawaii's every town are CDPs. A CDP's name is written without the
    " CDP" tag ("Silver Spring"): it is Census jargon nobody types. A
    place whose name is already listed as a county subdivision of the
    district (a borough that is both; a New England village sharing its
    town's name) is not repeated.
  * Every county it covers ("Montgomery County"), named as
    county_district_crosswalk.json names them, so a search by county finds
    every district in that county rather than only the few that happen to
    contain no named place.

Towns and places are ordered by how many of the district's people live
in them, so the first few names (all the page shows before "& N more")
are the district's largest communities.

Run from the repo root (network required: ~90MB of district files plus
20-100MB per state, cached in --cache):

    python backend/scripts/fetch_state_leg_crosswalk.py RI
    python backend/scripts/fetch_state_leg_crosswalk.py RI VT NH   # several
    python backend/scripts/fetch_state_leg_crosswalk.py --existing  # every state already in the file
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Runs from the repo root on a bare python3: app.contact is standard library only.
sys.path.insert(0, str(REPO / "backend"))
from app.contact import BOT_USER_AGENT  # noqa: E402

OUTPUT = REPO / "backend" / "app" / "data" / "state_leg_district_crosswalk.json"

_BEF = (
    "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2027/"
    "2026-state-legislative-bef/{chamber}26.zip"
)
_CHAMBER_FILES = {"upper": "sldu", "lower": "sldl"}
_PL = (
    "https://www2.census.gov/programs-surveys/decennial/2020/data/"
    "01-Redistricting_File--PL_94-171/{folder}/{st}2020.pl.zip"
)

# Today's names for the 2020 codes (UTF-8, unlike the P.L. file, which
# writes "Utqiagvik" for Utqiaġvik).
_GAZETTEER = {
    kind: "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/"
    f"2025_Gaz_{file}_national.zip"
    for kind, file in (("place", "place"), ("cousub", "cousubs"))
}

# Current boundaries, for the places and towns created since 2020 (see
# reassign): incorporated places are layer 4, CDPs layer 5, county
# subdivisions layer 1.
_TIGERWEB = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/Places_CouSub_ConCity_SubMCD/MapServer/{layer}/query"
)
_TIGERWEB_LAYERS = {"place": (4, 5), "cousub": (1,)}

_STRONG_MCD_STATES = {
    "CT", "ME", "MA", "MI", "MN", "NH", "NJ", "NY", "PA", "RI", "VT", "WI",
}

# Census fills the gaps between real towns — open water, mostly — with a
# placeholder county subdivision carrying this name. Its blocks are
# almost all unpopulated, but a lighthouse keeper's would list a town
# nobody lives in, so it is excluded by name.
_NOT_A_TOWN = "County subdivisions not defined"

# The P.L. 94-171 geographic header's columns (2020 technical
# documentation, Chapter 6), 0-based. Pipe-delimited, no header row.
_SUMLEV, _GEOCODE, _COUNTY, _COUSUB, _PLACE, _BASENAME, _NAME, _POP = 2, 9, 14, 17, 29, 86, 87, 90
_LAT, _LON = 92, 93  # the block's internal point
_BLOCK, _COUNTY_ROW, _COUSUB_ROW, _PLACE_ROW = "750", "050", "060", "160"
_NO_PLACE = "99999"

# When an overlap is too small to list. Block assignment is exact, so
# every populated overlap is real people; the floor only guards the one
# inexactness a block file has — a block the district line cuts through
# is assigned whole to one side (the BEF's BlockSplits list), so a
# block's worth of residents can land in the wrong district. A populated
# 2020 block holds a median of 26-49 people (Georgia, Texas, Rhode
# Island, Maryland), so 50 is about one block.
#
# An absolute count rather than a share, deliberately. A share of the
# place hid 265 Fayetteville residents in the neighbouring Arkansas
# senate district (0.3% of the city), and a share of the district can't
# work where districts are huge: 0.5% of a Texas senate seat is 4,700
# people. Measured on the 2026 files for RI, MD, TX, AR, CA and GA, the
# total listed moves under 2% anywhere from 1 to 250 residents, and Rhode
# Island's town lists equal the grid method's at every value. Over-
# listing costs a reader one extra name; under-listing hides the race
# they vote in.
_MIN_RESIDENTS = 50

_STATES = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"), "DC": ("11", "District_of_Columbia"),
    "FL": ("12", "Florida"),
    "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"), "ID": ("16", "Idaho"),
    "IL": ("17", "Illinois"), "IN": ("18", "Indiana"), "IA": ("19", "Iowa"),
    "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"), "LA": ("22", "Louisiana"),
    "ME": ("23", "Maine"), "MD": ("24", "Maryland"), "MA": ("25", "Massachusetts"),
    "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"), "MS": ("28", "Mississippi"),
    "MO": ("29", "Missouri"), "MT": ("30", "Montana"), "NE": ("31", "Nebraska"),
    "NV": ("32", "Nevada"), "NH": ("33", "New_Hampshire"), "NJ": ("34", "New_Jersey"),
    "NM": ("35", "New_Mexico"), "NY": ("36", "New_York"), "NC": ("37", "North_Carolina"),
    "ND": ("38", "North_Dakota"), "OH": ("39", "Ohio"), "OK": ("40", "Oklahoma"),
    "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"), "RI": ("44", "Rhode_Island"),
    "SC": ("45", "South_Carolina"), "SD": ("46", "South_Dakota"), "TN": ("47", "Tennessee"),
    "TX": ("48", "Texas"), "UT": ("49", "Utah"), "VT": ("50", "Vermont"),
    "VA": ("51", "Virginia"), "WA": ("53", "Washington"), "WV": ("54", "West_Virginia"),
    "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"),
}
# Abbreviation -> FIPS, also read by scripts/fetch_ces_approval.py (one state table).
_FIPS = {state: fips for state, (fips, _folder) in _STATES.items()}


# ---------------------------------------------------------------------------
# Pure functions (unit-tested with a trimmed real fixture)
# ---------------------------------------------------------------------------


def district_key(code: str) -> str | None:
    """A BEF district code as parse_state_leg_office reads the same
    district off a ballot label: leading zeros dropped ("007" -> "7"), a
    trailing letter kept ("01A" -> "1A", Minnesota's and Maryland's
    subdistricts), and a district named by one letter kept as that letter
    ("00A" -> "A", Alaska's senate). A code in any other shape ("ZZZ",
    unassigned water; Massachusetts's "D01" and Vermont's "ADD", whose
    ballots name districts by place) belongs to no seat a contest label
    identifies this way and is skipped rather than guessed at."""
    code = code.strip().upper().lstrip("0")
    return code if re.fullmatch(r"\d+[A-Z]?|[A-Z]", code) else None


def read_geo(
    lines: Iterable[str], strong_mcd: bool, points: dict | None = None
) -> tuple[dict, dict]:
    """(blocks, names) from a P.L. 94-171 geographic header.

    blocks: 15-digit block GEOID -> (population, unit ids) for populated
    blocks only — an empty block can't put anyone in a district. A unit
    id is ("county", code), ("cousub", code) or ("place", code).
    names: unit id -> (display name, base name).
    points, when given, is filled with each populated block's internal
    point as (longitude, latitude).
    """
    blocks: dict[str, tuple[int, tuple]] = {}
    names: dict[tuple[str, str], tuple[str, str]] = {}
    for line in lines:
        f = line.rstrip("\r\n").split("|")
        level = f[_SUMLEV]
        if level == _BLOCK:
            pop = int(f[_POP] or 0)
            if not pop:
                continue
            units = [("county", f[_COUNTY])]
            if strong_mcd:
                units.append(("cousub", f[_COUSUB]))
            if f[_PLACE] != _NO_PLACE:
                units.append(("place", f[_PLACE]))
            blocks[f[_GEOCODE]] = (pop, tuple(units))
            if points is not None:
                points[f[_GEOCODE]] = (float(f[_LON]), float(f[_LAT]))
        elif level == _COUNTY_ROW:
            names[("county", f[_COUNTY])] = (f[_NAME], f[_BASENAME])
        elif level == _COUSUB_ROW and strong_mcd:
            names[("cousub", f[_COUSUB])] = (f[_NAME], f[_BASENAME])
        elif level == _PLACE_ROW:
            names[("place", f[_PLACE])] = (f[_NAME], f[_BASENAME])
    return blocks, names


def current_names(lines: Iterable[str], state_fips: str, kind: str) -> dict[tuple[str, str], str]:
    """Unit id -> its name today, from a Census Gazetteer file ("place"
    or "cousub"). The blocks are 2020's, and so are the P.L. file's names,
    but a place keeps its code when it incorporates or changes its legal
    type, so a CDP that became a city since ("Mountain House CDP" ->
    "Mountain House city") is named as it is now. Both codes are the
    GEOID's last five digits."""
    out = {}
    for line in lines:
        f = line.rstrip("\r\n").split("|")
        if f[1].startswith(state_fips):
            out[(kind, f[1][-5:])] = f[4]
    return out


def inside(rings: list, x: float, y: float) -> bool:
    """Even-odd ray cast: holes and multi-part polygons need no special
    case, since every ring's crossings count."""
    hit = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            (xi, yi), (xj, yj) = ring[i], ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
    return hit


def reassign(blocks: dict, points: Mapping[str, tuple[float, float]], polygons: Mapping[tuple, list]) -> int:
    """Move each block whose internal point lies in one of `polygons`
    (unit id -> rings) into that unit, in place of the 2020 unit of the
    same kind. For the places and towns created since 2020, which the
    2020 block file can't name: a merger (Cahokia Heights), a CDP that
    took a new code, a town incorporated as a village. Returns the number
    of blocks moved."""
    boxes = []
    for unit, rings in polygons.items():
        xs = [p[0] for ring in rings for p in ring]
        ys = [p[1] for ring in rings for p in ring]
        boxes.append((min(xs), min(ys), max(xs), max(ys), unit, rings))
    moved = 0
    for geoid, (x, y) in points.items():
        # A new city in a strong-MCD state is usually a new place AND a
        # new county subdivision at once, so every kind is tried.
        hits = {unit[0]: unit for x0, y0, x1, y1, unit, rings in boxes
                if x0 <= x <= x1 and y0 <= y <= y1 and inside(rings, x, y)}
        if hits:
            pop, units = blocks[geoid]
            blocks[geoid] = (pop, tuple(u for u in units if u[0] not in hits) + tuple(hits.values()))
            moved += 1
    return moved


def crosswalk(
    blocks: Mapping[str, tuple[int, tuple]],
    names: Mapping[tuple[str, str], tuple[str, str]],
    assignment: Iterable[tuple[str, str]],
) -> dict[str, list[str]]:
    """District key -> its names: county subdivisions, then places, then
    counties, each group by population inside the district, largest
    first. `assignment` streams (block GEOID, BEF district code)."""
    overlap: collections.Counter = collections.Counter()
    unit_pop: collections.Counter = collections.Counter()
    for geoid, code in assignment:
        district = district_key(code)
        block = blocks.get(geoid)
        if district is None or block is None:
            continue
        pop, units = block
        for unit in units:
            overlap[district, unit] += pop
            unit_pop[unit] += pop

    kept: dict[str, list[tuple[int, tuple]]] = collections.defaultdict(list)
    for (district, unit), pop in overlap.items():
        # A place smaller than twice the floor is listed where at least
        # half its people live, so no hamlet is left off every district.
        if pop >= min(_MIN_RESIDENTS, unit_pop[unit] / 2):
            kept[district].append((pop, unit))

    order = {"cousub": 0, "place": 1, "county": 2}
    out: dict[str, list[str]] = {}
    for district, units in kept.items():
        units.sort(key=lambda pu: (order[pu[1][0]], -pu[0], names[pu[1]][0]))
        town_bases = {names[u][1] for _, u in units if u[0] == "cousub"}
        listed: list[str] = []
        for _, unit in units:
            name, base = names[unit]
            name = name.removesuffix(" CDP")
            if name == _NOT_A_TOWN or name in listed:
                continue
            if unit[0] == "place" and base in town_bases:
                continue
            listed.append(name)
        out[district] = listed
    return out


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def download(url: str, cache: Path) -> Path:
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists():
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
        part = dest.with_suffix(".part")
        # www2.census.gov resets long transfers now and then; the 100MB
        # state files need a second try often enough to build it in.
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=300) as resp, open(part, "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                break
            except OSError:
                if attempt == 2:
                    raise
                print("   connection dropped, retrying")
        part.rename(dest)
    return dest


def _query(url: str, params: dict) -> dict:
    """POST (ring lists run far past any URL length). ArcGIS reports a
    failed query as HTTP 200 with an {"error": ...} body, so that raises
    rather than reading as "nothing there"."""
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(params).encode(), headers={"User-Agent": BOT_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read())
    if "error" in payload:
        raise RuntimeError(f"TIGERweb query failed: {payload['error']}")
    return payload


def new_unit_polygons(state_fips: str, units: Iterable[tuple[str, str]]) -> dict[tuple, list]:
    """Current TIGERweb boundaries (longitude/latitude) of the given
    place / county subdivision ids, a batch per layer."""
    out: dict[tuple, list] = {}
    for kind, layers in _TIGERWEB_LAYERS.items():
        codes = sorted(code for k, code in units if k == kind)
        for start in range(0, len(codes), 50):
            ids = ",".join(f"'{state_fips}{c}'" if kind == "place" else f"'{c}'" for c in codes[start:start + 50])
            for layer in layers:
                field = "GEOID" if kind == "place" else "COUSUB"
                where = f"{field} IN ({ids})" + ("" if kind == "place" else f" AND STATE='{state_fips}'")
                payload = _query(_TIGERWEB.format(layer=layer), {
                    "where": where, "outFields": field, "returnGeometry": "true",
                    "outSR": "4326", "geometryPrecision": "6", "f": "json",
                })
                for feature in payload.get("features") or []:
                    rings = (feature.get("geometry") or {}).get("rings")
                    if rings:
                        out[(kind, feature["attributes"][field][-5:])] = rings
    return out


def bef_rows(zip_path: Path, state: str, fips: str) -> Iterator[tuple[str, str]]:
    """(GEOID, district code) for one state's blocks: from the state's
    corrected file when the archive has one ("27_MN_SLDU26.txt"), else
    from the national file, which is sorted by GEOID so reading stops
    once the state is passed."""
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        corrected = [m for m in members if m.startswith(f"{fips}_{state}_")]
        member = corrected[0] if corrected else next(m for m in members if m.startswith("National"))
        with zf.open(member) as raw:
            reader = io.TextIOWrapper(raw, encoding="utf-8-sig")
            next(reader)
            seen = False
            for line in reader:
                if not line.startswith(fips):
                    if seen:
                        return
                    continue
                seen = True
                parts = line.rstrip("\r\n").split(",")
                yield parts[0], parts[5]


def build_state(state: str, cache: Path) -> dict[str, list[str]]:
    fips, folder = _STATES[state]
    pl = download(_PL.format(folder=folder, st=state.lower()), cache)
    strong_mcd = state in _STRONG_MCD_STATES
    points: dict[str, tuple[float, float]] = {}
    with zipfile.ZipFile(pl) as zf, zf.open(f"{state.lower()}geo2020.pl") as raw:
        blocks, names = read_geo(io.TextIOWrapper(raw, encoding="latin-1"), strong_mcd, points)
    created: dict[tuple, str] = {}
    for kind, url in _GAZETTEER.items():
        if kind == "cousub" and not strong_mcd:
            continue
        with zipfile.ZipFile(download(url, cache)) as zf, zf.open(zf.namelist()[0]) as raw:
            for unit, name in current_names(io.TextIOWrapper(raw, encoding="utf-8"), fips, kind).items():
                if unit in names:
                    names[unit] = (name, names[unit][1])
                else:
                    created[unit] = name
    if created:
        polygons = new_unit_polygons(fips, created)
        for unit in polygons:
            # The base name only matters for matching a place against a
            # town of the same name; the legal type is the last word.
            names[unit] = (created[unit], created[unit].rsplit(" ", 1)[0])
        moved = reassign(blocks, points, polygons)
        print(f"   {state}: {len(created)} place(s)/town(s) since 2020, "
              f"{len(polygons)} found on TIGERweb, {moved} blocks moved into them")
    del points
    out: dict[str, list[str]] = {}
    for chamber, prefix in _CHAMBER_FILES.items():
        bef = download(_BEF.format(chamber=prefix), cache)
        seats = crosswalk(blocks, names, bef_rows(bef, state, fips))
        print(f"   {state} {chamber}: {len(seats)} districts")
        out.update({f"{state}-{chamber}-{number}": listed for number, listed in seats.items()})
    if not out:
        raise SystemExit(f"{state}: no districts — refusing to write an empty crosswalk")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("states", nargs="*")
    parser.add_argument("--existing", action="store_true", help="rebuild every state already in the file")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-census")
    args = parser.parse_args(argv)

    existing = json.loads(OUTPUT.read_text()).get("districts", {}) if OUTPUT.exists() else {}
    states = [s.upper() for s in args.states]
    if args.existing:
        states += sorted({key.split("-")[0] for key in existing})
    if not states:
        parser.print_help()
        return 2
    unknown = [s for s in states if s not in _STATES]
    if unknown:
        print(f"unknown state(s): {', '.join(unknown)}")
        return 1
    args.cache.mkdir(parents=True, exist_ok=True)

    districts_out = dict(existing)
    for state in dict.fromkeys(states):
        built = build_state(state, args.cache)
        # Rebuild this state from scratch so a district that disappeared
        # in a remap doesn't survive as a stale entry.
        districts_out = {k: v for k, v in districts_out.items() if not k.startswith(f"{state}-")}
        districts_out.update(built)

    OUTPUT.write_text(json.dumps({
        "_source": (
            "U.S. Census Bureau: the Redistricting Data Office's 2026 state legislative "
            "Block Equivalency Files (sldu26.zip / sldl26.zip, August 2026, national files "
            "with the corrected per-state files replacing their rows), joined on the 2020 "
            "Census block to the 2020 P.L. 94-171 geographic header (block population, "
            "county, county subdivision and place, with their names). A district lists the "
            "county subdivisions (strong-MCD states only), places (incorporated places and "
            f"Census Designated Places) and counties with at least {_MIN_RESIDENTS} 2020 "
            "residents inside the district (or half the residents of a smaller place), "
            "named as in the Census Bureau's 2025 Gazetteer files. Generated by "
            "backend/scripts/fetch_state_leg_crosswalk.py."
        ),
        "districts": dict(sorted(districts_out.items())),
    }, indent=1) + "\n")
    print(f"wrote {OUTPUT} ({len(districts_out)} districts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
