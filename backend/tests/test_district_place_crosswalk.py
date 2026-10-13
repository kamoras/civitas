"""scripts/build_district_place_crosswalk.py and the file it writes
(app/data/congressional_district_places.json): the places in each U.S.
House district, which the district filters match a city or town against.

The pure functions run on the trimmed Rhode Island fixture the state
legislative crosswalk's tests use; the real inputs are 300MB+ Census files.
"""

import importlib.util
import json
import pathlib
import sys

from app.district_places import places_on_lines_of

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SCRIPTS = _BACKEND / "scripts"
_DATA = _BACKEND / "app" / "data"
_FIXTURES = _BACKEND / "tests" / "fixtures"


def _load(name: str):
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


places = _load("build_district_place_crosswalk")
sl = _load("fetch_state_leg_crosswalk")
cw = _load("build_county_district_crosswalk")

_BUNDLED = json.loads((_DATA / "congressional_district_places.json").read_text())
_COUNTIES = json.loads((_DATA / "county_district_crosswalk.json").read_text())["districts"]


def test_cd_key_matches_the_county_file():
    assert places.cd_key("05") == "5"
    assert places.cd_key("12") == "12"
    assert places.cd_key("00") == "0"  # at-large, "ST-0" like the county file
    assert places.cd_key("98") is None  # delegate seat
    assert places.cd_key("ZZ") is None  # unassigned water


def test_districts_list_towns_and_places_but_no_county():
    geo = (_FIXTURES / "fixtures_ri_pl_geo_trimmed.pl").read_text(encoding="latin-1").splitlines()
    blocks, names = sl.read_geo(geo, strong_mcd=True)
    # Every fixture block in one district, the delegate seat's code on none.
    assignment = [(geoid, "01") for geoid in blocks] + [("440000000000000", "98")]
    out = sl.crosswalk(places.places_only(blocks), names, assignment, key=places.cd_key)
    assert list(out) == ["1"]
    assert "Middletown town" in out["1"] and "Newport East" in out["1"]
    assert not any(n.endswith(" County") for n in out["1"])


def test_state_legislative_keys_are_unchanged_by_the_key_parameter():
    assert sl.crosswalk({}, {}, [("x", "007")]) == {}
    assert sl.district_key("007") == "7"


def test_bundled_file_has_both_maps_for_every_seat():
    election = _BUNDLED["congresses"][places.ELECTION]
    sitting = _BUNDLED["congresses"][places.SITTING]
    # The election map is the one the county file describes, seat for seat.
    assert set(election) == set(_COUNTIES)
    # Apportionment is the same for both Congresses, so the seats are too.
    assert set(sitting) == set(election)
    assert all(election.values()) and all(sitting.values())
    assert "_source" in _BUNDLED and "build_district_place_crosswalk.py" in _BUNDLED["_source"]


def test_bundled_names_are_what_a_reader_types():
    names = {n for seats in _BUNDLED["congresses"].values() for listed in seats.values() for n in listed}
    assert not any(n.endswith(" CDP") for n in names)
    # Counties come from the county file; a consolidated city-county
    # ("Macon-Bibb County") is a place and stays.
    counties = {c.removesuffix(" (part)") for listed in _COUNTIES.values() for c in listed}
    assert not names & {c for c in counties if c.endswith(" County")}
    assert not any("(part)" in n for n in names)
    for seats in _BUNDLED["congresses"].values():
        for listed in seats.values():
            assert len(listed) == len(set(listed))


def test_only_redrawn_states_differ_between_the_two_maps():
    election = _BUNDLED["congresses"][places.ELECTION]
    sitting = _BUNDLED["congresses"][places.SITTING]
    differs = {k.split("-")[0] for k in election if election[k] != sitting[k]}
    redrawn = {st for st, (plan, _) in cw.CYCLE_MAPS.items() if plan == cw.CD120}
    assert differs and differs <= redrawn


def test_some_places_are_split_between_districts():
    """A place listed under two districts is split; the filter says so."""
    election = _BUNDLED["congresses"][places.ELECTION]
    seen: dict[tuple[str, str], int] = {}
    for key, listed in election.items():
        for name in listed:
            seen[key.split("-")[0], name] = seen.get((key.split("-")[0], name), 0) + 1
    assert any(n > 1 for n in seen.values())


def test_loader_serves_each_map_and_nothing_for_another_congress():
    assert places_on_lines_of(120) == _BUNDLED["congresses"]["120"]
    assert places_on_lines_of(119) == _BUNDLED["congresses"]["119"]
    assert places_on_lines_of(121) is None
