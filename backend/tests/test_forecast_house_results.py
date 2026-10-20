"""The House fit's certified results: the FEC's Federal Elections workbook
and, for a year the FEC has not compiled, the Clerk of the House's
Statistics of the Presidential and Congressional Election
(scripts/fit_forecast_model.py). Lines are fed as clerk_lines gives them:
(left edge, text, footnote mark on the line)."""

import importlib.util
import pathlib
from datetime import date

import httpx
import pytest

from app import data_refresh as dr
from tests.xlsx_helpers import _workbook

BACKEND = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fit_forecast_model", BACKEND / "scripts" / "fit_forecast_model.py")
fit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fit)

NUM, NAME, FUSION = 50.9, 61.6, 77.6  # the edition's three indents


def _entry(x, text, votes, noted=False):
    return (x, f"{text} {'.' * 20} {votes}", noted)


def _state(name, house, recap, head=("Title of candidate Republican Democratic Total",)):
    return ([(270.0, name, False), (228.0, "FOR UNITED STATES REPRESENTATIVE", False), *house,
             (210.0, f"Recapitulation of Votes Cast in {name.title()}", False)]
            + [(50.0, h, False) for h in head] + [(38.0, r, False) for r in recap])


def _race(races, st, d):
    return next(r for r in races if (r["state"], r["district"]) == (st, d))


def test_a_fusion_nominees_lines_count_and_tallies_count_for_no_one():
    lines = _state("NEW YORK", [
        _entry(NUM, "1. Ann Roe, Republican", "200,000"),
        _entry(FUSION, "Conservative", "20,000"),
        _entry(NAME, "Bo Doe, Democrat", "190,000"),
        _entry(FUSION, "Working Families", "15,000"),
        _entry(NAME, "Blank", "9,000"),
        _entry(NAME, "Void", "1,000"),
    ], ["1st district ....... 200,000 190,000 ...... 435,000"])
    races, listed, left_out = fit.clerk_house(lines)
    assert listed == {("NY", 1)} and left_out == []
    # R 220,000 against D 205,000; the blank and void ballots are no one's.
    assert _race(races, "NY", 1)["margin"] == round(100 * 15_000 / 425_000, 3)
    assert _race(races, "NY", 1)["names"] == {"D": "Bo Doe", "R": "Ann Roe"}


def test_lines_that_disagree_with_the_recapitulation_stop_the_run():
    lines = _state("ALABAMA", [
        _entry(NUM, "1. Ann Roe, Republican", "200,000"),
        _entry(NAME, "Bo Doe, Democrat", "190,000"),
    ], ["1st district ....... 200,000 190,000 ...... 390,001"])
    with pytest.raises(SystemExit, match="AL-1's lines add up to 390,000; its recapitulation row says 390,001"):
        fit.clerk_house(lines)


def test_a_wide_recapitulation_takes_its_total_from_the_last_table():
    lines = _state("COLORADO", [
        _entry(NUM, "1. Ann Roe, Republican", "74,598"),
        _entry(NAME, "Bo Doe, Democrat", "264,606"),
        _entry(NAME, "Cy Poe, Green", "2,351"),
    ], ["1st district ...... 74,598 264,606 ....... 2,351",
        "Title of candidate Write-in (Independent) Total",
        "1st district ...... ............ 341,555"])
    races, _, _ = fit.clerk_house(lines)
    assert _race(races, "CO", 1)["margin"] == round(100 * (74_598 - 264_606) / (74_598 + 264_606), 3)


def test_a_footnoted_count_is_left_out_and_an_unopposed_seat_is_still_listed():
    lines = _state("LOUISIANA", [
        _entry(NUM, "5. Cy Poe, Democrat", "50,812"),
        _entry(NAME, "Ann Roe, Republican", "49,183", noted=True),  # a runoff's count
        _entry(NAME, "Di Coe, Republican", "30,124", noted=True),
        (NUM, f"6. Ed Loe, Republican {'.' * 20}", True),  # unopposed: no count
    ], ["5th district ....... 79,307 50,812 ...... 130,119", "6th district ....... ...... ......"])
    races, listed, left_out = fit.clerk_house(lines)
    assert races == [] and listed == {("LA", 5), ("LA", 6)}
    assert left_out == ["LA-5: a count carries a footnote (a runoff's count, a recount)"]


def test_a_ranked_choice_race_needs_its_last_round():
    first_choices = _state("ALASKA", [
        (NAME, "AT LARGE", False),
        _entry(NAME, "Ann Roe, Republican", "159,550"),
        _entry(NAME, "Bo Doe, Democrat", "152,828"),
        _entry(NAME, "Cy Poe, Independence", "13,010"),
    ], ["At large ....... 159,550 152,828 ...... 325,388"])
    races, listed, left_out = fit.clerk_house(first_choices, {"AK"})
    assert races == [] and listed == {("AK", 0)} and left_out[0].startswith("AK-0: ranked-choice")
    # Not a ranked-choice state: the plurality count is the result.
    assert fit.clerk_house(first_choices)[0][0]["district"] == 0
    last_round = _state("MAINE", [
        _entry(NUM, "2. Ann Roe, Republican", "194,445"),
        _entry(NAME, "Bo Doe, Democrat", "197,151"),
        _entry(NAME, "Exhausted Ballots", "11,340"),
    ], ["2d district ....... 194,445 197,151 ...... 402,936"])
    races, _, left_out = fit.clerk_house(last_round, {"ME"})
    assert left_out == [] and _race(races, "ME", 2)["margin"] < 0


def test_a_special_for_the_unexpired_term_is_not_the_general():
    lines = _state("TEXAS", [
        (200.0, "(For unexpired term ending January 3, 2025)", False),
        _entry(NUM, "18. Ann Roe, Democrat", "100"),
        (200.0, "(For full term beginning January 3, 2025)", False),
        _entry(NUM, "18. Bo Doe, Democrat", "300"),
        _entry(NAME, "Cy Poe, Republican", "100"),
    ], ["18th district ....... 100 300 ...... 400"])
    races, listed, _ = fit.clerk_house(lines)
    assert listed == {("TX", 18)} and _race(races, "TX", 18)["margin"] == -50.0


def test_two_general_listings_of_one_district_stop_the_run():
    lines = _state("OHIO", [
        _entry(NUM, "1. Ann Roe, Republican", "100"),
        _entry(NUM, "1. Bo Doe, Democrat", "100"),
    ], ["1st district ....... 100 100 ...... 200"])
    with pytest.raises(SystemExit, match="2 general-election listings"):
        fit.clerk_house(lines)


def _clerk_results(monkeypatch, on_ballot, lines=None):
    house = [(NAME, "AT LARGE", False), _entry(NAME, "Dusty Roe, Republican", "200"),
             _entry(NAME, "Sheryl Roe, Democrat", "100")]
    monkeypatch.setattr(fit, "clerk_lines",
                        lambda raw: _state("SOUTH DAKOTA", house, ["At large ....... 200 100 ...... 300"]))
    monkeypatch.setattr(fit, "ranked_choice_states", set)
    monkeypatch.setattr(fit, "fec_general_candidates", lambda year, cache: {("SD", 0): on_ballot})
    return fit.clerk_results(2024, b"", lines or {"SD-0": 1}, pathlib.Path("."))


def test_incumbency_is_the_fec_incumbents_own_party_nominee(monkeypatch):
    # Two nominees named Roe: the incumbent is the one of the FEC row's party.
    races, _ = _clerk_results(monkeypatch, [("R", "ROE", True), ("D", "ROE", False)])
    assert races[0]["inc"] == 1 and "names" not in races[0]


def test_an_incumbent_who_is_neither_nominee_stops_the_run(monkeypatch):
    with pytest.raises(SystemExit, match="incumbent is neither nominee"):
        _clerk_results(monkeypatch, [("R", "SMITH", True)])


def test_every_seat_of_the_lines_must_be_listed(monkeypatch):
    with pytest.raises(SystemExit, match=r"seats not listed \[\('SD', 1\)\]"):
        _clerk_results(monkeypatch, [], lines={"SD-0": 1, "SD-1": 1})


def test_a_misspelt_surname_still_matches_but_a_different_one_does_not():
    assert fit._same_name("HALVERSON", "Dana Halversen")
    assert fit._same_name("DANGELO", "Kim D’Angelo")
    assert not fit._same_name("SMITH", "Laurie Brackett")


def test_fec_party_cells_joining_codes_count_for_the_major_party(monkeypatch):
    """"D/IP" (fusion), "W(R)/R" (a write-in primary's nominee), "R*" and
    North Dakota's "DNL" are all major-party nominees; a candidate's ID
    repeated on an opponent's row does not merge the two."""
    head = ["STATE ABBREVIATION", "DISTRICT", "FEC ID#", "(I)", "CANDIDATE NAME (Last)", "CANDIDATE NAME", "PARTY",
            "GENERAL VOTES "]
    rows = [head,
            ["OR", "01", "H1", "(I)", "Doe", "Doe, Bo", "D/IP", "231198"],
            ["OR", "01", "H2", "", "Roe", "Roe, Ann", "W(R)/R", "116446"],
            ["TN", "07", "H3", "(I)", "Green", "Green, Al", "R*", "245188"],
            ["TN", "07", "H3", "", "Poe", "Poe, Cy", "DNL", "95839"],
            ["IN", "02-Full Term", "H4", "", "Loe", "Loe, Ed", "R", "125537"],
            ["IN", "02-Full Term", "H5", "", "Coe", "Coe, Di", "D", "62955"],
            ["IN", "02-Unexpired Term", "H4", "", "Loe", "Loe, Ed", "R", "118997"]]
    monkeypatch.setattr(fit, "_sheet_file", lambda raw, title: "sheet1")
    races = {(r["state"], r["district"]): r for r in fit.fec_house(_workbook(rows), "x")}
    assert races[("OR", 1)]["inc"] == -1 and races[("OR", 1)]["margin"] < 0
    assert races[("TN", 7)]["inc"] == 1 and races[("TN", 7)]["margin"] > 0
    assert races[("IN", 2)]["margin"] == round(100 * (125_537 - 62_955) / (125_537 + 62_955), 3)


def test_the_fits_refit_weekly_while_a_certified_general_is_missing(monkeypatch):
    docs = {"forecast_training.json": {"races": [{"year": 2024}]},
            "forecast_house_training.json": {"races": [{"year": 2022}]}}
    monkeypatch.setattr(dr, "read", lambda name: docs.get(name, {}))
    assert dr._fits_behind(date(2026, 10, 17))
    docs["forecast_house_training.json"] = {"races": [{"year": 2024}]}
    assert not dr._fits_behind(date(2026, 10, 17))


def _client_with(answers: list):
    """A client whose requests answer in turn from `answers` (an exception is raised)."""
    def handler(request):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)


def test_a_dropped_connection_is_retried_not_a_failed_refit(monkeypatch, tmp_path):
    answers = [httpx.RemoteProtocolError("Server disconnected without sending a response."),
               httpx.Response(200, content=b"PK cn24")]
    monkeypatch.setattr(fit, "make_async_client", _client_with(answers))
    assert fit.fetch("https://www.fec.gov/files/bulk-downloads/2024/cn24.zip", tmp_path / "cn24.zip") == b"PK cn24"
    assert answers == []


def test_a_source_down_through_every_retry_stops_the_run_with_nothing_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(fit, "make_async_client", _client_with([httpx.ConnectError("refused")] * 3))
    with pytest.raises(SystemExit, match="no answer after 3 attempts"):
        fit.fetch("https://www.fec.gov/files/bulk-downloads/2024/cn24.zip", tmp_path / "cn24.zip")
    assert not (tmp_path / "cn24.zip").exists()
