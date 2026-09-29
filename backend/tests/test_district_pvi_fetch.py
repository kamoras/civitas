"""Tests for the per-Congress district Cook PVI ingestion
(app/pipeline/fetch/district_pvi.py).

Covers the table/prose parsing, every gate (structure, provenance, the
revision's own prose vs its table, redrawn-vs-base Congress), the
never-write-bad-data / never-raise refresh contract, the sitting-Congress
switch, the elections reader, and — as a regression test for the 2026-09
bug — that member scoring's table holds the 119th-Congress lines while the
2026 election's holds the new ones. Network fetch is mocked; the wikitext
shapes match the real article's table ({{ushr|State|N|X}} then
{{Shading PVI|R|7}} / {{Shading PVI|D|value=2}} / {{Shading PVI|EVEN}}).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.analyze import score_calculator
from app.pipeline.fetch import district_pvi as dp

PAGE = "Cook Partisan Voting Index"
BUNDLED = dp.SOURCES_PATH.parent / "district_pvi.json"


def _pairs():
    out = []
    for st, n in sorted(dp.SEATS.items()):
        out.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
    return out


def _synthetic_result() -> dict[str, int]:
    """Every seat, alternating R+10 / D+10 — inside the gates' split
    bounds (150-285 each way, out of 435)."""
    return {f"{st}-{d}": (10 if i % 2 == 0 else -10) for i, (st, d) in enumerate(_pairs())}


def _cell(v: int, style: int) -> str:
    if v == 0:
        return "{{Shading PVI|EVEN}}"
    party = "R" if v > 0 else "D"
    return f"{{{{Shading PVI|{party}|value={abs(v)}}}}}" if style % 3 == 0 else f"{{{{Shading PVI|{party}|{abs(v)}}}}}"


def _words(n: int) -> str:
    return {7: "seven", 9: "nine"}.get(n, str(n))


def _wikitext(table: dict[str, int], label: str, *, counts=None, median=None) -> str:
    """A revision of the article: label in a citation, the section's prose
    summary (derived from `table` unless overridden), then the table."""
    vals = sorted(table.values())
    r, d, e = counts or (sum(v > 0 for v in vals), sum(v < 0 for v in vals), sum(v == 0 for v in vals))
    if median is None:
        mv = vals[len(vals) // 2]
        mk = next(k for k in sorted(table) if table[k] == mv)
    else:
        mk, mv = median
    st, dn = mk.split("-")
    mv_s = "EVEN" if mv == 0 else f"{'R' if mv > 0 else 'D'}+{abs(mv)}"
    rows = []
    for i, (key, v) in enumerate(sorted(table.items())):
        s, n = key.split("-")
        rows.append(
            f"|-\n| {{{{ushr|{dp.STATE_NAMES[s]}|{'AL' if n == '0' else n}|X}}}}\n| {_cell(v, i)}\n"
            "|{{Party shading/Text/Republican}}"
        )
    return (
        f"Intro.<ref name=\"r\">{{{{cite web|title={label}}}}}</ref>\n\n"
        "==By congressional district==\n"
        f"With a PVI of {mv_s}, {{{{ushr|{st}|{'AL' if dn == '0' else dn}}}}} was determined to be the median "
        "congressional district. "
        f"{{{{As of|2025}}}}, {_words(r)} districts are more Republican than the national average, "
        f"{_words(d)} districts are more Democratic than the national average, and {_words(e)} districts "
        "match the national average.\n\n"
        "{| class=\"wikitable sortable\"\n|+ Table\n|-\n! District\n! PVI\n! Party\n"
        + "\n".join(rows) + "\n|}\n\n==By state==\nstate table\n"
    )


def _revision(table, label, revid, ts="2026-01-01T00:00:00Z", **kw) -> dict:
    return {"title": PAGE, "revid": revid, "timestamp": ts, "content": _wikitext(table, label, **kw)}


def _source(revid, label, ts="2026-01-01T00:00:00Z", **extra) -> dict:
    return {"lines": f"lines {revid}", "cook_release": f"release {revid}", "window": "2020+2024",
            "revid": revid, "revision_timestamp": ts, "label": label, **extra}


# ── Parsing ────────────────────────────────────────────────────────────

class TestParse:
    def test_round_trips_every_cell_style(self):
        table = _synthetic_result()
        table["CA-1"], table["CA-2"] = 0, 33
        parsed, problems = dp.parse_district_table(_wikitext(table, "L"))
        assert problems == []
        assert parsed == table

    def test_at_large_is_district_zero(self):
        parsed, _ = dp.parse_district_table(_wikitext(_synthetic_result(), "L"))
        assert "AK-0" in parsed and "WY-0" in parsed

    def test_prose_median_district_is_not_a_row(self):
        # The median sentence's {{ushr|..}} sits above the table; it must
        # neither become a row nor trip the cell-count check.
        parsed, problems = dp.parse_district_table(_wikitext(_synthetic_result(), "L"))
        assert len(parsed) == 435 and problems == []

    def test_unparseable_row_is_reported_not_skipped(self):
        text = _wikitext(_synthetic_result(), "L").replace("{{Shading PVI|R|10}}", "{{Shading PVI|R|ten}}", 1)
        _, problems = dp.parse_district_table(text)
        assert any("unparseable" in p for p in problems)

    def test_row_in_an_unknown_layout_is_reported(self):
        text = _wikitext(_synthetic_result(), "L").replace("{{Shading PVI|R|10}}", "R+10", 1)
        _, problems = dp.parse_district_table(text)
        assert any("layout drift" in p for p in problems)

    def test_duplicate_row_is_reported(self):
        text = _wikitext(_synthetic_result(), "L")
        text = text.replace("|}\n", "|-\n| {{ushr|Alabama|1|X}}\n| {{Shading PVI|R|10}}\n|}\n", 1)
        _, problems = dp.parse_district_table(text)
        assert any("duplicate row for AL-1" in p for p in problems)

    def test_missing_section(self):
        assert dp.parse_district_table("no table here")[1] == ["no 'By congressional district' section"]

    def test_stated_summary_reads_number_words_and_median(self):
        table = _synthetic_result()
        s = dp.parse_stated_summary(_wikitext(table, "L", counts=(219, 207, 9), median=("CA-22", 1)))
        assert s == {"r": 219, "d": 207, "even": 9, "median_key": "CA-22", "median_pvi": 1}

    def test_congress_for_election(self):
        assert dp.congress_for_election(2024) == 119
        assert dp.congress_for_election(2026) == 120
        assert dp.congress_for_election(2028) == 121


# ── Gates ──────────────────────────────────────────────────────────────

class TestIngestionGates:
    def test_clean_synthetic_population_passes_gates(self):
        assert dp.ingestion_gates(_synthetic_result()) == []

    def test_missing_districts_fails_coverage_gate(self):
        result = _synthetic_result()
        del result["CA-1"]
        assert any("expected 435" in f for f in dp.ingestion_gates(result))

    def test_district_outside_apportionment_fails(self):
        result = _synthetic_result()
        del result["CA-1"]
        result["CA-53"] = 5
        failures = dp.ingestion_gates(result)
        assert any("CA-53" in f for f in failures)

    def test_out_of_range_value_fails_gate(self):
        result = _synthetic_result()
        result["CA-1"] = 90
        assert any("plausible" in f for f in dp.ingestion_gates(result))

    def test_lopsided_lean_split_fails_gate(self):
        result = {k: 10 for k in _synthetic_result()}
        assert any("lean split" in f for f in dp.ingestion_gates(result))


class TestSelfConsistency:
    def test_counts_must_match_the_revisions_own_prose(self):
        """The drift class that sank the infobox scrape, caught at the
        article level: an editor part-way through swapping in a new
        release leaves the prose describing the old table."""
        table = _synthetic_result()
        vals = list(table.values())
        stale = (sum(v > 0 for v in vals) - 3, sum(v < 0 for v in vals) + 3, 0)
        failures = dp.self_consistency_gates(table, dp.parse_stated_summary(_wikitext(table, "L", counts=stale)))
        assert any("disagree" in f for f in failures)

    def test_stated_median_must_hold_its_value(self):
        table = _synthetic_result()
        summary = dp.parse_stated_summary(_wikitext(table, "L", median=("AL-1", -4)))
        assert any("stated median AL-1" in f for f in dp.self_consistency_gates(table, summary))

    def test_median_one_step_off_is_tolerated_two_is_not(self):
        table = {k: (3 if v > 0 else -3) for k, v in _synthetic_result().items()}
        key = next(k for k, v in table.items() if v == 3)
        table[key] = 4  # the sentence names a district that holds R+4
        ok = dp.self_consistency_gates(table, {"r": 218, "d": 217, "even": 0, "median_key": key, "median_pvi": 4})
        assert ok == []
        table[key] = 5
        bad = dp.self_consistency_gates(table, {"r": 218, "d": 217, "even": 0, "median_key": key, "median_pvi": 5})
        assert any("not within 1" in f for f in bad)

    def test_missing_prose_is_a_failure(self):
        failures = dp.self_consistency_gates(_synthetic_result(), {})
        assert len(failures) == 2


class TestCrossCongress:
    def test_only_redrawn_states_may_differ(self):
        base = _synthetic_result()
        new = dict(base)
        new["TN-9"] = 9
        new["MO-5"] = 9  # a blocked map's value slipping in
        failures = dp.cross_congress_gates(new, base, ["TN"])
        assert any("did not redraw" in f and "MO-5" in f for f in failures)

    def test_redrawn_state_left_on_old_lines_fails(self):
        base = _synthetic_result()
        new = dict(base)
        new["TN-9"] = 9
        failures = dp.cross_congress_gates(new, base, ["TN", "UT"])
        assert failures == ["redrawn states identical to the base Congress (old lines?): ['UT']"]

    def test_clean_redraw_passes(self):
        base = _synthetic_result()
        new = dict(base, **{"TN-9": 9, "UT-1": -12})
        assert dp.cross_congress_gates(new, base, ["TN", "UT"]) == []


class TestProvenance:
    def test_label_must_be_on_the_revision(self):
        src = _source(5, "2025 Cook PVI (119th Congress)")
        rev = _revision(_synthetic_result(), "Introducing the 2026 Cook PVI", 5)
        assert any("label" in f for f in dp.provenance_gates(src, rev, PAGE))

    def test_wrong_revision_or_page_fails(self):
        src = _source(5, "L")
        rev = _revision(_synthetic_result(), "L", 6)
        rev["title"] = "Something else"
        failures = dp.provenance_gates(src, rev, PAGE)
        assert any("asked for revision 5" in f for f in failures)
        assert any("belongs to" in f for f in failures)

    def test_unfetchable_revision_fails(self):
        assert dp.provenance_gates(_source(5, "L"), None, PAGE) == ["pinned revision could not be fetched"]


# ── Refresh ────────────────────────────────────────────────────────────

def _two_congress_setup(monkeypatch, tmp_path, *, rev120=None, live=None, sitting=119):
    base = _synthetic_result()
    new = dict(base, **{"TN-9": 9, "UT-1": -12})
    sources = {
        "page": PAGE,
        "congresses": {
            "119": _source(101, "(119th Congress)"),
            "120": _source(202, "2026 Cook PVI", redrawn_from="119", redrawn_states=["TN", "UT"]),
        },
    }
    src_path = tmp_path / "sources.json"
    src_path.write_text(json.dumps(sources))
    monkeypatch.setattr(dp, "SOURCES_PATH", src_path)
    out = tmp_path / "district_pvi.json"
    monkeypatch.setattr(dp, "_PVI_PATH", str(out))
    monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path))
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
    monkeypatch.setattr(dp, "_file_cache", None)
    monkeypatch.setattr(dp, "_sitting_congress", lambda: sitting)
    revisions = {
        101: _revision(base, "(119th Congress)", 101),
        202: rev120 or _revision(new, "2026 Cook PVI", 202),
    }

    async def fake_fetch(*, revid=None, page=None):
        if revid is not None:
            return revisions.get(revid)
        return live or revisions[202]

    monkeypatch.setattr(dp, "_fetch_revision", fake_fetch)
    return out, base, new


class TestRefresh:
    async def test_writes_every_congress_and_sitting_lines(self, monkeypatch, tmp_path):
        out, base, new = _two_congress_setup(monkeypatch, tmp_path)
        assert await dp.refresh_district_pvi() is True
        written = json.loads(out.read_text())
        assert written["congress"] == 119
        assert written["districts"] == base
        assert written["congresses"]["120"]["districts"] == new
        assert "revision 101" in written["_source"]
        # Visible to member scoring without a restart.
        assert score_calculator._district_pvi()["TN-9"] == base["TN-9"]

    async def test_sitting_congress_without_a_source_writes_nothing(self, monkeypatch, tmp_path):
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=121)
        assert await dp.refresh_district_pvi() is False
        assert not out.exists()

    async def test_any_gate_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        base = _synthetic_result()
        mixed = dict(base, **{"TN-9": 9, "UT-1": -12, "MO-5": 9})  # MO didn't redraw
        rev = _revision(mixed, "2026 Cook PVI", 202)
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, rev120=rev)
        out.write_text(json.dumps({"districts": {"KEEP-0": 5}}))
        assert await dp.refresh_district_pvi() is False
        assert json.loads(out.read_text())["districts"] == {"KEEP-0": 5}

    async def test_unexpected_exception_never_raises(self, monkeypatch, tmp_path):
        _two_congress_setup(monkeypatch, tmp_path)

        async def boom(**kw):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(dp, "_fetch_revision", boom)
        assert await dp.refresh_district_pvi() is False

    async def test_live_article_drift_alerts_and_is_never_ingested(self, monkeypatch, tmp_path):
        base = _synthetic_result()
        edited = dict(base, **{"TN-9": 9, "UT-1": -12, "TX-35": 4})
        live = _revision(edited, "2026 Cook PVI", 303)
        out, _, new = _two_congress_setup(monkeypatch, tmp_path, live=live)
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await dp.refresh_district_pvi() is True
        assert "TX-35" in alert.call_args.args[1]
        assert json.loads(out.read_text())["congresses"]["120"]["districts"] == new

    async def test_live_article_at_the_pin_is_quiet(self, monkeypatch, tmp_path):
        _two_congress_setup(monkeypatch, tmp_path)
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await dp.refresh_district_pvi() is True
        alert.assert_not_called()


class TestEnsureSittingLines:
    async def _written(self, monkeypatch, tmp_path, sitting=119):
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=sitting)
        assert await dp.refresh_district_pvi() is True
        return out, base, new

    async def test_current_is_a_no_op(self, monkeypatch, tmp_path):
        out, _, _ = await self._written(monkeypatch, tmp_path)
        before = out.read_text()
        with patch.object(dp, "refresh_district_pvi", new_callable=AsyncMock) as refresh:
            assert await _in_thread(dp.ensure_sitting_lines) == "current"
        refresh.assert_not_called()
        assert out.read_text() == before

    async def test_new_congress_switches_locally_on_jan_3(self, monkeypatch, tmp_path):
        out, base, new = await self._written(monkeypatch, tmp_path)
        assert score_calculator._district_pvi()["TN-9"] == base["TN-9"]
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 120)
        with patch.object(dp, "refresh_district_pvi", new_callable=AsyncMock) as refresh:
            assert await _in_thread(dp.ensure_sitting_lines) == "reselected"
        refresh.assert_not_called()
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new
        assert score_calculator._district_pvi()["TN-9"] == 9

    async def test_pre_pinning_file_triggers_a_refresh(self, monkeypatch, tmp_path):
        out, base, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"_source": "Wikipedia district infoboxes", "districts": {"TN-9": 9}}))
        assert await _in_thread(dp.ensure_sitting_lines) == "refreshed"
        assert json.loads(out.read_text())["districts"]["TN-9"] == base["TN-9"]

    async def test_pre_pinning_file_is_replaced_from_the_bundle_when_offline(self, monkeypatch, tmp_path):
        """The infobox-era file is known to mix maps; with no network the
        checked-in pinned tables replace it rather than it staying live."""
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"districts": {"TN-9": 9, "MO-5": 9}}))
        with patch.object(dp, "refresh_district_pvi", new_callable=AsyncMock, return_value=False):
            assert await _in_thread(dp.ensure_sitting_lines) == "restored from bundle"
        written = json.loads(out.read_text())
        assert written["congress"] == 119
        assert (written["districts"]["TN-9"], written["districts"]["MO-5"]) == (-23, -12)
        assert score_calculator._district_pvi()["TN-9"] == -23

    async def test_failed_refresh_alerts(self, monkeypatch, tmp_path):
        _two_congress_setup(monkeypatch, tmp_path, sitting=121)
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp.ensure_sitting_lines) == "refresh failed"
        assert "121th Congress" in alert.call_args.args[1]


async def _in_thread(fn):
    # ensure_sitting_lines is called from the scheduler's worker thread,
    # where asyncio.run is legal; do the same here.
    import asyncio
    return await asyncio.to_thread(fn)


class TestElectionReader:
    async def test_reads_the_elected_congress_table(self, monkeypatch, tmp_path):
        _, base, new = _two_congress_setup(monkeypatch, tmp_path)
        assert await dp.refresh_district_pvi() is True
        table, meta = dp.district_pvi_for_congress(120)
        assert table == new
        assert meta["lines"] == "lines 202"
        assert dp.district_pvi_for_congress(119)[0] == base

    def test_unconfigured_congress_falls_back_to_sitting_lines(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dp, "_file_cache", {"districts": {"TN-9": -23, "OH-1": 2}})
        assert dp.district_pvi_for_congress(125) == ({"TN-9": -23, "OH-1": 2}, None)

    def test_configured_but_missing_table_drops_redrawn_states(self, monkeypatch, tmp_path):
        """Old-lines numbers for a redrawn state describe a different
        district; those seats fall back to the (labelled) state lean."""
        monkeypatch.setattr(dp, "_file_cache", {"districts": {"TN-9": -23, "OH-1": 2, "MO-5": -12}})
        table, meta = dp.district_pvi_for_congress(120)
        assert meta is None
        assert "TN-9" not in table and "OH-1" not in table
        assert table["MO-5"] == -12


# ── Regression: the 2026-09 mixed-map bug ──────────────────────────────

class TestBundledTablesAreOnTheRightLines:
    """The checked-in fallback, regenerated through the pinned sources.
    Before the fix the single table was a mix: TN-9 R+9 and MO-5 R+9
    (new / blocked maps) beside TX-35 D+19 (old map)."""

    @pytest.fixture()
    def bundled(self):
        return json.loads(BUNDLED.read_text())

    def test_member_scoring_reads_the_119th_congress_lines(self, bundled):
        assert bundled["congress"] == 119
        assert bundled["districts"] == bundled["congresses"]["119"]["districts"]
        d = bundled["districts"]
        assert (d["TX-35"], d["MO-5"], d["UT-1"], d["TN-9"]) == (-19, -12, 10, -23)

    def test_2026_election_reads_the_new_lines(self, bundled):
        d = bundled["congresses"]["120"]["districts"]
        # MO stays on its 2022 map (new map stayed 2026-09-25).
        assert (d["TX-35"], d["MO-5"], d["UT-1"], d["TN-9"]) == (4, -12, -12, 9)

    def test_bundled_tables_pass_every_structural_gate(self, bundled):
        sources = dp.load_sources()
        t119 = bundled["congresses"]["119"]["districts"]
        t120 = bundled["congresses"]["120"]["districts"]
        assert dp.ingestion_gates(t119) == [] and dp.ingestion_gates(t120) == []
        assert dp.cross_congress_gates(t120, t119, sources["congresses"]["120"]["redrawn_states"]) == []
        for c in ("119", "120"):
            assert bundled["congresses"][c]["_revision"]["revid"] == sources["congresses"][c]["revid"]

    def test_scoring_reads_the_bundled_sitting_table(self, bundled, monkeypatch, tmp_path):
        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
        assert score_calculator._seat_pvi("TN", 9) == -23
        assert score_calculator._seat_pvi("TX", 35) == -19
