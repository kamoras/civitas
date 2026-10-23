"""The generators the data refresh runs find their sources by rule and read
them by their own headers and wording (app/data_refresh.py). Offline: tiny
synthetic workbooks, questionnaires and listings."""

import importlib.util
import io
import zipfile
from xml.sax.saxutils import escape
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from app.acceptance import held_out_beats_in_use

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _col(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell(ref: str, value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _book(sheets: dict[str, list[list]]) -> bytes:
    """A real .xlsx with several named sheets, built with the standard library."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        entries, rels = [], []
        for n, (title, rows) in enumerate(sheets.items(), start=1):
            entries.append(f'<sheet name="{escape(title)}" sheetId="{n}" r:id="rId{n}"/>')
            rels.append(f'<Relationship Id="rId{n}" Type="{rel}/worksheet" Target="worksheets/sheet{n}.xml"/>')
            body = "".join(f'<row r="{r + 1}">' + "".join(_cell(f"{_col(c)}{r + 1}", v) for c, v in enumerate(row))
                           + "</row>" for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{n}.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="{rel}"><sheets>{"".join(entries)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + "</Relationships>")
    return buf.getvalue()


def test_egrid_reads_the_us_total_output_co2e_rate_by_its_headers():
    egrid = _script("fetch_egrid")
    book = _book({
        "Contents": [["eGRID Summary Tables 2031"]],
        "Table 1": [
            ["1. Subregion Output Emission Rates (eGRID2031)"],
            ["acronym", "name", "Total output emission rates", "CH₄", "N₂O", "CO₂e", "Non-baseload", "CO₂e"],
            ["acronym", "name", "CO₂", "CH₄", "N₂O", "CO₂e", "CO₂", "CO₂e"],
            ["AKGD", "ASCC Alaska Grid", 899.6, 0.08, 0.01, 905.1, 1077.5, 1080.0],
            ["U.S.", None, 650.0, 0.05, 0.007, 652.4, 1100.0, 1105.0],
        ],
    })
    assert egrid.us_rate(egrid.workbook_sheets(book)) == (652.4, 2031)
    page = '<a href="/system/files/documents/2032-06/summary_tables_rev1.xlsx">eGRID 2031 Summary Tables (xlsx)</a>'
    assert egrid.workbook_url(page) == "https://www.epa.gov/system/files/documents/2032-06/summary_tables_rev1.xlsx"


def test_maddison_release_is_the_newest_by_name_and_its_columns_are_read_by_header():
    gdp = _script("fetch_peer_gdp")
    items = [{"name": "Maddison Project Database 2023", "global_id": "doi:a"},
             {"name": "Maddison Project Database 2030", "global_id": "doi:b"},
             {"name": "Replication Data for Chapter 5", "global_id": "doi:c"}]
    assert gdp.newest_release(items) == (2030, "doi:b")
    book = _book({"Notes": [["About"], ["x"]],
                  "Full data": [["year", "countrycode", "country", "pop", "gdppc"],
                                [1950, "USA", "United States", 150, 15000.0], [1950, "FRA", "France", 40, 8000.0],
                                [1950, "XXX", "Elsewhere", 1, 100.0]]})
    assert list(gdp.per_capita_rows(book, {"USA", "FRA"})) == [("USA", 1950, 15000.0), ("FRA", 1950, 8000.0)]
    version = {"versionNumber": 2, "versionMinorNumber": 1, "files": [
        {"dataFile": {"id": 7, "filename": "mpd2030.xlsx"}}, {"dataFile": {"id": 8, "filename": "mpd2030.dta"}}],
        "metadataBlocks": {"citation": {"fields": [{"typeName": "publication", "value": [
            {"publicationCitation": {"value": "Bolt and van Zanden (2031)"}}]}]}}}
    assert gdp.release_file(version) == (7, "mpd2030.xlsx", "2.1", "Bolt and van Zanden (2031)")


def test_ces_surveys_are_found_by_name_and_their_items_mapped_by_wording():
    ces = _script("fetch_ces_approval")
    assert ces.survey_year("CES Common Content, 2025") == 2025
    assert ces.survey_year("2017 CCES Common Content") == 2017
    assert ces.survey_year("Cumulative CES Common Content") is None
    assert ces.survey_year("2020 - 2024 Cooperative Election Study Panel Survey") is None
    old = ces.wordings(["Do you approve of the way each is doing their job?", "CC24_312a", "President Biden",
                        "CC24_312f- Show if CurrentHouseName", "$CurrentHouseName",
                        "CC24_312g- Show if CurrentSen1Name", "$CurrentSen1Name",
                        "Generally speaking, do you think of yourself as a ...?", "CC24_400 party"])
    # A new survey renumbers and reorders the grid.
    new = ces.wordings(["Do you approve of the way each is doing their job?",
                        "CC26_318g- Show if $CurrentSen1Name", "CurrentSen1Name",
                        "CC26_318a President Smith", "CC26_318f- Show if $CurrentHouseName", "CurrentHouseName",
                        "Generally speaking, do you think of yourself as a ...?", "CC26_401 party"])

    def encode(texts):  # a stand-in model: letter counts, unit length
        vecs = np.array([[t.count(c) for c in "abcdefghijklmnopqrstuvwxyz0123456789$"] for t in texts], float)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    assert ces.map_items(old, new, ["CC24_312f", "CC24_312g"], encode) == {"CC24_312f": "CC26_318f",
                                                                          "CC24_312g": "CC26_318g"}
    assert "not in the questionnaire" in ces.map_items(old, new, ["CC24_999"], encode)
    assert ces.district_column(["cdid118", "cdid119", "cdid120"], datetime(2025, 11, 1)) == "cdid119"
    first, last = ces.fielded([{"starttime": "2.079984094E12"}, {"starttime": "2025-10-02T10:00:00"}])
    assert (first.year, first.month, last.month) == (2025, 10, 11)
    rows = [{"Q": "1"}, {"Q": "3"}, {"Q": "5"}, {"Q": ""}]
    assert ces.plausible(rows, [("Q",)]) is None
    assert "approve" in ces.plausible([{"Q": "1"}] * 20 + [{"Q": "3"}], [("Q",)])


def test_the_forecast_priors_are_kept_unless_they_win_on_the_forward_check():
    fit = _script("fit_forecast_model")
    win = fit.priors_comparison({"senate": ([-3.0, -3.1, -2.9, -3.2], [-2.0, -2.1, -2.2, -1.9]),
                                 "house": ([-3.0] * 5, [-2.5, -2.6, -2.4, -2.5, -2.6])})
    assert held_out_beats_in_use({"_comparison": win}, {}) is None
    noise = fit.priors_comparison({"senate": ([-3.0, -3.1, -2.9], [-3.1, -3.0, -2.95]),
                                   "house": ([-3.0, -3.0], [-3.0, -3.0])})
    assert "within noise" in held_out_beats_in_use({"_comparison": noise}, {})


def test_cel_is_read_for_the_newest_congress_both_chambers_have_coded():
    commem = _script("calibrate_commemorative")
    page = ('<a href="https://x.org/2025/06/CELHouse93to118-REVISED.xlsx">h</a>'
            '<a href="https://x.org/2025/06/CELHouse93to118Reduced.dta">r</a>'
            '<a href="https://x.org/2027/05/CELHouse93to119.xlsx">h</a>'
            '<a href="https://x.org/2027/03/CELSenate93to119.xls">s</a>')
    congress, files = commem.cel_sources(page)
    assert congress == 119 and files["senate"].endswith("CELSenate93to119.xls")
    assert files["house"].endswith("CELHouse93to119.xlsx")


def test_the_commemorative_comparison_is_paired_on_held_out_senators():
    commem = _script("calibrate_commemorative")
    rng = np.random.default_rng(1)
    rows, by = [], {}
    for i in range(40):
        bills = [{"stage": int(rng.integers(1, 5)), "margin": float(rng.uniform(0, 0.6))} for _ in range(6)]
        by[f"m{i}"] = bills
        # V&W's LES: substantive bills only (a commemorative bill, margin over 0.3, counts little).
        les = sum(b["stage"] * (0.2 if b["margin"] > 0.3 else 1.0) for b in bills)
        rows.append({"bio": f"m{i}", "majority": float(i % 2), "les": les})
    members = rows
    c = commem.held_out_comparison(members, by, refit=0.3, in_use=0.55)
    assert c["delta"] > c["se"] > 0 and set(c["subgroups"]) == {"majority 0.0", "majority 1.0"}
    same = commem.held_out_comparison(members, by, refit=0.3, in_use=0.3)
    assert same["delta"] == 0


def test_the_commemorative_rank_correlation_is_pandas_rank_corr():
    """The port's Spearman (scipy, ties averaged, missing pairs left out) is
    the research script's pandas `.rank().corr()`."""
    pd = pytest.importorskip("pandas")
    commem = _script("calibrate_commemorative")
    les = [3.0, 1.0, 2.0, 2.0, 5.0, None, 4.0]
    built = [0.1, 0.4, 0.2, 0.2, 0.9, 0.3, 0.5]
    members = [{"les": v} for v in les]
    expected = pd.Series(built).rank().corr(pd.Series(les, dtype=float).rank())
    assert commem._rank_corr(members, built) == pytest.approx(expected, abs=1e-12)
