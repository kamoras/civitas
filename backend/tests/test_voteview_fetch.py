"""Tests for the automated Voteview ideal-point ingestion (v6.11).

Covers the pure build/gate logic with synthetic member rows, the
read-merge-write persistence contract (each chamber owns only its own
section, same as party_ideology_bounds), and refresh_member_ideal_points'
never-write-bad-data / never-raise posture. Network fetch is mocked —
the CSV parse shape is pinned by the synthetic rows, matching Voteview's
published member-export columns (voteview.com/articles/data_help_members).
"""

import json
import random

from app.pipeline.analyze import score_calculator
from app.pipeline.fetch import voteview


def _synthetic_rows(state_pvi: dict[str, int]) -> list[dict]:
    """Two senators per state: D dim1 ≈ -0.4 + 0.005*pvi, R ≈ +0.4 +
    0.005*pvi, small noise — a population where redder seats elect more
    conservative members within both parties, the pattern the gates
    assert."""
    random.seed(7)
    rows = []
    i = 0
    for st, pvi in state_pvi.items():
        if st == "DC":
            continue
        for _ in range(2):
            i += 1
            party = 100 if pvi < 0 else 200
            base = -0.4 if party == 100 else 0.4
            rows.append({
                "chamber": "Senate",
                "bioguide_id": f"T{i:06d}",
                "party_code": str(party),
                "state_abbrev": st,
                "district_code": "0",
                "nominate_dim1": f"{base + 0.005 * pvi + random.uniform(-0.05, 0.05):.4f}",
            })
    return rows


class TestBuildAndGates:
    def test_clean_synthetic_population_passes_all_gates(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {})
        assert failures == []
        assert voteview.ingestion_gates("senate", data) == []
        assert 90 <= len(data["members"]) <= 105
        assert data["fit"]["D"]["b"] > 0 and data["fit"]["R"]["b"] > 0
        assert data["fit"]["D"]["a"] < data["fit"]["R"]["a"]
        assert data["extremity_p90"] > 0

    def test_members_without_estimates_are_skipped_not_zeroed(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        rows[0]["nominate_dim1"] = ""  # freshman pre-first-scaling
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {})
        assert rows[0]["bioguide_id"] not in data["members"]

    def test_sign_flip_fails_gates(self):
        """A negated dim1 column (the exact silent-corruption case the
        gates exist for) must fail loudly, not write."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nominate_dim1"] = f"{-float(r['nominate_dim1']):.4f}"
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {})
        assert voteview.ingestion_gates("senate", data)

    def test_tiny_population_fails_gates(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)[:30]
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {})
        assert failures or voteview.ingestion_gates("senate", data)

    def test_house_at_large_fallback_key(self):
        """Voteview district_code 1 for an at-large state resolves via the
        district table's ST-0 key."""
        row = {"state_abbrev": "AK", "district_code": "1"}
        assert voteview._seat_pvi_for(row, "house", {}, {"AK-0": 6}) == 6


class TestPersistence:
    def _patch_path(self, monkeypatch, tmp_path):
        path = tmp_path / "member_ideal_points.json"
        monkeypatch.setattr(score_calculator, "_MEMBER_IDEAL_POINTS_PATH", str(path))
        monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", None)
        return path

    def _section(self):
        return {
            "members": {"X000001": -0.35},
            "fit": {"D": {"a": -0.35, "b": 0.006}, "R": {"a": 0.35, "b": 0.006}},
            "extremity_p90": 0.2,
        }

    def test_write_then_load_roundtrip(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        score_calculator.write_member_ideal_points("senate", self._section())
        loaded = score_calculator._member_ideal_points("senate")
        assert loaded["members"] == {"X000001": -0.35}
        assert score_calculator._member_ideal_points("house") == {}

    def test_merge_preserves_other_chamber_section(self, monkeypatch, tmp_path):
        """A House run must not clobber the Senate section — the two
        pipelines run independently (same contract as
        write_party_ideology_bounds)."""
        path = self._patch_path(monkeypatch, tmp_path)
        score_calculator.write_member_ideal_points("senate", self._section())
        score_calculator.write_member_ideal_points("house", self._section())
        raw = json.loads(path.read_text())
        assert "senate" in raw and "house" in raw and "_source" in raw

    def test_write_failure_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            score_calculator, "_MEMBER_IDEAL_POINTS_PATH",
            "/nonexistent-dir/nope/member_ideal_points.json",
        )
        score_calculator.write_member_ideal_points("senate", self._section())  # must not raise

    def test_missing_file_loads_empty(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        assert score_calculator._member_ideal_points("senate") == {}


class TestRefresh:
    def _patch_path(self, monkeypatch, tmp_path):
        path = tmp_path / "member_ideal_points.json"
        monkeypatch.setattr(score_calculator, "_MEMBER_IDEAL_POINTS_PATH", str(path))
        monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", None)
        return path

    async def test_successful_refresh_writes_section(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()

        async def fake_rows(chamber, congress, client=None):
            return _synthetic_rows(state_pvi)

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert "senate" in json.loads(path.read_text())
        # scoring loader sees it immediately (cache invalidated on write)
        assert score_calculator._member_ideal_points("senate")["fit"]["D"]["b"] > 0

    async def test_fetch_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        path.write_text(json.dumps({"senate": {"members": {"KEEP": 0.1}}}))

        async def fake_rows(chamber, congress, client=None):
            return None

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        assert json.loads(path.read_text())["senate"]["members"] == {"KEEP": 0.1}

    async def test_gate_failure_does_not_write(self, monkeypatch, tmp_path):
        path = self._patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()
        bad = _synthetic_rows(state_pvi)
        for r in bad:
            r["nominate_dim1"] = f"{-float(r['nominate_dim1']):.4f}"  # sign flip

        async def fake_rows(chamber, congress, client=None):
            return bad

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        assert not path.exists()

    async def test_unexpected_exception_never_raises(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)

        async def boom(chamber, congress, client=None):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(voteview, "fetch_member_rows", boom)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
