"""app/census_lines.py's finders against a fake www2.census.gov, and the
data refresh's checks built on them (re-released block files, a newer
Gazetteer or state legislative file): every file found by rule, an outage
never read as "not published"."""

import io
import urllib.error
from datetime import date, datetime

import pytest

from app import census_lines as cl
from app import data_refresh as dr

RDO = cl.RDO
GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"


def _row(name: str, modified: str = "2026-08-31 12:00") -> str:
    return f'<tr><td><a href="{name}">{name}</a></td><td align="right">{modified}  </td></tr>'


def _page(*rows: str) -> str:
    return '<html><link href="css/x.css"><a href="https://www.census.gov">x</a>' + "".join(rows) + "</html>"


SITE = {
    f"{RDO}/": _page(_row("2025/"), _row("2027/"), _row("2023/")),
    f"{RDO}/2023/": _page(_row("2022-state-legislative-bef/")),
    f"{RDO}/2023/2022-state-legislative-bef/": _page(_row("sldu22.zip", "2023-01-01 10:00")),
    f"{RDO}/2025/": _page(_row("119-congressional-district-befs/"), _row("2024-state-legislative-bef/")),
    f"{RDO}/2025/119-congressional-district-befs/": _page(_row("cd119.zip", "2024-07-29 13:18")),
    f"{RDO}/2025/2024-state-legislative-bef/": _page(_row("sldu24.zip", "2024-07-30 07:54")),
    f"{RDO}/2027/": _page(_row("120-congressional-district-befs/"), _row("2026-state-legislative-bef/")),
    # The site's templated pages name entries by href alone, with no date.
    f"{RDO}/2027/120-congressional-district-befs/": _page('<a href="CD120_BlockSplits.pdf">x</a>',
                                                          '<a href="cd120.zip">cd120.zip</a>'),
    f"{RDO}/2027/2026-state-legislative-bef/": _page(_row("sldu26.zip"), _row("sldl26.zip")),
    GAZ: _page(_row("2025_Gazetteer/"), _row("2026_Gazetteer/")),
    f"{GAZ}2026_Gazetteer/": _page(_row("2026_Gaz_counties_national.zip")),
    f"{GAZ}2025_Gazetteer/": _page(_row("2025_Gaz_place_national.zip"), _row("2025_Gaz_cousubs_national.zip")),
}


@pytest.fixture
def web(monkeypatch):
    served = set()

    def get(url, timeout=120, attempts=3):
        if url not in SITE:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return SITE[url].encode()

    monkeypatch.setattr(cl, "_get", get)
    monkeypatch.setattr(cl, "exists", lambda url: url in served)
    return served


def test_a_listing_reads_dated_rows_and_bare_links_and_skips_page_furniture(web):
    page = cl.listing(f"{RDO}/2027/120-congressional-district-befs/")
    assert page == {"CD120_BlockSplits.pdf": "", "cd120.zip": ""}
    assert cl.listing(f"{RDO}/2025/119-congressional-district-befs/") == {"cd119.zip": "2024-07-29 13:18"}


def test_each_congress_s_block_file_is_found_where_it_convenes_or_anywhere(web):
    assert cl.find_congress_bef(119) == (f"{RDO}/2025/119-congressional-district-befs/cd119.zip", "2024-07-29 13:18")
    assert cl.find_congress_bef(120) == (f"{RDO}/2027/120-congressional-district-befs/cd120.zip", "")
    assert cl.find_congress_bef(121) is None  # not published yet


def test_the_newest_state_legislative_file_for_a_cycle_or_before(web):
    assert cl.find_sld_bef(2026, "sldu") == (f"{RDO}/2027/2026-state-legislative-bef/sldu26.zip",
                                             "2026-08-31 12:00", 2026)
    assert cl.find_sld_bef(2028, "sldl")[2] == 2026  # 2028's not out: the newest before it
    assert cl.find_sld_bef(2024, "sldu")[2] == 2024
    assert cl.find_sld_bef(2021, "sldu") is None


def test_the_newest_gazetteer_that_has_the_file(web):
    # 2026's directory holds no place file yet: 2025's is the newest that does.
    assert cl.newest_gazetteer("place") == f"{GAZ}2025_Gazetteer/2025_Gaz_place_national.zip"
    assert cl.newest_gazetteer("cousub").endswith("2025_Gaz_cousubs_national.zip")
    SITE[f"{GAZ}2026_Gazetteer/"] += _row("2026_Gaz_place_national.zip")
    try:
        assert cl.newest_gazetteer("place").endswith("2026_Gaz_place_national.zip")
    finally:
        SITE[f"{GAZ}2026_Gazetteer/"] = _page(_row("2026_Gaz_counties_national.zip"))


def test_tiger_and_cartographic_shapes_by_vintage_rule(web, monkeypatch):
    t = cl.TIGER
    web.update({f"{t}/TIGER2025/CD/tl_2025_01_cd119.zip", f"{t}/TIGER2026/CD/tl_2026_01_cd120.zip",
                f"{t}/GENZ2024/shp/cb_2024_us_cd119_500k.zip"})
    assert cl.tiger_cd_url(119, "01").endswith("TIGER2025/CD/tl_2025_01_cd119.zip")
    assert cl.tiger_cd_url(120, "01").endswith("TIGER2026/CD/tl_2026_01_cd120.zip")  # not TIGER2027
    assert cl.tiger_cd_url(121, "01") is None
    assert cl.cartographic_cd_url(119).endswith("cb_2024_us_cd119_500k.zip")
    assert cl.cartographic_cd_url(120) is None
    SITE[t + "/"] = _page(_row("TIGER2026/"), _row("GENZ2025/"), _row("TIGER2025/"))
    web.add(f"{t}/TIGER2025/PRISECROADS/tl_2025_01_prisecroads.zip")
    assert cl.tiger_years() == [2026, 2025]
    assert cl.newest(t + "/TIGER{y}/PRISECROADS/tl_{y}_01_prisecroads.zip").endswith("TIGER2025/PRISECROADS/"
                                                                                     "tl_2025_01_prisecroads.zip")


def test_a_dropped_connection_is_tried_again_and_a_4xx_is_an_answer(monkeypatch):
    calls = []

    class Body(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise ConnectionResetError("reset")
        if "missing" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return Body(b"ok")

    monkeypatch.setattr(cl.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    assert cl._get("https://x/a") == b"ok" and len(calls) == 2
    with pytest.raises(urllib.error.HTTPError):
        cl._get("https://x/missing")
    assert len(calls) == 3  # once: a 404 is not retried


def test_exists_reads_a_404_as_not_published_and_raises_on_an_outage(monkeypatch):
    def urlopen(req, timeout=0):
        code = 404 if "gone" in req.full_url else 503
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

    monkeypatch.setattr(cl.urllib.request, "urlopen", urlopen)
    assert cl.exists("https://x/gone") is False
    with pytest.raises(urllib.error.HTTPError):
        cl.exists("https://x/down")


# --- the refresh's checks ------------------------------------------------------


def _ctx(doc, last=None):
    return dr.Context("x.json", doc, date(2026, 10, 23), last)


def test_a_re_released_block_file_is_due_weekly(web):
    old = {"_inputs": {"bef": {"119": [f"{RDO}/2025/119-congressional-district-befs/cd119.zip", "2024-07-01 00:00"]}}}
    assert dr._rereleased([old], _ctx(old)) is True
    same = {"_inputs": {"bef": {"119": list(cl.find_congress_bef(119))}}}
    assert dr._rereleased([same], _ctx(same)) is False
    assert dr._rereleased([old], _ctx(old, last=datetime.now())) is False  # tried this week


def test_an_unreadable_listing_changes_nothing(monkeypatch):
    def down(congress):
        raise OSError("down")

    monkeypatch.setattr(dr.census_lines, "find_congress_bef", down)
    doc = {"_inputs": {"bef": {"119": ["u", "2024-07-01 00:00"]}}}
    assert dr._rereleased([doc], _ctx(doc)) is False
    monkeypatch.setattr(dr.census_lines, "newest_gazetteer", lambda kind: (_ for _ in ()).throw(OSError("down")))
    assert dr._new_gazetteer(_ctx({"_inputs": {"gazetteer": {"place": "x"}}})) is False


def test_a_newer_gazetteer_is_due_monthly(web):
    newest = cl.newest_gazetteer("place")
    assert dr._new_gazetteer(_ctx({"_inputs": {"gazetteer": {"place": "older.zip"}}})) is True
    assert dr._new_gazetteer(_ctx({"_inputs": {"gazetteer": {"place": newest}}})) is False
    assert dr._new_gazetteer(_ctx({})) is False


def test_a_newer_state_legislative_file_is_due(web, monkeypatch):
    monkeypatch.setattr(dr, "_election", lambda: type("E", (), {"cycle": 2026})())
    newest = cl.newest_gazetteer("place")
    doc = {"_inputs": {"sldu": [f"{RDO}/2025/2024-state-legislative-bef/sldu24.zip", "2024-07-30 07:54"],
                       "gazetteer": {"place": newest}}}
    assert dr._state_leg_due(_ctx(doc)) is True
    doc["_inputs"]["sldu"] = list(cl.find_sld_bef(2026, "sldu")[:2])
    assert dr._state_leg_due(_ctx(doc)) is False
    assert dr._state_leg_due(_ctx(doc, last=datetime.now())) is False


def test_the_state_legislative_and_overlap_gates():
    old = {"districts": {f"RI-lower-{i}": ["x"] for i in range(75)} | {"VT-upper-1": ["y"]}}
    assert dr._state_leg_gate(old, old) == []
    lost = {"districts": {k: v for k, v in old["districts"].items() if not k.startswith("VT")}}
    assert dr._state_leg_gate(lost, old)[0] == "states missing: ['VT']"
    assert dr._overlap_gate({"cycle": "2026", "states": {"TX": [["TX-1", "TX-1", 10]]}}, {}) == []
    assert dr._overlap_gate({"cycle": "2026", "states": {"TX": [["TX-1", "TX-1", 0]]}}, {}) == ["TX: no residents"]
    assert dr._overlap_gate({}, {}) == ["no cycle or no states"]


def test_the_places_gate_counts_seats_and_names(monkeypatch):
    monkeypatch.setattr(dr, "line_targets", lambda: (119, 119))
    table = {f"{chr(65 + i // 26)}{chr(65 + i % 26)}-{i}": ["Springfield", "Shelbyville"] for i in range(435)}
    old = {"congresses": {"119": table}}
    assert dr._places_gate(old, old) == []
    renamed = {"congresses": {"119": {k: ["Elsewhere"] for k in table}}}
    assert dr._places_gate(renamed, old) == ["the 119th Congress's places keep 0 of 870 names"]
    assert dr._places_gate({"congresses": {"120": table}}, old)[0] == "built for [120], not [119]"


def test_an_unreadable_table_is_no_table_never_a_guess(monkeypatch):
    from app import district_lean, district_places, redistricting
    from app.api import elections

    monkeypatch.setattr(dr, "read", lambda name: {})
    assert district_places.places_on_lines_of(119) is None
    assert district_places.counties_on_lines_of(119) is None
    assert redistricting.overlap_file() == {}
    assert district_lean.computed_lean(119) == {}
    assert elections._state_leg_towns() == {}
