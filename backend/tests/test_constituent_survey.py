"""CES constituent approval: the estimator (scripts/fetch_ces_approval.py)
and the join to member records (services/constituent_survey.py)."""

import importlib.util
import pathlib

import pytest

from app.services import constituent_survey
from app.time_utils import utcnow

_SPEC = importlib.util.spec_from_file_location(
    "fetch_ces_approval",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch_ces_approval.py",
)
ces = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ces)


def _row(state_fips="23", pid3="1", house=None, sen1=None, weight="1.0", cd="2"):
    row = {"inputstate": state_fips, "pid3": pid3, "commonweight": weight, "cdid118": cd}
    if house:
        row.update({"CurrentHouseName": house[0], "CurrentHouseParty": house[1], "CC24_312f": house[2]})
    if sen1:
        row.update({"CurrentSen1Name": sen1[0], "CurrentSen1Party": sen1[1], "CC24_312g": sen1[2]})
    return row


class TestEstimator:
    def test_weighted_approval_among_those_with_an_opinion(self):
        rows = [
            _row(sen1=("Susan Collins", "Republican", "1"), weight="2.0"),
            _row(sen1=("Susan Collins", "Republican", "4"), weight="1.0"),
            _row(sen1=("Susan Collins", "Republican", "5"), weight="9.0"),  # not sure
        ]
        cells = ces.tally(rows)
        est = ces._estimate(cells[("ME", "senate", "", "Susan Collins", "R")]["D"])
        assert est["rate"] == pytest.approx(2 / 3)
        assert est["n"] == 2 and est["not_sure"] == 1
        # Kish: (2 + 1)^2 / (4 + 1)
        assert est["n_eff"] == pytest.approx(9 / 5)

    def test_state_comes_from_the_fips_code(self):
        cells = ces.tally([_row(state_fips="6", house=("Nancy Pelosi", "Democratic", "2"), cd="11")])
        assert ("CA", "house", "11", "Nancy Pelosi", "D") in cells

    def _members(self, rates, n=100):
        """Members of one cell with the given approval rates."""
        return [
            {"chamber": "house", "member_party": "D", "by_party": {"D": {"rate": r, "n": n, "n_eff": n}}}
            for r in rates
        ]

    def test_a_cell_whose_members_differ_is_shrunk_by_the_measured_spread(self):
        prior = ces.priors(self._members([0.5, 0.7, 0.9] * 5))
        p = prior["house/D/D"]
        assert p["mu"] == pytest.approx(0.7, abs=1e-6)
        assert p["k"] is not None and p["k"] > 0

    def test_a_cell_whose_members_cant_be_told_apart_is_not_measurable(self):
        # Every member at the same true rate: the spread is all sampling noise.
        prior = ces.priors(self._members([0.5] * 15))
        assert prior["house/D/D"]["k"] is None

    def test_unmeasurable_cells_get_no_per_member_figure(self):
        rows = []
        for i in range(12):
            name = f"Member {chr(65 + i)}"
            rows += [_row(house=(name, "Democratic", "1"), pid3="2", cd=str(i + 1)) for _ in range(3)]
            rows += [_row(house=(name, "Democratic", "3"), pid3="2", cd=str(i + 1)) for _ in range(3)]
        built = ces.build(ces.tally(rows))
        assert all(m["by_party"]["R"]["shrunk"] is None for m in built["members"])
        assert all(m["by_party"]["R"]["rate"] == 0.5 for m in built["members"])


@pytest.fixture
def survey(monkeypatch):
    def member(state, chamber, name, party, district=None, d=0.2, r=0.6):
        return {
            "state": state, "chamber": chamber, "district": district, "name": name, "member_party": party,
            "by_party": {
                "D": {"n": 88, "rate": d, "shrunk": d, "n_eff": 60.0, "not_sure": 3},
                "R": {"n": 72, "rate": r, "shrunk": r, "n_eff": 50.0, "not_sure": 2},
                "I": {"n": 12, "rate": 0.5, "shrunk": None, "n_eff": 8.0, "not_sure": 1},
            },
        }

    data = {
        "survey": "CES 2024", "fielded": "2024-10/2024-11", "fielded_year": utcnow().year - 2,
        "members": [
            member("ME", "senate", "Susan Collins", "R"),
            member("IL", "senate", "Dick Durbin", "D"),
            member("AZ", "house", "Raul Grijalva", "D", "7"),
            member("GA", "house", "Buddy Carter", "R", "1"),
            member("TX", "house", "Al Green", "D", "9"),
            member("TX", "house", "Mark Green", "R", "7"),
            member("TN", "house", "Mark Green", "R", "7"),
        ],
    }
    monkeypatch.setattr(constituent_survey, "_survey_cache", data)
    return data


class TestJoin:
    def test_a_member_is_found_by_state_chamber_and_name(self, survey):
        got = constituent_survey.constituent_approval("senate", "ME", "Susan M. Collins", "R", 28)
        assert got["surveyed_as"] == "Susan Collins"
        assert [g["party"] for g in got["by_party"]] == ["D", "R", "I"]
        assert got["by_party"][0]["approve"] == 0.2

    def test_an_unmeasurable_group_carries_no_figure(self, survey):
        got = constituent_survey.constituent_approval("senate", "ME", "Susan Collins", "R", 28)
        assert got["by_party"][2]["approve"] is None and got["by_party"][2]["respondents"] == 12

    def test_a_quoted_nickname_counts_as_the_first_name(self, survey):
        got = constituent_survey.constituent_approval("house", "GA", 'Earl L. "Buddy" Carter', "R", 11, 1)
        assert got["surveyed_as"] == "Buddy Carter"

    def test_a_first_name_spelled_differently_matches_a_unique_same_party_surname(self, survey):
        got = constituent_survey.constituent_approval("senate", "IL", "Richard J. Durbin", "D", 28)
        assert got["surveyed_as"] == "Dick Durbin"

    def test_a_namesake_successor_does_not_inherit_the_reading(self, survey):
        # Adelita Grijalva took the seat after the survey was fielded.
        assert constituent_survey.constituent_approval("house", "AZ", "Adelita S. Grijalva", "D", 1, 7) is None

    def test_same_surname_in_the_state_is_told_apart_by_party_and_district(self, survey):
        assert constituent_survey.constituent_approval("house", "TX", "Al Green", "D", 20, 9)["surveyed_as"] == "Al Green"
        assert constituent_survey.constituent_approval("house", "TX", "Mark Green", "R", 8, 7)["surveyed_as"] == "Mark Green"

    def test_another_chamber_or_state_is_never_matched(self, survey):
        assert constituent_survey.constituent_approval("house", "ME", "Susan Collins", "R", 28, 2) is None
        assert constituent_survey.constituent_approval("senate", "NH", "Susan Collins", "R", 28) is None

    def test_no_survey_data_means_no_reading(self, monkeypatch):
        monkeypatch.setattr(constituent_survey, "_survey_cache", {})
        assert constituent_survey.constituent_approval("senate", "ME", "Susan Collins", "R", 28) is None


def test_the_bundled_data_joins_to_its_own_members():
    """The checked-in file is well formed: every entry's own name finds it."""
    constituent_survey._survey_cache = None
    data = constituent_survey._survey()
    assert data["members"] and data["fielded_year"]
    years = utcnow().year - data["fielded_year"]
    for m in data["members"][:50]:
        district = int(m["district"]) if m.get("district") else None
        got = constituent_survey.constituent_approval(m["chamber"], m["state"], m["name"], m["member_party"], years, district)
        assert got is not None and got["surveyed_as"] == m["name"], m["name"]
