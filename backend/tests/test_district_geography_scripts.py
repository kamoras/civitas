"""Pure functions of the congressional-district geography builders
(scripts/build_county_district_crosswalk.py, scripts/build_district_topology.py).

Tiny inline data only: the real inputs are 300MB+ Census files.
"""

import datetime as dt
import importlib.util
import json
import pathlib
import sys

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
MAPS = {
    "UT": (cw.CD120, "court-adopted map"),
    "MO": (cw.CD119, "new map stayed"),
}


def test_map_for_state_defaults_to_cd119():
    assert cw.map_for_state("UT", MAPS) == cw.CD120
    assert cw.map_for_state("MO", MAPS) == cw.CD119
    assert cw.map_for_state("VT", MAPS) == cw.CD119


def test_real_cycle_keeps_missouri_on_its_2022_map():
    # The 2025 Missouri map is in the Census CD120 file but was stayed.
    assert cw.map_for_state("MO") == cw.CD119
    assert cw.map_for_state("TX") == cw.CD120
    assert cw.map_for_state("UT") == cw.CD120


def test_choose_assignment_takes_cd120_only_where_listed():
    cd119 = {
        "490010001001000": "01",
        "490010001001001": "01",
        "290010001001000": "05",
        "500010001001000": "00",
    }
    cd120 = [
        ("490010001001000", "03"),
        ("490010001001001", "01"),
        ("290010001001000", "04"),  # Missouri's stayed map: ignored
        ("500010001001000", "00"),
    ]
    chosen, problems = cw.choose_assignment(cd119, cd120, FIPS, MAPS)
    assert problems == []
    assert chosen == {
        "490010001001000": "03",
        "490010001001001": "01",
        "290010001001000": "05",
        "500010001001000": "00",
    }


def test_choose_assignment_refuses_unlisted_state_that_changed():
    cd119 = {"500010001001000": "00", "500010001001001": "00"}
    cd120 = [("500010001001000", "01"), ("500010001001001", "00")]
    _, problems = cw.choose_assignment(cd119, cd120, FIPS, MAPS)
    assert problems == [
        "VT: 1 blocks differ between CD119 and CD120 but VT has no CYCLE_MAPS entry"
    ]


def test_choose_assignment_flags_cd120_state_missing_blocks():
    cd119 = {"490010001001000": "01", "490010001001001": "01"}
    cd120 = [("490010001001000", "02")]
    _, problems = cw.choose_assignment(cd119, cd120, FIPS, MAPS)
    assert problems == ["UT: 1 blocks missing from CD120"]


def test_choose_assignment_flags_an_unlisted_state_missing_from_cd120():
    cd119 = {"500010001001000": "00", "500010001001001": "00"}
    cd120 = [("500010001001000", "00")]
    _, problems = cw.choose_assignment(cd119, cd120, FIPS, MAPS)
    assert problems == ["VT: 1 blocks in CD119 missing from CD120 but VT has no CYCLE_MAPS entry"]


def test_choose_assignment_flags_a_cd120_entry_whose_lines_did_not_change():
    """A stale entry would tell the live-results sync the state's seats
    have no holder."""
    cd119 = {"490010001001000": "01"}
    cd120 = [("490010001001000", "01")]
    _, problems = cw.choose_assignment(cd119, cd120, FIPS, MAPS)
    assert problems == ["UT: listed as CD120 but its lines are identical to CD119"]


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


def test_source_note_names_each_state_map_and_reason():
    note = cw.source_note(MAPS, dt.date(2026, 9, 28), all_cd119=False)
    assert "MO CD119 (new map stayed)" in note
    assert "UT CD120 (court-adopted map)" in note
    assert "built 2026-09-28" in note
    assert "build_county_district_crosswalk.py" in note


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
