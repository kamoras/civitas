"""GET /api/politicians/house-districts: a state's House districts by
county, on the lines of the Congress in office (the sitting members'),
never the coming election's map in a redrawn state."""

import json
import pathlib

import pytest
from fastapi import HTTPException

from app.api import politicians
from app.config import settings
from app.district_lines import lookup_lines
from app.district_places import places_on_lines_of
from app.state_names import STATE_NAMES


def _body(resp) -> dict:
    return json.loads(resp.body)


def test_sitting_congress_reads_its_own_lines(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)
    body = _body(politicians.house_districts_by_county(state="tx"))
    assert body["available"] is True and body["congress"] == 119 and body["state"] == "TX"
    numbers = [d["district"] for d in body["districts"]]
    assert numbers == sorted(numbers) and len(numbers) == 38
    assert all(d["counties"] for d in body["districts"])


def test_each_district_lists_its_places_on_the_same_lines(monkeypatch):
    # A reader types a city or town as often as a county: each district
    # carries the places on the very lines its counties are from.
    for congress in (119, 120):
        monkeypatch.setattr(settings, "CURRENT_CONGRESS", congress)
        body = _body(politicians.house_districts_by_county(state="TX"))
        assert all(d["places"] == places_on_lines_of(congress)[f"TX-{d['district']}"] for d in body["districts"])
        assert all(d["places"] for d in body["districts"])


def test_redrawn_state_differs_between_the_two_congresses(monkeypatch):
    # TX redrew for 2026: the lines its sitting members won are not the
    # 2026 election's, so the two files must answer differently.
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)
    sitting = _body(politicians.house_districts_by_county(state="TX"))["districts"]
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    elected_2026 = _body(politicians.house_districts_by_county(state="TX"))["districts"]
    assert sitting != elected_2026


def test_unredrawn_state_is_the_same_on_both(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)
    a = _body(politicians.house_districts_by_county(state="PA"))["districts"]
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    assert a == _body(politicians.house_districts_by_county(state="PA"))["districts"]


def test_no_file_for_the_congress_says_so_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 121)
    body = _body(politicians.house_districts_by_county(state="TX"))
    assert body == {"state": "TX", "congress": 121, "available": False, "districts": [], "lookupLines": None}


LOOKUP = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "lookup"


@pytest.mark.parametrize("congress", [119, 120])
def test_lookup_lines_name_a_folder_holding_the_state(monkeypatch, congress):
    # The 120th sits on Jan 3, 2027: asking for congress-120/<ST> by its
    # number found no file for the 41 states that kept their map. The
    # folder is the map's, so every state is found with nothing rebuilt.
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", congress)
    for st in STATE_NAMES:
        if st == "DC" or not _body(politicians.house_districts_by_county(state=st))["available"]:
            continue
        folder = _body(politicians.house_districts_by_county(state=st))["lookupLines"]
        assert (LOOKUP / folder / f"{st}.json").exists(), (st, folder)


def test_lookup_lines_follow_each_state_s_map(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    assert lookup_lines("TX", 120) == "congress-120"  # redrew for 2026
    assert lookup_lines("NY", 120) == "congress-119"  # kept its map
    assert lookup_lines("MO", 120) == "congress-119"  # its 2025 map stayed
    assert lookup_lines("TX", 119) == "congress-119"
    assert lookup_lines("TX", 121) is None  # 2028's maps not listed yet


def test_unknown_state_is_404():
    with pytest.raises(HTTPException) as e:
        politicians.house_districts_by_county(state="ZZ")
    assert e.value.status_code == 404


def test_route_is_not_shadowed_by_the_profile_route():
    paths = [r.path for r in politicians.router.routes]
    assert paths.index("/politicians/house-districts") < paths.index("/politicians/{politician_id}")


def test_the_121st_congress_reads_unchanged_maps_and_never_crashes(monkeypatch):
    """2029-01-03, the 121st in office before anyone builds its crosswalk:
    with the 2028 maps confirmed and no redraw, every state reads the
    120th's districts (the same lines); with the cycle unconfirmed the
    finder answers unavailable instead of failing."""
    from app import district_lines

    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 121)
    monkeypatch.setattr(district_lines, "_maps", lambda: {"2026": {"TX": {"map": "CD120"}}})
    assert _body(politicians.house_districts_by_county(state="TX"))["available"] is False
    monkeypatch.setattr(district_lines, "_maps", lambda: {"2026": {"TX": {"map": "CD120"}}, "2028": {}})
    body = _body(politicians.house_districts_by_county(state="TX"))
    assert body["available"] is True and len(body["districts"]) == 38
