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
from types import SimpleNamespace

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
                "nominate_number_of_votes": "500",
            })
    return rows


def _patch_path(monkeypatch, tmp_path):
    path = tmp_path / "member_ideal_points.json"
    monkeypatch.setattr(score_calculator, "_MEMBER_IDEAL_POINTS_PATH", str(path))
    monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", None)
    return path


# A synthetic calibration, not the shipped one (tests read the shipped file separately).
REL = {"n0": 109.0, "reference_votes": 200.0, "half_weight_votes": 52.2, "uncounted_weight": 0.2}


class TestBuildAndGates:
    def test_clean_synthetic_population_passes_all_gates(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
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
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert rows[0]["bioguide_id"] not in data["members"]

    def test_sign_flip_fails_gates(self):
        """A negated dim1 column (the exact silent-corruption case the
        gates exist for) must fail loudly, not write."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nominate_dim1"] = f"{-float(r['nominate_dim1']):.4f}"
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert voteview.ingestion_gates("senate", data)

    def test_tiny_population_fails_gates(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)[:30]
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert failures or voteview.ingestion_gates("senate", data)

    def test_counts_are_recorded_and_placeholders_dropped(self):
        """v6.27: each member's scaled-vote count is stored for the score's
        reliability weight. Voteview's 0, 0 placeholder is no estimate and
        is left out; a position with no count is kept, uncounted."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nokken_poole_dim1"], r["nokken_poole_dim2"] = r["nominate_dim1"], "0.1"
        rows[0].update(nokken_poole_dim1="0.0", nokken_poole_dim2="0.0", nominate_number_of_votes="2")
        rows[1].update(nominate_number_of_votes="")  # a career position, no count: uncounted
        rows[3].update(nominate_number_of_votes="", nominate_dim1="")  # neither: just sworn in
        rows[2].update(nominate_number_of_votes="12")
        rows[4].update(nominate_number_of_votes="0")  # a count of 0 with a career position: uncounted
        data, failures = voteview.build_chamber_ideal_points(
            rows, "senate", state_pvi, {}, reliability=REL, congress=119)
        assert failures == []
        assert rows[0]["bioguide_id"] not in data["members"]
        assert rows[1]["bioguide_id"] in data["members"] and rows[1]["bioguide_id"] not in data["votes"]
        assert data["votes"][rows[2]["bioguide_id"]] == 12
        assert data["votes"][rows[3]["bioguide_id"]] == 0
        assert rows[4]["bioguide_id"] in data["members"] and rows[4]["bioguide_id"] not in data["votes"]
        assert data["reliability"] == REL and data["congress"] == 119
        assert data["measure"] == "Nokken-Poole"

    def test_the_scale_comes_from_full_records(self):
        """Thin records' noise can't widen the saturation scale: it is taken
        over full records only (reference_votes or more)."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nokken_poole_dim1"], r["nokken_poole_dim2"] = r["nominate_dim1"], "0.1"
        base, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        for i, r in enumerate(rows[:40]):  # 40 members on 2 votes each: noise of +/-0.4, either way
            noisy = float(r["nominate_dim1"]) + (0.4 if i % 2 else -0.4)
            r.update(nokken_poole_dim1=f"{noisy:.4f}", nominate_number_of_votes="2")
        thin, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        unweighted, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability={})
        assert unweighted["extremity_p90"] > 3 * base["extremity_p90"]
        assert thin["extremity_p90"] < 1.5 * base["extremity_p90"]

    def test_an_export_where_every_record_is_thin_has_no_scale_of_its_own(self):
        """Early in a Congress: the build leaves the scale to be carried, so
        the weights pull positions toward 50 instead of a scale shrunk with
        them cancelling them."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nominate_number_of_votes"] = "5"
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert failures == [] and data["extremity_p90"] is None and data["fit"]

    def test_a_record_of_exactly_reference_votes_is_full(self):
        """A record of reference_votes (200) votes counts in full: it enters
        the saturation scale's full records."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nominate_number_of_votes"] = "200"
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert failures == [] and data["extremity_p90"] is not None

    def test_a_zero_first_dimension_with_a_second_is_an_estimate(self):
        """The placeholder is 0 on both dimensions: a genuine estimate at
        exactly 0 on the first, placed on the second, is kept."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nokken_poole_dim1"], r["nokken_poole_dim2"] = r["nominate_dim1"], "0.1"
        rows[0].update(nokken_poole_dim1="0.0", nokken_poole_dim2="0.3")
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert data["measure"] == "Nokken-Poole"
        assert data["members"][rows[0]["bioguide_id"]] == 0.0

    def test_a_nokken_poole_placeholder_does_not_drop_a_dw_nominate_position(self):
        """When the chamber is read on DW-NOMINATE (Nokken-Poole published
        for too few members), a row whose Nokken-Poole columns hold the 0, 0
        placeholder still has its DW-NOMINATE position, and is kept."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        rows[0].update(nokken_poole_dim1="0.0", nokken_poole_dim2="0.0")
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert data["measure"] != "Nokken-Poole"
        assert data["members"][rows[0]["bioguide_id"]] == round(float(rows[0]["nominate_dim1"]), 4)

    def test_float_coded_exports_parse(self):
        """The 115th-117th exports write party and district codes as floats
        ("200.0", "3.0"); they used to fail with no members in either party."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["party_code"] += ".0"
        data, failures = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert failures == [] and voteview.ingestion_gates("senate", data) == []

    def test_a_nan_party_code_reads_as_no_party(self):
        """A NaN code (some exports write "nan") is no party, not a crash
        that keeps the stale section."""
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)
        rows[0]["party_code"] = "nan"
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert data["parties"][rows[0]["bioguide_id"]] is None

    def test_one_senator_per_state_fails_the_gate(self):
        state_pvi = score_calculator._state_pvi()
        rows = _synthetic_rows(state_pvi)[::2]
        data, _ = voteview.build_chamber_ideal_points(rows, "senate", state_pvi, {}, reliability=REL)
        assert data["seats"] >= 45
        assert any("seated members" in f for f in voteview.ingestion_gates("senate", data))

    def test_replacements_and_delegates_do_not_fail_the_house_gate(self):
        """The 119th House export had 451 rows by October 2026 (delegates
        and mid-Congress replacements); a row bound of 450 failed it every
        run. The gate counts seats."""
        district_pvi = {f"S{i // 10}-{i % 10 + 1}": (i % 41) - 20 for i in range(435)}
        rows = []
        for i, key in enumerate(district_pvi):
            st, d = key.split("-")
            party = "100" if district_pvi[key] < 0 else "200"
            pos = (-0.4 if party == "100" else 0.4) + 0.005 * district_pvi[key] + 0.01 * ((i * 7) % 11 - 5)
            rows.append({"chamber": "House", "bioguide_id": f"H{i}", "party_code": party, "state_abbrev": st,
                         "district_code": d, "nominate_dim1": f"{pos:.4f}", "nominate_number_of_votes": "500"})
        rows += [dict(rows[j], bioguide_id=f"REPL{j}", nominate_number_of_votes="40") for j in range(10)]
        rows[0]["district_code"] += ".0"  # the same seat, float-coded
        rows += [{"chamber": "House", "bioguide_id": f"DEL{j}", "party_code": "100", "state_abbrev": "VI",
                  "district_code": "0", "nominate_dim1": "-0.4", "nominate_number_of_votes": "300"} for j in range(6)]
        data, failures = voteview.build_chamber_ideal_points(rows, "house", {}, district_pvi, reliability=REL)
        assert len(data["members"]) == 451 and data["seats"] == 435
        assert failures == [] and voteview.ingestion_gates("house", data) == []

    def test_house_at_large_fallback_key(self):
        """Voteview district_code 1 for an at-large state resolves via the
        district table's ST-0 key."""
        row = {"state_abbrev": "AK", "district_code": "1"}
        assert voteview._seat_pvi_for(row, "house", {}, {"AK-0": 6}) == 6


class TestPersistence:
    def _section(self):
        return {
            "members": {"X000001": -0.35},
            "fit": {"D": {"a": -0.35, "b": 0.006}, "R": {"a": 0.35, "b": 0.006}},
            "extremity_p90": 0.2,
        }

    def test_write_then_load_roundtrip(self, monkeypatch, tmp_path):
        _patch_path(monkeypatch, tmp_path)
        score_calculator.write_member_ideal_points("senate", self._section())
        loaded = score_calculator._member_ideal_points("senate")
        assert loaded["members"] == {"X000001": -0.35}
        assert score_calculator._member_ideal_points("house") == {}

    def test_merge_preserves_other_chamber_section(self, monkeypatch, tmp_path):
        """A House run must not clobber the Senate section — the two
        pipelines run independently (same contract as
        write_party_ideology_bounds)."""
        path = _patch_path(monkeypatch, tmp_path)
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
        _patch_path(monkeypatch, tmp_path)
        assert score_calculator._member_ideal_points("senate") == {}


async def _record(asked, congress, rows):
    asked.append(congress)
    return rows


class TestRefresh:
    async def test_a_switch_reads_the_id_the_last_congress_lacked(self, monkeypatch, tmp_path):
        """With a member listed twice, the refresh fetches the last
        Congress's export to tell the post-switch id apart; without one, it
        fetches nothing more."""
        _patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()
        rows = [{**r, "icpsr": str(1000 + i)} for i, r in enumerate(_synthetic_rows(state_pvi))]
        since = {**rows[0], "icpsr": "91000", "party_code": "328", "nominate_dim1": "0.9",
                 "nominate_number_of_votes": "40"}
        asked = []

        async def fake_rows(chamber, congress, client=None):
            asked.append(congress)
            return [since] + rows if congress == 119 else rows

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert asked == [119, 118]
        assert score_calculator._member_ideal_points("senate")["members"][rows[0]["bioguide_id"]] == 0.9
        asked.clear()
        monkeypatch.setattr(voteview, "fetch_member_rows",
                            lambda *a, **k: _record(asked, a[1], rows))
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert asked == [119]

    async def test_a_switch_that_cant_be_told_apart_keeps_the_previous_section(self, monkeypatch, tmp_path):
        """No last-Congress export: the refresh keeps the previous section
        rather than guess. Both ids new (a switch in a first Congress): the
        vote export's first roll calls decide; if that export can't be
        read either, the previous section is kept."""
        path = _patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()
        rows = [{**r, "icpsr": str(1000 + i)} for i, r in enumerate(_synthetic_rows(state_pvi))]
        since = {**rows[0], "icpsr": "91000", "party_code": "328", "nominate_dim1": "0.9",
                 "nominate_number_of_votes": "40"}
        last = {"rows": None}

        async def fake_rows(chamber, congress, client=None):
            return [since] + rows if congress == 119 else last["rows"]
        firsts = {"rolls": None}

        async def fake_firsts(chamber, congress, client=None):
            return firsts["rolls"]
        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        monkeypatch.setattr(voteview, "fetch_first_rolls", fake_firsts)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        assert not path.exists()
        last["rows"] = []  # an empty export tells nothing apart either
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        last["rows"] = rows[1:]  # neither of the switcher's ids is in it
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        firsts["rolls"] = {}  # read, but neither id has voted: still unsettled
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        firsts["rolls"] = {"1000": 1, "91000": 400}
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert score_calculator._member_ideal_points("senate")["members"][rows[0]["bioguide_id"]] == 0.9

    async def test_successful_refresh_writes_section(self, monkeypatch, tmp_path):
        path = _patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()

        async def fake_rows(chamber, congress, client=None):
            return _synthetic_rows(state_pvi)

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert "senate" in json.loads(path.read_text())
        # scoring loader sees it immediately (cache invalidated on write)
        section = score_calculator._member_ideal_points("senate")
        assert section["fit"]["D"]["b"] > 0
        # v6.27: the section names its Congress and carries the shipped
        # reliability, or every section would read as current forever and
        # no position would be weighted.
        assert section["congress"] == 119
        assert section["reliability"] == score_calculator._position_reliability("senate")
        assert section["reliability"]["half_weight_votes"] > 0
        assert section["votes"]

    async def test_an_early_congress_carries_the_last_scale_and_scores_near_50(self, monkeypatch, tmp_path):
        """Every record 5 votes thin: the section carries the chamber's last
        scale, so each position counts a fraction of a full record's and the
        component sits near 50 for everyone (the weights don't cancel)."""
        path = _patch_path(monkeypatch, tmp_path)
        state_pvi = score_calculator._state_pvi()
        # A section carried before scale_congress was written names no scale
        # Congress; its own Congress stands in.
        path.write_text(json.dumps({"senate": {"members": {}, "extremity_p90": 0.2, "congress": 118,
                                               "scale_congress": None}}))
        rows = _synthetic_rows(state_pvi)
        for r in rows:
            r["nominate_number_of_votes"] = "5"

        async def fake_rows(chamber, congress, client=None):
            return rows

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        monkeypatch.setattr(score_calculator, "_position_reliability", lambda chamber: dict(REL))
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        section = score_calculator._member_ideal_points("senate")
        assert section["extremity_p90"] == 0.2 and section["scale_congress"] == 118
        weight = score_calculator.position_confidence(5, section["reliability"])
        assert weight < 0.1
        # A position at the old scale's saturation scores 50 - 50 * weight, not 0.
        assert abs(score_calculator.position_congruence_score(0.2, 0.2, weight) - 50) < 5

    async def test_a_new_congress_keeps_the_last_ones_positions_for_the_flank_rule(self, monkeypatch, tmp_path):
        """A v6.27 section of the 118th becomes the 119th's "prior", kept
        through the 119th's later refreshes; a pre-v6.27 one (no
        reliability, so no weights) is not kept."""
        path = _patch_path(monkeypatch, tmp_path)
        rows = _synthetic_rows(score_calculator._state_pvi())

        async def fake_rows(chamber, congress, client=None):
            return rows

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        monkeypatch.setattr(score_calculator, "_position_reliability", lambda chamber: dict(REL))
        last = {"members": {"OLD": 0.4}, "votes": {"OLD": 600}, "congress": 118, "reliability": dict(REL),
                "extremity_p90": 0.2}
        path.write_text(json.dumps({"senate": last}))
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        prior = score_calculator._member_ideal_points("senate")["prior"]
        assert prior == {"congress": 118, "members": {"OLD": 0.4}, "votes": {"OLD": 600}, "parties": {},
                     "reliability": dict(REL)}
        # A section that records parties passes them on: the flank rule
        # tells a switch between Congresses by them.
        assert voteview.previous_positions(
            {"congress": 118, "members": {"OLD": 0.4}, "parties": {"OLD": "D"}, "reliability": dict(REL)}, 119,
        )["parties"] == {"OLD": "D"}
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert score_calculator._member_ideal_points("senate")["prior"] == prior

        path.write_text(json.dumps({"senate": {k: v for k, v in last.items() if k != "reliability"}}))
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert "prior" not in score_calculator._member_ideal_points("senate")

        # Only the Congress just before: a 117th section is not kept for the 119th.
        path.write_text(json.dumps({"senate": {**last, "congress": 117}}))
        assert await voteview.refresh_member_ideal_points("senate", 119) is True
        assert "prior" not in score_calculator._member_ideal_points("senate")

    async def test_fetch_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        path = _patch_path(monkeypatch, tmp_path)
        path.write_text(json.dumps({"senate": {"members": {"KEEP": 0.1}}}))

        async def fake_rows(chamber, congress, client=None):
            return None

        monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False
        assert json.loads(path.read_text())["senate"]["members"] == {"KEEP": 0.1}

    async def test_gate_failure_does_not_write(self, monkeypatch, tmp_path):
        path = _patch_path(monkeypatch, tmp_path)
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
        _patch_path(monkeypatch, tmp_path)

        async def boom(chamber, congress, client=None):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(voteview, "fetch_member_rows", boom)
        assert await voteview.refresh_member_ideal_points("senate", 119) is False


def test_a_member_listed_twice_is_read_on_their_latest_record():
    """A party switch during a Congress puts a member in the export twice,
    under two ICPSR ids. They are read on the record since the switch, and
    only it enters the fits and the seated count (one seat, one position);
    until Voteview places that record, they have no position, rather than
    being read on the old one."""
    state_pvi = score_calculator._state_pvi()
    rows = [{**r, "icpsr": str(1000 + i)} for i, r in enumerate(_synthetic_rows(state_pvi))]
    bio = rows[0]["bioguide_id"]
    other = "200" if rows[0]["party_code"] == "100" else "100"
    since = {**rows[0], "icpsr": "91000", "party_code": other, "nominate_dim1": "0.9",
             "nominate_number_of_votes": "40"}
    assert voteview.switched_members(rows + [since]) and not voteview.switched_members(rows)

    def build(rs, **kw):
        return voteview.build_chamber_ideal_points(rs, "senate", state_pvi, {}, reliability=REL, **kw)[0]
    base, alone = build(rows), build(rows[1:] + [since])
    latest = {bio: "91000"}
    # The new id, though shorter and listed first, is the latest: it alone
    # enters the other party's fit.
    data = build([since] + rows, latest=latest)
    assert data["members"][bio] == 0.9 and data["votes"][bio] == 40
    assert data["fit"] == alone["fit"] != base["fit"] and data["seated"] == base["seated"]
    # The section names the switcher and the party of the record it reads.
    assert data["switched"] == [bio] and data["parties"][bio] == voteview.PARTY_CODES[int(other)]
    # Without `latest`, the later row in the export (a caller that passes
    # none; the pipeline and the scripts pass it).
    assert build([since] + rows)["members"][bio] == base["members"][bio]
    # Not placed yet: no position, and the old record stays out of the fits.
    data = build(rows + [{**since, "nominate_dim1": ""}], latest=latest)
    assert bio not in data["members"] and data["fit"] == build(rows[1:])["fit"]
    # Its party is still recorded: the flank rule tells a switch by it.
    assert data["parties"][bio] == voteview.PARTY_CODES[int(other)]


def test_a_latest_record_that_is_a_placeholder_is_no_position():
    """On Nokken-Poole rows, a switcher whose record since the switch is
    Voteview's 0, 0 placeholder has no position: never the old record."""
    state_pvi = score_calculator._state_pvi()
    rows = [{**r, "icpsr": str(1000 + i), "nokken_poole_dim1": r["nominate_dim1"], "nokken_poole_dim2": "0.1"}
            for i, r in enumerate(_synthetic_rows(state_pvi))]
    bio = rows[0]["bioguide_id"]
    placeholder = {**rows[0], "icpsr": "91000", "nokken_poole_dim1": "0", "nokken_poole_dim2": "0"}
    data, _ = voteview.build_chamber_ideal_points(
        rows + [placeholder], "senate", state_pvi, {}, reliability=REL, latest={bio: "91000"})
    assert bio not in data["members"]


def test_switcher_latest_tells_the_new_id_apart():
    """The latest id is the one the last Congress's export lacked; when both
    are new (a switch in a member's first Congress), the one whose first
    roll call comes last, or that has none yet; with neither, unresolved."""
    rows = [{"bioguide_id": "A1", "icpsr": "10"}, {"bioguide_id": "A1", "icpsr": "90.0"},
            {"bioguide_id": "B1", "icpsr": "20"}, {"bioguide_id": "B1", "icpsr": "21"},
            {"bioguide_id": "C1", "icpsr": "30"}]
    latest, unresolved = voteview.switcher_latest(rows, {"10", "30"})
    assert latest == {"A1": "90"} and unresolved == ["B1"]
    # An id spelled "10.0" in either export is the same id.
    earlier = {voteview._icpsr({"icpsr": "10.0"}), "30"}
    assert voteview.switcher_latest(rows, earlier)[0] == {"A1": "90"}
    latest, unresolved = voteview.switcher_latest(rows, {"10", "30"}, {"20": 300, "21": 5, "10": 1})
    assert latest == {"A1": "90", "B1": "20"} and unresolved == []
    latest, _ = voteview.switcher_latest(rows, {"10", "30"}, {"20": 300})
    assert latest["B1"] == "21"  # no roll call yet: the newest
    assert voteview.switcher_latest(rows, {"10", "30"}, {})[1] == ["B1"]  # neither has voted


async def test_first_rolls_are_each_ids_earliest_roll_call(monkeypatch):
    """fetch_first_rolls: each ICPSR id's earliest roll call with a row,
    whatever the id's spelling; a row with no roll number says nothing; a
    failed fetch is None."""
    text = "icpsr,rollnumber,cast_code\n10.0,7,1\n10,3,1\n90,12,6\n90,,1\n90,40,1\n"

    async def fetched(*a, **k):
        return SimpleNamespace(text=text)
    monkeypatch.setattr(voteview, "fetch_with_retry", fetched)
    assert await voteview.fetch_first_rolls("house", 119, client=SimpleNamespace()) == {"10": 3, "90": 12}

    async def failed(*a, **k):
        return None
    monkeypatch.setattr(voteview, "fetch_with_retry", failed)
    assert await voteview.fetch_first_rolls("house", 119, client=SimpleNamespace()) is None


async def test_an_unresolved_switcher_alerts_once_and_clears(monkeypatch, tmp_path):
    """A switcher whose latest record can't be told apart raises an ops
    alert (the kept section stops being current at the next Congress); a
    clean refresh resolves it."""
    _patch_path(monkeypatch, tmp_path)
    state_pvi = score_calculator._state_pvi()
    rows = [{**r, "icpsr": str(1000 + i)} for i, r in enumerate(_synthetic_rows(state_pvi))]
    since = {**rows[0], "icpsr": "91000"}
    sent, resolved = [], []
    from app import ops_alerts
    monkeypatch.setattr(ops_alerts, "send_ops_alert",
                        lambda subject, body, **k: sent.append((k["condition"], k["dedupe_key"], body)))
    monkeypatch.setattr(ops_alerts, "resolve_ops_alert", lambda c: resolved.append(c))

    async def fake_rows(chamber, congress, client=None):
        return [since] + rows if congress == 119 else None
    monkeypatch.setattr(voteview, "fetch_member_rows", fake_rows)
    assert await voteview.refresh_member_ideal_points("senate", 119) is False
    # Once a Congress: the dedupe key names it.
    assert [(c, d) for c, d, _ in sent] == [("voteview-switcher-senate", "voteview-switcher-senate-119")]
    assert "119th Congress" in sent[0][2] and resolved == []

    async def clean(chamber, congress, client=None):
        return rows
    monkeypatch.setattr(voteview, "fetch_member_rows", clean)
    # Settled, the alert clears even if a later gate keeps the old section.
    monkeypatch.setattr(voteview, "ingestion_gates", lambda chamber, data: ["synthetic gate failure"])
    assert await voteview.refresh_member_ideal_points("senate", 119) is False
    assert resolved == ["voteview-switcher-senate"]


def test_the_seat_gate_bounds_each_chamber_both_ways():
    """Seats with a positioned member must fall within the chamber's size:
    more than 435 House seats (or 50 Senate states) is a bad join, too few
    a truncated export."""
    def data(seats):
        return {"members": {}, "seats": seats, "seated": 435,
                "fit": {"D": {"a": -0.4, "b": 0.01}, "R": {"a": 0.4, "b": 0.01}}, "extremity_p90": 0.2}
    def seat_failures(chamber, seats):
        return [f for f in voteview.ingestion_gates(chamber, data(seats)) if "seats with a member" in f]
    assert seat_failures("house", 435) == [] and seat_failures("house", 380) == []
    assert seat_failures("house", 436) and seat_failures("house", 379)
    assert seat_failures("senate", 50) == [] and seat_failures("senate", 45) == []
    assert seat_failures("senate", 51) and seat_failures("senate", 44)


def test_the_seated_floor_holds_at_its_value_in_each_chamber():
    """At least 90 senators and 380 representatives must be seated with a
    position: those counts pass, one fewer fails."""
    def seated_failures(chamber, seated):
        seats = 50 if chamber == "senate" else 435
        data = {"members": {}, "seats": seats, "seated": seated,
                "fit": {"D": {"a": -0.4, "b": 0.01}, "R": {"a": 0.4, "b": 0.01}}, "extremity_p90": 0.2}
        return [f for f in voteview.ingestion_gates(chamber, data) if "seated members" in f]
    assert seated_failures("senate", 90) == [] and seated_failures("senate", 89)
    assert seated_failures("house", 380) == [] and seated_failures("house", 379)


def test_a_carried_scale_keeps_a_sections_own_and_names_where_it_was_measured():
    """A section with its own scale keeps it; one without takes the last
    section's, named by the Congress it was measured on, through a chain of
    carries (the scale_congress of a carried scale, not the section's)."""
    own = {"extremity_p90": 0.3, "congress": 120}
    assert voteview.with_carried_scale(own, {"extremity_p90": 0.2, "congress": 119}) is own
    thin = {"extremity_p90": None, "congress": 120}
    assert voteview.with_carried_scale(thin, {"extremity_p90": 0.2, "congress": 119}) == {
        "extremity_p90": 0.2, "congress": 120, "scale_congress": 119}
    carried = {"extremity_p90": 0.2, "congress": 119, "scale_congress": 118}
    assert voteview.with_carried_scale(thin, carried)["scale_congress"] == 118
    assert voteview.with_carried_scale(thin, {}) is thin


def test_an_at_large_seat_counts_once_however_voteview_codes_it():
    """Voteview codes some at-large seats 1 and others 0: a state with one
    seat resolves to district 0 either way, so the seat gate counts it once;
    a districted state keeps its number."""
    district_pvi = {"WY-0": 25, "TX-3": 10}
    assert voteview._seat_district({"state_abbrev": "WY", "district_code": "1"}, district_pvi) == 0
    assert voteview._seat_district({"state_abbrev": "WY", "district_code": "0"}, district_pvi) == 0
    assert voteview._seat_district({"state_abbrev": "TX", "district_code": "3"}, district_pvi) == 3
