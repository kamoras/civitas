"""The admin network counters cover every backend container: a container
sees only its own interfaces, so the API records its totals for the
pipeline process (which serves the admin API) to add."""

import json
import time

from app import net_stats


def _at(monkeypatch, tmp_path, own=(100, 10)):
    monkeypatch.setattr(net_stats, "_api_path", lambda: str(tmp_path / "api_network.json"))
    monkeypatch.setattr(net_stats, "own_totals", lambda: own)


def test_the_api_containers_totals_are_added(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, own=(1000, 100))
    net_stats.record_api_totals()  # records own_totals: stands in for the API container
    _at(monkeypatch, tmp_path, own=(5, 7))
    assert net_stats.backend_totals() == {"rx": 1005, "tx": 107, "includesApi": True}


def test_a_stale_record_is_left_out(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, own=(5, 7))
    (tmp_path / "api_network.json").write_text(json.dumps({"rx": 1, "tx": 1, "at": time.time() - 3600}))
    assert net_stats.backend_totals() == {"rx": 5, "tx": 7, "includesApi": False}


def test_no_record_is_this_container_alone(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, own=(5, 7))
    assert net_stats.backend_totals() == {"rx": 5, "tx": 7, "includesApi": False}


def test_own_totals_reads_proc_net_dev():
    rx, tx = net_stats.own_totals()
    assert rx >= 0 and tx >= 0
