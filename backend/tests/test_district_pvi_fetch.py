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
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.analyze import score_calculator
from app.pipeline.fetch import district_pvi as dp

PAGE = "Cook Partisan Voting Index"
BUNDLED = dp.SOURCES_PATH.parent / "district_pvi.json"


class _Unclosable:
    """The test's one session, handed to code that closes what it opens."""

    def __init__(self, session):
        self._session = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest.fixture(autouse=True)
def _leases_on_the_test_database(db_session, monkeypatch):
    """The refresh and every House run take the DISTRICT_LINES lease (a
    row in api_cache): give them the test's database."""
    monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))


def _pairs():
    out = []
    for st, n in sorted(dp.SEATS.items()):
        out.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
    return out


def _synthetic_result() -> dict[str, int]:
    """Every seat, alternating R+10 / D+10 — inside the gates' split
    bounds (150-285 each way, out of 435)."""
    return {f"{st}-{d}": (10 if i % 2 == 0 else -10) for i, (st, d) in enumerate(_pairs())}


def _redraw(base: dict[str, int], states) -> dict[str, int]:
    """A redraw of `states` in the synthetic population: two adjacent
    districts trade leans, as when a map moves voters between them — the
    table changes but the state's mean district lean doesn't (a redraw
    moves voters between a state's districts, not out of it). Adjacent
    synthetic seats alternate R+10/D+10, so the last two differ."""
    new = dict(base)
    for st in states:
        n = dp.SEATS[st]
        a, b = f"{st}-{n - 1}", f"{st}-{n}"
        new[a], new[b] = base[b], base[a]
    return new


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

    def _table_with_median_3(self):
        table = {k: (3 if v > 0 else -3) for k, v in _synthetic_result().items()}
        return table, next(k for k, v in table.items() if v == 3)

    def test_median_must_match_exactly_by_default(self):
        table, key = self._table_with_median_3()
        summary = {"r": 218, "d": 217, "even": 0, "median_key": key, "median_pvi": 3}
        assert dp.self_consistency_gates(table, summary) == []
        table[key] = 4  # the sentence names a district that holds R+4; the median is R+3
        summary["median_pvi"] = 4
        assert any("not within 0" in f for f in dp.self_consistency_gates(table, summary))

    def test_a_stated_tolerance_allows_exactly_that_much(self):
        table, key = self._table_with_median_3()
        table[key] = 4
        summary = {"r": 218, "d": 217, "even": 0, "median_key": key, "median_pvi": 4}
        assert dp.self_consistency_gates(table, summary, median_tolerance=1) == []
        table[key] = 5
        summary["median_pvi"] = 5
        assert any("not within 1" in f for f in dp.self_consistency_gates(table, summary, median_tolerance=1))

    def test_a_tolerance_without_its_reason_is_refused(self):
        table = _synthetic_result()
        rev = _revision(table, "L", 5)
        _, failures = dp.check_table(_source(5, "L", median_tolerance=1), rev, PAGE)
        assert any("_why_median_tolerance" in f for f in failures)
        _, failures = dp.check_table(
            _source(5, "L", median_tolerance=1, _why_median_tolerance="a stated reason"), rev, PAGE,
        )
        assert failures == []

    def test_the_120th_pin_tolerance_is_the_only_one_and_says_why(self):
        """Pins transcribe Cook's table, whose median Cook computed from it:
        exact unless a change after the release moved it, and then only
        with the change written down."""
        sources = dp.load_sources()["congresses"]
        assert {c: s.get("median_tolerance", 0) for c, s in sources.items()} == {"119": 0, "120": 1}
        assert "Missouri" in sources["120"]["_why_median_tolerance"]

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
        new = _redraw(base, ["TN"])
        failures = dp.cross_congress_gates(new, base, ["TN", "UT"])
        assert failures == ["redrawn states identical to the base Congress (old lines?): ['UT']"]

    def test_clean_redraw_passes(self):
        base = _synthetic_result()
        assert dp.cross_congress_gates(_redraw(base, ["TN", "UT"]), base, ["TN", "UT"]) == []

    def test_a_redraw_that_moves_only_two_seats_passes(self):
        """No minimum share of changed districts: North Carolina's 2025
        map changed 2 of its 14 districts' PVI."""
        base = _synthetic_result()
        new = _redraw(base, ["NC"])
        assert sum(new[k] != base[k] for k in new if k.startswith("NC-")) == 2
        assert dp.cross_congress_gates(new, base, ["NC"]) == []

    def test_a_half_updated_redrawn_state_fails(self):
        """One district of a redrawn state on the new lines, the rest on
        the old: the voters it gained are still counted where they were,
        so the state's mean lean moves — a redraw alone can't do that."""
        base = _synthetic_result()
        new = dict(base, **{"UT-1": base["UT-1"] - 22})
        failures = dp.cross_congress_gates(new, base, ["UT"])
        assert len(failures) == 1 and "mean district lean" in failures[0] and "UT -5.50" in failures[0]

    def test_a_redrawn_state_missing_a_seat_fails(self):
        base = _synthetic_result()
        new = _redraw(base, ["UT"])
        del new["UT-4"]
        assert dp.cross_congress_gates(new, base, ["UT"]) == [
            "redrawn states missing seats in this or the base table: ['UT']"
        ]


class TestCookWindow:
    def test_a_redraw_on_a_different_cook_window_is_refused_with_its_own_message(self):
        """A new window moves every district, so the "states that did not
        redraw" check would list hundreds of seats — say what is wrong."""
        base = _synthetic_result()
        failures = dp.cross_congress_gates(
            {k: v + 1 for k, v in base.items()}, base, ["TN"], window="2024+2028", base_window="2020+2024",
        )
        assert len(failures) == 1 and "Cook window 2024+2028 differs" in failures[0]
        assert "drop redrawn_from" in failures[0]

    def test_check_table_reads_the_base_entrys_window(self):
        base = _synthetic_result()
        new = _redraw(base, ["TN"])
        src = _source(202, "2026 Cook PVI", redrawn_from="119", redrawn_states=["TN"], window="2024+2028")
        _, failures = dp.check_table(src, _revision(new, "2026 Cook PVI", 202), PAGE, base, _source(101, "x"))
        assert any("Cook window 2024+2028 differs from the base Congress's 2020+2024" in f for f in failures)
        _, failures = dp.check_table(
            dict(src, window="2020+2024"), _revision(new, "2026 Cook PVI", 202), PAGE, base, _source(101, "x"),
        )
        assert failures == []

    def test_the_real_pins_share_one_window(self):
        sources = dp.load_sources()["congresses"]
        for c, src in sources.items():
            if src.get("redrawn_from"):
                assert src["window"] == sources[src["redrawn_from"]]["window"], c


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
    """sitting=None leaves the sitting Congress to the (patched) clock."""
    base = _synthetic_result()
    new = _redraw(base, ["TN", "UT"])
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
    if sitting is not None:
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

    async def test_sitting_congress_without_a_source_still_writes_the_configured_tables(self, monkeypatch, tmp_path):
        """A missing entry can't fail the weekly refresh wholesale: the
        configured tables are still (re)validated and the live-drift check
        still runs; members stay on the newest pinned lines."""
        out, _, new = _two_congress_setup(monkeypatch, tmp_path, sitting=121)
        with patch.object(dp, "_check_live_drift", new_callable=AsyncMock) as drift:
            assert await dp.refresh_district_pvi() is True
        drift.assert_awaited_once()
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new
        assert set(written["congresses"]) == {"119", "120"}

    async def test_a_sitting_congress_older_than_every_pin_writes_nothing(self, monkeypatch, tmp_path):
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=118)
        assert await dp.refresh_district_pvi() is False
        assert not out.exists()

    async def test_any_gate_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        base = _synthetic_result()
        mixed = dict(_redraw(base, ["TN", "UT"]), **{"MO-5": -base["MO-5"]})  # MO didn't redraw
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
        edited = dict(_redraw(base, ["TN", "UT"]), **{"TX-35": 4})
        live = _revision(edited, "2026 Cook PVI", 303)
        out, _, new = _two_congress_setup(monkeypatch, tmp_path, live=live)
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await dp.refresh_district_pvi() is True
        assert "TX-35" in alert.call_args.args[1]
        assert json.loads(out.read_text())["congresses"]["120"]["districts"] == new

    async def test_drift_alert_is_keyed_on_the_difference_not_the_revision(self, monkeypatch, tmp_path):
        """An unrelated edit elsewhere in the article makes a new live
        revid with the same table difference: same key, so one alert. A
        different difference is a new key."""
        base = _synthetic_result()
        edited = dict(_redraw(base, ["TN", "UT"]), **{"TX-35": 4})
        keys = []
        for revid, table in ((303, edited), (304, edited), (305, dict(edited, **{"TX-9": 1}))):
            _two_congress_setup(monkeypatch, tmp_path, live=_revision(table, "2026 Cook PVI", revid))
            with patch("app.ops_alerts.send_ops_alert") as alert:
                assert await dp.refresh_district_pvi() is True
            keys.append(alert.call_args.kwargs["dedupe_key"])
        assert keys[0] == keys[1]
        assert keys[2] != keys[0]

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
        with patch.object(dp, "_refresh", new_callable=AsyncMock) as refresh:
            assert await _in_thread(dp._ensure_sitting_lines) == "current"
        refresh.assert_not_called()
        assert out.read_text() == before

    async def test_new_congress_switches_locally_at_noon_et_on_jan_3(self, monkeypatch, tmp_path):
        """Driven by the clock, not a patched Congress number and not
        settings.CURRENT_CONGRESS (computed once at process start): the
        same process switches at noon Eastern on Jan 3, 2027, no restart,
        no fetch."""
        clock = {"now": datetime(2026, 12, 31, 12)}
        monkeypatch.setattr("app.time_utils.utcnow", lambda: clock["now"])
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=None)
        assert await dp.refresh_district_pvi() is True
        assert json.loads(out.read_text())["congress"] == 119
        with patch.object(dp, "_refresh", new_callable=AsyncMock) as refresh:
            clock["now"] = datetime(2027, 1, 1, 8)  # Jan 1: still the 119th
            assert await _in_thread(dp._ensure_sitting_lines) == "current"
            clock["now"] = datetime(2027, 1, 3, 16, 59)  # 11:59 ET
            assert await _in_thread(dp._ensure_sitting_lines) == "current"
            assert score_calculator._district_pvi()["TN-9"] == base["TN-9"]
            clock["now"] = datetime(2027, 1, 3, 17, 0)  # noon ET
            assert await _in_thread(dp._ensure_sitting_lines) == "reselected"
            assert await _in_thread(dp._ensure_sitting_lines) == "current"
        refresh.assert_not_called()
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new
        assert score_calculator._district_pvi()["TN-9"] == new["TN-9"] != base["TN-9"]

    async def test_a_pinned_current_congress_holds_the_lines(self, monkeypatch, tmp_path):
        """An operator's env pin (archived-DB re-run) wins over the clock."""
        from app import config

        monkeypatch.setattr("app.time_utils.utcnow", lambda: datetime(2027, 6, 1))
        monkeypatch.setattr(config, "settings", config.Settings(CURRENT_CONGRESS=119))
        out, base, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=None)
        assert await dp.refresh_district_pvi() is True
        assert json.loads(out.read_text())["districts"] == base
        with patch.object(dp, "_refresh", new_callable=AsyncMock):
            assert await _in_thread(dp._ensure_sitting_lines) == "current"

    async def test_pre_pinning_file_triggers_a_refresh(self, monkeypatch, tmp_path):
        out, base, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"_source": "Wikipedia district infoboxes", "districts": {"TN-9": 9}}))
        assert await _in_thread(dp._ensure_sitting_lines) == "refreshed"
        assert json.loads(out.read_text())["districts"]["TN-9"] == base["TN-9"]

    async def test_pre_pinning_file_is_replaced_from_the_bundle_when_offline(self, monkeypatch, tmp_path):
        """The infobox-era file is known to mix maps; with no network the
        checked-in pinned tables replace it rather than it staying live."""
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"districts": {"TN-9": 9, "MO-5": 9}}))
        with patch.object(dp, "_refresh", new_callable=AsyncMock, return_value=False):
            assert await _in_thread(dp._ensure_sitting_lines) == "restored from bundle"
        written = json.loads(out.read_text())
        assert written["congress"] == 119
        assert (written["districts"]["TN-9"], written["districts"]["MO-5"]) == (-23, -12)
        assert score_calculator._district_pvi()["TN-9"] == -23

    async def test_failed_refresh_alerts(self, monkeypatch, tmp_path):
        async def down(**kw):
            return None

        _two_congress_setup(monkeypatch, tmp_path, sitting=120)
        monkeypatch.setattr(dp, "_fetch_revision", down)
        monkeypatch.setattr(dp, "BUNDLED_PATH", tmp_path / "no-bundle.json")
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "refresh failed"
        assert "120th Congress" in alert.call_args.args[1]

    async def test_unconfigured_congress_alerts_once_per_congress_and_never_fetches(self, monkeypatch, tmp_path):
        """Nothing can be fetched for a Congress the sources file doesn't
        name, so nothing is — night after night — and the alert's key is
        the Congress, not the day. Members stay on the newest pinned lines."""
        out, _, new = await self._written(monkeypatch, tmp_path)
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 121)
        with patch.object(dp, "_refresh", new_callable=AsyncMock) as refresh, \
             patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        refresh.assert_not_called()
        assert {c.kwargs["dedupe_key"] for c in alert.call_args_list} == {"district-pvi-no-source-121"}
        assert "121st Congress" in alert.call_args.args[1]
        assert "120th Congress's lines" in alert.call_args.args[1]
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new

    async def test_unconfigured_congress_with_a_pre_pinning_file_still_restores_pinned_tables(
        self, monkeypatch, tmp_path,
    ):
        out, _, new = _two_congress_setup(monkeypatch, tmp_path, sitting=121)
        out.write_text(json.dumps({"districts": {"TN-9": 9, "MO-5": 9}}))
        with patch("app.ops_alerts.send_ops_alert"):
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new

    async def test_an_advanced_pin_is_fetched_before_the_next_run_not_next_sunday(self, monkeypatch, tmp_path):
        """The file holds the sitting Congress's table, but the sources file
        now pins a different revision for a Congress (a correction, a court
        ruling): that is not "current"."""
        out, _, _ = await self._written(monkeypatch, tmp_path)
        data = json.loads(out.read_text())
        data["congresses"]["120"]["_revision"]["revid"] = 199  # written from an older pin
        out.write_text(json.dumps(data))
        assert await _in_thread(dp._ensure_sitting_lines) == "refreshed"
        assert json.loads(out.read_text())["congresses"]["120"]["_revision"]["revid"] == 202
        assert await _in_thread(dp._ensure_sitting_lines) == "current"

    async def test_a_congress_added_to_the_sources_file_is_fetched(self, monkeypatch, tmp_path):
        out, _, _ = await self._written(monkeypatch, tmp_path)
        data = json.loads(out.read_text())
        del data["congresses"]["120"]
        out.write_text(json.dumps(data))
        with patch.object(dp, "_refresh", new_callable=AsyncMock, return_value=True) as refresh:
            assert await _in_thread(dp._ensure_sitting_lines) == "refreshed"
        refresh.assert_awaited_once()

    async def test_a_stale_pin_does_not_reselect_at_the_switch(self, monkeypatch, tmp_path):
        """Jan 3 with a stale 120th pin on file: fetch the pinned table, don't
        copy the stale one up."""
        out, _, _ = await self._written(monkeypatch, tmp_path)
        data = json.loads(out.read_text())
        data["congresses"]["120"]["_revision"]["revid"] = 199
        out.write_text(json.dumps(data))
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 120)
        with patch.object(dp, "_refresh", new_callable=AsyncMock, return_value=True) as refresh:
            assert await _in_thread(dp._ensure_sitting_lines) == "refreshed"
        refresh.assert_awaited_once()

    async def test_a_stale_pin_falls_back_to_the_bundle_when_offline(self, monkeypatch, tmp_path):
        out, _, _ = await self._written(monkeypatch, tmp_path)
        data = json.loads(out.read_text())
        data["congresses"]["119"]["_revision"]["revid"] = 99
        out.write_text(json.dumps(data))
        with patch.object(dp, "_refresh", new_callable=AsyncMock, return_value=False):
            assert await _in_thread(dp._ensure_sitting_lines) == "restored from bundle"
        assert json.loads(out.read_text())["congress"] == 119

    async def test_unconfigured_congress_with_a_stale_pin_on_file_refreshes_the_configured_ones(
        self, monkeypatch, tmp_path,
    ):
        out, _, _ = await self._written(monkeypatch, tmp_path)
        data = json.loads(out.read_text())
        data["congresses"]["120"]["_revision"]["revid"] = 199
        out.write_text(json.dumps(data))
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 121)
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        written = json.loads(out.read_text())
        assert written["congresses"]["120"]["_revision"]["revid"] == 202 and written["congress"] == 120
        assert "120th Congress's lines" in alert.call_args.args[1]

    async def test_an_environment_pin_older_than_every_pinned_table_says_so(self, monkeypatch, tmp_path):
        """CURRENT_CONGRESS=118 pinned for an archived re-run: no table
        describes the 118th's lines, and the alert must not call the file's
        119th table "the latest pinned" lines for it."""
        from app import config

        out, _, _ = await self._written(monkeypatch, tmp_path)
        monkeypatch.setattr(config, "settings", config.Settings(CURRENT_CONGRESS=118))
        monkeypatch.setattr(dp, "_sitting_congress", config.sitting_congress)
        before = out.read_text()
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        text = alert.call_args.args[1]
        assert "CURRENT_CONGRESS is pinned in the environment to 118" in text
        assert "the earliest is the 119th" in text
        assert "which are NOT the 118th's" in text and "latest pinned" not in text
        assert out.read_text() == before


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

    async def test_provenance_dates_the_table_by_its_revision_not_the_fetch(self, monkeypatch, tmp_path):
        _two_congress_setup(monkeypatch, tmp_path)
        assert await dp.refresh_district_pvi() is True
        _, meta = dp.district_pvi_for_congress(120)
        assert meta["asOf"] == "2026-01-01T00:00:00Z"  # the pinned revision's timestamp
        assert meta["revision"]["revid"] == 202
        assert meta["fetchedOn"] and meta["fetchedOn"] != meta["asOf"]
        assert (meta["congress"], meta["forCongress"]) == (120, 120)

    async def test_an_unpinned_later_congress_gets_the_latest_lines_not_the_sitting_ones(self, monkeypatch, tmp_path):
        """The day after the 2026 election the elections pages ask for the
        121st Congress (2028's). With no pin for it, the latest lines known
        are the 120th's — the ones just voted on — never the sitting 119th's,
        which would put every redrawn state back on its old map."""
        _, base, new = _two_congress_setup(monkeypatch, tmp_path)
        assert await dp.refresh_district_pvi() is True
        table, meta = dp.district_pvi_for_congress(121)
        assert table == new and table != base
        assert (meta["congress"], meta["forCongress"]) == (120, 121)

    def test_an_unpinned_later_congress_drops_states_redrawn_for_it(self, monkeypatch):
        blocks = {"120": {"districts": {"TN-9": 9, "OH-1": 1}, "_revision": {}}}
        monkeypatch.setattr(dp, "_file_cache", {"congress": 120, "congresses": blocks})
        monkeypatch.setattr(dp, "load_sources", lambda: {"congresses": {
            "120": {}, "121": {"redrawn_states": ["OH"]},
        }})
        table, meta = dp.district_pvi_for_congress(121)
        assert table == {"TN-9": 9}
        assert meta["omittedRedrawnStates"] == ["OH"]

    def test_a_congress_older_than_every_pin_gets_no_district_table(self, monkeypatch):
        monkeypatch.setattr(dp, "_file_cache", {"congresses": {"119": {"districts": {"TN-9": -23}}}})
        assert dp.district_pvi_for_congress(118) == ({}, None)

    def test_pre_pinning_file_serves_its_one_table(self, monkeypatch, tmp_path):
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

    def test_the_top_level_is_the_table_of_the_congress_it_names(self, bundled):
        """scripts/fetch_district_pvi.py --congress N puts N's table (the
        newest at or below N) at the top level and says so in "congress" —
        whenever it is regenerated, not only while the 119th sits."""
        blocks = bundled["congresses"]
        assert str(bundled["congress"]) in blocks
        assert bundled["districts"] == blocks[str(bundled["congress"])]["districts"]
        assert bundled["_lines"] == blocks[str(bundled["congress"])]["_lines"]

    def test_119th_congress_members_are_scored_on_the_lines_they_were_elected_on(self, bundled):
        """The 2026-09 regression, on the 119th's own table and on what a
        House run selects from the bundle while the 119th sits (_reselect,
        the "restored from bundle" path) — neither depends on which
        Congress sat when the bundle was regenerated."""
        d = bundled["congresses"]["119"]["districts"]
        assert (d["TX-35"], d["MO-5"], d["UT-1"], d["TN-9"]) == (-19, -12, 10, -23)
        selected = dp._reselect(bundled, 119)
        assert selected["congress"] == 119 and selected["districts"] == d

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

    def test_real_pins_conserve_each_redrawn_states_mean_and_a_half_update_would_not(self, bundled):
        """REDRAW_MEAN_SHIFT_MAX against the real 2026 redraws: all nine
        pass; the 120th table with only TN-9 moved to the new map (the
        shape of an edit caught half-way) does not."""
        t119 = bundled["congresses"]["119"]["districts"]
        t120 = bundled["congresses"]["120"]["districts"]
        redrawn = dp.load_sources()["congresses"]["120"]["redrawn_states"]
        assert dp.cross_congress_gates(t120, t119, redrawn) == []
        half = dict(t119, **{"TN-9": t120["TN-9"]})
        failures = dp.cross_congress_gates(half, t119, redrawn)
        assert any("mean district lean" in f and "TN +3.56" in f for f in failures)

    def test_scoring_reads_the_bundled_top_level_table(self, bundled, monkeypatch, tmp_path):
        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
        top = bundled["districts"]
        assert score_calculator._seat_pvi("TN", 9) == top["TN-9"]
        assert score_calculator._seat_pvi("TX", 35) == top["TX-35"]

    def test_the_regeneration_script_names_its_congress_explicitly(self):
        """Not the clock: a default of sitting_congress() made the bundle's
        top level — and these tests — depend on the day it was run."""
        import subprocess
        import sys

        script = dp.SOURCES_PATH.parents[2] / "scripts" / "fetch_district_pvi.py"
        out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        assert out.returncode != 0 and "--congress" in out.stderr


# ── Every House run settles the lines under a lease it holds throughout ──

class TestHouseRunsHoldTheLines:
    async def test_the_lines_are_settled_before_the_house_scores(self, monkeypatch):
        order = []
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: order.append("pvi") or "current")

        async def house():
            order.append("house")
            return {"status": "completed"}

        assert await dp.run_house_on_sitting_lines(house) == {"status": "completed"}
        assert order == ["pvi", "house"]

    async def test_nothing_rewrites_the_lines_while_a_house_run_scores(self, monkeypatch, tmp_path):
        """While a House run holds the lease, a refresh writes nothing and a
        second House run is refused without touching the file."""
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path)
        seen = {}
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")

        async def second_house():
            raise AssertionError("must not run")

        async def house():
            seen["refresh"] = await dp.refresh_district_pvi()
            seen["second"] = await dp.run_house_on_sitting_lines(second_house)
            return {"status": "completed"}

        await dp.run_house_on_sitting_lines(house)
        assert seen["refresh"] is False and not out.exists()
        # The skip names its holder, from the same read as the code.
        assert seen["second"] == {"status": "skipped", "reason": "held_elsewhere", "holder": "House run"}
        # Released afterwards.
        assert await dp.refresh_district_pvi() is True

    async def test_a_house_run_started_without_the_lease_is_left_alone(self, monkeypatch):
        """A House run another process started without this lease (an older
        image mid-rollout): skip, and don't touch its lines."""
        monkeypatch.setattr("app.pipeline.run_tracker.run_in_progress", lambda db, model, *a: True)
        called = []
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: called.append(1))

        async def house():
            raise AssertionError("must not run")

        assert await dp.run_house_on_sitting_lines(house) == {"status": "skipped", "reason": "already_running"}
        assert called == []

    async def test_a_failing_check_does_not_stop_the_run(self, monkeypatch):
        def boom():
            raise RuntimeError("disk")

        monkeypatch.setattr(dp, "_ensure_sitting_lines", boom)

        async def house():
            return {"status": "completed"}

        assert await dp.run_house_on_sitting_lines(house) == {"status": "completed"}


class TestAHouseRunWaitsForARefresh:
    """A District PVI refresh holding the lines is minutes of work: a House
    run waits for it (bounded) instead of skipping — which in the nightly
    chain also ended the chain before Stock trades and Election."""

    async def test_waits_for_the_refresh_then_runs(self, monkeypatch, db_session):
        import asyncio

        from app.pipeline import lease

        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        assert token is not None

        async def finish_refresh():
            await asyncio.sleep(0.05)
            lease.release(db_session, lease.DISTRICT_LINES, token)

        async def house():
            return {"status": "completed"}

        releaser = asyncio.create_task(finish_refresh())
        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=5, poll_s=0.01)
        await releaser
        assert result == {"status": "completed"}

    async def test_a_refresh_that_outlasts_the_wait_is_named_in_the_skip(self, db_session):
        from app.pipeline import lease
        from app.pipeline.run_tracker import skip_reason_text

        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO) is not None

        async def house():
            raise AssertionError("must not run")

        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=0.03, poll_s=0.01)
        assert result == {"status": "skipped", "reason": "held_elsewhere", "holder": dp.REFRESH_WHO}
        text = skip_reason_text(result["reason"], who=result["holder"])
        assert text.startswith("District PVI refresh is already running")

    async def test_another_house_run_is_not_waited_for(self, db_session):
        import time

        from app.pipeline import lease

        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO) is not None

        async def house():
            raise AssertionError("must not run")

        started = time.monotonic()
        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=5, poll_s=0.5)
        assert time.monotonic() - started < 0.5
        assert result == {"status": "skipped", "reason": "held_elsewhere", "holder": "House run"}


class TestTheLeaseSaysWhoHoldsIt:
    def test_a_refusal_carries_its_code_and_holder_from_one_read(self, db_session):
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        with lease.job(lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO) as granted:
            assert not granted
            assert (granted.code, granted.holder) == (lease.REFUSED_HELD, dp.REFRESH_WHO)
            assert granted.why.startswith("District PVI refresh is already running")

    async def test_job_async_too(self, db_session):
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO)
        async with lease.job_async(lease.DISTRICT_LINES, who=dp.REFRESH_WHO) as granted:
            assert (granted.code, granted.holder) == (lease.REFUSED_HELD, "House run")

    def test_the_district_lines_lease_goes_stale_after_an_hour_like_the_senate_runs(self):
        """Held for a whole House run, whose beats stall behind SQLite
        writers like the Senate run's: taken over at ten minutes, a refresh
        could rewrite the lines under a run still scoring."""
        from datetime import timedelta

        from app.pipeline import lease

        assert lease.stale_after(lease.DISTRICT_LINES) == timedelta(minutes=60) == lease.stale_after(lease.SENATE_RUN)
        assert lease.max_hold(lease.DISTRICT_LINES) > timedelta(0)


class TestStoredScoresKeepTheirLines:
    """A House score records the Congress whose lines it used, and the
    breakdown recomputes on those lines — between a switch and the House
    run that rescores a member (or after one that failed), and for a member
    who left when the lines changed."""

    def _file(self, monkeypatch, tmp_path, sitting):
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=sitting)
        blocks = {
            "119": {"_source": "s", "_lines": "old", "_window": "w", "districts": base},
            "120": {"_source": "s", "_lines": "new", "_window": "w", "districts": new},
        }
        out.write_text(json.dumps(dp._payload(blocks, sitting)))
        dp._reset_caches()
        return out, base, new

    def test_lines_of_reads_another_congress_in_this_context_only(self, monkeypatch, tmp_path):
        import threading

        _, base, new = self._file(monkeypatch, tmp_path, 120)
        assert base["TN-9"] != new["TN-9"]
        assert dp.lines_congress() == 120
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]
        seen = {}
        with dp.lines_of(119) as congress:
            assert congress == 119
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
            # A House run scoring in another thread meanwhile is unaffected.
            t = threading.Thread(target=lambda: seen.setdefault("other", score_calculator._seat_pvi("TN", 9)))
            t.start()
            t.join()
        assert seen["other"] == new["TN-9"]
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]
        # States that didn't redraw read the same either way.
        with dp.lines_of(119):
            assert score_calculator._seat_pvi("CA", 12) == new["CA-12"] == base["CA-12"]

    def test_unrecorded_or_missing_lines_read_the_current_table(self, monkeypatch, tmp_path):
        _, _, new = self._file(monkeypatch, tmp_path, 120)
        for congress in (None, 120, 117):
            with dp.lines_of(congress) as used:
                assert used == 120
                assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]

    def test_the_house_run_records_the_lines_it_scored_on(self, monkeypatch, tmp_path, db_session):
        from app.models import Representative
        from app.services.representative_service import upsert_representative

        self._file(monkeypatch, tmp_path, 119)
        upsert_representative(db_session, {"id": "tn9", "name": "M", "state": "TN", "district": 9, "party": "D"})
        db_session.commit()
        assert db_session.get(Representative, "tn9").district_lines_congress == 119

    def test_a_startup_rescore_records_the_lines_it_rescored_on(self, monkeypatch, tmp_path, db_session):
        """main's startup Constituent Alignment rescore rewrites current
        members' scores on the lines in effect; a former member keeps the
        lines their score was stored on."""
        from app.models import Representative

        self._file(monkeypatch, tmp_path, 120)
        db_session.add_all([
            Representative(id="cur", name="C", state="TN", district=9, party="D", is_current=True,
                           district_lines_congress=119),
            Representative(id="gone", name="G", state="TN", district=8, party="R", is_current=False,
                           district_lines_congress=119),
        ])
        db_session.commit()
        dp.stamp_house_lines(lambda: _Unclosable(db_session))
        db_session.expire_all()
        assert db_session.get(Representative, "cur").district_lines_congress == 120
        assert db_session.get(Representative, "gone").district_lines_congress == 119

    def test_the_breakdown_uses_the_stored_scores_lines(self, monkeypatch, tmp_path):
        """Stored on the 119th's lines; the file has since switched to the
        120th: the breakdown's Constituent Alignment reads the 119th's."""
        from types import SimpleNamespace

        from app.services import _scorecard_common

        _, base, new = self._file(monkeypatch, tmp_path, 120)
        seen = []
        monkeypatch.setattr(_scorecard_common, "build_score_breakdown_entity", lambda e, **kw: {
            "district": e.district, "state": e.state, "votingRecord": {"partyLineRecord": None},
        })
        monkeypatch.setattr(_scorecard_common, "explain_scores", lambda d: seen.append(
            score_calculator._seat_pvi(d["state"], d["district"])) or {})
        for recorded in (119, 120, None):
            rep = SimpleNamespace(state="TN", district=9, district_lines_congress=recorded)
            _scorecard_common.score_breakdown(None, rep, lobbying_donation_attr="donation_to_representative")
        assert seen == [base["TN-9"], new["TN-9"], new["TN-9"]]

    async def test_the_house_run_rereads_the_lines_under_its_lease(self, monkeypatch, tmp_path):
        """Another process may have switched the file since this one cached
        it: the run scores (and records) what the file holds now."""
        out, _, new = self._file(monkeypatch, tmp_path, 119)
        assert dp.lines_congress() == 119
        # Another process writes the 120th's lines (under the lease, before
        # this run takes it) — this process's caches never hear of it.
        out.write_text(json.dumps(dp._reselect(json.loads(out.read_text()), 120)))
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        seen = {}

        async def house():
            seen["congress"] = dp.lines_congress()
            seen["tn9"] = score_calculator._seat_pvi("TN", 9)
            return {"status": "completed"}

        await dp.run_house_on_sitting_lines(house)
        assert seen == {"congress": 120, "tn9": new["TN-9"]}


class TestTriggeredRunsCheckTheSittingLines:
    @pytest.fixture()
    def recorded(self, monkeypatch):
        order, captured = [], {}
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: order.append("pvi"))

        async def house():
            order.append("house")
            return {}

        async def senate(**kw):
            order.append("senate")
            return {"status": "completed"}

        async def supp():
            order.append("supplementary")
            return {}

        monkeypatch.setattr("app.pipeline.house_pipeline.run_house_pipeline", house)
        monkeypatch.setattr("app.pipeline.supplementary_pipeline.run_supplementary_pipeline", supp)
        monkeypatch.setattr("app.pipeline.senate_pipeline.run_senate_pipeline", senate)
        monkeypatch.setattr("app.api.pipeline.run_senate_pipeline", senate)
        for mod in ("app.api.admin", "app.api.pipeline"):
            monkeypatch.setattr(f"{mod}.run_pipeline_in_thread", lambda f, **kw: captured.setdefault("run", f))
        return order, captured

    async def test_house_trigger(self, recorded, db_session):
        from app.api import admin

        order, captured = recorded
        await admin.admin_trigger_house_pipeline(db=db_session)
        await captured["run"]()
        assert order == ["pvi", "house"]

    async def test_house_trigger_refuses_while_a_house_run_holds_the_lines(self, recorded, db_session):
        from fastapi import HTTPException

        from app.api import admin
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who="House run")
        with pytest.raises(HTTPException) as err:
            await admin.admin_trigger_house_pipeline(db=db_session)
        assert err.value.status_code == 409
        assert err.value.detail.startswith("House run is already running")
        assert "run" not in recorded[1]

    async def test_house_trigger_waits_for_a_refresh_rather_than_refusing(self, recorded, db_session):
        """A refresh holding the lines is not "the House pipeline is already
        running": the run is started, and waits for it."""
        from app.api import admin
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        answer = await admin.admin_trigger_house_pipeline(db=db_session)
        assert "District PVI refresh" in answer["message"] and "run" in recorded[1]

    async def test_admin_trigger(self, recorded, db_session):
        """The lines are settled right before the House run, under its lease
        — not before the Senate run takes its lock, where a network refresh
        would widen the double-trigger window."""
        from app.api import admin

        order, captured = recorded
        await admin.admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        await captured["run"]()
        assert order == ["senate", "supplementary", "pvi", "house"]

    async def test_token_trigger(self, recorded, db_session, monkeypatch):
        from app.api import pipeline
        from app.config import settings

        order, captured = recorded
        monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "t")
        await pipeline.trigger_pipeline(authorization="Bearer t", senator=None, fetch_only=False, db=db_session)
        await captured["run"]()
        assert order == ["senate", "pvi", "house"]
