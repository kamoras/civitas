"""scripts/fetch_state_leg_crosswalk.py and the file it writes.

The fixture is trimmed from the real inputs: Rhode Island's 2020 P.L.
94-171 geographic header (a few blocks per town / place / district
combination in Jamestown, Middletown, Newport and Westerly, plus their
name rows) and the matching rows of the 2026 lower-chamber block
equivalency file.
"""

import importlib.util
import json
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _BACKEND / "tests" / "fixtures"
_DATA = _BACKEND / "app" / "data"

_spec = importlib.util.spec_from_file_location(
    "fetch_state_leg_crosswalk", _BACKEND / "scripts" / "fetch_state_leg_crosswalk.py"
)
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def _geo_lines() -> list[str]:
    return (_FIXTURES / "fixtures_ri_pl_geo_trimmed.pl").read_text(encoding="latin-1").splitlines()


def _assignment() -> list[tuple[str, str]]:
    rows = (_FIXTURES / "fixtures_ri_sldl26_trimmed.txt").read_text().splitlines()[1:]
    return [(r.split(",")[0], r.split(",")[5]) for r in rows]


def _build(lines=None) -> dict[str, list[str]]:
    blocks, names = cw.read_geo(lines or _geo_lines(), strong_mcd=True)
    return cw.crosswalk(blocks, names, _assignment())


def test_district_key_matches_ballot_label_parsing():
    assert cw.district_key("074") == "74"
    assert cw.district_key("01A") == "1A"
    assert cw.district_key("00A") == "A"
    assert cw.district_key("ZZZ") is None
    assert cw.district_key("D01") is None
    assert cw.district_key("A-1") is None


def test_towns_then_places_then_county_largest_first():
    out = _build()
    assert out["74"] == ["Middletown town", "Jamestown town", "Melville", "Newport East", "Newport County"]
    assert out["37"] == ["Westerly town", "Misquamicut", "Watch Hill", "Weekapaug", "Washington County"]


def test_cdp_written_without_census_tag():
    names = {n for listed in _build().values() for n in listed}
    assert "Newport East" in names
    assert not any(n.endswith(" CDP") for n in names)


def test_place_named_like_its_town_is_not_repeated():
    out = _build()
    # Newport city is both a place and a county subdivision; Westerly CDP
    # lies inside Westerly town.
    assert out["75"].count("Newport city") == 1
    assert "Westerly" not in out["37"]


def test_unpopulated_blocks_and_water_placeholder_list_nothing():
    lines = _geo_lines()
    # Give one of the placeholder's (empty) blocks residents: it must
    # still never be listed as a town.
    for i, line in enumerate(lines):
        f = line.split("|")
        if f[cw._SUMLEV] == cw._BLOCK and f[cw._COUSUB] == "00000":
            f[cw._POP] = "500"
            lines[i] = "|".join(f)
            break
    else:
        raise AssertionError("fixture has no placeholder block")
    names = {n for listed in _build(lines).values() for n in listed}
    assert cw._NOT_A_TOWN not in names


def test_overlap_below_the_floor_is_dropped_unless_it_holds_half_a_small_place(monkeypatch):
    blocks, names = cw.read_geo(_geo_lines(), strong_mcd=True)
    pop = {geoid: p for geoid, (p, _units) in blocks.items()}
    # One real block of Watch Hill, alone in district 37.
    geoid = next(g for g, (_p, units) in blocks.items() if ("place", "75200") in units)
    one_block = [(geoid, "037")]
    monkeypatch.setattr(cw, "_MIN_RESIDENTS", pop[geoid] + 1)
    # The whole (one-block) place lives here: listed despite the floor.
    assert "Watch Hill" in cw.crosswalk(blocks, names, one_block)["37"]
    # Half of it elsewhere is still half: listed in both.
    two = {**blocks, "x": (pop[geoid], blocks[geoid][1])}
    both = cw.crosswalk(two, names, one_block + [("x", "036")])
    assert "Watch Hill" in both["36"] and "Watch Hill" in both["37"]
    # A sliver of a larger place below the floor is not.
    big = {**blocks, "x": (pop[geoid] * 3, blocks[geoid][1])}
    split = cw.crosswalk(big, names, one_block + [("x", "036")])
    assert "Watch Hill" in split["36"] and "37" not in split


def test_current_name_replaces_the_2020_one():
    current = cw.current_names(
        ["USPS|GEOID|GEOIDFQ|ANSICODE|NAME|LSAD", "RI|4475200|1600000US4475200|0|Watch Hill CDP|57",
         "CT|0975200|1600000US0975200|0|Elsewhere town|43"],
        "44", "place",
    )
    assert current == {("place", "75200"): "Watch Hill CDP"}


def test_block_inside_a_place_created_since_2020_moves_into_it():
    square = [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]
    hole = [[[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]]
    blocks = {
        "in": (10, (("county", "001"), ("cousub", "11111"), ("place", "22222"))),
        "hole": (10, (("county", "001"), ("place", "22222"))),
        "out": (10, (("county", "001"), ("place", "22222"))),
    }
    points = {"in": (1, 1), "hole": (5, 5), "out": (20, 5)}
    polygons = {("place", "33333"): square + hole, ("cousub", "44444"): square}
    assert cw.reassign(blocks, points, polygons) == 2
    assert set(blocks["in"][1]) == {("county", "001"), ("place", "33333"), ("cousub", "44444")}
    # Inside the hole the new place doesn't reach; the new town does.
    assert set(blocks["hole"][1]) == {("county", "001"), ("place", "22222"), ("cousub", "44444")}
    assert blocks["out"][1] == (("county", "001"), ("place", "22222"))


def _crosswalk_file() -> dict[str, list[str]]:
    return json.loads((_DATA / "state_leg_district_crosswalk.json").read_text())["districts"]


def test_every_district_lists_a_county_named_as_the_house_crosswalk_names_it():
    house = json.loads((_DATA / "county_district_crosswalk.json").read_text())["districts"]
    counties: dict[str, set[str]] = {}
    for key, listed in house.items():
        counties.setdefault(key.split("-")[0], set()).update(
            re.sub(r" \(part\)$", "", c) for c in listed
        )
    missing = [
        key for key, listed in _crosswalk_file().items()
        if not counties[key.split("-")[0]].intersection(listed)
    ]
    assert missing == []


def test_no_census_jargon_in_bundled_names():
    for listed in _crosswalk_file().values():
        assert listed
        assert not any(n.endswith(" CDP") or n == cw._NOT_A_TOWN for n in listed)
        assert len(listed) == len(set(listed))
