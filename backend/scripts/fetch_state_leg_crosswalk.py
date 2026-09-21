"""Build app/data/state_leg_district_crosswalk.json — which towns each
state legislative district covers.

This is the state-legislative analog of county_district_crosswalk.json,
and it exists for the same reason: Civitas never asks a visitor for their
address (AGENTS.md core design principle 8), so "which of these 75
districts is mine" has to be answerable from something a person already
knows. For a U.S. House district that is the county. State legislative
districts are far smaller than a county — Rhode Island fits 75 lower-
chamber seats into 5 of them — so the useful unit is the town or city.

WHY NOT THE OBVIOUS SOURCES. All three were probed live on 2026-09-21 and
all three fail, which is worth recording so nobody re-walks them:

  The results feed itself. A state legislative contest is a single
  reporting unit (Enhanced Voting returns reportingStatus 1/1 and an
  empty crossCounties for every one of Rhode Island's 133). There is no
  locality breakdown to read.

  Census Block Assignment Files. baf2020/ is ideal in shape — SLDL, SLDU
  and MCD keyed on the same block, joining to exactly 75 lower and 38
  upper districts for Rhode Island — but it was published in February
  2021, so its districts are the ones drawn in 2011-12, before nearly
  every state's 2021-22 redistricting. baf/ is older still (2010 blocks).
  Right method, wrong decade.

  A spatial query with esriSpatialRelIntersects. Hopelessly over-broad:
  a district whose boundary merely TOUCHES the town counts as
  intersecting it. Jamestown RI (~5,500 people, comfortably inside one
  ~14,000-person district) came back as 7 districts; Cranston came back
  as 16 against a true 9.

WHAT THIS DOES INSTEAD. Fetch the CURRENT district polygons and the town
polygons from TIGERweb, lay a grid over each town, and ask which district
contains each sample point that falls inside the town.

There is no sliver problem in that formulation, which is the whole point
of using it: legislative districts TILE the state with no gaps and no
overlaps, so every point inside the town is inside exactly one district
and every count is a real measure of the town-district overlap area. The
threshold below is therefore only suppressing grid quantisation, not
guessing at what counts as a real intersection.

VERIFIED against the independent block-assignment answer (baf2020, joined
MCD -> SLDL): for Rhode Island this reproduces Jamestown -> 74,
Barrington -> 66 and 67, Cranston -> 9 districts, and Providence -> all
14, exactly. Two methods with nothing in common agreeing to the district
is the reason to trust either.

Network required to regenerate; the output is bundled and static, the
same treatment county_district_crosswalk.json gets, because district
boundaries only move when a state redistricts.

    python scripts/fetch_state_leg_crosswalk.py RI
    python scripts/fetch_state_leg_crosswalk.py RI VT NH   # several
"""

import collections
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

# TIGERweb's Legislative service. Layers 1 and 2 are the CURRENT
# ("2026") upper and lower chamber districts — the vintage the block
# assignment files cannot give us.
_LEG_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/Legislative/MapServer/{layer}/query"
)
_CHAMBER_LAYERS = {"upper": 1, "lower": 2}

# County Subdivisions — towns and cities. This is the right unit in the
# states that actually govern by town (all of New England, plus the
# upper Midwest and mid-Atlantic). A state that organises around
# incorporated places instead should pass layer 4 of the same service;
# see _TOWN_LAYER's use below.
_TOWN_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/Places_CouSub_ConCity_SubMCD/MapServer/{layer}/query"
)
_TOWN_LAYER = 1

# Census fills the gaps between real towns — open water, mostly — with a
# placeholder "subdivision" carrying this name. It is a real polygon and
# really does overlap districts along a coast, so it survives every
# geometric test and has to be excluded by name. Rhode Island's Newport
# and Jamestown districts pick it up off Narragansett Bay, where it would
# have rendered as a town nobody lives in.
_NOT_A_TOWN = "County subdivisions not defined"

# TIGERweb answers in Web Mercator. Passing any other inSR silently
# returns ZERO features rather than erroring, which reads exactly like a
# state with no districts.
_SR = "102100"

# Samples per side of each town's bounding box. 260 puts ~42,000 points
# inside a city the size of Providence, which is enough resolution for
# its smallest real slice (district 14, 0.61% of the city) to land tens
# of points rather than one or two.
_GRID = 260

# Share of a town's interior samples a district needs to be listed for
# it. Deliberately far below the smallest real slice measured (0.61%):
# over-listing costs a reader one extra row to scan, while under-listing
# hides the race they actually vote in. Only grid quantisation is being
# filtered here, not genuine overlap.
_MIN_SHARE = 0.0025

_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}

_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / \
    "state_leg_district_crosswalk.json"


def _query(url: str, params: dict) -> dict:
    """POST, not GET: the ring lists fetched here run to thousands of
    points and are far past any practical URL length.

    Raises on an error payload. ArcGIS reports a failed query as HTTP
    200 with an {"error": ...} body, so a caller that just reads
    `features` sees an empty list and cannot tell a broken request from a
    state with nothing in it — which is exactly how Minnesota first came
    back as "0 towns" and wrote an empty crosswalk without complaining.
    """
    request = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read())
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(f"TIGERweb query failed: {payload['error']}")
    return payload


# Asking for every polygon at once fails outright above a few hundred:
# Minnesota has 2,762 county subdivisions and that request errors rather
# than truncating. Pages are requested explicitly instead, ordered so the
# offsets are stable.
_PAGE = 400


def _query_all(url: str, params: dict) -> list[dict]:
    """Every matching feature, paged. Stops when a page comes back short
    AND the service is no longer flagging more to fetch, so a state that
    fits in one page costs one request."""
    features: list[dict] = []
    while True:
        payload = _query(url, {
            **params, "resultOffset": len(features),
            "resultRecordCount": _PAGE, "orderByFields": "OID",
        })
        page = payload.get("features") or []
        features.extend(page)
        if not page:
            break
        if len(page) < _PAGE and not payload.get("exceededTransferLimit"):
            break
    return features


def _bbox(rings: list) -> tuple[float, float, float, float]:
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _spans(rings: list, y: float) -> list[tuple[float, float]]:
    """The x-intervals where the horizontal line at `y` is inside the
    polygon, as sorted (start, end) pairs.

    This is a scanline, and it is what makes the whole script tractable.
    Testing each sample point against every edge would be a few billion
    edge comparisons for one state — Rhode Island alone is 39 towns by a
    260x260 grid against 113 district polygons. Crossing each edge ONCE
    per row instead, and then answering every sample on that row from the
    resulting intervals, turns that into a few million.

    Sorting the crossings and pairing them off is the same even-odd rule
    ray casting uses, so holes and multi-part towns need no special case:
    an enclave contributes two extra crossings and simply splits the
    interval that contained it.
    """
    xs = []
    for ring in rings:
        count = len(ring)
        j = count - 1
        for i in range(count):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y):
                xs.append((xj - xi) * (y - yi) / (yj - yi) + xi)
            j = i
    xs.sort()
    return list(zip(xs[0::2], xs[1::2]))


def _span_lookup(spans: list[tuple[float, float, str]], x: float) -> str | None:
    """Which district's span covers `x`. Spans on one row don't overlap
    (districts tile the state), so a linear walk is correct; the row's
    span count is small enough that a bisect would buy nothing."""
    for start, end, number in spans:
        if start <= x <= end:
            return number
    return None


def _districts(state_fips: str, chamber: str) -> list[tuple[str, list, tuple]]:
    features = _query_all(_LEG_URL.format(layer=_CHAMBER_LAYERS[chamber]), {
        "where": f"STATE='{state_fips}'", "outFields": "BASENAME",
        "returnGeometry": "true", "outSR": _SR, "f": "json",
    })
    out = []
    for feature in features:
        basename = (feature.get("attributes") or {}).get("BASENAME") or ""
        rings = (feature.get("geometry") or {}).get("rings")
        # Normalised the same way parse_state_leg_office normalises what
        # it reads off a ballot label: leading zeros dropped, a trailing
        # letter kept and upper-cased (Minnesota's house districts are
        # "10A"/"10B"). A BASENAME in any other shape belongs to a
        # chamber that doesn't identify its seats the way the contest
        # labels do, and is skipped rather than guessed at.
        match = re.match(r"0*(\d+)([A-Za-z]?)$", basename.strip())
        if rings and match:
            out.append((match.group(1) + match.group(2).upper(), rings, _bbox(rings)))
    return out


def _towns(state_fips: str) -> list[tuple[str, list]]:
    features = _query_all(_TOWN_URL.format(layer=_TOWN_LAYER), {
        "where": f"STATE='{state_fips}'", "outFields": "NAME",
        "returnGeometry": "true", "outSR": _SR, "f": "json",
    })
    return [
        ((f.get("attributes") or {}).get("NAME") or "", f["geometry"]["rings"])
        for f in features
        if (f.get("geometry") or {}).get("rings")
        and (f.get("attributes") or {}).get("NAME") != _NOT_A_TOWN
    ]


def _towns_for_districts(towns: list, districts: list) -> dict[str, set[str]]:
    covers: dict[str, set[str]] = collections.defaultdict(set)
    for name, rings in towns:
        if not name:
            continue
        x0, y0, x1, y1 = _bbox(rings)
        # Narrow to the districts whose bounding box overlaps this town's
        # AT ALL, once, before touching a single row. Filtering per row on
        # latitude alone still re-scanned every district on the far side
        # of the state: Minnesota has 2,762 county subdivisions against
        # 201 districts, and without this the run does not finish in any
        # useful time. A township typically overlaps one or two.
        nearby = [
            (number, drings, box) for number, drings, box in districts
            if box[0] <= x1 and box[2] >= x0 and box[1] <= y1 and box[3] >= y0
        ]
        hits: collections.Counter = collections.Counter()
        sampled = 0
        for j in range(_GRID):
            y = y0 + (y1 - y0) * (j + 0.5) / _GRID
            town_spans = _spans(rings, y)
            if not town_spans:
                continue
            # Of those, only the ones this row actually crosses.
            row_spans = []
            for number, drings, (bx0, by0, bx1, by1) in nearby:
                if by0 <= y <= by1:
                    row_spans.extend((s0, s1, number) for s0, s1 in _spans(drings, y))
            for i in range(_GRID):
                x = x0 + (x1 - x0) * (i + 0.5) / _GRID
                if not any(s0 <= x <= s1 for s0, s1 in town_spans):
                    continue
                sampled += 1
                number = _span_lookup(row_spans, x)
                if number is not None:
                    hits[number] += 1
        if not sampled:
            continue
        for number, count in hits.items():
            if count / sampled >= _MIN_SHARE:
                covers[number].add(name)
    return covers


def main() -> int:
    states = [s.upper() for s in sys.argv[1:]]
    if not states:
        print(__doc__)
        return 2

    existing = {}
    if _OUTPUT.exists():
        existing = json.loads(_OUTPUT.read_text()).get("districts", {})

    districts_out = dict(existing)
    for state in states:
        fips = _FIPS.get(state)
        if not fips:
            print(f"unknown state {state}")
            return 1
        towns = _towns(fips)
        if not towns:
            print(f"{state}: no towns returned — refusing to write an empty crosswalk")
            return 1
        print(f"{state}: {len(towns)} towns")
        # Rebuild this state from scratch so a district that disappeared
        # in a remap doesn't survive as a stale entry.
        districts_out = {k: v for k, v in districts_out.items()
                         if not k.startswith(f"{state}-")}
        for chamber in ("upper", "lower"):
            seats = _districts(fips, chamber)
            covers = _towns_for_districts(towns, seats)
            for number, names in covers.items():
                districts_out[f"{state}-{chamber}-{number}"] = sorted(names)
            print(f"   {chamber}: {len(seats)} seats, {len(covers)} with town coverage")

    _OUTPUT.write_text(json.dumps({
        "_source": (
            "U.S. Census Bureau TIGERweb: current-vintage state legislative "
            "district polygons (TIGERweb/Legislative layers 1 and 2) against "
            "county-subdivision polygons, resolved by point sampling inside "
            "each town. See scripts/fetch_state_leg_crosswalk.py for why the "
            "block assignment files and a plain spatial intersect both give "
            "wrong answers here."
        ),
        "districts": dict(sorted(districts_out.items())),
    }, indent=1, sort_keys=False) + "\n")
    print(f"wrote {_OUTPUT} ({len(districts_out)} districts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
