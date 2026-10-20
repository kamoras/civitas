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

from app import committee_routing
from app import data_refresh as dr
from app.config import settings
from app.election_phase import ActiveElection
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
    assert detail == "Time limit: 1 seconds."
    assert where.startswith("Full output in the pipeline log at ")


def test_a_source_that_does_not_answer_is_said_so(alerts):
    spec = dr.DataFile("t_down.json", command=lambda ctx: [
        sys.executable, "-c", "raise ConnectionRefusedError(111, 'Connection refused')"])
    dr.refresh_one(spec, _ctx("t_down.json"))
    lead, detail, _ = _alert_body(alerts).split("\n")
    assert "its generator could not reach its source." in lead
    assert detail.startswith("ConnectionRefusedError: [Errno 111] Connection refused (<string>:1 in <module>)")


def test_output_summary_points_at_our_frame_not_the_librarys():
    from app.error_utils import output_summary

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
         patch.object(dr, "_record_try", side_effect=lambda n, o: tried.append(n)):
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


def test_the_routing_fit_is_refit_when_its_inputs_change(monkeypatch):
    monkeypatch.setattr(committee_routing, "data_hash", lambda: "new-inputs")
    assert dr._routing_due(_ctx(doc={"data_hash": "old-inputs"}))
    assert not dr._routing_due(_ctx(doc={"data_hash": "new-inputs"}))
    assert dr._routing_gate({"data_hash": "new-inputs", "k": 5, "threshold": 0.4}, {}) == []
    assert dr._routing_gate({"data_hash": "old-inputs", "k": 5, "threshold": 0.4}, {})


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

def test_a_newer_survey_is_flagged_once_it_should_be_out():
    assert dr._ces_stale(_ctx(doc={"fielded_year": 2024}, today=date(2027, 3, 1))) is None
    assert "2026" in dr._ces_stale(_ctx(doc={"fielded_year": 2024}, today=date(2027, 4, 2)))
    assert dr._ces_stale(_ctx(doc={"fielded_year": 2026}, today=date(2028, 6, 1))) is None


def test_calibrations_tied_to_a_congress_are_flagged_when_the_next_one_is_complete(monkeypatch):
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    assert dr._position_confidence_stale(_ctx(doc={"calibrated_through": 119})) is None
    assert dr._commemorative_stale(_ctx(doc={"les_spearman_118th": 0.9})) is None
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 121)
    assert "120th has ended" in dr._position_confidence_stale(_ctx(doc={"calibrated_through": 119}))
    assert "119th" in dr._commemorative_stale(_ctx(doc={"les_spearman_118th": 0.9}))


def test_the_district_lean_window_goes_stale_after_the_next_presidential_election():
    doc = {"_window": "2020+2024"}
    assert dr._district_lean_stale(_ctx(doc=doc, today=date(2028, 12, 14))) is None
    assert "2028" in dr._district_lean_stale(_ctx(doc=doc, today=date(2028, 12, 15)))


def test_a_newer_maddison_release_is_found_by_search(monkeypatch):
    class Resp:
        def __init__(self, names):
            self.names = names

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"items": [{"name": n} for n in self.names]}}

    doc = {"_source": "Maddison Project Database 2023 (Bolt & van Zanden 2024)"}
    monkeypatch.setattr(dr, "_record_try", lambda n, o: None)
    monkeypatch.setattr(dr.httpx, "get", lambda *a, **k: Resp(["Maddison Project Database 2023"]))
    assert dr._peer_gdp_stale(_ctx("peer_gdp_per_capita.json", doc)) is None
    monkeypatch.setattr(dr.httpx, "get", lambda *a, **k: Resp(["Maddison Project Database 2026", "x"]))
    assert "2026 is published" in dr._peer_gdp_stale(_ctx("peer_gdp_per_capita.json", doc))
    assert dr._peer_gdp_stale(_ctx("peer_gdp_per_capita.json", doc, last=datetime.now())) is None


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
