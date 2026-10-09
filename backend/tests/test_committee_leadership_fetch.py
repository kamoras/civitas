"""Tests for the automated committee-membership / chamber-leadership
ingestion (app/pipeline/fetch/committee_leadership.py).

Covers the pure build/gate logic (ported verbatim from the old manual
scripts/fetch_committee_data.py) and refresh_committee_leadership_data's
never-write-bad-data / never-raise persistence contract, mirroring
test_voteview_fetch.py's approach for the same class of ingest. Network
fetch is mocked — the YAML shapes are pinned by synthetic fixtures
matching unitedstates/congress-legislators' published schema.
"""

import json

import pytest

from app.pipeline.fetch import committee_leadership as cl
from app.pipeline.fetch import house_leadership
from app.pipeline.transform import committee_data


@pytest.fixture(autouse=True)
def _no_clerk_pages(monkeypatch):
    """The Clerk's leadership list is its own module's test; here it is
    unreadable, so the refresh keeps congress-legislators' titles."""
    async def unreadable(client, url):
        return None

    monkeypatch.setattr(house_leadership, "_fetch", unreadable)


def _synthetic_source():
    membership_raw = {
        "SSFI": [{"bioguide": f"M{i:06d}", "title": "Chairman" if i == 0 else None} for i in range(410)],
    }
    committees_raw = [{"thomas_id": "SSFI", "name": "Senate Committee on Finance", "type": "senate"}]
    legislators_raw = [
        {
            "id": {"bioguide": "M000001"},
            "leadership_roles": [{"title": "Senate Majority Leader", "start": "2025-01-03"}],
        },
        {
            "id": {"bioguide": "M000002"},
            "leadership_roles": [{"title": "Speaker of the House", "start": "2025-01-03", "end": "2020-01-01"}],
        },
    ] + [
        {"id": {"bioguide": f"M{i:06d}"}, "leadership_roles": [{"title": f"Title {i}", "start": "2025-01-03"}]}
        for i in range(3, 20)
    ]
    return membership_raw, committees_raw, legislators_raw


class TestBuildAndGates:
    def test_clean_synthetic_source_passes_gates(self):
        membership_raw, committees_raw, legislators_raw = _synthetic_source()
        membership = cl.build_committee_membership(membership_raw, committees_raw)
        roles = cl.build_leadership_roles(legislators_raw)
        assert cl.ingestion_gates(membership, roles) == []
        assert len(membership) == 410
        assert roles["M000001"] == "Senate Majority Leader"
        assert "M000002" not in roles  # expired role
        assert 10 <= len(roles) <= 80

    def test_tenures_keep_every_role_with_its_dates(self):
        """The current-title map drops dates and past roles; the tenure map
        keeps both, so a vote can be checked against the title held on its
        own date (Schumer led the majority until 2025-01-03)."""
        legislators_raw = [{
            "id": {"bioguide": "S000148"},
            "leadership_roles": [
                {"title": "Senate Minority Leader", "chamber": "senate", "start": "2025-01-03"},
                {"title": "Senate Majority Leader", "chamber": "senate",
                 "start": "2023-01-03", "end": "2025-01-03"},
            ],
        }, {"id": {"bioguide": "X000001"}}]
        tenures = cl.build_leadership_tenures(legislators_raw)
        assert tenures == {"S000148": [
            {"title": "Senate Majority Leader", "chamber": "senate", "start": "2023-01-03", "end": "2025-01-03"},
            {"title": "Senate Minority Leader", "chamber": "senate", "start": "2025-01-03", "end": None},
        ]}
        # The existing current-title consumers are unchanged.
        assert cl.build_leadership_roles(legislators_raw) == {"S000148": "Senate Minority Leader"}

    def test_tenure_dates_parsed_as_yaml_dates_become_iso_strings(self):
        import datetime
        tenures = cl.build_leadership_tenures([{
            "id": {"bioguide": "T000250"},
            "leadership_roles": [{"title": "Senate Majority Leader", "start": datetime.date(2025, 1, 3)}],
        }])
        assert tenures["T000250"][0]["start"] == "2025-01-03"

    def test_low_membership_coverage_fails_gate(self):
        membership = {f"M{i:06d}": [] for i in range(50)}  # far below the 400 floor
        roles = {"M000001": "Senate Majority Leader"}
        failures = cl.ingestion_gates(membership, roles)
        assert any("coverage" in f for f in failures)

    def test_leadership_count_out_of_bounds_fails_gate(self):
        membership = {f"M{i:06d}": [] for i in range(410)}
        failures = cl.ingestion_gates(membership, {})  # 0 leadership roles
        assert any("leadership-role count" in f for f in failures)


async def _fetch_synthetic(filename, client):
    membership_raw, committees_raw, legislators_raw = _synthetic_source()
    return {
        "committee-membership-current.yaml": membership_raw,
        "committees-current.yaml": committees_raw,
        "legislators-current.yaml": legislators_raw,
    }[filename]


class TestRefresh:
    def _patch_paths(self, monkeypatch, tmp_path):
        membership_path = tmp_path / "committee_membership.json"
        leadership_path = tmp_path / "leadership_roles.json"
        monkeypatch.setattr(cl, "_MEMBERSHIP_PATH", str(membership_path))
        monkeypatch.setattr(cl, "_LEADERSHIP_PATH", str(leadership_path))
        monkeypatch.setattr(cl, "_TENURES_PATH", str(tmp_path / "leadership_tenures.json"))
        committee_data.clear_committee_data_cache()
        return membership_path, leadership_path

    async def test_successful_refresh_writes_both_files(self, monkeypatch, tmp_path):
        membership_path, leadership_path = self._patch_paths(monkeypatch, tmp_path)

        monkeypatch.setattr(cl, "_fetch_yaml", _fetch_synthetic)
        assert await cl.refresh_committee_leadership_data() is True
        assert json.loads(leadership_path.read_text())["roles"]["M000001"] == "Senate Majority Leader"
        assert len(json.loads(membership_path.read_text())["membership"]) == 410
        tenures = json.loads((tmp_path / "leadership_tenures.json").read_text())["tenures"]
        assert tenures["M000001"] == [
            {"title": "Senate Majority Leader", "chamber": None, "start": "2025-01-03", "end": None},
        ]
        # An expired role is dropped from the current-title map but kept,
        # with its dates, in the tenure map.
        assert tenures["M000002"][0]["end"] == "2020-01-01"

    async def test_fetch_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        membership_path, leadership_path = self._patch_paths(monkeypatch, tmp_path)
        leadership_path.write_text(json.dumps({"roles": {"KEEP": "Speaker of the House"}}))

        async def fake_fetch(filename, client):
            return None

        monkeypatch.setattr(cl, "_fetch_yaml", fake_fetch)
        assert await cl.refresh_committee_leadership_data() is False
        assert json.loads(leadership_path.read_text())["roles"] == {"KEEP": "Speaker of the House"}

    async def test_gate_failure_does_not_write(self, monkeypatch, tmp_path):
        membership_path, leadership_path = self._patch_paths(monkeypatch, tmp_path)

        async def fake_fetch(filename, client):
            # Tiny membership well below the coverage floor.
            return {
                "committee-membership-current.yaml": {},
                "committees-current.yaml": [],
                "legislators-current.yaml": [],
            }[filename]

        monkeypatch.setattr(cl, "_fetch_yaml", fake_fetch)
        assert await cl.refresh_committee_leadership_data() is False
        assert not membership_path.exists()
        assert not leadership_path.exists()

    async def test_unexpected_exception_never_raises(self, monkeypatch, tmp_path):
        self._patch_paths(monkeypatch, tmp_path)

        async def boom(filename, client):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(cl, "_fetch_yaml", boom)
        assert await cl.refresh_committee_leadership_data() is False

    async def test_successful_refresh_clears_downstream_loader_cache(self, monkeypatch, tmp_path):
        """Same immediate-visibility contract as write_member_ideal_points —
        normalize_members.py must see fresh data without a process restart."""
        membership_path, leadership_path = self._patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(committee_data, "_PERSISTENT_DATA_DIR", tmp_path)
        monkeypatch.setattr(committee_data, "_DATA_DIR", tmp_path / "no-bundled-fallback")
        assert committee_data.load_leadership_roles() == {}  # loaded (and cached) before the refresh

        monkeypatch.setattr(cl, "_fetch_yaml", _fetch_synthetic)
        assert await cl.refresh_committee_leadership_data() is True
        assert committee_data.load_leadership_roles()["M000001"] == "Senate Majority Leader"
        assert committee_data.load_leadership_tenures()["M000001"][0]["start"] == "2025-01-03"
        assert committee_data.leadership_tenures_on_volume() is True
        committee_data.clear_committee_data_cache()
