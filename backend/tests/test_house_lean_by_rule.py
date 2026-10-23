"""The House fit's pre-election leans reach each new election by rule
(scripts/fit_forecast_model.house_leans): an election after the history
file's last takes the computed lean's table for the lines it seats, when
that table's window is the two presidential elections before it."""

import importlib.util
import json
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fit_forecast_model", _BACKEND / "scripts" / "fit_forecast_model.py")
fit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fit)


def test_the_window_before_each_election():
    assert fit.last_two_presidential(2024) == (2016, 2020)
    assert fit.last_two_presidential(2026) == fit.last_two_presidential(2028) == (2020, 2024)
    assert fit.last_two_presidential(2030) == (2024, 2028)


def test_2026_takes_the_120th_table_and_nothing_older_moves():
    history = json.loads((_BACKEND / "app" / "data" / "forecast_house_lean.json").read_text())
    computed = json.loads((_BACKEND / "app" / "data" / "district_lean.json").read_text())
    leans = fit.house_leans()
    assert {y: leans["leans"][y] for y in history["leans"]} == history["leans"]
    assert leans["leans"]["2026"] == computed["congresses"]["120"]["districts"]
    assert len(fit._lines(leans, 2026)) == 435 and fit._lines(leans, 2024) == history["lines"]["119"]
    # The 119th's table is 2024's election, which the history file already holds.
    assert "2024" in history["leans"] and leans["_method"]["2024"] == history["_method"]["2024"]
