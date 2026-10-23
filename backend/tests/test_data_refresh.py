"""Generated data files refresh themselves (app/data_refresh.py): each by its
own script on its own cadence, gated, the last good copy kept and an alert
opened on a failure, and a staleness alert where a person must regenerate.
Offline: the generators here are tiny inline scripts or mocked."""

import importlib.util
import json
import pathlib
import re
import sys
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import scoring_revisions
from app.api import feed as feed_api
from app import data_refresh as dr
from app.config import settings
from app.election_phase import ActiveElection
from app.error_utils import output_summary
from app.pipeline.analyze import score_calculator, score_version
from app.pipeline.fetch.state_candidates_common import fec_party_label
from app.state_names import STATE_NAMES


def _write_runtime(name: str, doc: dict) -> None:
    path = pathlib.Path(dr.runtime_data_path(name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _writer(doc: dict) -> list[str]:
    """A generator that writes `doc` to {out}."""
    return [sys.executable, "-c", f"import json,sys; open(sys.argv[1],'w').write(json.dumps({doc!r}))", "{out}"]


def _ctx(name="x.json", doc=None, today=date(2028, 3, 1), last=None):
    return dr.Context(name, doc or {}, today, last)


@pytest.fixture
def alerts():
    with patch.object(dr, "send_ops_alert") as send, patch.object(dr, "resolve_ops_alert") as resolve:
        yield SimpleNamespace(send=send, resolve=resolve)


# ── reading ──────────────────────────────────────────────────────────

def test_the_refreshed_copy_wins_and_a_rewrite_is_noticed():
    bundled = dr.read("fec_party_codes.json")
    assert "REP" in bundled["codes"]
    _write_runtime("fec_party_codes.json", {"codes": {"ZZZ": "Refreshed"}})
    assert dr.read("fec_party_codes.json") == {"codes": {"ZZZ": "Refreshed"}}
    assert fec_party_label("ZZZ") == "Refreshed"
    pathlib.Path(dr.runtime_data_path("fec_party_codes.json")).unlink()
    assert dr.read("fec_party_codes.json") == bundled


# ── one refresh ──────────────────────────────────────────────────────

def test_a_gated_candidate_is_installed_with_its_provenance(alerts):
    spec = dr.DataFile("t_ok.json", command=lambda ctx: _writer({"rows": [1, 2]}))
    assert dr.refresh_one(spec, _ctx("t_ok.json")) == "refreshed"
    doc = json.loads(pathlib.Path(dr.runtime_data_path("t_ok.json")).read_text())
    assert doc["rows"] == [1, 2]
    assert doc["_refreshed"]["sha256"] == dr.content_sha256({"rows": [1, 2]})
    assert doc["_refreshed"]["command"].startswith(sys.executable)
    alerts.resolve.assert_called_with("data-refresh-t-ok")
    # The same content again writes nothing.
    assert dr.refresh_one(spec, _ctx("t_ok.json", doc)) == "unchanged"


def test_a_candidate_failing_its_gate_keeps_the_copy_in_use_and_alerts(alerts):
    _write_runtime("t_gate.json", {"rows": [1, 2, 3]})
    spec = dr.DataFile("t_gate.json", command=lambda ctx: _writer({"rows": []}),
                       gate=lambda new, old: [] if new["rows"] else ["no rows"])
    assert dr.refresh_one(spec, _ctx("t_gate.json", dr.read("t_gate.json"))).startswith("kept")
    assert dr.read("t_gate.json") == {"rows": [1, 2, 3]}
    assert alerts.send.call_args.kwargs["condition"] == "data-refresh-t-gate"
    assert "no rows" in alerts.send.call_args.args[1]


def test_a_failing_generator_keeps_the_copy_in_use_and_alerts(alerts):
    _write_runtime("t_fail.json", {"rows": [1]})
    spec = dr.DataFile("t_fail.json", command=lambda ctx: [sys.executable, "-c", "raise SystemExit('source moved')"])
    assert "crashed (exit code 1)" in dr.refresh_one(spec, _ctx("t_fail.json"))
    assert dr.read("t_fail.json") == {"rows": [1]}
    assert "source moved" in alerts.send.call_args.args[1]
    assert not list(pathlib.Path(dr.runtime_data_path("t_fail.json")).parent.glob(".t_fail.json.*"))


# A generator that prints progress, then crashes in a helper: the output the
# 2026-10 bill_title_token_df.json alert dumped, cut mid-word ("onres.zip").
_CRASHING_SCRIPT = """
import sys
def fetch(url):
    print("downloading " + url)
def main():
    for i in range(400):
        fetch(f"https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hconres/{i}/BILLSTATUS-119hconres.zip")
    print("Wrote /data/.bill_title_token_df.json.candidate", file=sys.stderr)
    main.cache_clear()
main()
"""


def _alert_body(alerts) -> str:
    return alerts.send.call_args.args[1]


def test_a_crash_alert_names_the_exception_and_its_line_not_the_output(alerts, tmp_path, caplog):
    script = tmp_path / "build_thing.py"
    script.write_text(_CRASHING_SCRIPT)
    spec = dr.DataFile("t_crash.json", command=lambda ctx: [sys.executable, str(script), "{out}"])
    with caplog.at_level("WARNING", logger="app.data_refresh"):
        outcome = dr.refresh_one(spec, _ctx("t_crash.json"))
    body = _alert_body(alerts)
    lead, detail, where = body.split("\n")
    assert lead == ("app/data/t_crash.json was not refreshed: its generator crashed (exit code 1). "
                    "The previous copy stays in use.")
    assert detail == "AttributeError: 'function' object has no attribute 'cache_clear' (build_thing.py:9 in main)"
    assert re.fullmatch(r"Full output in the pipeline log at \d{4}-\d\d-\d\d \d\d:\d\d UTC\.", where)
    assert "govinfo" not in body and "Traceback" not in body and "Wrote" not in body
    assert outcome.startswith("kept: its generator crashed") and "govinfo" not in outcome
    # The whole output is in the log, stamped as the alert says.
    logged = caplog.text
    assert where.removeprefix("Full output in the pipeline log at ").rstrip(".") in logged
    assert "BILLSTATUS-119hconres.zip" in logged and "Traceback (most recent call last):" in logged


def test_a_gate_refusal_alert_gives_the_gates_reason(alerts):
    spec = dr.DataFile("t_refused.json", command=lambda ctx: _writer({"rows": []}),
                       gate=lambda new, old: ["no rows", "12 titles where the copy in use counts 9000"])
    dr.refresh_one(spec, _ctx("t_refused.json"))
    assert _alert_body(alerts) == (
        "app/data/t_refused.json was not refreshed: its gate refused the new candidate. "
        "The previous copy stays in use.\nno rows; 12 titles where the copy in use counts 9000"
    )


def test_a_timeout_alert_gives_the_limit(alerts):
    spec = dr.DataFile("t_slow.json", timeout_s=1, command=lambda ctx: [
        sys.executable, "-c", "import time; print('working', flush=True); time.sleep(60)"])
    assert dr.refresh_one(spec, _ctx("t_slow.json")).startswith("kept: its generator ran too long")
    lead, detail, where = _alert_body(alerts).split("\n")
    assert lead == ("app/data/t_slow.json was not refreshed: its generator ran too long and was stopped. "
                    "The previous copy stays in use.")
    assert detail == "Time limit: 1 second."
    assert where.startswith("Full output in the pipeline log at ")


def test_a_source_that_does_not_answer_is_said_so(alerts):
    spec = dr.DataFile("t_down.json", command=lambda ctx: [
        sys.executable, "-c", "raise ConnectionRefusedError(111, 'Connection refused')"])
    dr.refresh_one(spec, _ctx("t_down.json"))
    lead, detail, _ = _alert_body(alerts).split("\n")
    assert "its generator could not reach its source." in lead
    assert detail.startswith("ConnectionRefusedError: [Errno 111] Connection refused (<string>:1 in <module>)")


def test_output_summary_points_at_our_frame_not_the_librarys():
    trace = (
        "progress 1\nTraceback (most recent call last):\n"
        '  File "/app/scripts/build_x.py", line 245, in main\n    fetch()\n'
        '  File "/usr/local/lib/python3.13/site-packages/httpx/_client.py", line 900, in get\n'
        "    raise ConnectError(msg)\n    ^^^^^^^^^^^^^^^^^^^^^^^\n"
        "httpx.ConnectError: [Errno -2] Name or service not known\n"
    )
    assert output_summary(trace) == "httpx.ConnectError: [Errno -2] Name or service not known (build_x.py:245 in main)"
    assert output_summary("one\nsource moved\n\n") == "source moved"
    assert output_summary("x " * 400).endswith(" …")
    assert output_summary("") is None


def test_a_refresh_that_moves_scores_says_so(alerts):
    spec = dr.DataFile("t_scores.json", command=lambda ctx: _writer({"v": 1}), moves_scores=True)
    dr.refresh_one(spec, _ctx("t_scores.json"))
    assert "Scores that read it move at the next run" in alerts.send.call_args.args[1]
    assert alerts.send.call_args.kwargs.get("condition") is None


# ── the daily pass ───────────────────────────────────────────────────

def test_heavy_files_wait_for_the_pipeline_and_stale_ones_alert(alerts):
    tried = []
    heavy = dr.DataFile("t_heavy.json", command=lambda ctx: _writer({"v": 1}), due=lambda ctx: True, heavy=True)
    stale = dr.DataFile("t_stale.json", stale=lambda ctx: "a newer one is out")
    fresh = dr.DataFile("t_fresh.json")
    with patch.object(dr, "_last_try", return_value=(None, False)), \
         patch.object(dr, "_record_try", side_effect=lambda n, o, r=None: tried.append(n)):
        out = dr.run_refreshes([heavy, stale, fresh], pipeline_busy=True)
        assert out == {"t_heavy.json": "waiting for the pipeline", "t_stale.json": "current", "t_fresh.json": "current"}
        assert tried == []
        assert alerts.send.call_args.kwargs["condition"] == "data-stale-t-stale"
        assert dr.run_refreshes([heavy], pipeline_busy=False)["t_heavy.json"] == "refreshed"
    assert tried == ["t_heavy.json"]
    assert "data-stale-t-fresh" in [c.args[0] for c in alerts.resolve.call_args_list]


def test_one_files_error_never_stops_the_rest(alerts):
    def boom(ctx):
        raise RuntimeError("bad spec")

    with patch.object(dr, "_last_try", return_value=(None, False)):
        out = dr.run_refreshes([dr.DataFile("t_err.json", stale=boom), dr.DataFile("t_after.json")])
    assert out == {"t_err.json": "error", "t_after.json": "current"}


# ── when each file is due, at future dates ───────────────────────────

def _election(day, phase="campaign"):
    return ActiveElection(election_day=day, phase=phase, results_until=None, last_result_change=None)


def test_poll_closes_are_read_for_the_coming_election_in_its_year(freeze_utcnow):
    freeze_utcnow(datetime(2028, 3, 1, 12))
    with patch.object(dr, "_election", return_value=_election(date(2028, 11, 7))):
        assert dr._poll_close_command(_ctx(today=date(2027, 12, 1))) is None
        ctx = _ctx(doc={"year": 2026}, today=date(2028, 3, 1))
        assert dr._poll_close_due(ctx) and dr._poll_close_command(ctx)[-2] == "2028"
        assert not dr._poll_close_due(_ctx(doc={"year": 2026}, today=date(2028, 3, 1), last=datetime(2028, 2, 27)))
        freeze_utcnow(datetime(2028, 9, 1, 12))
        assert dr._poll_close_due(_ctx(doc={"year": 2028}, today=date(2028, 9, 1), last=datetime(2028, 7, 1)))


def test_the_voter_calendar_is_rebuilt_for_the_coming_election_from_200_days_out(freeze_utcnow):
    freeze_utcnow(datetime(2028, 5, 1, 12))
    with patch.object(dr, "_election", return_value=_election(date(2028, 11, 7))):
        assert not dr._calendar_due(_ctx(today=date(2028, 4, 1)))
        ctx = _ctx(today=date(2028, 5, 1))
        assert dr._calendar_due(ctx)
        assert dr._calendar_command(ctx)[2:4] == ["--election", "2028-11-07"]
    with patch.object(dr, "_election", return_value=_election(date(2026, 11, 3), "results")):
        assert dr._calendar_command(ctx) is None


def test_jurisdictions_are_reread_weekly_until_they_are_the_sitting_congresses(monkeypatch, freeze_utcnow):
    freeze_utcnow(datetime(2027, 1, 20, 12))
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    assert dr._jurisdictions_due(_ctx(doc={"congress": 119}, last=datetime(2027, 1, 12)))
    assert not dr._jurisdictions_due(_ctx(doc={"congress": 120}, last=datetime(2027, 1, 12)))


def test_the_routing_fit_is_refit_daily_from_the_data_under_the_acceptance_rule():
    spec = next(f for f in dr.DATA_FILES if f.name == "committee_routing_calibration.json")
    argv = spec.command(_ctx(spec.name))
    assert "{in_use}" in argv and "{out}" in argv and spec.accept is dr.precision_rule_accepts
    assert spec.due(_ctx(spec.name, last=datetime(2000, 1, 1)))
    good = {"reference": "data", "k": 15, "threshold": 0.5, "labels": {"bill": 9, "none": 3}}
    assert dr._routing_gate(good, {}) == []
    assert dr._routing_gate({**good, "reference": None}, {})
    assert dr._routing_gate({**good, "labels": {"bill": 9, "none": 0}}, {})


# ── gates ────────────────────────────────────────────────────────────

def test_the_poll_close_gate():
    states = {s: {"close": "20:00"} for s in STATE_NAMES if s != "DC"}
    assert dr._poll_close_gate({"states": states}, {"states": states}) == []
    assert dr._poll_close_gate({"states": {**states, "OH": {"close": "09:00"}}}, {})
    moved = {s: {"close": "21:00"} for s in states}
    assert "changed at once" in dr._poll_close_gate({"states": moved}, {"states": states})[0]


def test_the_calendar_gate_allows_a_carried_calendar_to_lose_items_but_not_a_rechecked_one():
    old = {"electionDate": "2026-11-03", "states": {"WA": {"items": [1] * 10}, "OR": {"items": [1] * 10}}}
    carried = {"electionDate": "2028-11-07", "states": {"WA": {"items": [1] * 4}, "OR": {"items": [1] * 4}}}
    assert dr._calendar_gate(carried, old) == []
    assert dr._calendar_gate({**carried, "electionDate": "2026-11-03"}, old)
    assert dr._calendar_gate({"electionDate": "2028-11-07", "states": {"WA": {"items": [1] * 10}}}, old)


def test_the_population_gate_allows_a_new_census_and_holds_a_jump_within_one():
    base = {s: 5.0 for s in STATE_NAMES if s != "DC"}
    assert dr._population_gate({"census": 2020, "states": base}, {"census": 2020, "states": base}) == []
    jumped = {**base, "TX": 6.0}
    assert dr._population_gate({"census": 2020, "states": jumped}, {"census": 2020, "states": base})
    assert dr._population_gate({"census": 2030, "states": jumped}, {"census": 2020, "states": base}) == []


def test_the_coverage_gate_holds_a_big_move():
    old = {"house": {"floors": {"les_reference": 0.5}}, "senate": {"floors": {"les_reference": 0.5}}}
    assert dr._coverage_gate(old, old) == []
    moved = {"house": {"floors": {"les_reference": 0.95}}, "senate": {"floors": {"les_reference": 0.5}}}
    assert dr._coverage_gate(moved, old)


# ── staleness of what a person regenerates ───────────────────────────

def test_the_ces_gate_holds_an_older_or_thinner_survey():
    old = {"fielded_year": 2024, "members": [{}] * 500, "priors": {"x": 1}}
    assert dr._ces_gate({"fielded_year": 2025, "members": [{}] * 480, "priors": {"x": 1}}, old) == []
    assert dr._ces_gate({"fielded_year": 2022, "members": [{}] * 500, "priors": {"x": 1}}, old)
    assert dr._ces_gate({"fielded_year": 2026, "members": [{}] * 100, "priors": {"x": 1}}, old)


def test_the_position_weight_is_refit_once_its_newest_congress_has_ended(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)
    assert not dr._position_confidence_due(_ctx(doc={"calibrated_through": 119}))
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    assert dr._position_confidence_due(_ctx(doc={"calibrated_through": 119}))
    old = dict(dr.read("position_confidence.json"))
    assert dr._position_confidence_gate(old, old) == []
    assert dr._position_confidence_gate({**old, "structure": "trend"}, old)
    assert dr._position_confidence_gate({**old, "calibrated_through": old["calibrated_through"] - 1}, old)
    spec = next(f for f in dr.DATA_FILES if f.name == "position_confidence.json")
    assert "{in_use}" in spec.command(_ctx(spec.name)) and spec.scoring_inputs(old) == dr._position_inputs(old)


def test_the_commemorative_gate_holds_other_prototypes():
    old = dict(dr.read("commemorative_calibration.json"))
    assert dr._commemorative_gate(old, old) == []
    assert dr._commemorative_gate({**old, "prototype_hash": "x"}, old)
    assert dr._commemorative_gate({**old, "threshold": 1.5}, old)


def test_the_vote_grades_gate():
    good = {"chambers": {"senate": {"partial": 33, "full": 53}, "house": {"partial": 15, "full": 30}}}
    assert dr._grades_gate(good, good) == []
    assert dr._grades_gate({"chambers": {"senate": {"partial": 60, "full": 53}}}, {})
    doubled = {"chambers": {"senate": {"partial": 70, "full": 120}, "house": {"partial": 15, "full": 30}}}
    assert any("moved" in f for f in dr._grades_gate(doubled, good))


def test_the_district_lean_window_goes_stale_after_the_next_presidential_election():
    doc = {"_window": "2020+2024"}
    assert dr._district_lean_stale(_ctx(doc=doc, today=date(2028, 12, 14))) is None
    assert "2028" in dr._district_lean_stale(_ctx(doc=doc, today=date(2028, 12, 15)))


def test_the_maps_priors_are_fitted_once_their_election_is_certified(monkeypatch):
    """From the January 3 after the election the priors were stated for, the
    refit also fits them (kept only under the acceptance rule, inside the
    script), and the first day it can, it runs: no alert asks anyone to."""
    monkeypatch.setattr(dr, "utcnow", lambda: datetime(2027, 1, 3, 16))
    spec = next(f for f in dr.DATA_FILES if f.name == "forecast_model.json")
    assert spec.stale(_ctx(spec.name)) is None
    for day, fitted in ((date(2026, 10, 30), False), (date(2027, 1, 2), False), (date(2027, 1, 3), True)):
        assert ("--fit-priors" in spec.command(_ctx(spec.name, today=day))) is fitted
    yesterday, in_use = datetime(2027, 1, 2, 16), dict(dr.read("forecast_model.json"))
    assert dr._forecast_due(_ctx(spec.name, in_use, today=date(2027, 1, 3), last=yesterday))
    measured = {**in_use, "priorsComparison": {}}
    assert not dr._forecast_due(_ctx(spec.name, measured, today=date(2027, 1, 3), last=yesterday))


def test_the_peer_gdp_gate_holds_a_changed_rule_or_a_shorter_series():
    old = {"peers": ["AUS"], "release": 2023, "countries": {"AUS": {"2022": 1.0}, "USA": {"2022": 2.0}}}
    assert dr._peer_gdp_gate({**old, "release": 2026, "countries": {"AUS": {"2025": 1}, "USA": {"2025": 2}}}, old) == []
    assert dr._peer_gdp_gate({**old, "peers": ["AUS", "NZL"]}, old)
    assert dr._peer_gdp_gate({**old, "countries": {"USA": {"2022": 2.0}}}, old)
    assert dr._peer_gdp_gate({**old, "countries": {"AUS": {"2021": 1}, "USA": {"2021": 2}}}, old)


def test_a_check_that_did_not_look_today_leaves_its_alert_as_it_is(alerts):
    """A monthly release check returning UNCHECKED neither sends nor
    resolves: returning None resolved its alert the day after it opened."""
    spec = dr.DataFile("x.json", stale=lambda ctx: dr.UNCHECKED)
    with patch.object(dr, "read", return_value={}), patch.object(dr, "_last_try", return_value=(None, False)):
        assert dr.run_refreshes([spec]) == {"x.json": "current"}
    alerts.send.assert_not_called()
    alerts.resolve.assert_not_called()


def test_the_eavs_gate_refuses_an_older_survey_a_missing_state_or_other_items():
    states = {s: {} for s in STATE_NAMES}
    old = {"year": 2024, "items": {"A1a": "x"}, "states": states}
    assert dr._eavs_gate({"year": 2026, "items": {"A1a": "x"}, "states": states}, old) == []
    assert dr._eavs_gate({"year": 2022, "items": {"A1a": "x"}, "states": states}, old)
    assert dr._eavs_gate({"year": 2026, "items": {"A1a": "x"}, "states": {"AK": {}}}, old)
    assert dr._eavs_gate({"year": 2026, "items": {"B1": "y"}, "states": states}, old)


def test_the_agency_crosswalk_gate_refuses_a_threshold_that_separates_nothing_or_shrunk_lists():
    old = {"threshold": 0.83, "youden_j": 0.9, "fr_agencies": 400, "toptier_agencies": 110, "labelled": 80}
    assert dr._agency_crosswalk_gate(dict(old), old) == []
    assert dr._agency_crosswalk_gate({**old, "youden_j": 0}, old)
    assert dr._agency_crosswalk_gate({**old, "toptier_agencies": 20}, old)


def test_the_occupation_crosswalk_is_refreshed_when_a_newer_acs_or_onet_is_out(monkeypatch, db_session):
    """Yearly, and within a week of a newer release; the publishers are asked
    at most monthly, and one that can't be read is no newer release."""
    doc = {"acs_year": 2024, "onet_version": "31.0"}
    monkeypatch.setattr(dr, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(dr, "utcnow", lambda: datetime(2027, 3, 1))
    last_week = datetime(2027, 2, 20)
    monkeypatch.setattr(dr, "_url_exists", lambda url: False)
    monkeypatch.setattr(dr, "_get_text", lambda url: "db_31_0_text.zip")
    assert not dr._occupation_due(_ctx(dr.OCCUPATION_FILE, doc, today=date(2027, 3, 1), last=last_week))
    asked = []
    monkeypatch.setattr(dr, "_url_exists", lambda url: asked.append(url) or "2025" in url)
    doc2 = {**doc, "acs_year": 2023}  # a different file: asked afresh
    assert dr._occupation_due(_ctx(dr.OCCUPATION_FILE, doc2, today=date(2027, 3, 1), last=last_week))
    assert asked
    monkeypatch.setattr(dr, "_get_text", lambda url: None)
    assert dr._occupation_gate({"acs_year": 2024, "occupations": {"a": {"industry": "X"}}, "titles": {"t": "a"}},
                               {"acs_year": 2024, "occupations": {"a": {"industry": "X"}}, "titles": {"t": "a"}}) == []
    assert dr._occupation_gate({"acs_year": 2022}, {"acs_year": 2024})


def test_the_court_finding_gate_holds_an_older_release_or_an_impossible_interval():
    old = {"scdbRelease": "2026_01", "estimate": 0.02, "low": -0.01, "high": 0.05, "draws": 200,
           "firstTerm": 1937, "lastTerm": 2025, "justices": 43}
    assert dr._court_finding_gate({**old, "scdbRelease": "2027_01", "lastTerm": 2026, "justices": 44}, old) == []
    assert dr._court_finding_gate({**old, "scdbRelease": "2025_02"}, old)
    assert dr._court_finding_gate({**old, "estimate": 0.2}, old)
    assert dr._court_finding_gate({**old, "justices": 40}, old)


def test_only_the_district_lean_is_left_on_a_staleness_check():
    """Its next window needs the next presidential election's precinct
    returns; its alert states that they are out, not a task."""
    assert [f.name for f in dr.DATA_FILES if f.command is None] == ["district_lean.json"]
    why = dr._district_lean_stale(_ctx(doc={"_window": "2020+2024"}, today=date(2028, 12, 20)))
    assert "2028" in why and not any(w in why for w in ("rerun", "Rerun", "research", "commit", "point "))


def test_an_installed_refit_that_moves_scoring_inputs_records_a_scoring_revision(alerts):
    base = score_version.ALGORITHM_VERSION
    _write_runtime("t_rev.json", {"threshold": 0.3})
    spec = dr.DataFile("t_rev.json", command=lambda ctx: _writer({"threshold": 0.3, "note": "new measurement"}),
                       scoring_inputs=lambda d: d.get("threshold"))
    assert dr.refresh_one(spec, _ctx("t_rev.json", dr.read("t_rev.json"))) == "refreshed"
    assert score_version.scoring_version() == base  # the same threshold: no revision
    spec = dr.DataFile("t_rev.json", command=lambda ctx: _writer(
        {"threshold": 0.4, "_change": "threshold 0.3 -> 0.4"}), scoring_inputs=lambda d: d.get("threshold"))
    assert dr.refresh_one(spec, _ctx("t_rev.json", dr.read("t_rev.json"))) == "refreshed"
    assert score_version.scoring_version() == f"{base}.1"
    assert scoring_revisions.revisions()[-1]["why"] == "threshold 0.3 -> 0.4"
    assert f"{base}.1" in alerts.send.call_args.args[0] and alerts.send.call_args.kwargs.get("condition") is None


def test_every_generated_file_on_record_exists_and_every_command_names_a_real_script():
    for spec in dr.DATA_FILES:
        assert (dr.BUNDLED / spec.name).exists(), spec.name
        if spec.command and spec.name not in ("poll_close_times.json", "election_calendar.json"):
            argv = spec.command(_ctx(spec.name))
            assert pathlib.Path(argv[1]).exists() and "{out}" in argv, spec.name


def test_the_house_rules_are_found_for_the_newest_congress_the_page_links():
    script = dr.BACKEND / "scripts" / "fetch_committee_jurisdictions.py"
    spec = importlib.util.spec_from_file_location("fetch_committee_jurisdictions", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    page = ('<a href="/sites/files/documents/houserules119thupdated.pdf">119th</a>'
            '<a href="https://rules.house.gov/sites/files/documents/houserules120th.pdf">120th</a>')
    url, congress = module.house_rules_url(page)
    assert congress == 120 and url.endswith("houserules120th.pdf")
    with pytest.raises(SystemExit):
        module.house_rules_url("<p>no links</p>")


def test_the_population_source_says_which_census_it_reports():
    script = dr.BACKEND / "scripts" / "fetch_state_population.py"
    spec = importlib.util.spec_from_file_location("fetch_state_population", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.latest_census("<th>2020 census rank</th><td>2010 census</td>") == 2020
    assert module.latest_census("<th>2030 census rank</th> 2020 census") == 2030


def test_a_failed_try_is_retried_within_a_week_whatever_the_cadence():
    with patch.object(dr, "utcnow", return_value=datetime(2026, 10, 26)):
        failed = dr.Context("name_casing.json", {}, date(2026, 10, 26), datetime(2026, 10, 18), last_failed=True)
        kept_fine = dr.Context("name_casing.json", {}, date(2026, 10, 26), datetime(2026, 10, 18))
        assert not failed.tried_within(365)
        assert kept_fine.tried_within(365)
    # A try that finished at 18:48 is a week old at the 15:40 run a week on,
    # and a day old at the next day's (2026-10-18's heavy refreshes).
    with patch.object(dr, "utcnow", return_value=datetime(2026, 10, 25, 15, 40)):
        assert not dr.Context("x.json", {}, date(2026, 10, 25), datetime(2026, 10, 18, 18, 48), True).tried_within(7)
        assert not dr.Context("x.json", {}, date(2026, 10, 25), datetime(2026, 10, 24, 18, 48)).tried_within(1)
        assert dr.Context("x.json", {}, date(2026, 10, 25), datetime(2026, 10, 25, 9, 0)).tried_within(1)


def test_the_last_try_remembers_a_kept_outcome(db_session):
    with patch.object(dr, "SessionLocal", return_value=db_session), patch.object(db_session, "close"):
        dr._record_try("x.json", "kept: 8832 forms")
        assert dr._last_try("x.json")[1] is True
        dr._record_try("x.json", "refreshed")
        assert dr._last_try("x.json")[1] is False


def test_name_casing_refresh_reads_as_many_issues_as_the_bundled_table():
    # 2026-10-18: the refresh ran the script's default of 120 issues against a
    # table built from the whole year (247), and its gate refused the result.
    spec = next(f for f in dr.DATA_FILES if f.name == "name_casing.json")
    argv = spec.command(dr.Context(spec.name, {}, date(2027, 3, 1), None))
    issues = int(argv[argv.index("--days") + 1])
    bundled = json.loads((dr.BUNDLED / "name_casing.json").read_text())["_source"]
    assert issues >= int(re.search(r"the first (\d+) issues", bundled)[1])
    assert argv[argv.index("--year") + 1] == "2026"


def test_a_search_calibration_is_refit_within_a_week_of_a_kind_its_fit_never_saw(monkeypatch):
    monkeypatch.setattr(dr.site_search, "index_kinds", lambda: {"member", "bill", "nomination"})
    eight_days = datetime(2028, 2, 22)
    monkeypatch.setattr(dr, "utcnow", lambda: datetime(2028, 3, 1))
    assert dr._search_due(_ctx("s.json", {"kinds": ["member", "bill"]}, last=eight_days))
    assert not dr._search_due(_ctx("s.json", {"kinds": ["member", "bill", "nomination"]}, last=eight_days))
    assert dr._search_due(_ctx("s.json", {"kinds": ["member", "bill", "nomination"]}, last=datetime(2028, 1, 20)))
    for spec in (f for f in dr.DATA_FILES if f.name in ("site_search_calibration.json",
                                                         "search_fusion_calibration.json")):
        argv = spec.command(_ctx(spec.name))
        assert "{in_use}" in argv and "{out}" in argv and spec.accept is dr.held_out_beats_in_use
        assert spec.stale(_ctx(spec.name)) is None  # no alert asks anyone to rerun it


# ── the acceptance rule ──────────────────────────────────────────────

def _comparison(delta, se, **more):
    return {"params": {"x": 2}, "_comparison": {"metric": "m", "delta": delta, "se": se, **more}}


@pytest.mark.parametrize("candidate,why", [
    (_comparison(0.01, 0.02), "within noise"),
    (_comparison(0.05, 0.01, subgroups={"name": {"delta": -0.3, "se": 0.1}, "typo": {"delta": 0.2, "se": 0.1}}),
     "loses more than one standard error on name"),
    (_comparison(0.05, 0.01, refused=["the refit jumps wrong on 2 fit-half probes"]), "jumps wrong"),
    ({"params": {"x": 2}}, "no held-out comparison"),
])
def test_a_refit_that_does_not_clear_the_rule_keeps_the_copy_in_use_on_record_without_an_alert(alerts, candidate,
                                                                                               why):
    _write_runtime("t_acc.json", {"params": {"x": 1}})
    spec = dr.DataFile("t_acc.json", command=lambda ctx: _writer(candidate), accept=dr.held_out_beats_in_use)
    outcome = dr.refresh_one(spec, _ctx("t_acc.json", dr.read("t_acc.json")))
    assert outcome.startswith("in use:") and why in outcome
    doc = dr.read("t_acc.json")
    assert doc["params"] == {"x": 1}
    assert why in doc["_refreshed"]["kept"]["why"]
    alerts.send.assert_not_called()
    alerts.resolve.assert_called_with("data-refresh-t-acc")
    # Not a failure (those start "kept"): it waits its cadence, not the week's retry.
    assert not outcome.startswith("kept")


def test_a_refit_that_wins_by_more_than_a_standard_error_is_installed(alerts):
    _write_runtime("t_win.json", {"params": {"x": 1}})
    candidate = _comparison(0.05, 0.01, subgroups={"name": {"delta": -0.005, "se": 0.01}})
    spec = dr.DataFile("t_win.json", command=lambda ctx: _writer(candidate), accept=dr.held_out_beats_in_use)
    assert dr.refresh_one(spec, _ctx("t_win.json", dr.read("t_win.json"))) == "refreshed"
    assert dr.read("t_win.json")["params"] == {"x": 2}


def test_a_refit_that_reproduces_the_parameters_in_use_installs_its_new_measurements(alerts):
    _write_runtime("t_same.json", {"params": {"x": 1}, "kinds": ["a"]})
    candidate = {"params": {"x": 1}, "kinds": ["a", "b"], "_comparison": {"unchanged": True}}
    spec = dr.DataFile("t_same.json", command=lambda ctx: _writer(candidate), accept=dr.held_out_beats_in_use)
    assert dr.refresh_one(spec, _ctx("t_same.json", dr.read("t_same.json"))) == "refreshed"
    assert dr.read("t_same.json")["kinds"] == ["a", "b"]


def _side(passed, correct, bound, recall):
    return {"passed": passed, "correct": correct, "precision": correct / passed if passed else None,
            "precision_exact_lower": bound, "recall": recall}


@pytest.mark.parametrize("comparison,installed", [
    ({"candidate": _side(100, 90, 0.82, 0.3), "in_use": _side(80, 60, 0.64, 0.2), "recall_delta": 0.1,
      "recall_se": 0.01}, True),
    ({"candidate": _side(100, 70, 0.61, 0.3), "in_use": _side(80, 60, 0.64, 0.2), "recall_delta": 0.1,
      "recall_se": 0.01}, False),
    ({"candidate": _side(10, 9, 0.82, 0.1), "in_use": _side(80, 70, 0.80, 0.2), "recall_delta": -0.1,
      "recall_se": 0.01}, False),
    ({"unchanged": True}, True),
    ({"unchanged": True, "refused": ["no threshold meets the precision rule"]}, False),
])
def test_a_gate_refit_from_data_labels_installs_only_under_the_precision_rule(alerts, comparison, installed):
    _write_runtime("t_gate.json", {"threshold": 0.4})
    candidate = {"threshold": 0.5, "_comparison": comparison}
    spec = dr.DataFile("t_gate.json", command=lambda ctx: _writer(candidate), accept=dr.precision_rule_accepts)
    outcome = dr.refresh_one(spec, _ctx("t_gate.json", dr.read("t_gate.json")))
    assert (outcome == "refreshed") is installed, outcome
    assert dr.read("t_gate.json")["threshold"] == (0.5 if installed else 0.4)
    alerts.send.assert_not_called()


def test_a_requested_refit_is_due_at_the_next_run(db_session):
    with patch.object(dr, "SessionLocal", return_value=db_session), patch.object(db_session, "close"):
        dr._record_try("t_req.json", "refreshed")
        assert not dr._every(1)(_ctx("t_req.json", last=dr._last_try("t_req.json")[0]))
        dr.request_refit("t_req.json", "drift")
        assert dr._last_try("t_req.json")[0] is None
        assert dr._every(1)(_ctx("t_req.json", last=dr._last_try("t_req.json")[0]))


def test_the_script_is_handed_the_copy_in_use(alerts):
    """{in_use} is a file holding the copy in use, refreshed or bundled."""
    _write_runtime("t_inuse.json", {"params": {"x": 7}})
    script = ("import json,sys; old=json.load(open(sys.argv[1])); "
              "json.dump({'params': old['params'], 'seen': old['params']['x']}, open(sys.argv[2],'w'))")
    spec = dr.DataFile("t_inuse.json", command=lambda ctx: [sys.executable, "-c", script, "{in_use}", "{out}"])
    assert dr.refresh_one(spec, _ctx("t_inuse.json", dr.read("t_inuse.json"))) == "refreshed"
    assert dr.read("t_inuse.json")["seen"] == 7


def test_a_shipped_fit_supersedes_an_accepted_refit(alerts, tmp_path):
    """A commit that ships a new fit replaces a refit installed before it,
    and the next refit is measured against what shipped."""
    (tmp_path / "t_sup.json").write_text(json.dumps({"params": {"x": 1}}))
    seen = []

    def command(ctx):
        seen.append(ctx.doc.get("params"))
        return _writer(_comparison(0.0, 0.01))

    spec = dr.DataFile("t_sup.json", command=command, accept=dr.held_out_beats_in_use)
    with patch.object(dr, "BUNDLED", tmp_path), patch.object(dr, "_record_try"), \
         patch.object(dr, "_last_try", return_value=(None, False, None)):
        dr.install(spec, {"params": {"x": 2}}, ["refit"])
        (tmp_path / "t_sup.json").write_text(json.dumps({"params": {"x": 3}}))  # a commit ships x=3
        assert dr.run_refreshes([spec])["t_sup.json"].startswith("in use:")
    assert seen[-1] == {"x": 3}
    assert dr.read("t_sup.json")["params"] == {"x": 3}


def test_a_shipped_regeneration_is_refreshed_the_next_day(alerts, tmp_path):
    # 2026-10-19: #1113 committed the House fit through 2024, and the map
    # kept serving the refit of 2026-10-18 (through 2022) from /data.
    (tmp_path / "t_ship.json").write_text(json.dumps({"v": 1}))
    spec = dr.DataFile("t_ship.json", command=lambda ctx: _writer({"v": 2}), due=lambda ctx: False)
    with patch.object(dr, "BUNDLED", tmp_path), patch.object(dr, "_record_try"), \
         patch.object(dr, "_last_try", return_value=(None, False)):
        assert dr.refresh_one(spec, _ctx("t_ship.json")) == "refreshed"
        assert dr.read("t_ship.json")["_refreshed"]["bundled"] == dr.content_sha256({"v": 1})
        assert dr.run_refreshes([spec]) == {"t_ship.json": "current"}  # nothing shipped since
        (tmp_path / "t_ship.json").write_text(json.dumps({"v": 3}))  # a commit regenerates it
        assert dr.bundled_moved("t_ship.json", dr.read("t_ship.json"))
        # The same content as in use: nothing installed, but now measured
        # against what shipped, so it is not refreshed again every day.
        assert dr.run_refreshes([spec]) == {"t_ship.json": "unchanged"}
        assert not dr.bundled_moved("t_ship.json", dr.read("t_ship.json"))
        # A refreshed copy installed before the record existed (every copy
        # in production on 2026-10-22, the House fit's among them) is
        # refreshed once, which records it; the bundled copy itself never is.
        assert dr.bundled_moved("t_ship.json", {"v": 2, "_refreshed": {"at": "2026-10-18T18:48:52"}})
        assert not dr.bundled_moved("t_ship.json", {"v": 3})


def test_a_failed_try_is_retried_the_next_day_once_what_it_runs_changes(alerts, tmp_path):
    # 2026-10-18: three refreshes failed; each fix shipped within a day (two in
    # the generator script, one in the command line) and waited out the week.
    script = tmp_path / "gen.py"
    script.write_text("broken")
    tried = []
    spec = dr.DataFile("t_fix.json", command=lambda ctx: [sys.executable, str(script), "{out}"],
                       due=lambda ctx: False)
    with patch.object(dr, "BACKEND", tmp_path), patch.object(dr, "utcnow", return_value=datetime(2026, 10, 20)), \
         patch.object(dr, "refresh_one", side_effect=lambda spec, ctx: tried.append(1) or "kept: crashed"), \
         patch.object(dr, "_record_try"):
        failed_with = dr.recipe(spec.command(None))
        yesterday = datetime(2026, 10, 19, 18, 0)
        with patch.object(dr, "_last_try", return_value=(yesterday, True, failed_with)):
            dr.run_refreshes([spec])
            assert tried == []  # nothing changed: the week's retry stands
            script.write_text("fixed")
            dr.run_refreshes([spec])
            assert tried == [1]
        # A failure recorded before recipes were counts as changed.
        with patch.object(dr, "_last_try", return_value=(yesterday, True, None)):
            dr.run_refreshes([spec])
            assert tried == [1, 1]
        # Never twice in a day, and never for a try that succeeded.
        with patch.object(dr, "_last_try", return_value=(datetime(2026, 10, 20, 9), True, None)):
            dr.run_refreshes([spec])
        with patch.object(dr, "_last_try", return_value=(yesterday, False, None)):
            dr.run_refreshes([spec])
        assert tried == [1, 1]


def test_a_refit_under_the_acceptance_rule_is_measured_the_day_after_its_code_changes(alerts, tmp_path):
    """A ranking change ships; the search calibration is refit against it the
    next day, not at the month's end."""
    script = tmp_path / "fit.py"
    script.write_text("ranker v1")
    tried = []
    spec = dr.DataFile("t_rank.json", command=lambda ctx: [sys.executable, str(script), "{out}"],
                       due=lambda ctx: False, accept=dr.held_out_beats_in_use)
    with patch.object(dr, "BACKEND", tmp_path), patch.object(dr, "utcnow", return_value=datetime(2026, 10, 20)), \
         patch.object(dr, "refresh_one", side_effect=lambda spec, ctx: tried.append(1) or "in use: within noise"), \
         patch.object(dr, "_record_try"):
        fitted_with = dr.recipe(spec.command(None))
        with patch.object(dr, "_last_try", return_value=(datetime(2026, 10, 19), False, fitted_with)):
            dr.run_refreshes([spec])
            assert tried == []
            script.write_text("ranker v2")
            dr.run_refreshes([spec])
            assert tried == [1]


def test_a_fix_in_a_module_the_script_imports_is_retried_the_next_day(alerts, tmp_path):
    # The script names only itself; the fix lands two imports away, in a
    # module reached by a relative import.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "app" / "analyze").mkdir(parents=True)
    script = tmp_path / "scripts" / "gen.py"
    script.write_text("import json\nfrom app.analyze import reader\n")
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "analyze" / "__init__.py").write_text("")
    (tmp_path / "app" / "analyze" / "reader.py").write_text("from . import helpers\nimport app.unrelated_missing\n")
    helpers = tmp_path / "app" / "analyze" / "helpers.py"
    helpers.write_text("x.cache_clear()\n")
    (tmp_path / "app" / "untouched.py").write_text("")
    tried = []
    spec = dr.DataFile("t_dep.json", command=lambda ctx: [sys.executable, str(script), "{out}"],
                       due=lambda ctx: False)
    with patch.object(dr, "BACKEND", tmp_path), patch.object(dr, "utcnow", return_value=datetime(2026, 10, 20)), \
         patch.object(dr, "refresh_one", side_effect=lambda spec, ctx: tried.append(1) or "kept: crashed"), \
         patch.object(dr, "_record_try"):
        failed_with = dr.recipe(spec.command(None))
        (tmp_path / "app" / "untouched.py").write_text("changed, but nothing imports it")
        assert dr.recipe(spec.command(None)) == failed_with
        with patch.object(dr, "_last_try", return_value=(datetime(2026, 10, 19, 18), True, failed_with)):
            dr.run_refreshes([spec])
            assert tried == []
            helpers.write_text("pass\n")
            dr.run_refreshes([spec])
            assert tried == [1]


def test_recipe_follows_the_real_generators_imports():
    spec = next(f for f in dr.DATA_FILES if f.name == "bill_title_token_df.json")
    files = {f.relative_to(dr.BACKEND).as_posix()
             for f in dr._backend_imports(dr.BACKEND / "scripts" / "calibrate_lda_bill_match.py")}
    assert "app/pipeline/analyze/lobbying_records.py" in files
    assert len(dr.recipe(spec.command(_ctx(spec.name)))) == 16


def test_a_try_records_the_recipe_it_ran(db_session):
    with patch.object(dr, "SessionLocal", return_value=db_session), patch.object(db_session, "close"):
        dr._record_try("x.json", "kept: crashed", "abc")
        assert dr._last_try("x.json")[1:] == (True, "abc")


def test_state_pvi_gate_refuses_a_flipped_or_short_candidate():
    old = json.loads((dr.BUNDLED / "state_pvi.json").read_text())
    assert dr._state_pvi_gate(old, old) == []
    flipped = {**old, "states": {st: -v for st, v in old["states"].items()}}
    assert any("DC" in f for f in dr._state_pvi_gate(flipped, old))
    assert any("moved more than 5" in f for f in dr._state_pvi_gate(flipped, old))
    short = {**old, "states": {st: v for st, v in old["states"].items() if st != "WY"}}
    assert dr._state_pvi_gate(short, old)


def test_scoring_reads_the_refreshed_position_weight(monkeypatch):
    bundled = score_calculator._position_reliability("house")
    doc = dict(dr.read("position_confidence.json"))
    doc["chambers"] = {c: {**v, "n0": 99.0} for c, v in doc["chambers"].items()}
    _write_runtime("position_confidence.json", doc)
    assert score_calculator._position_reliability("house")["n0"] == 99.0
    pathlib.Path(dr.runtime_data_path("position_confidence.json")).unlink()
    assert score_calculator._position_reliability("house") == bundled


def test_the_changelog_lists_scoring_revisions_newest_first():
    scoring_revisions.record("v9.1", "position_confidence.json", "a" * 64, "structure pooled kept")
    scoring_revisions.record("v9.1", "commemorative_calibration.json", "b" * 64, "threshold 0.33 -> 0.31")
    app = FastAPI()
    app.include_router(feed_api.router, prefix="/api")
    listed = TestClient(app).get("/api/score-revisions").json()["revisions"]
    assert [r["version"] for r in listed] == ["v9.1.2", "v9.1.1"]
    assert scoring_revisions.scoring_version("v9.1") == "v9.1.2" and scoring_revisions.scoring_version("v9.2") == "v9.2"
