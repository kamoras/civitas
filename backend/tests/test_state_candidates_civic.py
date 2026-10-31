"""The Google Civic candidate source (state_candidates_civic.py), on the
shapes voterInfoQuery answers with: contests copied from Michigan's live
answers of 2026-10-28 (office labels, district names and scopes, the
parties as VIP prints them), cut to a few races each."""

import pytest

from app.pipeline.fetch import state_candidates_civic as civic

ELECTIONS = {"elections": [
    {"id": "2000", "name": "VIP Test Election", "electionDay": "2031-12-06", "ocdDivisionId": "ocd-division/country:us"},
    {"id": "12000", "name": "2026 General Midterm Election", "electionDay": "2026-11-03",
     "ocdDivisionId": "ocd-division/country:us"},
]}


def _contest(office, district, scope, *cands):
    return {"type": "General", "office": office, "district": {"name": district, "scope": scope},
            "candidates": [{"name": n, "party": p} for n, p in cands]}


STATEWIDE = [
    _contest("Governor/Lieutenant Governor", "Michigan", "statewide",
             ("Jocelyn Benson/Winnie Brinks", "DEMOCRATIC"), ("John James/Jay DeBoyer", "REPUBLICAN")),
    _contest("United States Senator", "Michigan", "statewide",
             ("Abdul El-Sayed", "DEMOCRATIC"), ("Mike Rogers", "REPUBLICAN"), ("Tim Long", "U.S. TAXPAYERS")),
    # A city's own treasurer: its office alone reads as the state treasurer.
    _contest("Treasurer", "JACKSON CITY", "citywide", ("David R. Hammontree", "NON PARTISAN")),
    _contest("Justice of Supreme Court", "Supreme Court", "judicial", ("Noah P. Hood", "NO PARTY AFFILIATION")),
]


def _ballot(house, upper, lower, extra=()):
    return {"contests": [
        *STATEWIDE,
        _contest("Representative in Congress", f"{house} District", "congressional", *extra or (
            ("Sean McCann", "DEMOCRATIC"), ("Bill Huizenga", "REPUBLICAN"))),
        _contest("State Senator", f"{upper} District", "stateUpper", ("Julie M. Rogers", "DEMOCRATIC")),
        _contest("Representative in State Legislature", f"{lower} District", "stateLower",
                 ("Matt Longjohn", "DEMOCRATIC")),
    ]}


POINTS = {
    "XX-H-4": [[1.0, 1.0]], "XX-H-5": [[2.0, 2.0]],
    "XX-upper-19": [[1.0, 1.0]], "XX-upper-20": [[2.0, 2.0]],
    "XX-lower-40": [[1.0, 1.0]], "XX-lower-41": [[2.0, 2.0], [3.0, 3.0]],
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    civic._answers.clear()
    monkeypatch.setattr(civic.settings, "GOOGLE_CIVIC_API_KEY", "test-key")
    monkeypatch.setattr(civic, "_points", lambda state: {k.replace("XX", state): v for k, v in POINTS.items()})


def _serve(monkeypatch, answers: dict, elections=ELECTIONS):
    """`answers`: address -> payload (civic._NO_DATA, None for a failed fetch)."""
    asked = []

    async def get_json(client, url, params, label, expected_statuses=()):
        if url.endswith("/elections"):
            return elections
        asked.append(params["address"])
        return answers.get(params["address"], civic._NO_DATA)

    monkeypatch.setattr(civic, "_get_json", get_json)
    return asked


async def _read(state="XX", source=None):
    return await civic.fetch_confirmed_candidates(None, 2026, state, source or {})


def _by_office(records):
    out = {}
    for r in records:
        out.setdefault((r["office"], r["district"]), []).append(r)
    return out


@pytest.mark.asyncio
async def test_each_district_comes_from_its_own_label_and_name(monkeypatch):
    _serve(monkeypatch, {"1.0,1.0": _ballot("4th", "19th", "40th"), "2.0,2.0": _ballot("5th", "20th", "41st")})
    records = _by_office(await _read())
    assert {r["last_name"] for r in records[("H", 4)]} == {"McCann", "Huizenga"}
    assert ("H", 5) in records and ("upper", "20") in records and ("lower", "41") in records
    assert {r["last_name"] for r in records[("S", None)]} == {"El-Sayed", "Rogers", "Long"}


@pytest.mark.asyncio
async def test_a_ticket_is_joined_and_a_city_office_or_judgeship_is_not_a_state_office(monkeypatch):
    _serve(monkeypatch, {"1.0,1.0": _ballot("4th", "19th", "40th"), "2.0,2.0": _ballot("5th", "20th", "41st")})
    records = await _read()
    governors = {r["last_name"] for r in records if r["office"] == "governor"}
    assert governors == {"Jocelyn Benson and Winnie Brinks", "John James and Jay DeBoyer"}
    assert not any(r["office"] == "treasurer" for r in records)
    assert {r["office"] for r in records} <= {"S", "H", "governor", "upper", "lower"}


@pytest.mark.asyncio
async def test_a_house_label_with_no_number_is_at_large_only_in_a_one_seat_state(monkeypatch):
    bare = {"contests": [*STATEWIDE, _contest("U.S. Representative", "", "", ("A Person", "DEMOCRATIC"))]}
    _serve(monkeypatch, {"1.0,1.0": bare, "2.0,2.0": bare, "3.0,3.0": bare})
    assert not any(r["office"] == "H" for r in await _read())  # two seats on file: refused
    civic._answers.clear()
    monkeypatch.setattr(civic, "_points", lambda state: {"XX-H-0": [[1.0, 1.0]], "XX-upper-1": [[2.0, 2.0]]})
    assert [r["district"] for r in await _read() if r["office"] == "H"] == [None]


@pytest.mark.asyncio
async def test_virginia_s_house_label_is_read_by_its_congressional_scope(monkeypatch):
    va = {"contests": [*STATEWIDE, {"office": "Member, House of Representatives (9th District)",
                                    "district": {"name": "09", "scope": "congressional"},
                                    "candidates": [{"name": "Morgan Griffith", "party": "Republican"}]}]}
    _serve(monkeypatch, {"1.0,1.0": va})
    assert [(r["office"], r["district"]) for r in await _read() if r["office"] == "H"] == [("H", 9)]


@pytest.mark.asyncio
async def test_answers_that_disagree_about_a_race_drop_it(monkeypatch):
    _serve(monkeypatch, {
        "1.0,1.0": _ballot("4th", "19th", "40th"),
        "2.0,2.0": _ballot("4th", "20th", "41st", extra=(("Sean McCann", "DEMOCRATIC"),)),
    })
    records = await _read()
    assert not any(r["office"] == "H" and r["district"] == 4 for r in records)


@pytest.mark.asyncio
async def test_an_unread_district_is_named_and_the_rest_is_complete(monkeypatch):
    # 41's points both fail to parse: Google's answer for a point it can't place.
    _serve(monkeypatch, {"1.0,1.0": _ballot("4th", "19th", "40th")})
    records = await _read()
    assert not records.state_offices_incomplete
    assert ("lower", "41") in records.unread_districts and ("lower", "40") not in records.unread_districts


@pytest.mark.asyncio
async def test_no_statewide_offices_means_no_state_office_read(monkeypatch):
    house_only = {"contests": [_contest("Representative in Congress", "4th District", "congressional",
                                        ("Sean McCann", "DEMOCRATIC"))]}
    _serve(monkeypatch, {"1.0,1.0": house_only})
    records = await _read()
    assert records.state_offices_incomplete and {r["office"] for r in records} == {"H"}


@pytest.mark.asyncio
async def test_a_state_with_no_vip_ballot_is_an_empty_answer_in_a_few_requests(monkeypatch):
    asked = _serve(monkeypatch, {})
    assert await _read() == []
    assert len(asked) == civic.PROBE_POINTS


@pytest.mark.asyncio
async def test_no_general_listed_is_empty_and_a_failed_read_is_none(monkeypatch):
    _serve(monkeypatch, {}, elections={"elections": []})
    assert await _read() == []
    _serve(monkeypatch, {}, elections=None)
    assert await _read() is None
    _serve(monkeypatch, {"1.0,1.0": None})
    assert await _read() is None


@pytest.mark.asyncio
async def test_each_point_is_asked_once_a_day(monkeypatch):
    asked = _serve(monkeypatch, {"1.0,1.0": _ballot("4th", "19th", "40th"), "2.0,2.0": _ballot("5th", "20th", "41st")})
    await _read()
    first = len(asked)
    await _read()
    assert len(asked) == first


@pytest.mark.asyncio
async def test_fixed_addresses_are_asked_first(monkeypatch):
    asked = _serve(monkeypatch, {"100 Capitol": _ballot("4th", "19th", "40th"), "2.0,2.0": _ballot("5th", "20th", "41st")})
    await _read(source={"address": "100 Capitol"})
    assert asked[0] == "100 Capitol" and "1.0,1.0" not in asked  # 4th, 19th and 40th were already read


def test_vip_capital_party_names_read_as_the_state_writes_them():
    assert civic._party_label("U.S. TAXPAYERS") == "U.S. Taxpayers"
    assert civic._party_label("Working Class Party") == "Working Class Party"


@pytest.mark.asyncio
async def test_a_point_google_cannot_place_is_not_an_answer(monkeypatch):
    # The first three points fail to parse; the state's ballot is still read.
    points = {"XX-H-4": [[9.0, 9.0], [8.0, 8.0], [7.0, 7.0], [1.0, 1.0]], "XX-H-5": [[2.0, 2.0]]}
    monkeypatch.setattr(civic, "_points", lambda state: {k.replace("XX", state): v for k, v in points.items()})
    unplaced = {f"{n}.0,{n}.0": civic._UNPLACED for n in (7, 8, 9)}
    _serve(monkeypatch, {**unplaced, "1.0,1.0": _ballot("4th", "19th", "40th"), "2.0,2.0": _ballot("5th", "20th", "41st")})
    assert ("H", 4) in _by_office(await _read())


@pytest.mark.asyncio
async def test_a_state_none_of_whose_points_are_placed_is_a_failed_read(monkeypatch):
    many = {"XX-H-1": [[float(n), float(n)] for n in range(30)]}
    monkeypatch.setattr(civic, "_points", lambda state: {k.replace("XX", state): v for k, v in many.items()})
    _serve(monkeypatch, {f"{float(n)},{float(n)}": civic._UNPLACED for n in range(30)})
    assert await _read() is None


def test_disagreeing_answers_about_unlike_races_are_logged_not_raised():
    """California, 2026-10-30: the warning naming the races Google's answers
    disagreed on sorted their keys, and a key's district or seat is None
    beside another's string, so the TypeError took down the state's whole
    read. Those races are left out; every other race still reads."""
    reading = civic._Reading(one_seat=False)
    kept = {"office": "S", "district": None, "seat": None, "last_name": "Doe", "party": "DEM"}
    reading.races = {("S", None, None): [kept], ("S", None, "special"): [], ("upper", "7", None): []}
    reading.conflicts = {("S", None, "special"), ("upper", "7", None), ("upper", None, None)}
    assert reading.records() == [kept]
