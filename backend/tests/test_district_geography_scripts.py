"""Pure functions of the congressional-district geography builders
(scripts/build_county_district_crosswalk.py, scripts/build_district_topology.py).

Tiny inline data only: the real inputs are 300MB+ Census files.
"""

import datetime as dt
import importlib.util
import json
import pathlib
import sys

from app import census_lines

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cw = _load("build_county_district_crosswalk")
topo = _load("build_district_topology")

FIPS = {"29": "MO", "49": "UT", "50": "VT", "11": "DC"}
# The maps file's shape: UT redrew for 2026, MO's new map was stayed.
CYCLES = {"2026": {"UT": {"map": "CD120", "reason": "court-adopted map"},
                   "MO": {"map": "CD119", "reason": "new map stayed"}}}


def test_each_state_s_lines_come_from_the_map_it_votes_on():
    assert census_lines.state_maps(120, ["UT", "MO", "VT"], CYCLES) == {"UT": 120, "MO": 119, "VT": 119}
    assert census_lines.state_maps(119, ["UT", "MO", "VT"], CYCLES) == {"UT": 119, "MO": 119, "VT": 119}
    # A map stands until replaced: 2028 lists nothing, so 2026's carry.
    assert census_lines.state_maps(121, ["UT"], {**CYCLES, "2028": {}}) == {"UT": 120}
    # The real file keeps Missouri on its 2022 map.
    real = census_lines.maps_cycles()
    assert census_lines.state_maps(120, ["MO", "TX"], real) == {"MO": 119, "TX": 120}


def test_an_unreviewed_election_gets_no_table():
    assert census_lines.listed(CYCLES, 120) and census_lines.listed(CYCLES, 119)
    assert not census_lines.listed(CYCLES, 121)


def _digests(**by_congress):
    return {int(c[1:]): v for c, v in by_congress.items()}


def test_check_maps_allows_a_listed_stay_and_a_listed_redraw():
    maps = {"UT": 120, "MO": 119, "VT": 119}
    digests = _digests(c119={"UT": (1, 9), "MO": (2, 9), "VT": (3, 9)},
                       c120={"UT": (4, 9), "MO": (5, 9), "VT": (3, 9)})
    assert cw.check_maps(120, maps, digests, CYCLES["2026"], {"UT": 119, "MO": 119, "VT": 119}) == []


def test_check_maps_refuses_an_unlisted_state_whose_blocks_moved():
    maps = {"UT": 120, "MO": 119, "VT": 119}
    digests = _digests(c119={"UT": (1, 9), "MO": (2, 9), "VT": (3, 9)},
                       c120={"UT": (4, 9), "MO": (2, 9), "VT": (6, 9)})
    problems = cw.check_maps(120, maps, digests, CYCLES["2026"], {"UT": 119, "MO": 119, "VT": 119})
    assert problems == ["VT: its blocks differ between the 119th and 120th Congress files but the maps file "
                        "doesn't list it for the election that seats the 120th"]


def test_check_maps_flags_a_redraw_entry_whose_lines_did_not_change():
    """A stale entry would tell the live-results sync the state's seats
    have no holder."""
    maps = {"UT": 120}
    digests = _digests(c119={"UT": (1, 9)}, c120={"UT": (1, 9)})
    assert cw.check_maps(120, maps, digests, {"UT": {}}, {"UT": 119}) == [
        "UT: listed as on the 120th Congress's map but its lines are identical to the 119th's"]


def test_check_maps_flags_a_state_missing_from_its_map():
    digests = _digests(c119={"MO": (2, 9)}, c120={"MO": (2, 9), "UT": (4, 9)})
    assert cw.check_maps(120, {"UT": 119, "MO": 119}, digests, {"UT": {}}, {}) == [
        "UT: no blocks in the 119th Congress's file"]


def test_part_tag_is_any_block_in_a_second_district():
    assignment = {
        # Salt Lake County: blocks in two districts -> "(part)" in both.
        "490350001001000": "01",
        "490350001001001": "04",
        # Box Elder: wholly in 1.
        "490030001001000": "01",
        # A water block with no district doesn't make a county split.
        "490030001001001": "ZZ",
        # Vermont at-large -> "VT-0".
        "500010001001000": "00",
        # DC's delegate seat is not a House race.
        "110010001001000": "98",
    }
    names = {
        "49035": "Salt Lake County",
        "49003": "Box Elder County",
        "50001": "Addison County",
        "11001": "District of Columbia",
    }
    districts = cw.build_districts(cw.county_districts(assignment), names, FIPS)
    assert districts == {
        "UT-1": ["Box Elder County", "Salt Lake County (part)"],
        "UT-4": ["Salt Lake County (part)"],
        "VT-0": ["Addison County"],
    }


def test_district_keys_use_plain_string_order():
    assignment = {
        f"0600{i}0001001000": f"{n:02d}" for i, n in enumerate([1, 10, 2], start=1)
    }
    names = {f"0600{i}": f"C{i}" for i in range(1, 4)}
    districts = cw.build_districts(cw.county_districts(assignment), names, {"06": "CA"})
    assert list(districts) == ["CA-1", "CA-10", "CA-2"]


def test_source_note_names_each_redrawn_state_and_its_file():
    note = cw.source_note(120, {"UT": 120, "MO": 119}, {119: "https://x/cd119.zip", 120: "https://x/cd120.zip"},
                          {"UT": "court-adopted map"}, dt.date(2026, 9, 28))
    assert "UT on the 120th Congress's map (court-adopted map)" in note
    assert "https://x/cd119.zip" in note and "https://x/cd120.zip" in note
    assert "built 2026-09-28" in note and "build_county_district_crosswalk.py" in note


def test_bundled_files_name_their_congress():
    data = _SCRIPTS.parent / "app" / "data"
    election = json.loads((data / "county_district_crosswalk.json").read_text())
    sitting = json.loads((data / "county_district_crosswalk_sitting.json").read_text())
    assert election["congress"] == census_lines.seated_by(2026)
    assert sitting["congress"] == census_lines.seated_by(2024)
    assert len(sitting["districts"]) == len(election["districts"]) == 435
    # Each records the files it was read from, for the refresh's re-release check.
    assert set(election["_inputs"]["bef"]) == {"119", "120"} and set(sitting["_inputs"]["bef"]) == {"119"}


def test_bef_rows_take_a_state_s_own_file_over_the_national_rows(tmp_path):
    import zipfile
    z = tmp_path / "cd9.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("NationalCD9.txt", "GEOID,CDFP\r\n490010001001000,01\r\n500010001001000,00\r\n")
        zf.writestr("CD9_49.txt", "GEOID,STATEFP,COUNTYFP,TRACTCE,BLOCKCE,CDFP\n490010001001000,49,001,000100,1000,03\n")
    assert list(census_lines.bef_rows(z, "49", "UT")) == [("490010001001000", "03")]
    assert list(census_lines.bef_rows(z, "50", "VT")) == [("500010001001000", "00")]
    assert sorted(census_lines.bef_all_rows(z)) == [("490010001001000", "03"), ("500010001001000", "00")]


def test_parse_threshold_reads_mapshaper_stats():
    log = "[simplify] Simplification statistics\n   Simplification threshold: 2293.9253 meters\n"
    assert topo.parse_threshold(log) == "2293.9253m"


def test_parse_threshold_fails_loudly():
    try:
        topo.parse_threshold("[o] Wrote x.json")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def _topology(districts, layer="tl_2020_49_tabblock20"):
    return {
        "type": "Topology",
        "bbox": [0, 0, 1, 1],
        "arcs": [],
        "objects": {
            layer: {
                "type": "GeometryCollection",
                "geometries": [
                    {
                        "type": "Polygon",
                        "arcs": [],
                        "properties": {"district": d, "st": "49"},
                    }
                    for d in districts
                ],
            }
        },
    }


def test_validate_and_normalize_renames_layer_and_strips_properties():
    blob, problems = topo.validate_and_normalize(
        _topology([1, 2]), "UT", {"UT": {1, 2}}
    )
    assert problems == []
    out = json.loads(blob)
    assert list(out["objects"]) == ["districts"]
    assert [g["properties"] for g in out["objects"]["districts"]["geometries"]] == [
        {"district": 1},
        {"district": 2},
    ]


def test_validate_and_normalize_reports_mismatch_and_collapse():
    t = _topology([1, 2])
    t["objects"]["tl_2020_49_tabblock20"]["geometries"][1]["type"] = None
    _, problems = topo.validate_and_normalize(t, "UT", {"UT": {1, 2, 3}})
    assert problems == [
        "UT: [1, 2] != crosswalk [1, 2, 3]",
        "UT: districts lost to simplification: [2]",
    ]
