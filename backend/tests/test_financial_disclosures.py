"""Tests for the annual financial disclosure (asset holdings) ingest.

fd_common: value brackets and the two chambers' asset-type vocabularies.
house_fd: Schedule A parsed from word positions — against REAL
pdfplumber extract_words output (fixtures_house_fd_words.json, see its
_source) as well as small synthetic layouts for the edge cases.
senate_fd: the eFD Part 3 assets table.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import fd_common
from app.pipeline.fetch.fd_common import (
    HOUSE_ASSET_TYPE_CATEGORY,
    HOLDING_CATEGORIES,
    SENATE_ASSET_SUBTYPE_CATEGORY,
    SENATE_ASSET_TYPE_CATEGORY,
    house_category,
    parse_holding_value,
    senate_category,
    split_account,
    strip_house_code,
)
from app.pipeline.fetch.house_fd import filer_status, parse_schedule_a
from app.pipeline.fetch.senate_fd import is_annual_title, is_senator_filing, parse_assets_table

FIXTURE = json.loads((Path(__file__).parent / "fixtures_house_fd_words.json").read_text())


class TestParseHoldingValue:
    @pytest.mark.parametrize("text,expected", [
        ("$15,001 - $50,000", (15001.0, 50000.0)),
        ("$1 - $1,000", (1.0, 1000.0)),
        ("$1,000,001 - $5,000,000", (1000001.0, 5000000.0)),
        # Open-ended top brackets: floor only, encoded low == high.
        ("Over $50,000,000", (50000000.0, 50000000.0)),
        ("Spouse/DC Over $1,000,000", (1000000.0, 1000000.0)),
        # The Senate's lowest bracket is a real range with a $1,000 ceiling.
        ("None (or less than $1,001)", (0.0, 1000.0)),
        # House "None": nothing held at year end.
        ("None", (0.0, 0.0)),
        ("Undetermined", (None, None)),
        ("Unascertainable", (None, None)),
        ("--", (None, None)),
        ("", (None, None)),
        # A bare exact figure is not a bracket, and low == high is reserved
        # for the open-ended encoding — so it is not guessed at.
        ("$209,630.10", (None, None)),
    ])
    def test_shapes(self, text, expected):
        assert parse_holding_value(text) == expected


class TestCategories:
    def test_every_house_code_on_the_clerks_list_maps_to_a_real_category(self):
        # The Clerk's published list has 48 codes (2026-09).
        assert len(HOUSE_ASSET_TYPE_CATEGORY) == 48
        assert set(HOUSE_ASSET_TYPE_CATEGORY.values()) <= set(HOLDING_CATEGORIES)

    def test_every_senate_value_maps_to_a_real_category(self):
        assert set(SENATE_ASSET_TYPE_CATEGORY.values()) <= set(HOLDING_CATEGORIES)
        assert set(SENATE_ASSET_SUBTYPE_CATEGORY.values()) <= set(HOLDING_CATEGORIES)

    def test_house_codes(self):
        assert house_category("ST") == "STOCKS"
        assert house_category("ef") == "FUNDS"
        assert house_category("RP") == "REAL_ESTATE"
        assert house_category(None) == "OTHER"
        assert house_category("ZZ") == "OTHER"

    def test_senate_subtype_decides_corporate_securities(self):
        """"Corporate Securities" covers listed stock, private stock and
        bonds alike on the Senate form — the subtype decides."""
        assert senate_category("Corporate Securities", "Stock") == "STOCKS"
        assert senate_category("Corporate Securities", "Non-Public Stock") == "BUSINESS"
        assert senate_category("Corporate Securities", "Corporate Bond") == "BONDS"
        assert senate_category("Corporate Securities", "") == "OTHER"

    def test_senate_types(self):
        assert senate_category("Mutual Funds", "Exchange Traded Fund/Note") == "FUNDS"
        assert senate_category("Bank Deposit") == "CASH"
        assert senate_category("  real   estate ", "Residential") == "REAL_ESTATE"
        assert senate_category("Something New") == "OTHER"

    def test_every_category_has_a_label_and_color(self):
        for meta in HOLDING_CATEGORIES.values():
            assert meta["label"] and meta["color"].startswith("#")
        # Color follows the category: no two share one.
        colors = [m["color"] for m in HOLDING_CATEGORIES.values()]
        assert len(set(colors)) == len(colors)


class TestNameHelpers:
    def test_split_account(self):
        assert split_account("IRA ⇒ Apple Inc. (AAPL)") == ("IRA", "Apple Inc. (AAPL)")
        assert split_account("LLC ⇒ Sub ⇒ Land") == ("LLC ⇒ Sub", "Land")
        assert split_account("Bank of America") == (None, "Bank of America")

    def test_strip_house_code(self):
        assert strip_house_code("Apple Inc. (AAPL) [ST]") == ("Apple Inc. (AAPL)", "ST")
        # Some filers type the code straight onto the name.
        assert strip_house_code("Nationwide Fixed Fund[MF]") == ("Nationwide Fixed Fund", "MF")
        assert strip_house_code("No code here") == ("No code here", None)


class TestHouseScheduleAOnRealFilings:
    def test_small_report(self):
        holdings = parse_schedule_a(FIXTURE["small_report"])
        assert [(h.asset_type, h.value_low, h.value_high) for h in holdings] == [
            ("RP", 250001.0, 500000.0),  # the bracket wraps onto a second line
            ("BA", 1001.0, 15000.0),
            ("BA", 15001.0, 50000.0),
        ]
        assert holdings[1].asset_name == "Bank of America"
        assert holdings[0].category == "REAL_ESTATE"
        assert filer_status(FIXTURE["small_report"]) == "Member"

    def test_rows_continue_across_a_page_break_and_stop_at_schedule_b(self):
        holdings = parse_schedule_a(FIXTURE["page_break"])
        names = [h.asset_name for h in holdings]
        # This row's account and value are at the foot of one page, its
        # asset name at the head of the next.
        lucid = holdings[names.index("Lucid Group, Inc. (LCID)")]
        assert lucid.account == "Webull Financial LLC"
        assert lucid.value_text == "None"
        assert lucid.ticker == "LCID"
        # Schedule B (transactions) follows on the second page and lists
        # the same funds again — none of it may be read as a holding.
        assert len(holdings) == 18
        assert names[-1] == "Webull Financial LLC - Cash Account"
        assert all(h.asset_type for h in holdings)

    def test_nested_account_and_spouse_owner(self):
        holdings = parse_schedule_a(FIXTURE["page_break"])
        dacha = next(h for h in holdings if h.asset_type == "RP")
        assert dacha.owner == "spouse"
        assert dacha.account == "Shirleys Dacha LLC"


def _w(text, x0, top, size=9.0):
    return {"text": text, "x0": x0, "top": top, "size": size}


def _header(top):
    return [
        _w("Asset", 25, top, 9.4), _w("Owner", 259, top, 9.4), _w("Value", 298, top, 9.4),
        _w("of", 328, top, 9.4), _w("Asset", 340, top, 9.4), _w("Income", 380, top, 9.4),
        _w("Type(s)", 419, top, 9.4), _w("Income", 463, top, 9.4),
    ]


def _heading(letter, top):
    return [_w("S       ", 22, top, 12.0), _w(f"{letter}:", 92, top, 12.0)]


class TestHouseScheduleASynthetic:
    def test_detail_lines_are_not_assets(self):
        page = _heading("A", 10) + _header(30) + [
            _w("Acme", 25, 50), _w("Corp", 60, 50), _w("(ACME)", 90, 50), _w("[ST]", 130, 50),
            _w("SP", 258, 50), _w("$1,001", 297, 50), _w("-", 326, 50), _w("$15,000", 332, 50),
            _w("D          :", 25, 62, 8.5), _w("Inherited", 78, 62, 8.5), _w("asset", 116, 62, 8.5),
            _w("Farm", 25, 90), _w("[FA]", 60, 90), _w("JT", 258, 90), _w("Undetermined", 297, 90),
        ] + _heading("B", 120)
        holdings = parse_schedule_a([page])
        assert [(h.asset_name, h.owner, h.category) for h in holdings] == [
            ("Acme Corp (ACME)", "spouse", "STOCKS"),
            ("Farm", "joint", "REAL_ESTATE"),
        ]
        assert holdings[1].value_low is None

    def test_a_row_with_no_code_is_closed_by_the_next_rows_bracket(self):
        page = _header(30) + [
            _w("Mystery", 25, 50), _w("$1,001", 297, 50), _w("-", 326, 50), _w("$15,000", 332, 50),
            _w("Known", 25, 80), _w("[BA]", 60, 80), _w("$15,001", 297, 80), _w("-", 331, 80), _w("$50,000", 337, 80),
        ]
        holdings = parse_schedule_a([page])
        assert [(h.asset_name, h.category, h.value_high) for h in holdings] == [
            ("Mystery", "OTHER", 15000.0),
            ("Known", "CASH", 50000.0),
        ]

    def test_none_disclosed_is_an_empty_schedule_not_a_failure(self):
        page = _heading("A", 10) + [_w("None", 25, 30), _w("disclosed.", 50, 30)] + _heading("B", 60)
        assert parse_schedule_a([page]) == []

    def test_no_schedule_a_at_all_is_unreadable(self):
        assert parse_schedule_a([[_w("Something", 25, 10)]]) is None
        assert parse_schedule_a([]) is None


_SENATE_PAGE = """
<html><body>
<section><h3>Part 2. Earned and Non-Investment Income</h3>
<table><thead><tr><th></th><th>#</th><th>Who Was Paid</th></tr></thead>
<tbody><tr><td></td><td>1</td><td>Spouse</td></tr></tbody></table></section>
<section><h3>Part 3. Assets</h3>
<table><thead><tr><th></th><th>Asset</th><th>Asset Type</th><th>Owner</th><th>Value</th><th>Income Type</th><th>Income</th></tr></thead>
<tbody>
<tr><td>1</td><td><strong>Truist</strong><div class="muted">(Richmond, VA)</div></td><td>Bank Deposit</td><td>Spouse</td><td>$1,001 - $15,000</td><td>None</td><td></td></tr>
<tr><td>2</td><td><strong>CollegeInvest Fund</strong><div class="muted"><em>Institution:</em> CollegeInvest</div></td>
  <td>Education Savings Plans<div class="muted">529 College Savings Plan</div></td><td>Joint</td><td>--</td><td></td><td></td></tr>
<tr><td>2.1</td><td><strong>Income Portfolio</strong></td><td>Mutual Funds<div class="muted">Mutual Fund</div></td><td>Joint</td><td>$50,001 - $100,000</td><td></td><td></td></tr>
<tr><td>2.2</td><td><strong>Old Portfolio</strong></td><td>Mutual Funds<div class="muted">Mutual Fund</div></td><td>Joint</td><td>None (or less than $1,001)</td><td></td><td></td></tr>
<tr><td>3</td><td><strong>Apple Inc. (AAPL)</strong></td><td>Corporate Securities<div class="muted">Stock</div></td><td>Self</td><td>Over $50,000,000</td><td></td><td></td></tr>
<tr><td>4</td><td><strong>Family LLC</strong></td><td>Business Entity<div class="muted">Limited Liability Company (LLC)</div></td><td>Dependent Child</td><td>Unascertainable</td><td></td><td></td></tr>
</tbody></table></section>
</body></html>
"""


class TestSenateAssetsTable:
    def test_parses_leaves_not_containers(self):
        holdings = parse_assets_table(_SENATE_PAGE)
        names = [h.asset_name for h in holdings]
        # "CollegeInvest Fund" itemizes its underlying assets (2.1, 2.2), so
        # it is a container, not a holding.
        assert names == ["Truist", "Income Portfolio", "Old Portfolio", "Apple Inc. (AAPL)", "Family LLC"]
        income = holdings[1]
        assert income.account == "CollegeInvest Fund"
        assert income.category == "FUNDS"
        assert income.asset_type == "Mutual Funds — Mutual Fund"
        assert income.owner == "joint"

    def test_values_types_and_owners(self):
        by_name = {h.asset_name: h for h in parse_assets_table(_SENATE_PAGE)}
        assert (by_name["Truist"].category, by_name["Truist"].owner) == ("CASH", "spouse")
        assert (by_name["Old Portfolio"].value_low, by_name["Old Portfolio"].value_high) == (0.0, 1000.0)
        apple = by_name["Apple Inc. (AAPL)"]
        assert (apple.category, apple.ticker, apple.value_low, apple.value_high) == ("STOCKS", "AAPL", 5e7, 5e7)
        llc = by_name["Family LLC"]
        assert (llc.category, llc.value_low) == ("BUSINESS", None)
        assert llc.owner == "dependent"

    def test_page_without_part_3_is_unreadable(self):
        assert parse_assets_table("<html><body><p>Paper filing</p></body></html>") is None

    def test_part_3_without_a_table_lists_nothing(self):
        assert parse_assets_table("<section><h3>Part 3. Assets</h3><p>None disclosed.</p></section>") == []


class TestSenateSearchRowFilters:
    def test_is_senator_filing(self):
        assert is_senator_filing({"office": "Baldwin, Tammy (Senator)"})
        assert is_senator_filing({"office": "Senator"})
        assert not is_senator_filing({"office": "Candidate (Candidate)"})
        assert not is_senator_filing({"office": "Former Senator"})

    def test_is_annual_title(self):
        assert is_annual_title("Annual Report for CY 2025")
        assert is_annual_title("Annual Report for CY 2025 (Amendment 1)")
        assert is_annual_title("New Filer Report for 03/24/2026")
        assert not is_annual_title("Candidate Report")
        assert not is_annual_title("Termination Report")


def test_fd_common_has_no_name_based_classification():
    """Categories come only from the filer-declared asset type (AGENTS.md
    principle 1) — the module exposes no function that takes an asset name
    and returns a category."""
    import inspect

    for name, fn in inspect.getmembers(fd_common, inspect.isfunction):
        if fn.__module__ == fd_common.__name__ and "category" in name:
            assert "asset_name" not in inspect.signature(fn).parameters
