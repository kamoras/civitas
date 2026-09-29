"""Tests for the automated per-district Cook PVI ingestion
(app/pipeline/fetch/district_pvi.py).

Covers pure parse/gate logic (ported verbatim from the old manual
scripts/fetch_district_pvi.py) and refresh_district_pvi's never-write-
bad-data / never-raise persistence contract, mirroring
test_committee_leadership_fetch.py / test_voteview_fetch.py for the same
class of ingest. Network fetch is mocked — the wikitext shapes are pinned
by synthetic fixtures matching the real infobox field format.
"""

import json

from app.pipeline.analyze import score_calculator
from app.pipeline.fetch import district_pvi as dp


class TestParsePvi:
    def test_parses_r_lean(self):
        assert dp.parse_pvi("{{Infobox\n| cpvi = R+12\n}}") == 12

    def test_parses_d_lean(self):
        assert dp.parse_pvi("{{Infobox\n| cook_pvi = D+7\n}}") == -7

    def test_parses_even(self):
        assert dp.parse_pvi("{{Infobox\n| cpvi = EVEN\n}}") == 0

    def test_missing_field_returns_none(self):
        assert dp.parse_pvi("{{Infobox\n| party = Democratic\n}}") is None


# A made-up House: nothing in the module assumes an apportionment, so the
# tests don't use the real one either.
_APPORTIONMENT = {
    f"S{i:02d}": {"name": f"State {i}", "seats": n}
    for i, n in enumerate([1] * 6 + [2] * 6 + [3] * 3 + [4] * 6 + [5] * 2 + [6] * 3 + [7] * 2
                          + [8] * 6 + [9] * 4 + [10, 11, 12, 13, 14, 14, 15, 17, 17, 26, 28, 38, 52])
}
_SEATS = {st: a["seats"] for st, a in _APPORTIONMENT.items()}


def _pairs():
    pairs = []
    for st, n in sorted(_SEATS.items()):
        pairs.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
    return pairs


def _synthetic_result() -> dict[str, int]:
    """One district per seat, an even R/D split inside the gates' bounds."""
    return {f"{st}-{d}": 10 if i % 2 == 0 else -10 for i, (st, d) in enumerate(_pairs())}


class TestIngestionGates:
    def test_clean_synthetic_population_passes_gates(self):
        result = _synthetic_result()
        assert dp.ingestion_gates(result, _SEATS) == []

    def test_missing_districts_fails_coverage_gate(self):
        result = _synthetic_result()
        del result[next(iter(result))]
        failures = dp.ingestion_gates(result, _SEATS)
        assert any(f"expected {sum(_SEATS.values())}" in f for f in failures)

    def test_out_of_range_value_fails_gate(self):
        result = _synthetic_result()
        result[next(iter(result))] = 90
        failures = dp.ingestion_gates(result, _SEATS)
        assert any("plausible" in f for f in failures)

    def test_lopsided_lean_split_fails_gate(self):
        result = {k: 10 for k in _synthetic_result()}  # every seat R-leaning
        failures = dp.ingestion_gates(result, _SEATS)
        assert any("lean split" in f for f in failures)


class TestRefresh:
    def _patch_path(self, monkeypatch, tmp_path):
        path = tmp_path / "district_pvi.json"
        monkeypatch.setattr(dp, "_PVI_PATH", str(path))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)

        async def apportionment(client):
            return _APPORTIONMENT

        monkeypatch.setattr(dp, "fetch_house_apportionment", apportionment)
        return path

    async def test_successful_refresh_writes_file(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        # Point the downstream loader's persistent-volume dir at tmp_path
        # too, so this also verifies the refresh is immediately visible
        # without a process restart (same contract as write_member_ideal_points).
        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path))
        result = _synthetic_result()

        # title -> wikitext, built the same way refresh_district_pvi itself
        # maps titles to district keys, so the fake fetch is a pure lookup.
        title_to_wikitext = {}
        for st, d in _pairs():
            pvi = result[f"{st}-{d}"]
            sign = "R" if pvi > 0 else "D"
            title_to_wikitext[dp.district_title(_APPORTIONMENT[st]["name"], d)] = f"| cpvi = {sign}+{abs(pvi)}"

        async def fake_batch(titles, client):
            return {t: title_to_wikitext[t] for t in titles}

        monkeypatch.setattr(dp, "_fetch_batch", fake_batch)
        assert await dp.refresh_district_pvi() is True
        written = json.loads(path.read_text())
        assert len(written["districts"]) == sum(_SEATS.values())
        some = next(iter(written["districts"]))
        assert score_calculator._district_pvi()[some] == written["districts"][some]

    async def test_fetch_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        path.write_text(json.dumps({"districts": {"KEEP-0": 5}}))

        async def fake_batch(titles, client):
            return {}

        monkeypatch.setattr(dp, "_fetch_batch", fake_batch)
        assert await dp.refresh_district_pvi() is False
        assert json.loads(path.read_text())["districts"] == {"KEEP-0": 5}

    async def test_gate_failure_does_not_write(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)

        async def fake_batch(titles, client):
            # Only ever return one district's wikitext, well short of 435 —
            # the coverage gate must reject this rather than write a
            # partial table.
            first = titles[0]
            return {first: "| cpvi = R+5"}

        monkeypatch.setattr(dp, "_fetch_batch", fake_batch)
        assert await dp.refresh_district_pvi() is False
        assert not path.exists()

    async def test_unexpected_exception_never_raises(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)

        async def boom(titles, client):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(dp, "_fetch_batch", boom)
        assert await dp.refresh_district_pvi() is False


    async def test_no_apportionment_keeps_previous_data(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        path.write_text(json.dumps({"districts": {"KEEP-0": 5}}))

        async def unreadable(client):
            return {}

        monkeypatch.setattr(dp, "fetch_house_apportionment", unreadable)
        assert await dp.refresh_district_pvi() is False
        assert json.loads(path.read_text())["districts"] == {"KEEP-0": 5}


class TestApportionment:
    """The House's seats come from the Clerk's list, never a typed table."""

    _XML = b"""<MemberData><members>
    <member><statedistrict>AK00</statedistrict><member-info><bioguideID>B1</bioguideID>
      <state postal-code="AK"><state-fullname>Alaska</state-fullname></state><district>At Large</district></member-info></member>
    <member><statedistrict>FL19</statedistrict><member-info><bioguideID>D1</bioguideID>
      <state postal-code="FL"><state-fullname>Florida</state-fullname></state><district>19th</district></member-info></member>
    <member><statedistrict>FL20</statedistrict><member-info><bioguideID/>
      <state postal-code="FL"><state-fullname>Florida</state-fullname></state><district>20th</district></member-info></member>
    <member><statedistrict>DC00</statedistrict><member-info><bioguideID>N1</bioguideID>
      <state postal-code="DC"><state-fullname>District of Columbia</state-fullname></state><district>Delegate</district></member-info></member>
    <member><statedistrict>PR00</statedistrict><member-info><bioguideID>P1</bioguideID>
      <state postal-code="PR"><state-fullname>Puerto Rico</state-fullname></state><district>Resident Commissioner</district></member-info></member>
    </members></MemberData>"""

    def test_counts_voting_seats_vacancies_included_delegates_not(self):
        from app.pipeline.fetch.house_clerk import parse_apportionment

        assert parse_apportionment(self._XML) == {
            "AK": {"name": "Alaska", "seats": 1},
            "FL": {"name": "Florida", "seats": 2},
        }

    def test_unreadable_list_is_empty(self):
        from app.pipeline.fetch.house_clerk import parse_apportionment

        assert parse_apportionment(b"<not xml") == {}
