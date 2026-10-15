"""scripts/build_district_lean.py, the file it writes, and the loader the
site reads it through (app/district_lean.py).

The script's arithmetic is tested on toy inputs (no download, no
projection); the shipped file is held to its own gates' outcome."""

import importlib.util
import json
import pathlib
import struct

import numpy as np
import pytest

from app import district_lean

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_district_lean", _BACKEND / "scripts" / "build_district_lean.py")
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)

DATA = json.loads((_BACKEND / "app" / "data" / "district_lean.json").read_text())
CORRECTIONS = json.loads((_BACKEND / "app" / "data" / "district_lean_corrections.json").read_text())
COOK = json.loads((_BACKEND / "app" / "data" / "district_pvi.json").read_text())["congresses"]


# --- the script's pieces ------------------------------------------------------


def _shp(rings: list[list[tuple[float, float]]]) -> bytes:
    """One polygon record in a shapefile, as read_shp reads it."""
    pts = [p for r in rings for p in r]
    parts, n = [], 0
    for r in rings:
        parts.append(n)
        n += len(r)
    body = struct.pack("<i4d", 5, 0, 0, 0, 0) + struct.pack("<ii", len(rings), len(pts))
    body += struct.pack(f"<{len(parts)}i", *parts) + struct.pack(f"<{2 * len(pts)}d", *[c for p in pts for c in p])
    return b"\0" * 100 + struct.pack(">ii", 1, len(body) // 2) + body + struct.pack(">ii", 2, 2) + struct.pack("<i", 0)


def test_read_shp_reads_rings_and_null_shapes():
    square = [(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)]
    hole = [(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]
    shapes = bl.read_shp(_shp([square, hole]))
    assert len(shapes) == 2 and shapes[1] is None
    assert [len(r) for r in shapes[0]] == [5, 5]


def test_read_dbf_strips_padding_and_nuls():
    fields = [(b"G20PREDBID", b"N", 10), (b"NAME", b"C", 6)]
    header_len = 32 + 32 * len(fields) + 1
    head = struct.pack("<4xIHH20x", 1, header_len, 1 + 16)
    for name, kind, width in fields:
        head += name.ljust(11, b"\0") + kind + b"\0" * 4 + bytes([width]) + b"\0" * 15
    raw = head + b"\r" + b" " + b"12\0\0\0\0\0\0\0\0" + b"Ada   "
    assert bl.read_dbf(raw, ("G20PREDBID", "NAME"))[0] | {"_deleted": False} == {
        "G20PREDBID": "12", "NAME": "Ada", "_deleted": False}
    with pytest.raises(ValueError):
        bl.read_dbf(raw, ("G20PRERTRU",))


def test_locate_honours_holes_and_first_match():
    square = np.array([(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)], float)
    hole = np.array([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)], float)
    inner = np.array([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)], float)
    points = np.array([(0.5, 0.5), (2, 2), (9, 9)])
    owner = bl.locate(points, [[square, hole], None, [inner]])
    assert owner.tolist() == [0, 2, -1]


def test_lean_is_cooks_formula_with_r_positive():
    # A district 10 points more Republican than the nation in both years.
    assert bl.lean((40, 60), (40, 60), 0.5, 0.5) == pytest.approx(10.0)
    # 2024 counts three times 2020: +8 in 2024 only is R+6.
    assert bl.lean((50, 50), (42, 58), 0.5, 0.5) == pytest.approx(6.0)


def test_mode_rows_collapse_to_the_total_when_both_exist():
    assert bl._collapse({"TOTAL": {"D": 5}, "EARLY": {"D": 2}, "DAY": {"D": 3}}) == {"D": 5}
    assert bl._collapse({"EARLY": {"D": 2}, "DAY": {"D": 3}}) == {"D": 5}


@pytest.mark.parametrize("precinct,expected", [
    ("Hopewell Boro 2", "Hopewell Boro"),
    ("Hopewell Twp 4", "Hopewell Twp."),
    ("East Windsor 1", "East Windsor Twp."),
    ("Borough of Alpha District 1", "Alpha Boro"),
    ("Town of Phillipsburg District 3", "Phillpsburg Town"),  # the official table's own spelling
    ("Trenton West Provisional", "Trenton, City of"),
    ("Nowhere 1", None),
])
def test_precincts_match_their_official_municipality(precinct, expected):
    table = ["Hopewell Boro", "Hopewell Twp.", "East Windsor Twp.", "West Windsor Twp.", "Alpha Boro",
             "Phillpsburg Town", "Trenton, City of"]
    assert bl.match_municipality(precinct, table) == expected


def test_allocation_exact_multi_unopposed_and_county_wide():
    seats = {"1", "2", "3"}
    county_20 = {("A", "1"): [60, 40], ("A", "2"): [40, 60], ("B", "3"): [50, 50], ("B", "2"): [10, 10]}
    units = [
        bl.Unit("A", [60, 40], {"001": 90}),            # exact, district 1
        bl.Unit("A", [40, 60], {"002": 95}),            # exact, district 2
        bl.Unit("A", [100, 100], {"001": 50, "002": 150}),  # spans both: split by its House votes
        bl.Unit("B", [30, 20], {"002": 40}),            # exact, district 2 in county B
        bl.Unit("B", [70, 30], {}),                     # no House contest: B's unlabelled seat, 3
        bl.Unit("A", [10, 10], spread=True),            # county-wide row
    ]
    d24, cells, fine, stats = bl.allocate_2024(units, seats, county_20)
    assert sum(d24["3"]) == pytest.approx(100) and d24["3"] == pytest.approx([70, 30])
    total = [sum(v[k] for v in d24.values()) for k in (0, 1)]
    assert total == pytest.approx([310, 260])
    # The multi-district unit puts three times the turnout in district 2.
    assert sum(d24["2"]) > sum(d24["1"])
    assert stats["unlabelled"] == 100 and stats["countyWide"] == 20
    assert cells[("B", "3")] == pytest.approx([70, 30])


def test_swing_transfer_is_exact_for_a_whole_cell_and_keeps_cell_totals():
    county = ["A", "A", "A", "A"]
    b119 = ["1", "1", "1", "1"]
    b120 = ["1", "1", "2", "2"]
    sld = ["9", "9", "8", "8"]
    v20 = np.array([[10, 10], [10, 10], [30, 10], [30, 10]], float)
    pop = np.ones(4)
    cell24 = {("A", "1"): [100.0, 60.0]}
    fine24 = {("A", "1", "9"): [20.0, 40.0], ("A", "1", "8"): [80.0, 20.0]}
    out, stats = bl.swing_transfer(county, b119, b120, sld, v20, pop, cell24, fine24)
    assert out["1"] == pytest.approx([20, 40]) and out["2"] == pytest.approx([80, 20])
    assert stats["blocksInFineCells"] == 4


# --- the shipped file ---------------------------------------------------------


@pytest.mark.parametrize("congress", ["119", "120"])
def test_every_district_of_both_maps_once(congress):
    unavailable = DATA["unavailable"][congress]
    assert set(DATA["congresses"][congress]["districts"]) | set(unavailable) == set(COOK[congress]["districts"])


@pytest.mark.parametrize("congress", ["119", "120"])
def test_validation_against_cook_as_recorded_by_the_build(congress):
    v = DATA["validation"]["againstCook"][congress]
    assert v["districts"] + len(DATA["unavailable"][congress]) == 435
    assert v["meanAbsDiff"] < 0.5 and v["r"] > 0.999
    ours = DATA["congresses"][congress]["districts"]
    worst = max(abs(ours[k] - COOK[congress]["districts"][k]) for k in ours)
    assert worst == pytest.approx(v["maxAbsDiff"], abs=0.06)  # the file rounds to one decimal


def test_provenance_names_open_inputs_and_no_share_alike_source():
    text = json.dumps(DATA["inputs"]) + DATA["_source"]
    assert "CC BY 4.0" in text and "CC0" in text
    assert not any("SA" in i["license"] for i in DATA["inputs"])
    assert "tonmcg" not in json.dumps(DATA) and "Dave" not in json.dumps(DATA)


def test_every_correction_cites_an_official_source():
    sources = CORRECTIONS["sources"]
    for fix in CORRECTIONS["fixes"]:
        assert fix["why"], fix
        assert fix["official"] in sources or fix.get("official_url", "").startswith("https://"), fix
    for src in sources.values():
        assert src["url"].startswith("https://")


# --- the loader ---------------------------------------------------------------


def test_loader_serves_each_congress_and_nothing_for_an_unbuilt_one():
    assert district_lean.computed_lean(119) == DATA["congresses"]["119"]["districts"]
    assert district_lean.computed_lean(121) == {}
    assert district_lean.computed_lean(None) == {}
    assert district_lean.lean_for("WY", 0, 120) == DATA["congresses"]["120"]["districts"]["WY-0"]
    assert district_lean.meta()["href"] == "/about/data#computed-lean"


def test_texas_is_the_independent_cross_check_and_california_is_labelled_not():
    checks = DATA["validation"]["crossChecks"]
    assert all(v["independent"] for k, v in checks.items() if k.startswith("TX-"))
    assert checks["CA-119-2024"]["independent"] is False  # also CA's correction source
    assert checks["TX-119-2024"]["maxAbsDiffPts"] <= 0.1
