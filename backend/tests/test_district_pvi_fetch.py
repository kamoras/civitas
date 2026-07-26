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


def _synthetic_result() -> dict[str, int]:
    """One district per state/seat, plausible R/D split matching the
    ingestion gates' bounds (150-285 each way, out of 435)."""
    result = {}
    pairs = []
    for st, n in sorted(dp.SEATS.items()):
        pairs.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
    for i, (st, d) in enumerate(pairs):
        result[f"{st}-{d}"] = 10 if i % 2 == 0 else -10
    return result


class TestIngestionGates:
    def test_clean_synthetic_population_passes_gates(self):
        result = _synthetic_result()
        assert dp.ingestion_gates(result) == []

    def test_missing_districts_fails_coverage_gate(self):
        result = _synthetic_result()
        del result["CA-1"]
        failures = dp.ingestion_gates(result)
        assert any("expected 435" in f for f in failures)

    def test_out_of_range_value_fails_gate(self):
        result = _synthetic_result()
        result["CA-1"] = 90
        failures = dp.ingestion_gates(result)
        assert any("plausible" in f for f in failures)

    def test_lopsided_lean_split_fails_gate(self):
        result = {k: 10 for k in _synthetic_result()}  # every seat R-leaning
        failures = dp.ingestion_gates(result)
        assert any("lean split" in f for f in failures)


class TestRefresh:
    def _patch_path(self, monkeypatch, tmp_path):
        path = tmp_path / "district_pvi.json"
        monkeypatch.setattr(dp, "_PVI_PATH", str(path))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
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
        for st, n in sorted(dp.SEATS.items()):
            seats = [0] if n == 1 else list(range(1, n + 1))
            for d in seats:
                pvi = result[f"{st}-{d}"]
                sign = "R" if pvi > 0 else "D"
                title_to_wikitext[dp.district_title(st, d)] = f"| cpvi = {sign}+{abs(pvi)}"

        async def fake_batch(titles, client):
            return {t: title_to_wikitext[t] for t in titles}

        monkeypatch.setattr(dp, "_fetch_batch", fake_batch)
        assert await dp.refresh_district_pvi() is True
        written = json.loads(path.read_text())
        assert len(written["districts"]) == 435
        assert score_calculator._district_pvi()["CA-1"] == written["districts"]["CA-1"]

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
