"""The election map's training polls (app/forecast/training_polls.py) and
their automatic refit (app/data_refresh.py): one fixture per gate, the
poll window measured from the data, no look-ahead in the forward check,
and the alerts' wording. Offline throughout."""

import importlib.util
import json
import pathlib
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import data_refresh as dr
from app.forecast import model
from app.forecast import training_polls as tp

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DATA = BACKEND / "app" / "data"

TERMS = ("You are welcome to use this data free of charge for any purpose, but please attribute it to "
         "Silver Bulletin.")
PAGE = (
    '<p>And here is the raw data used to calculate the averages. ' + TERMS + '</p>'
    '<div class="file-embed-wrapper"><div class="file-embed-details-h1">Pollster Stats Full 2026</div>'
    '<a class="file-embed-button" href="https://www.natesilver.net/api/v1/file/aaaa-1111.xlsx">Download</a></div>'
    '<div class="file-embed-wrapper"><div class="file-embed-details-h1">Rawpolls 011226</div>'
    '<a class="file-embed-button" href="https://www.natesilver.net/api/v1/file/bbbb-2222.xlsx">Download</a></div>'
)


def row(pid, kind="Sen-G", loc="PA", poll="2024-10-20", election="2024-11-05", d=46.0, r=48.0, actual=(48.6, 48.8),
        other="", margin=None):
    """One kept row, in ROW_COLUMNS order (cand1 the Democrat)."""
    margin = d - r if margin is None else margin
    values = {
        "poll_id_nate": str(pid), "race": f"{election[:4]}_{kind}_{loc}", "location": loc, "type_detail": kind,
        "pollster": "Example Poll", "partisan": "", "polldate": poll, "samplesize": "800",
        "cand1_party": "DEM", "cand1_pct": f"{d:g}", "cand2_party": "REP", "cand2_pct": f"{r:g}",
        "cand3_pct": other, "margin_poll": f"{margin:g}", "electiondate": election,
        "cand1_actual": f"{actual[0]:g}", "cand2_actual": f"{actual[1]:g}",
    }
    return [values[c] for c in tp.ROW_COLUMNS]


def doc(rows, certified=None, terms=TERMS, columns=tp.EXPECTED_COLUMNS):
    return {"terms": terms, "columns": list(columns), "rowColumns": list(tp.ROW_COLUMNS), "rows": rows,
            "certified": certified if certified is not None else {}}


# The accepted copy: a 2022 cycle with Senate and generic-ballot polls,
# reaching 21 days before election day.
OLD = doc([
    row(1, poll="2022-10-18", election="2022-11-08"),
    row(2, kind="House-G", loc="US", poll="2022-11-01", election="2022-11-08"),
    row(3, poll="2022-11-07", election="2022-11-08"),
])
# A good update: the same rows, and the 2024 cycle with both halves.
NEW_2024 = [row(10, poll="2024-10-20"), row(11, kind="House-G", loc="US", poll="2024-10-30")]
CERTIFIED = {"2024-PA": 100 * (48.8 - 48.6) / (48.8 + 48.6)}


def good():
    return doc(OLD["rows"] + NEW_2024, CERTIFIED)


def test_a_good_update_passes():
    assert tp.gate(good(), OLD) == []


# ── one fixture per gate ────────────────────────────────────────────

def test_terms_gate():
    failures = tp.gate(dict(good(), terms="All rights reserved."), OLD)
    assert failures and failures[0].startswith("terms:")
    assert tp.gate(dict(good(), terms=None), OLD)[0].startswith("terms:")


def test_columns_gate():
    cols = [c for c in tp.EXPECTED_COLUMNS if c != "samplesize"] + ["sample_n"]
    (failure,) = tp.gate(dict(good(), columns=cols), OLD)
    assert failure == "columns: not the expected set (added ['sample_n'], dropped ['samplesize'])"
    reordered = list(tp.EXPECTED_COLUMNS[1:]) + [tp.EXPECTED_COLUMNS[0]]
    assert tp.gate(dict(good(), columns=reordered), OLD) == [
        "columns: not the expected set (added none, dropped none, reordered)"]


def test_append_only_gate_refuses_a_lost_poll():
    (failure,) = tp.gate(doc(OLD["rows"][1:] + NEW_2024, CERTIFIED), OLD)
    assert failure == "append-only: 1 accepted polls are gone (ids 1)"


def test_append_only_gate_refuses_more_corrections_than_its_bound():
    changed = [row(1, poll="2022-10-18", election="2022-11-08", d=40.0)] + OLD["rows"][1:]
    (failure,) = tp.gate(doc(changed + NEW_2024, CERTIFIED), OLD)
    assert failure.startswith("append-only: 1 accepted polls changed, more than 0.5% of 3")


def test_append_only_gate_allows_a_bounded_correction():
    many = doc([row(i, poll="2022-10-20", election="2022-11-08") for i in range(1, 401)]
               + [row(500, kind="House-G", loc="US", poll="2022-11-01", election="2022-11-08")])
    corrected = [list(r) for r in many["rows"]]
    corrected[0][tp.ROW_COLUMNS.index("cand1_pct")] = "45"
    corrected[0][tp.ROW_COLUMNS.index("margin_poll")] = "-3"
    assert tp.gate(doc(corrected), many) == []


def test_window_gate_refuses_polls_older_than_the_accepted_reach():
    assert tp.window_days(OLD) == 21  # measured from the file, never typed
    (failure,) = tp.gate(doc(OLD["rows"] + NEW_2024 + [row(12, poll="2024-10-01")], CERTIFIED), OLD)
    assert failure == ("window: 1 polls end more than 21 days before their election, the accepted file's reach "
                       "(ids 12)")


def test_window_gate_refuses_a_poll_after_its_election():
    (failure,) = tp.gate(doc(OLD["rows"] + NEW_2024 + [row(12, poll="2024-11-06")], CERTIFIED), OLD)
    assert failure == "window: 1 polls end after their election (ids 12)"


def test_shares_gate():
    bad = [row(12, d=60.0, r=45.0), row(13, d=46.0, r=48.0, margin=4.0), row(14, d=101.0, r=0.0)]
    failures = tp.gate(doc(OLD["rows"] + NEW_2024 + bad, CERTIFIED), OLD)
    assert failures == ["shares: poll 12: the two shares add to 105", "shares: poll 13: margin_poll 4 is not 46 - 48",
                        "shares: poll 14: a share outside 0-100"]


def test_new_cycle_gate_needs_senate_and_generic_polls():
    (failure,) = tp.gate(doc(OLD["rows"] + NEW_2024[1:], CERTIFIED), OLD)
    assert failure == "new cycle: 2024 has no Senate general polls"
    (failure,) = tp.gate(doc(OLD["rows"] + NEW_2024[:1], CERTIFIED), OLD)
    assert failure == "new cycle: 2024 has no generic-ballot polls"


def test_new_cycle_is_a_general_election_day_only():
    # A row dated off the statutory general (a December runoff) is no cycle.
    runoff = row(12, poll="2024-11-20", election="2024-12-03")
    assert tp.cycle_of(dict(zip(tp.ROW_COLUMNS, runoff))) is None


def test_results_gate_against_medsl():
    (failure,) = tp.gate(doc(OLD["rows"] + NEW_2024, {"2024-PA": 3.0}), OLD)
    assert failure == "results: 2024-PA stated R+0.21, MEDSL certified R+3.00"
    # A race MEDSL doesn't list as Democrat-versus-Republican is never fitted.
    assert tp.gate(doc(OLD["rows"] + NEW_2024, {}), OLD) == []


def test_the_checked_in_copy_passes_its_own_gate():
    bundled = json.loads((DATA / tp.FILE_NAME).read_text())
    assert tp.gate(bundled, bundled) == []
    assert bundled["columns"] == list(tp.EXPECTED_COLUMNS)
    assert all(t in bundled["terms"] for t in tp.TERMS)
    assert bundled["_attribution"] == "Silver Bulletin"


# ── the page ────────────────────────────────────────────────────────

def test_the_page_gives_its_terms_and_its_raw_polls_attachment():
    assert tp.terms_sentence(PAGE) == TERMS
    assert tp.attachment(PAGE) == ("Rawpolls 011226", "https://www.natesilver.net/api/v1/file/bbbb-2222.xlsx")
    # Substack's escaped preload copy reads the same way.
    escaped = PAGE.replace("<", "\\u003c").replace('"', '\\"')
    assert tp.attachment(escaped)[0] == "Rawpolls 011226"


def test_a_page_without_the_attachment_is_an_outage_not_no_polls(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("fetch_sb", BACKEND / "scripts" / "fetch_silver_bulletin_polls.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "get", lambda url: PAGE.replace("Rawpolls", "Archive").encode())
    out = pathlib.Path(dr.runtime_data_path("sb_candidate.json"))
    assert script.main([str(out), "--cache", str(out.parent)]) == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "outage:" in err and "no longer links a 'Rawpolls' attachment" in err

    def down(url):
        raise tp.FetchFailed(f"{url} could not be read (timed out)")

    monkeypatch.setattr(script, "get", down)
    assert script.main([str(out), "--cache", str(out.parent)]) == 2
    assert f"outage: {tp.PAGE_URL} could not be read (timed out)" in capsys.readouterr().err


# ── the window, derived ─────────────────────────────────────────────

def _race(year, polls, margin=-2.0, lean=0.0):
    return {"year": year, "state": "PA", "special": False, "margin": margin, "lean": lean, "inc": 0, "polls": polls}


def test_the_poll_window_is_the_training_polls_reach():
    rows = [_race(2020, [["A", 3, 600, -1.0, ""], ["B", 17, 600, 1.0, ""]])]
    generic = {"2020": [["G", 9, 1000, -2.0, ""]]}
    assert model.poll_window(rows, generic) == 17
    generic["2020"].append(["G", 30, 1000, -2.0, ""])
    assert model.poll_window(rows, generic) == 30
    with pytest.raises(ValueError):
        model.poll_window([_race(2020, [])], {})


def test_each_fit_records_the_window_it_used():
    training = json.loads((DATA / "forecast_training.json").read_text())
    fitted = json.loads((DATA / "forecast_model.json").read_text())
    reach = model.poll_window(training["races"], training["generic"])
    assert {f["windowDays"] for f in fitted["fits"].values()} == {reach}
    polls = json.loads((DATA / tp.FILE_NAME).read_text())
    assert reach == tp.window_days(polls)


def test_a_poll_outside_the_window_is_not_averaged():
    polls = [model.Poll("A", 5, 600, -1.0), model.Poll("B", 30, 600, 9.0)]
    assert [p.pollster for p in model.eligible(polls, 0, 21)] == ["A"]
    assert [p.pollster for p in model.eligible(polls, 10, 21)] == ["B"]


# ── no look-ahead ───────────────────────────────────────────────────

def _cycles(years):
    rows, generic = [], {}
    for i, y in enumerate(years):
        for s in range(8):
            lean = (s - 4) * 3.0
            rows.append(dict(_race(y, [["A", 2, 800, lean + i * 0.3, ""], ["B", 10, 800, lean - 1, ""]],
                                   margin=lean + (s % 3) - 1, lean=lean), state=f"S{s}"))
        generic[str(y)] = [["G", 3, 1500, i - 2.0, ""], ["H", 12, 1500, i - 1.0, ""]]
    return rows, generic


def test_the_forward_check_fits_on_earlier_cycles_only():
    rows, generic = _cycles([2016, 2018, 2020, 2022])
    seen = []
    real_fit = model.fit

    def spy(train, gen, horizon, coef=None):
        seen.append(({r["year"] for r in train}, {int(y) for y in gen}))
        return real_fit(train, gen, horizon, coef)

    with patch.object(model, "fit", spy):
        fw = model.forward(rows, generic, 0)
    assert fw["cycle"] == 2022 and fw["trainedOn"] == [2016, 2020] and fw["races"] == 8
    (years, gen_years), = seen
    assert years == {2016, 2018, 2020} and gen_years == {2016, 2018, 2020}
    # A later cycle's polls never reach it either: adding one changes nothing.
    later = dict(generic, **{"2024": [["G", 3, 1500, 9.0, ""]]})
    assert model.forward(rows, later, 0) == fw


def test_leave_one_cycle_out_never_fits_on_the_cycle_it_predicts():
    rows, generic = _cycles([2016, 2018, 2020])
    with pytest.raises(AssertionError, match="must not see"):
        model.predict_cycle(rows, rows, generic, 2018, 0)


def test_the_coverage_band_is_the_binomial_one():
    lo, hi = model.coverage_band(32, 0.8)
    assert lo < 0.8 < hi and (lo, hi) == (0.656, 0.938)
    assert model.coverage_band(400, 0.8)[1] - model.coverage_band(400, 0.8)[0] < hi - lo


# ── the refresh: polls, then the refit ──────────────────────────────

def _spec(name):
    return next(s for s in dr.DATA_FILES if s.name == name)


def test_the_polls_and_the_refit_are_on_record_in_that_order():
    names = [s.name for s in dr.DATA_FILES]
    assert names.index(tp.FILE_NAME) < names.index("forecast_model.json")
    fit = _spec("forecast_model.json")
    assert fit.outputs == ("forecast_training.json", "forecast_house_training.json", "forecast_house_model.json")
    argv = fit.command(dr.Context("forecast_model.json", {}, None, None))
    for name in fit.outputs:
        assert "{out:%s}" % name in argv
    assert _spec(tp.FILE_NAME).gate is tp.gate


def test_the_refit_is_due_when_the_polls_in_use_change(monkeypatch):
    monkeypatch.setattr(dr, "read", lambda name: {"file": {"sha256": "new"}})
    due = _spec("forecast_model.json").due
    assert due(dr.Context("forecast_model.json", {"_polls": {"sha256": "old"}}, None, None))
    recent = dr.utcnow().replace(microsecond=0)
    assert not due(dr.Context("forecast_model.json", {"_polls": {"sha256": "new"}}, None, recent))
    assert due(dr.Context("forecast_model.json", {"_polls": {"sha256": "new"}}, None, datetime(2000, 1, 1)))


def test_outputs_are_installed_together_only_when_the_gate_passes(alerts_fixture):
    writer = [sys.executable, "-c",
              "import json,sys; open(sys.argv[1],'w').write(json.dumps({'fits': {'0': 1}, 'evaluation': {'0': {}}}));"
              "open(sys.argv[2],'w').write(json.dumps({'races': [1]}))", "{out}", "{out:t_side.json}"]
    spec = dr.DataFile("t_main.json", command=lambda ctx: writer, outputs=("t_side.json",),
                       gate=lambda new, old: ["held"] if old.get("hold") else [])
    pathlib.Path(dr.runtime_data_path("t_main.json")).write_text(json.dumps({"hold": True}))
    assert dr.refresh_one(spec, dr.Context("t_main.json", {"hold": True}, None, None)).startswith("kept: held")
    assert dr.read("t_side.json") == {}
    assert dr.refresh_one(spec, dr.Context("t_main.json", {}, None, None)) == "refreshed"
    assert dr.read("t_side.json")["races"] == [1] and dr.read("t_main.json")["fits"] == {"0": 1}


def _forward_docs(*checks):
    """{model file: doc} with forward checks (chamber, horizon, held, n, cycle)."""
    docs = {"forecast_model.json": {"evaluation": {}}, "forecast_house_model.json": {"evaluation": {}}}
    for chamber, h, held, n, cycle in checks:
        name = "forecast_model.json" if chamber == "Senate" else "forecast_house_model.json"
        docs[name]["evaluation"][str(h)] = {"forward": {
            "cycle": cycle, "trainedOn": [1998, cycle - 2], "races": n, "coverage80": held / n,
            "coverage80Band": model.coverage_band(n, 0.8), "coverage80Test": model.coverage_test(held, n)}}
    return docs


SENATE_2024 = [("Senate", 0, 30, 32, 2024), ("Senate", 7, 31, 32, 2024), ("Senate", 14, 31, 32, 2024)]
HOUSE_2022 = [("House", 0, 332, 386, 2022), ("House", 7, 336, 386, 2022), ("House", 14, 333, 386, 2022)]


def test_one_race_of_32_too_wide_does_not_alert():
    # 31 of 32 is one race past the 95% band's edge (30): not actionable.
    assert model.coverage_band(32, 0.8)[1] == 0.938
    assert dr._forward_finding(_forward_docs(*SENATE_2024, *HOUSE_2022)) is None
    assert dr._forward_finding(_forward_docs(("Senate", 0, 31, 32, 2024))) is None


def test_the_checked_in_fits_raise_no_forward_alert():
    docs = {name: json.loads((DATA / name).read_text()) for name in ("forecast_model.json", "forecast_house_model.json")}
    assert dr._forward_finding(docs) is None


def test_ranges_too_narrow_alert_after_the_correction():
    finding = dr._forward_finding(_forward_docs(*SENATE_2024[:2], ("Senate", 14, 18, 32, 2024), *HOUSE_2022))
    assert finding == (
        "the forward check (each chamber's newest cycle, fitted on earlier cycles only) fails at familywise 5% "
        "(Holm, 12 hypotheses): Senate 2024, 14 days out (fitted on 1998-2022): too narrow: 80% ranges held 18 of "
        "32 results (56%), p=0.002 against 80%. The refit is published and the map shows these figures.")


def test_ranges_far_too_wide_alert_naming_the_knob():
    finding = dr._forward_finding(_forward_docs(("House", 0, 384, 386, 2022)))
    assert "House 2022, 0 days out" in finding and "far too wide: 80% ranges held 384 of 386 (99%)" in finding
    assert "the knob is the spread's scale factor (model.fit_scale, `scale` in the fit)" in finding


def test_a_borderline_miss_survives_one_test_but_not_the_family():
    # 20 of 32 (p=0.017) is flagged as the only check (two hypotheses); among
    # the twelve a full refit tests it is not.
    assert 0.05 / 12 < model.coverage_test(20, 32)["under"] < 0.05 / 2
    assert dr._forward_finding(_forward_docs(("Senate", 0, 20, 32, 2024))) is not None
    assert dr._forward_finding(_forward_docs(("Senate", 0, 20, 32, 2024), *SENATE_2024[1:], *HOUSE_2022)) is None


def test_holm_steps_down():
    assert model.holm({"a": 0.01, "b": 0.02, "c": 0.04}, 0.05) == {"a", "b", "c"}
    assert model.holm({"a": 0.01, "b": 0.03, "c": 0.04}, 0.05) == {"a"}
    assert model.holm({"a": 0.02, "b": 0.03}, 0.05) == {"a", "b"}
    assert model.holm({"a": 0.03, "b": 0.04}, 0.05) == set()


def test_the_forward_check_alert_is_raised_after_install_and_publishes(alerts_fixture):
    model_doc = _forward_docs(("Senate", 0, 18, 32, 2024))["forecast_model.json"]
    model_doc["fits"] = {"0": 1}
    writer = [sys.executable, "-c", f"import json,sys; open(sys.argv[1],'w').write(json.dumps({model_doc!r}))",
              "{out}"]
    spec = dr.DataFile("forecast_model.json", command=lambda ctx: writer, check=dr._forward_finding)
    assert dr.refresh_one(spec, dr.Context("forecast_model.json", {}, None, None)) == "refreshed"
    assert dr.read("forecast_model.json")["evaluation"]["0"]["forward"]["coverage80Test"]["held"] == 18
    assert alerts_fixture.send.call_args.kwargs["condition"] == "data-check-forecast-model"
    assert "too narrow" in alerts_fixture.send.call_args.args[1]


def test_the_refit_gate_never_drops_a_cycle():
    old = {"fits": {"0": {}}, "evaluation": {"0": {"cycles": [2020, 2022]}}}
    assert dr._forecast_gate({"fits": {"0": {}}, "evaluation": {"0": {"cycles": [2020, 2022, 2024]}}}, old) == []
    assert dr._forecast_gate({"fits": {"0": {}}, "evaluation": {"0": {"cycles": [2022]}}}, old) == [
        "the refit dropped cycles [2020]"]
    assert dr._forecast_gate({"fits": {}, "evaluation": {}}, old)[0].startswith("horizons fitted []")


@pytest.fixture
def alerts_fixture():
    with patch.object(dr, "send_ops_alert") as send, patch.object(dr, "resolve_ops_alert") as resolve:
        yield SimpleNamespace(send=send, resolve=resolve)
