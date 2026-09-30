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

import asyncio
import json
from collections import Counter
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.analyze import score_calculator
from app.pipeline.fetch import district_pvi as dp
from app.state_names import STATE_NAMES

PAGE = "Cook Partisan Voting Index"
BUNDLED = dp.SOURCES_PATH.parent / "district_pvi.json"
# The real apportionment, counted from the bundled 119th-Congress table (the
# module reads it from the House Clerk's member list, house_seats): these
# tests build tables for real states, TN and UT among them.
SEATS = dict(Counter(k.split("-")[0] for k in json.loads(BUNDLED.read_text())["congresses"]["119"]["districts"]))


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
    monkeypatch.setattr(dp, "house_seats", AsyncMock(return_value=SEATS))


def _pairs():
    out = []
    for st, n in sorted(SEATS.items()):
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
        n = SEATS[st]
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
            f"|-\n| {{{{ushr|{STATE_NAMES[s]}|{'AL' if n == '0' else n}|X}}}}\n| {_cell(v, i)}\n"
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


class TestIngestionGates:
    def test_clean_synthetic_population_passes_gates(self):
        assert dp.ingestion_gates(_synthetic_result(), SEATS) == []

    def test_missing_districts_fails_coverage_gate(self):
        result = _synthetic_result()
        del result["CA-1"]
        assert any("expected 435" in f for f in dp.ingestion_gates(result, SEATS))

    def test_district_outside_apportionment_fails(self):
        result = _synthetic_result()
        del result["CA-1"]
        result["CA-53"] = 5
        failures = dp.ingestion_gates(result, SEATS)
        assert any("CA-53" in f for f in failures)

    def test_out_of_range_value_fails_gate(self):
        result = _synthetic_result()
        result["CA-1"] = 90
        assert any("plausible" in f for f in dp.ingestion_gates(result, SEATS))

    def test_lopsided_lean_split_fails_gate(self):
        result = {k: 10 for k in _synthetic_result()}
        assert any("lean split" in f for f in dp.ingestion_gates(result, SEATS))


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
        _, failures = dp.check_table(_source(5, "L", median_tolerance=1), rev, PAGE, seats=SEATS)
        assert any("_why_median_tolerance" in f for f in failures)
        _, failures = dp.check_table(
            _source(5, "L", median_tolerance=1, _why_median_tolerance="a stated reason"), rev, PAGE, seats=SEATS,
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
        failures = dp.cross_congress_gates(new, base, ["TN"], SEATS)
        assert any("did not redraw" in f and "MO-5" in f for f in failures)

    def test_redrawn_state_left_on_old_lines_fails(self):
        base = _synthetic_result()
        new = _redraw(base, ["TN"])
        failures = dp.cross_congress_gates(new, base, ["TN", "UT"], SEATS)
        assert failures == ["redrawn states identical to the base Congress (old lines?): ['UT']"]

    def test_clean_redraw_passes(self):
        base = _synthetic_result()
        assert dp.cross_congress_gates(_redraw(base, ["TN", "UT"]), base, ["TN", "UT"], SEATS) == []

    def test_a_redraw_that_moves_only_two_seats_passes(self):
        """No minimum share of changed districts: North Carolina's 2025
        map changed 2 of its 14 districts' PVI."""
        base = _synthetic_result()
        new = _redraw(base, ["NC"])
        assert sum(new[k] != base[k] for k in new if k.startswith("NC-")) == 2
        assert dp.cross_congress_gates(new, base, ["NC"], SEATS) == []

    def test_a_half_updated_redrawn_state_fails(self):
        """One district of a redrawn state on the new lines, the rest on
        the old: the voters it gained are still counted where they were,
        so the state's mean lean moves — a redraw alone can't do that."""
        base = _synthetic_result()
        new = dict(base, **{"UT-1": base["UT-1"] - 22})
        failures = dp.cross_congress_gates(new, base, ["UT"], SEATS)
        assert len(failures) == 1 and "mean district lean" in failures[0] and "UT -5.50" in failures[0]

    def test_a_redrawn_state_missing_a_seat_fails(self):
        base = _synthetic_result()
        new = _redraw(base, ["UT"])
        del new["UT-4"]
        assert dp.cross_congress_gates(new, base, ["UT"], SEATS) == [
            "redrawn states missing seats in this or the base table: ['UT']"
        ]


class TestCookWindow:
    def test_a_redraw_on_a_different_cook_window_is_refused_with_its_own_message(self):
        """A new window moves every district, so the "states that did not
        redraw" check would list hundreds of seats — say what is wrong."""
        base = _synthetic_result()
        failures = dp.cross_congress_gates(
            {k: v + 1 for k, v in base.items()}, base, ["TN"], SEATS, window="2024+2028", base_window="2020+2024",
        )
        assert len(failures) == 1 and "Cook window 2024+2028 differs" in failures[0]
        assert "drop redrawn_from" in failures[0]

    def test_check_table_reads_the_base_entrys_window(self):
        base = _synthetic_result()
        new = _redraw(base, ["TN"])
        src = _source(202, "2026 Cook PVI", redrawn_from="119", redrawn_states=["TN"], window="2024+2028")
        _, failures = dp.check_table(src, _revision(new, "2026 Cook PVI", 202), PAGE, base, _source(101, "x"), seats=SEATS)
        assert any("Cook window 2024+2028 differs from the base Congress's 2020+2024" in f for f in failures)
        _, failures = dp.check_table(
            dict(src, window="2020+2024"), _revision(new, "2026 Cook PVI", 202), PAGE, base, _source(101, "x"), seats=SEATS,
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
    monkeypatch.setattr(score_calculator, "_district_pvi_stamp", None)
    monkeypatch.setattr(dp, "_file_cache", None)
    monkeypatch.setattr(dp, "_file_stamp", None)
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
        """No table describes the 118th's lines, so nothing is written —
        but the pinned tables are still fetched and gated and the live-drift
        check still runs, so a pin that needs advancing isn't hidden for as
        long as an environment pin stands."""
        from app import config

        monkeypatch.setattr(config, "settings", config.Settings(CURRENT_CONGRESS=118))
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=118)
        with patch.object(dp, "_check_live_drift", new_callable=AsyncMock) as drift:
            assert await dp.refresh_district_pvi() is False
        drift.assert_awaited_once()
        assert set(drift.await_args.args[1]["congresses"]) == {"119", "120"}
        assert not out.exists()

    async def test_any_gate_failure_keeps_previous_data(self, monkeypatch, tmp_path):
        base = _synthetic_result()
        mixed = dict(_redraw(base, ["TN", "UT"]), **{"MO-5": -base["MO-5"]})  # MO didn't redraw
        rev = _revision(mixed, "2026 Cook PVI", 202)
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, rev120=rev)
        out.write_text(json.dumps({"districts": {"KEEP-0": 5}}))
        assert await dp.refresh_district_pvi() is False
        assert json.loads(out.read_text())["districts"] == {"KEEP-0": 5}

    async def test_unreadable_apportionment_keeps_previous_data(self, monkeypatch, tmp_path):
        """The gates need the House's seats; with the Clerk's list
        unreadable there is nothing to check a table against, so nothing is
        written — never a fallback seat count."""
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"districts": {"KEEP-0": 5}}))
        monkeypatch.setattr(dp, "house_seats", AsyncMock(return_value={}))
        assert await dp.refresh_district_pvi() is False
        assert json.loads(out.read_text())["districts"] == {"KEEP-0": 5}
        payload, failures = await dp.build_payload(dp.load_sources(), 119)
        assert payload is None and failures == ["no apportionment from the House Clerk's member list"]

    async def test_a_changed_apportionment_fails_the_gates(self, monkeypatch, tmp_path):
        """Seats come from the Clerk each refresh: a table that no longer
        matches the House's apportionment is refused."""
        out, _, _ = _two_congress_setup(monkeypatch, tmp_path)
        out.write_text(json.dumps({"districts": {"KEEP-0": 5}}))
        monkeypatch.setattr(dp, "house_seats", AsyncMock(return_value=dict(SEATS, TX=SEATS["TX"] + 1)))
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
        """Driven by the clock, not a patched Congress number: a process
        started in December switches at the first job after noon Eastern on
        Jan 3, 2027 (app.config.scoring_congress advances the Congress the
        job holds), no restart, no fetch."""
        from app import config

        clock = {"now": datetime(2026, 12, 31, 12)}
        monkeypatch.setattr("app.time_utils.utcnow", lambda: clock["now"])
        monkeypatch.setattr(config, "settings", config.Settings())
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=None)

        def job(fn):
            with config.scoring_congress():
                return fn()

        assert await _in_thread(lambda: job(lambda: asyncio.run(dp.refresh_district_pvi()))) is True
        assert json.loads(out.read_text())["congress"] == 119
        with patch.object(dp, "_refresh", new_callable=AsyncMock) as refresh:
            clock["now"] = datetime(2027, 1, 1, 8)  # Jan 1: still the 119th
            assert await _in_thread(lambda: job(dp._ensure_sitting_lines)) == "current"
            clock["now"] = datetime(2027, 1, 3, 16, 59)  # 11:59 ET
            assert await _in_thread(lambda: job(dp._ensure_sitting_lines)) == "current"
            assert score_calculator._district_pvi()["TN-9"] == base["TN-9"]
            clock["now"] = datetime(2027, 1, 3, 17, 0)  # noon ET
            assert await _in_thread(lambda: job(dp._ensure_sitting_lines)) == "reselected"
            assert await _in_thread(lambda: job(dp._ensure_sitting_lines)) == "current"
        refresh.assert_not_called()
        written = json.loads(out.read_text())
        assert written["congress"] == 120 and written["districts"] == new
        assert score_calculator._district_pvi()["TN-9"] == new["TN-9"] != base["TN-9"]
        assert config.settings.CURRENT_CONGRESS == 120

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
        monkeypatch.setattr(dp, "_sitting_congress", lambda: config.settings.CURRENT_CONGRESS)
        before = out.read_text()
        with patch("app.ops_alerts.send_ops_alert") as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        text = alert.call_args.args[1]
        assert "CURRENT_CONGRESS is pinned in the environment to 118" in text
        assert "the earliest is the 119th" in text
        assert "which are NOT the 118th's" in text and "latest pinned" not in text
        assert out.read_text() == before

    async def test_an_older_pin_with_no_file_names_the_bundled_table_it_serves(self, monkeypatch, tmp_path):
        """No file on the volume: scoring falls back to the bundled copy's
        top-level table (the 119th's), and the alert says so rather than
        "no pinned table"; it also says the weekly refresh writes nothing
        while the pin stands."""
        from app import config

        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=118)
        monkeypatch.setattr(config, "settings", config.Settings(CURRENT_CONGRESS=118))
        bundled = json.loads(dp.BUNDLED_PATH.read_text())
        with patch.object(dp, "_check_live_drift", new_callable=AsyncMock), patch(
            "app.ops_alerts.send_ops_alert",
        ) as alert:
            assert await _in_thread(dp._ensure_sitting_lines) == "no source configured"
        assert not out.exists()
        text = alert.call_args.args[1]
        assert f"the {bundled['congress']}th Congress's lines (the bundled copy's top-level table" in text
        assert "which are NOT the 118th's" in text and "scoring is on no pinned table" not in text
        assert "every weekly District PVI refresh" in text and "live-drift check still runs" in text
        assert dp.lines_congress() == bundled["congress"]


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
        monkeypatch.setattr(dp, "_file_stamp", None)
        monkeypatch.setattr(dp, "load_sources", lambda: {"congresses": {
            "120": {}, "121": {"redrawn_states": ["OH"]},
        }})
        table, meta = dp.district_pvi_for_congress(121)
        assert table == {"TN-9": 9}
        assert meta["omittedRedrawnStates"] == ["OH"]

    def test_a_congress_older_than_every_pin_gets_no_district_table(self, monkeypatch):
        monkeypatch.setattr(dp, "_file_cache", {"congresses": {"119": {"districts": {"TN-9": -23}}}})
        monkeypatch.setattr(dp, "_file_stamp", None)
        assert dp.district_pvi_for_congress(118) == ({}, None)

    def test_pre_pinning_file_serves_its_one_table(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dp, "_file_cache", {"districts": {"TN-9": -23, "OH-1": 2}})
        monkeypatch.setattr(dp, "_file_stamp", None)
        assert dp.district_pvi_for_congress(125) == ({"TN-9": -23, "OH-1": 2}, None)

    def test_configured_but_missing_table_drops_redrawn_states(self, monkeypatch, tmp_path):
        """Old-lines numbers for a redrawn state describe a different
        district; those seats fall back to the (labelled) state lean."""
        monkeypatch.setattr(dp, "_file_cache", {"districts": {"TN-9": -23, "OH-1": 2, "MO-5": -12}})
        monkeypatch.setattr(dp, "_file_stamp", None)
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
        # SEATS is counted from the 119th table itself, so it can't catch a
        # seat missing there: check it against the statutory size of the
        # House (2 U.S.C. §2a) and the 120th table, which a redraw keeps on
        # the same apportionment.
        assert sum(SEATS.values()) == 435
        assert Counter(k.split("-")[0] for k in t120) == Counter(SEATS)
        assert dp.ingestion_gates(t119, SEATS) == [] and dp.ingestion_gates(t120, SEATS) == []
        assert dp.cross_congress_gates(t120, t119, sources["congresses"]["120"]["redrawn_states"], SEATS) == []
        for c in ("119", "120"):
            assert bundled["congresses"][c]["_revision"]["revid"] == sources["congresses"][c]["revid"]

    def test_real_pins_conserve_each_redrawn_states_mean_and_a_half_update_would_not(self, bundled):
        """REDRAW_MEAN_SHIFT_MAX against the real 2026 redraws: all nine
        pass; the 120th table with only TN-9 moved to the new map (the
        shape of an edit caught half-way) does not."""
        t119 = bundled["congresses"]["119"]["districts"]
        t120 = bundled["congresses"]["120"]["districts"]
        redrawn = dp.load_sources()["congresses"]["120"]["redrawn_states"]
        assert dp.cross_congress_gates(t120, t119, redrawn, SEATS) == []
        half = dict(t119, **{"TN-9": t120["TN-9"]})
        failures = dp.cross_congress_gates(half, t119, redrawn, SEATS)
        assert any("mean district lean" in f and "TN +3.56" in f for f in failures)

    def test_scoring_reads_the_bundled_top_level_table(self, bundled, monkeypatch, tmp_path):
        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
        monkeypatch.setattr(score_calculator, "_district_pvi_stamp", None)
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

    async def test_a_refresh_released_mid_take_is_retried_not_skipped(self, monkeypatch, db_session):
        """The take fails while the refresh holds the lease, which is gone
        by the time the refusal reads its holder: REFUSED_BUSY, no holder.
        The House run takes the now-free lease instead of skipping — which
        would end the nightly chain."""
        from app.pipeline import lease

        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        assert token is not None
        real_holder, reads = lease.holder, []

        def holder_after_release(db, tier):
            if tier == lease.DISTRICT_LINES and not reads:
                lease.release(db_session, lease.DISTRICT_LINES, token)
                reads.append(tier)
            return real_holder(db, tier)

        monkeypatch.setattr(lease, "holder", holder_after_release)

        async def house():
            return {"status": "completed"}

        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=5, poll_s=30)
        assert result == {"status": "completed"}
        assert reads == [lease.DISTRICT_LINES]

    async def test_a_lease_that_stays_busy_is_skipped_at_the_deadline(self, monkeypatch):
        import contextlib

        from app.pipeline import lease

        takes = []

        @contextlib.asynccontextmanager
        async def busy(tier, *, who=None):
            takes.append(tier)
            yield lease.Granted("busy", code=lease.REFUSED_BUSY)

        monkeypatch.setattr(lease, "job_async", busy)

        async def house():
            raise AssertionError("must not run")

        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=0.05, poll_s=0.01)
        assert result == {"status": "skipped", "reason": lease.REFUSED_BUSY, "holder": None}
        assert len(takes) > 1

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
    breakdown recomputes on the same district lines — between a switch and
    the House run that rescores a member (or after one that failed), and
    for a member who left when the lines changed."""

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

    def _stale_house_on_119(self, db_session, monkeypatch, tmp_path):
        """40 current TN members stored on the 119th's lines with a stale
        Constituent Alignment reference (so the startup rescore runs), and
        one former member."""
        from app.models import Representative
        from app.pipeline.analyze.signal_overlap import SIGNAL_OVERLAP
        from tests.test_constituent_rescore import _make_stale, _seed_house

        monkeypatch.setattr(SIGNAL_OVERLAP, "live_path", tmp_path / "signal_overlap_live.json")
        monkeypatch.setattr(SIGNAL_OVERLAP, "_cache", None)
        _make_stale("house")
        _seed_house(db_session)
        for i, r in enumerate(db_session.query(Representative).order_by(Representative.id)):
            r.state, r.district, r.district_lines_congress = "TN", i % 9 + 1, 119
        db_session.add(Representative(id="gone", name="G", state="TN", district=8, party="R",
                                      is_current=False, district_lines_congress=119))
        db_session.commit()

    def test_a_startup_rescore_records_its_lines_with_the_scores(self, monkeypatch, tmp_path, db_session):
        """main's startup rescore rewrites current members' Constituent
        Alignment on the sitting (120th) lines and records the 120th in the
        same commit — before it re-measures the signal overlap, which reads
        each member's breakdown on their recorded lines. Recorded afterwards
        (the old stamp_house_lines), every breakdown the overlap check read
        was on the 119th's lines beside a score just written on the 120th's.
        A former member keeps the lines their score was stored on."""
        from app.main import rescore_constituent_alignment_on_current_lines
        from app.models import Representative
        from app.services import _scorecard_common
        from tests.test_constituent_rescore import _factory

        _, base, new = self._file(monkeypatch, tmp_path, 120)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        real, seen = _scorecard_common.explain_scores, []

        def spy(d):
            seen.append((d["district"], score_calculator._seat_pvi(d["state"], d["district"])))
            return real(d)

        monkeypatch.setattr(_scorecard_common, "explain_scores", spy)
        assert rescore_constituent_alignment_on_current_lines(_factory(db_session)) == ["house"]

        assert len(seen) == 40
        assert any(base[f"TN-{d}"] != new[f"TN-{d}"] for d, _ in seen)
        assert [pvi for _, pvi in seen] == [new[f"TN-{d}"] for d, _ in seen]
        db_session.expire_all()
        lines = {r.id: r.district_lines_congress for r in db_session.query(Representative)}
        assert lines.pop("gone") == 119
        assert set(lines.values()) == {120}

    def test_a_house_run_committing_after_a_startup_rescore_keeps_its_lines(
        self, monkeypatch, tmp_path, db_session,
    ):
        """Another backend (mid-rollout) runs the House once the rescore has
        committed, storing a member on the lines it read: nothing the
        rescore does afterwards re-stamps that member with the rescore's
        lines (the old separate stamp_house_lines bulk update did)."""
        import app.pipeline.analyze.signal_overlap as so
        from app.main import rescore_constituent_alignment_on_current_lines
        from app.models import Representative
        from tests.test_constituent_rescore import _factory

        self._file(monkeypatch, tmp_path, 120)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        other = _factory(db_session)

        def a_house_run_commits(db, chamber):
            s = other()
            try:
                s.get(Representative, "H000").district_lines_congress = 121
                s.commit()
            finally:
                s.close()

        monkeypatch.setattr(so, "record_signal_overlap", a_house_run_commits)
        assert rescore_constituent_alignment_on_current_lines(_factory(db_session)) == ["house"]
        db_session.expire_all()
        assert db_session.get(Representative, "H000").district_lines_congress == 121
        assert db_session.get(Representative, "H001").district_lines_congress == 120

    def test_the_startup_rescore_leaves_the_house_to_a_run_holding_the_lines(
        self, monkeypatch, tmp_path, db_session,
    ):
        """A triggered House run holds DISTRICT_LINES while it switches the
        lines and upserts, before it has a HousePipelineRun row the
        rescore's run_in_progress check could see. The rescore must not
        commit scores on the old lines, recorded as the old lines, over the
        run's rows: it takes the lease, and leaves the House alone when it
        can't."""
        from app.main import rescore_constituent_alignment_on_current_lines
        from app.models import Representative
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        self._file(monkeypatch, tmp_path, 119)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO)
        assert token is not None
        # The run has switched the lines and stored a member on them.
        self_file = json.loads((tmp_path / "district_pvi.json").read_text())
        (tmp_path / "district_pvi.json").write_text(json.dumps(dp._reselect(self_file, 120)))
        db_session.get(Representative, "H000").district_lines_congress = 120
        db_session.get(Representative, "H000").score_constituent_alignment = 77
        db_session.commit()
        try:
            assert "house" not in rescore_constituent_alignment_on_current_lines(_factory(db_session))
        finally:
            lease.release(db_session, lease.DISTRICT_LINES, token)
        db_session.expire_all()
        row = db_session.get(Representative, "H000")
        assert (row.district_lines_congress, row.score_constituent_alignment) == (120, 77)
        # With the lines free, the rescore does the House on the lines it holds.
        assert rescore_constituent_alignment_on_current_lines(_factory(db_session)) == ["house"]
        db_session.expire_all()
        assert db_session.get(Representative, "H000").district_lines_congress == 120

    def test_the_startup_rescore_takes_the_lines_only_for_a_stale_house(self, monkeypatch, tmp_path, db_session):
        """A stale Senate alone doesn't touch DISTRICT_LINES (a House run
        would wait on it for nothing), and runs even while a House run
        holds the lines — it reads none."""
        from app.main import rescore_constituent_alignment_on_current_lines
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory, _make_stale, _seed_senate

        self._file(monkeypatch, tmp_path, 120)
        monkeypatch.setattr(
            "app.pipeline.analyze.signal_overlap.SIGNAL_OVERLAP.live_path", tmp_path / "overlap.json",
        )
        _make_stale("senate")
        _seed_senate(db_session)
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO)
        taken = []
        real_job = lease.job
        monkeypatch.setattr(lease, "job", lambda tier, **kw: taken.append(tier) or real_job(tier, **kw))
        try:
            assert rescore_constituent_alignment_on_current_lines(_factory(db_session)) == ["senate"]
        finally:
            lease.release(db_session, lease.DISTRICT_LINES, token)
        assert taken == []

    def _stale_house_held_by_refresh(self, monkeypatch, tmp_path, db_session):
        from app.pipeline import lease

        self._file(monkeypatch, tmp_path, 120)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        assert token is not None
        return token

    def _house_lines(self, db_session):
        from app.models import Representative

        db_session.expire_all()
        return db_session.get(Representative, "H000").district_lines_congress

    def test_the_startup_rescore_waits_for_a_refresh_holding_nothing(self, monkeypatch, tmp_path, db_session):
        """A District PVI refresh holding the lines is waited for (bounded;
        nothing else retries the rescore) — between passes that hold
        nothing: no STARTUP_RESCORE lease and no writer registration, so a
        data reset isn't refused for the wait."""
        import time

        from app import background
        from app.main import _run_startup_rescore
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        token = self._stale_house_held_by_refresh(monkeypatch, tmp_path, db_session)
        timer = _run_startup_rescore(_factory(db_session), deadline=time.monotonic() + 30, poll_s=0.3)
        assert timer is not None
        assert self._house_lines(db_session) == 119
        # Waiting: nothing held.
        assert lease.holder(db_session, lease.STARTUP_RESCORE) is None
        assert "startup-rescore" not in background.running_writers()
        with background.exclusive("admin data reset"):
            pass
        lease.release(db_session, lease.DISTRICT_LINES, token)  # the refresh finishes
        timer.join(5)  # the next pass has been started as a writer
        for _ in range(200):  # ...and runs to the end before teardown closes the database
            if "startup-rescore" not in background.running_writers():
                break
            time.sleep(0.05)
        assert "startup-rescore" not in background.running_writers()
        assert self._house_lines(db_session) == 120

    def test_a_reset_during_the_wait_drops_the_rescore(self, monkeypatch, tmp_path, db_session):
        import time

        from app import background
        from app.main import _run_startup_rescore
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        token = self._stale_house_held_by_refresh(monkeypatch, tmp_path, db_session)
        timer = _run_startup_rescore(_factory(db_session), deadline=time.monotonic() + 30, poll_s=0.1)
        try:
            with background.exclusive("admin data reset"):
                timer.join(5)  # the next pass is refused, not queued
        finally:
            lease.release(db_session, lease.DISTRICT_LINES, token)
        assert self._house_lines(db_session) == 119

    def test_the_startup_rescore_gives_up_on_a_stuck_refresh(self, monkeypatch, tmp_path, db_session, caplog):
        import time

        from app.main import _run_startup_rescore
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        token = self._stale_house_held_by_refresh(monkeypatch, tmp_path, db_session)
        try:
            with caplog.at_level("WARNING", logger="app.main"):
                assert _run_startup_rescore(_factory(db_session), deadline=time.monotonic(), poll_s=0.01) is None
        finally:
            lease.release(db_session, lease.DISTRICT_LINES, token)
        assert "gave up: the District PVI refresh held the district lines" in caplog.text
        assert self._house_lines(db_session) == 119

    def test_the_startup_rescore_leaves_the_house_to_a_house_run(self, monkeypatch, tmp_path, db_session, caplog):
        import time

        from app.main import _run_startup_rescore
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        self._file(monkeypatch, tmp_path, 120)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO)
        try:
            with caplog.at_level("INFO", logger="app.main"):
                assert _run_startup_rescore(_factory(db_session), deadline=time.monotonic() + 30, poll_s=0.01) is None
        finally:
            lease.release(db_session, lease.DISTRICT_LINES, token)
        assert "left to the House run holding the district lines" in caplog.text

    def _age(self, db_session, seconds):
        from datetime import timedelta

        from app.models import ApiCache
        from app.pipeline import lease
        from app.time_utils import utcnow

        db_session.query(ApiCache).filter(ApiCache.tier == lease.DISTRICT_LINES).update(
            {"cached_at": utcnow() - timedelta(seconds=seconds)}, synchronize_session=False,
        )
        db_session.commit()

    async def test_a_restart_releases_the_lines_a_killed_holder_left(self, monkeypatch, tmp_path, db_session):
        """A deploy or OOM kills a House run (or refresh, or rescore)
        holding DISTRICT_LINES. The restarted pipeline process releases the
        lease beside sweeping the run rows — its last beat already a beat
        interval and a half old — instead of leaving it to its hour-long
        stale window: the House trigger doesn't 409 for a dead run, and a
        House run goes ahead."""
        from app.api import admin
        from app.main import _invalidate_orphaned_pipelines
        from app.pipeline import lease

        for who in (dp.HOUSE_RUN_WHO, dp.REFRESH_WHO, dp.RESCORE_WHO):
            assert lease.acquire(db_session, lease.DISTRICT_LINES, who=who) is not None  # then killed
            self._age(db_session, lease.BEAT_S * 2)
            assert lease.holder(db_session, lease.DISTRICT_LINES) == who
            _invalidate_orphaned_pipelines()
            assert lease.holder(db_session, lease.DISTRICT_LINES) is None
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        started = []
        monkeypatch.setattr(admin, "run_pipeline_in_thread", lambda f, **kw: started.append(f))
        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO) is not None
        self._age(db_session, lease.BEAT_S * 2)
        _invalidate_orphaned_pipelines()
        assert admin.admin_trigger_house_pipeline(db=db_session) == {"message": "House pipeline triggered"}

        async def house():
            return {"status": "completed"}

        assert await dp.run_house_on_sitting_lines(house, refresh_wait_s=0) == {"status": "completed"}

    def test_a_holder_still_beating_keeps_its_lease(self, db_session):
        """A lease beaten recently may be live in another process (the role
        lock is per container; only the pipeline service's stop-first update
        rules that out). It is released only if it misses the next beat."""
        from app.pipeline import lease

        live = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO)
        timer = dp.release_orphaned_holds(recheck_after_s=0.3)
        assert timer is not None and lease.holder(db_session, lease.DISTRICT_LINES) == dp.HOUSE_RUN_WHO
        assert lease.beat(db_session, lease.DISTRICT_LINES, live)  # beaten after this process started
        timer.join(5)
        assert lease.holder(db_session, lease.DISTRICT_LINES) == dp.HOUSE_RUN_WHO
        lease.release(db_session, lease.DISTRICT_LINES, live)

        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO) is not None  # then killed
        timer = dp.release_orphaned_holds(recheck_after_s=0.3)
        assert lease.holder(db_session, lease.DISTRICT_LINES) == dp.REFRESH_WHO
        timer.join(5)
        assert lease.holder(db_session, lease.DISTRICT_LINES) is None
        assert not dp.waits_for(dp.HOUSE_RUN_WHO)  # the re-check is over

    def test_the_startup_rescore_waits_out_a_killed_house_run_s_lease(self, monkeypatch, tmp_path, db_session):
        """The deploy that starts this process killed a House run moments
        ago: its lease is too fresh to release at once, and the startup
        rescore must not leave the House to a run that is gone — it waits
        for the re-check to release it, then rescores."""
        import time

        from app import background
        from app.main import _run_startup_rescore
        from app.pipeline import lease
        from tests.test_constituent_rescore import _factory

        self._file(monkeypatch, tmp_path, 120)
        self._stale_house_on_119(db_session, monkeypatch, tmp_path)
        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO) is not None  # then killed
        recheck = dp.release_orphaned_holds(recheck_after_s=0.1)
        assert recheck is not None and dp.waits_for(dp.HOUSE_RUN_WHO)
        timer = _run_startup_rescore(_factory(db_session), deadline=time.monotonic() + 30, poll_s=0.6)
        assert timer is not None  # waiting, not left to the dead run
        recheck.join(5)
        assert lease.holder(db_session, lease.DISTRICT_LINES) is None
        timer.join(5)  # the next pass has been started as a writer
        # ...and runs to the end (the test's session is shared with it, so
        # nothing reads it until then).
        for _ in range(200):
            if "startup-rescore" not in background.running_writers():
                break
            time.sleep(0.05)
        assert "startup-rescore" not in background.running_writers()
        assert self._house_lines(db_session) == 120

    async def test_a_house_run_waits_out_a_killed_house_run_s_lease(self, monkeypatch, db_session):
        from app.pipeline import lease

        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        assert lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.HOUSE_RUN_WHO) is not None  # then killed
        recheck = dp.release_orphaned_holds(recheck_after_s=0.2)
        ran = []

        async def house():
            ran.append(True)
            return {"status": "completed"}

        try:
            assert await dp.run_house_on_sitting_lines(house, refresh_wait_s=5, poll_s=0.05) == {"status": "completed"}
        finally:
            recheck.join(5)
        assert ran == [True]

    async def test_a_house_run_waits_for_the_startup_rescore(self, monkeypatch, db_session):
        import asyncio

        from app.pipeline import lease

        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        token = lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.RESCORE_WHO)
        assert token is not None

        async def finish_rescore():
            await asyncio.sleep(0.05)
            lease.release(db_session, lease.DISTRICT_LINES, token)

        async def house():
            return {"status": "completed"}

        releaser = asyncio.create_task(finish_rescore())
        result = await dp.run_house_on_sitting_lines(house, refresh_wait_s=5, poll_s=0.01)
        await releaser
        assert result == {"status": "completed"}

    def test_every_read_of_the_table_honours_a_block(self, monkeypatch, tmp_path):
        """get_district_pvi_map, dict(), len(), iteration and bool() read
        the block's table like .get does."""
        _, base, new = self._file(monkeypatch, tmp_path, 120)
        lines = score_calculator._district_pvi()
        with dp.lines_of(119):
            assert score_calculator.get_district_pvi_map() == base
            assert dict(lines) == base == dict(lines.items()) == {k: lines[k] for k in lines}
            assert len(lines) == len(base) and sorted(lines.values()) == sorted(base.values())
            assert lines.copy() == base and bool(lines)
            assert lines == base and base == lines and lines != new
            assert list(reversed(lines)) == list(reversed(list(base)))
            import copy

            assert copy.copy(lines) == base and copy.deepcopy(lines) == base
            assert (lines | {}) == base and ({} | lines) == base
            assert json.loads(json.dumps(lines)) == base
            assert lines.own_table() == new
        assert score_calculator.get_district_pvi_map() == new

    def test_nested_blocks_inherit_the_enclosing_table(self, monkeypatch, tmp_path):
        """lines_of inside current_lines (or another lines_of): the current
        lines are the enclosing block's, not a fresh read of the file."""
        out, base, new = self._file(monkeypatch, tmp_path, 119)
        with dp.current_lines() as outer:
            out.write_text(json.dumps(dp._reselect(json.loads(out.read_text()), 120)))
            dp._reset_caches()
            for asked in (None, 119, 117):
                with dp.lines_of(asked) as used:
                    assert used == outer == 119
                    assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
                    assert dp.lines_congress() == 119
            with dp.lines_of(120) as used:
                assert used == 120 and score_calculator._seat_pvi("TN", 9) == new["TN-9"]
                with dp.current_lines() as inner:
                    assert inner == 120 and score_calculator._seat_pvi("TN", 9) == new["TN-9"]

    def test_a_reset_during_lines_of_keeps_the_override(self, monkeypatch, tmp_path):
        """A House run starting (or a refresh writing the file) resets the
        caches while the API's breakdown is inside lines_of: the breakdown
        still reads the recorded lines, not the sitting ones."""
        out, base, new = self._file(monkeypatch, tmp_path, 120)
        with dp.lines_of(119):
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
            dp._reset_caches()
            assert isinstance(score_calculator._district_pvi_cache, dp.SeatLines)
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
            dp._write(out, json.loads(out.read_text()))
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]

    def test_a_reset_in_another_thread_keeps_the_override(self, monkeypatch, tmp_path):
        import threading

        _, base, _ = self._file(monkeypatch, tmp_path, 120)
        entered, reset_done, seen = threading.Event(), threading.Event(), {}

        def breakdown():
            with dp.lines_of(119):
                entered.set()
                reset_done.wait(5)
                seen["tn9"] = score_calculator._seat_pvi("TN", 9)

        t = threading.Thread(target=breakdown)
        t.start()
        entered.wait(5)
        dp._reset_caches()
        reset_done.set()
        t.join()
        assert seen["tn9"] == base["TN-9"]

    def test_current_lines_holds_one_read_and_names_its_congress(self, monkeypatch, tmp_path):
        """main's startup rescore: the scores it computes and the Congress it
        records come from one read, though another backend switches the
        file (and this process re-reads it) mid-rescore."""
        out, base, new = self._file(monkeypatch, tmp_path, 119)
        seen = []
        with dp.current_lines() as congress:
            seen.append(score_calculator._seat_pvi("TN", 9))
            out.write_text(json.dumps(dp._reselect(json.loads(out.read_text()), 120)))
            dp._reset_caches()
            seen.append(score_calculator._seat_pvi("TN", 9))
        assert congress == 119
        assert seen == [base["TN-9"], base["TN-9"]]
        # Outside the block, scoring reads the file as it is now.
        assert dp.lines_congress() == 120
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]

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
        # this run takes it) — this process's caches never hear of it. This
        # job holds the 120th too (a job holding the 119th would be
        # superseded: TestJan3Boundary).
        out.write_text(json.dumps(dp._reselect(json.loads(out.read_text()), 120)))
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 120)
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        seen = {}

        async def house():
            seen["congress"] = dp.lines_congress()
            seen["tn9"] = score_calculator._seat_pvi("TN", 9)
            return {"status": "completed"}

        await dp.run_house_on_sitting_lines(house)
        assert seen == {"congress": 120, "tn9": new["TN-9"]}


class TestJan3Boundary:
    """One Congress for a House run's scored windows and its district lines
    (app.config.scoring_congress). The windows used to come from
    settings.CURRENT_CONGRESS, fixed at process start, while the lines
    followed the clock: a process started before Jan 3 2027 scored the
    119th's votes on the 120th's lines."""

    async def _house_run_at(self, monkeypatch, tmp_path, started, run_at):
        import asyncio
        import threading

        from app import config

        clock = {"now": started}
        monkeypatch.setattr("app.time_utils.utcnow", lambda: clock["now"])
        monkeypatch.setattr(config, "settings", config.Settings())
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=None)
        assert await dp.refresh_district_pvi() is True
        clock["now"] = run_at
        seen = {}

        def plain_thread(fn):
            box = []
            t = threading.Thread(target=lambda: box.append(fn()))
            t.start()
            t.join()
            return box[0]

        async def house():
            # What the House pipeline reads: windows off the setting, lines
            # off the table.
            seen["windows"] = config.settings.CURRENT_CONGRESS
            seen["lines"] = dp.lines_congress()
            seen["tn9"] = score_calculator._seat_pvi("TN", 9)
            # Another job in the process moves the process-wide value
            # mid-run (a plain thread: it doesn't share this run's context).
            plain_thread(lambda: setattr(config.settings, "CURRENT_CONGRESS", 125))
            seen["windows_after"] = config.settings.CURRENT_CONGRESS
            seen["windows_to_thread"] = await asyncio.to_thread(lambda: config.settings.CURRENT_CONGRESS)
            # A plain thread started inside the run does not inherit the
            # hold: it reads the process-wide value (documented in
            # app.background / AGENTS.md).
            seen["plain_thread"] = plain_thread(lambda: config.settings.CURRENT_CONGRESS)
            return {"status": "completed"}

        assert await dp.run_house_on_sitting_lines(house) == {"status": "completed"}
        return seen, base, new

    def _expect(self, congress, tn9):
        return {
            "windows": congress, "lines": congress, "tn9": tn9, "windows_after": congress,
            "windows_to_thread": congress, "plain_thread": 125,
        }

    async def test_a_process_started_before_jan_3_moves_windows_and_lines_together(self, monkeypatch, tmp_path):
        # Started Dec 20 (the 119th); the House run at 2027-01-04T03:00Z.
        seen, _, new = await self._house_run_at(
            monkeypatch, tmp_path, datetime(2026, 12, 20), datetime(2027, 1, 4, 3),
        )
        assert seen == self._expect(120, new["TN-9"])

    async def test_between_midnight_and_noon_on_jan_3_both_stay_on_the_outgoing_congress(
        self, monkeypatch, tmp_path,
    ):
        # Started 01:00 ET Jan 3 (a date rule said 120), run at 08:00 ET.
        seen, base, _ = await self._house_run_at(
            monkeypatch, tmp_path, datetime(2027, 1, 3, 6), datetime(2027, 1, 3, 13),
        )
        assert seen == self._expect(119, base["TN-9"])

    async def test_a_run_in_the_gap_by_a_process_started_before(self, monkeypatch, tmp_path):
        seen, base, _ = await self._house_run_at(
            monkeypatch, tmp_path, datetime(2026, 12, 20), datetime(2027, 1, 3, 13),
        )
        assert seen == self._expect(119, base["TN-9"])

    async def _pre_noon_job(self, monkeypatch, tmp_path, *, another_job_advances):
        import threading

        from app import config

        clock = {"now": datetime(2027, 1, 3, 16)}  # 11:00 ET
        monkeypatch.setattr("app.time_utils.utcnow", lambda: clock["now"])
        monkeypatch.setattr(config, "settings", config.Settings())
        out, base, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=None)
        seen = {}

        async def house():
            seen["windows"], seen["lines"] = config.settings.CURRENT_CONGRESS, dp.lines_congress()
            seen["tn9"] = score_calculator._seat_pvi("TN", 9)
            return {"status": "completed"}

        with config.scoring_congress():
            assert await dp.refresh_district_pvi() is True
            clock["now"] = datetime(2027, 1, 3, 18)  # 13:00 ET
            if another_job_advances:
                other = threading.Thread(target=config.advance_current_congress)  # a job starting now
                other.start()
                other.join()
                assert config.settings.__dict__["CURRENT_CONGRESS"] == 120
            result = await dp.run_house_on_sitting_lines(house)
        return result, seen, out, base

    async def test_a_job_holds_its_congress_through_noon(self, monkeypatch, tmp_path):
        """A job that started before noon (the nightly chain, a trigger)
        keeps the outgoing Congress for its House run past noon: windows
        and lines still agree."""
        result, seen, _, base = await self._pre_noon_job(monkeypatch, tmp_path, another_job_advances=False)
        assert result == {"status": "completed"}
        assert seen == {"windows": 119, "lines": 119, "tn9": base["TN-9"]}

    async def test_a_superseded_job_leaves_the_house_to_the_new_congress(self, monkeypatch, tmp_path):
        """Once a job started after noon has advanced the process to the
        120th, an older job's House run is skipped rather than settle — and
        score — the 119th's lines after the 120th's (which would flip the
        site back until the next job flipped it forward)."""
        from app.pipeline.run_tracker import SUPERSEDED, skip_reason_text

        result, seen, out, _ = await self._pre_noon_job(monkeypatch, tmp_path, another_job_advances=True)
        assert result == {"status": "skipped", "reason": SUPERSEDED}
        assert seen == {}
        assert "outgoing Congress" in skip_reason_text(SUPERSEDED)

    async def test_an_older_job_never_rewrites_newer_lines(self, monkeypatch, tmp_path):
        """The file already on the 120th's lines (a newer job, possibly in
        another process): a job holding the 119th neither reselects the
        119th's nor refreshes, and its House run is skipped."""
        from app import config
        from app.pipeline.run_tracker import SUPERSEDED

        out, _, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=120)
        assert await dp.refresh_district_pvi() is True
        before = out.read_text()
        monkeypatch.setattr("app.time_utils.utcnow", lambda: datetime(2027, 1, 4, 3))  # the 120th in office
        monkeypatch.setattr(config, "settings", config.Settings())
        monkeypatch.setattr(dp, "_sitting_congress", lambda: 119)
        assert await _in_thread(dp._ensure_sitting_lines) == "superseded"
        assert await dp.refresh_district_pvi() is False

        async def house():
            raise AssertionError("must not run")

        assert await dp.run_house_on_sitting_lines(house) == {"status": "skipped", "reason": SUPERSEDED}
        assert out.read_text() == before

    async def test_a_forward_pin_since_removed_does_not_stall_the_house(self, monkeypatch, tmp_path):
        """An operator pins CURRENT_CONGRESS=120 early (the file goes to the
        120th's lines), then removes the pin. The file is ahead of the clock
        — not a newer job — so the unpinned 119th's House run reselects the
        119th's lines and runs, instead of being skipped until Jan 3."""
        from app import config

        monkeypatch.setattr("app.time_utils.utcnow", lambda: datetime(2026, 10, 1))
        monkeypatch.setattr(config, "settings", config.Settings(CURRENT_CONGRESS=120))
        out, base, _ = _two_congress_setup(monkeypatch, tmp_path, sitting=None)
        assert await dp.refresh_district_pvi() is True
        assert json.loads(out.read_text())["congress"] == 120
        monkeypatch.setattr(config, "settings", config.Settings())  # the pin removed; restarted
        assert config.settings.CURRENT_CONGRESS == 119
        seen = {}

        async def house():
            seen["windows"], seen["lines"] = config.settings.CURRENT_CONGRESS, dp.lines_congress()
            seen["tn9"] = score_calculator._seat_pvi("TN", 9)
            return {"status": "completed"}

        assert await dp.run_house_on_sitting_lines(house) == {"status": "completed"}
        assert seen == {"windows": 119, "lines": 119, "tn9": base["TN-9"]}
        assert json.loads(out.read_text())["congress"] == 119
        assert await dp.refresh_district_pvi() is True

    def test_every_writer_holds_one_congress(self):
        """app.background.start_writer (every scheduled job, every trigger,
        the startup rescore) and writing() run their work inside
        scoring_congress — no writer can start without a held Congress."""
        from app import background, config

        seen = []
        background.start_writer(lambda: seen.append(config._RUN_CONGRESS.get()), name="t").join()
        with background.writing("t"):
            seen.append(config._RUN_CONGRESS.get())
        assert seen == [config.settings.CURRENT_CONGRESS] * 2
        assert config._RUN_CONGRESS.get() is None


class TestReadPathCaches:
    """The elections GET path: no parsing or table building per request."""

    def test_sources_are_parsed_once_per_version(self, tmp_path):
        import os

        path = tmp_path / "sources.json"
        path.write_text(json.dumps({"page": "P", "congresses": {"119": {}}}))
        first = dp.load_sources(path)
        assert dp.load_sources(path) is first
        path.write_text(json.dumps({"page": "P", "congresses": {"120": {}}}))
        os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        assert set(dp.load_sources(path)["congresses"]) == {"120"}

    async def test_district_pvi_for_congress_is_built_once_per_file(self, monkeypatch, tmp_path):
        out, base, new = _two_congress_setup(monkeypatch, tmp_path)
        assert await dp.refresh_district_pvi() is True
        calls = []
        real = dp._district_pvi_for_congress
        monkeypatch.setattr(dp, "_district_pvi_for_congress", lambda *a: calls.append(a[0]) or real(*a))
        first = dp.district_pvi_for_congress(120)
        assert dp.district_pvi_for_congress(120) is first and calls == [120]
        assert first[0] == new
        # Another process switches the file: rebuilt from the new read.
        import os

        data = json.loads(out.read_text())
        del data["congresses"]["120"]
        out.write_text(json.dumps(dp._reselect(data, 120, exact=False)))
        os.utime(out, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        assert dp.district_pvi_for_congress(120)[1]["congress"] == 119
        assert calls == [120, 120]

    def test_pvi_meta_is_read_once_per_version(self, monkeypatch, tmp_path):
        import os

        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path))
        monkeypatch.setattr(score_calculator, "_pvi_meta_cache", None)
        path = tmp_path / "district_pvi.json"
        path.write_text(json.dumps({"_source": "one", "districts": {"TN-9": 1}}))
        reads = []
        real = score_calculator._read_pvi_json
        monkeypatch.setattr(score_calculator, "_read_pvi_json", lambda *a, **k: reads.append(a[0]) or real(*a, **k))
        meta = score_calculator.get_pvi_meta()
        assert meta["districts"]["source"] == "one"
        meta["districts"] = None  # a caller replacing an entry
        again = score_calculator.get_pvi_meta()
        assert again["districts"]["source"] == "one" and len(reads) == 2
        path.write_text(json.dumps({"_source": "two", "districts": {"TN-9": 1}}))
        os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        assert score_calculator.get_pvi_meta()["districts"]["source"] == "two"


class TestAnotherProcessRewritesTheLines:
    """The pipeline process rewrites district_pvi.json (new pins, the
    sitting-Congress switch); the API processes (settings.PROCESS_ROLE) never
    hear of it except through the file's stamp (app/file_cache.py). Each
    rewrite here is plain file I/O — no _write/_reset_caches, which only
    reach the writer's own process."""

    def _setup(self, monkeypatch, tmp_path, sitting):
        out, base, new = _two_congress_setup(monkeypatch, tmp_path, sitting=sitting)
        blocks = {
            "119": {"_source": "s", "_lines": "old", "_window": "w", "districts": base},
            "120": {"_source": "s", "_lines": "new", "_window": "w", "districts": new},
        }
        self._stamp = 1_000_000_000
        self._rewrite(out, dp._payload(blocks, sitting))
        return out, base, new

    def _rewrite(self, out, payload):
        """Another process's atomic rewrite: a new file renamed into place,
        with a later mtime."""
        import os

        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        self._stamp += 1_000_000_000
        os.utime(tmp, ns=(self._stamp, self._stamp))
        os.replace(tmp, out)

    def _switch(self, out, congress):
        self._rewrite(out, dp._reselect(json.loads(out.read_text()), congress))

    def test_member_scoring_picks_up_the_switch_as_a_seat_lines(self, monkeypatch, tmp_path):
        out, base, new = self._setup(monkeypatch, tmp_path, 119)
        assert dp.lines_congress() == 119
        assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
        self._switch(out, 120)
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]
        assert isinstance(score_calculator._district_pvi_cache, dp.SeatLines)
        assert dp.lines_congress() == 120
        # The reloaded SeatLines still steers a breakdown onto older lines.
        with dp.lines_of(119) as congress:
            assert congress == 119
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
        assert score_calculator._seat_pvi("TN", 9) == new["TN-9"]

    def test_new_pins_reach_lines_of(self, monkeypatch, tmp_path):
        """A table pinned after this process first read the file is on hand
        for a breakdown stored on it."""
        out, base, new = self._setup(monkeypatch, tmp_path, 120)
        data = json.loads(out.read_text())
        del data["congresses"]["119"]
        self._rewrite(out, data)
        with dp.lines_of(119) as congress:
            assert congress == 120  # not on file: the current lines
        data["congresses"]["119"] = {"_source": "s", "_lines": "old", "_window": "w", "districts": base}
        self._rewrite(out, data)
        with dp.lines_of(119) as congress:
            assert congress == 119
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]

    def test_a_lines_of_block_keeps_its_table_through_a_rewrite(self, monkeypatch, tmp_path):
        """A breakdown on the current lines, the file switched mid-way: it
        reads one table start to finish, and names that table's Congress."""
        out, base, new = self._setup(monkeypatch, tmp_path, 120)
        seen = []
        with dp.lines_of(None) as congress:
            seen.append(score_calculator._seat_pvi("TN", 9))
            self._switch(out, 119)
            seen.append(score_calculator._seat_pvi("TN", 9))
            assert dp.lines_congress() == 120
        assert congress == 120
        assert seen == [new["TN-9"], new["TN-9"]]
        assert dp.lines_congress() == 119
        assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]

    def test_current_lines_keeps_its_table_through_a_rewrite(self, monkeypatch, tmp_path):
        out, base, _ = self._setup(monkeypatch, tmp_path, 119)
        with dp.current_lines() as congress:
            self._switch(out, 120)
            assert score_calculator._seat_pvi("TN", 9) == base["TN-9"]
            assert dp.lines_congress() == congress == 119

    async def test_a_house_run_scores_on_one_read_through_a_rewrite(self, monkeypatch, tmp_path):
        """Another backend (an older image, mid-rollout) rewriting the file
        during a House run: the run keeps scoring, and recording, the lines
        it started on."""
        out, _, new = self._setup(monkeypatch, tmp_path, 120)
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: "current")
        seen = []

        async def house():
            seen.append((dp.lines_congress(), score_calculator._seat_pvi("TN", 9)))
            self._switch(out, 119)
            seen.append((dp.lines_congress(), score_calculator._seat_pvi("TN", 9)))
            return {"status": "completed"}

        await dp.run_house_on_sitting_lines(house)
        assert seen == [(120, new["TN-9"]), (120, new["TN-9"])]
        assert dp.lines_congress() == 119

    def test_the_elections_reader_picks_up_new_pins(self, monkeypatch, tmp_path):
        out, base, new = self._setup(monkeypatch, tmp_path, 120)
        data = json.loads(out.read_text())
        only_119 = dict(data, congresses={"119": data["congresses"]["119"]})
        self._rewrite(out, dp._reselect(only_119, 120, exact=False))
        assert dp.district_pvi_for_congress(120)[1]["congress"] == 119
        self._rewrite(out, data)
        table, meta = dp.district_pvi_for_congress(120)
        assert meta["congress"] == 120
        assert table == new


class TestTriggeredRunsCheckTheSittingLines:
    @pytest.fixture()
    def recorded(self, monkeypatch):
        order, captured = [], {}
        monkeypatch.setattr(dp, "_ensure_sitting_lines", lambda: order.append("pvi"))

        def step(name):
            async def run(**_kw):
                order.append(name)
                return {"status": "completed"}
            return run

        # The admin House trigger calls run_house_pipeline by its home
        # module; a full trigger runs the nightly chain's links
        # (scheduler.nightly_links), through app.scheduler's names.
        monkeypatch.setattr("app.pipeline.house_pipeline.run_house_pipeline", step("house"))
        for name, label in (("run_senate_pipeline", "senate"), ("run_supplementary_pipeline", "supplementary"),
                            ("run_house_pipeline", "house"), ("run_stock_trades_pipeline", "stock trades"),
                            ("run_election_pipeline", "election")):
            monkeypatch.setattr(f"app.scheduler.{name}", step(label))
        monkeypatch.setattr("app.scheduler.pipelines_running", lambda: False)
        monkeypatch.setattr("app.scheduler.warm_bills", lambda: None)
        monkeypatch.setattr("app.ops_alerts.send_ops_alert", lambda *a, **kw: None)
        monkeypatch.setattr("app.ops_alerts.resolve_ops_alert", lambda *a, **kw: None)
        monkeypatch.setattr("app.api.admin.run_pipeline_in_thread", lambda f, **kw: captured.setdefault("run", f))
        return order, captured

    async def test_house_trigger(self, recorded, db_session):
        from app.api import admin

        order, captured = recorded
        admin.admin_trigger_house_pipeline(db=db_session)
        await captured["run"]()
        assert order == ["pvi", "house"]

    async def test_house_trigger_refuses_while_a_house_run_holds_the_lines(self, recorded, db_session):
        from fastapi import HTTPException

        from app.api import admin
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who="House run")
        with pytest.raises(HTTPException) as err:
            admin.admin_trigger_house_pipeline(db=db_session)
        assert err.value.status_code == 409
        assert err.value.detail.startswith("House run is already running")
        assert "run" not in recorded[1]

    async def test_house_trigger_waits_for_a_refresh_rather_than_refusing(self, recorded, db_session):
        """A refresh holding the lines is not "the House pipeline is already
        running": the run is started, and waits for it."""
        from app.api import admin
        from app.pipeline import lease

        lease.acquire(db_session, lease.DISTRICT_LINES, who=dp.REFRESH_WHO)
        answer = admin.admin_trigger_house_pipeline(db=db_session)
        assert "District PVI refresh" in answer["message"] and "run" in recorded[1]

    async def test_admin_trigger(self, recorded, db_session):
        """The lines are settled right before the House run, under its lease
        — not before the Senate run takes its lock, where a network refresh
        would widen the double-trigger window."""
        from app.api import admin

        order, captured = recorded
        admin.admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        await captured["run"]()
        assert order == ["senate", "supplementary", "pvi", "house", "stock trades", "election"]

    async def test_token_trigger(self, recorded, db_session, monkeypatch):
        from app.api import pipeline
        from app.config import settings

        order, captured = recorded
        monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "t")
        pipeline.trigger_pipeline(authorization="Bearer t", senator=None, fetch_only=False, db=db_session)
        await captured["run"]()
        # The same chain as the admin trigger (app.pipeline_chain).
        assert order == ["senate", "supplementary", "pvi", "house", "stock trades", "election"]
