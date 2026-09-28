"""The admin network figures cover every backend container: a container
sees only its own interfaces, so the API records its rate for the pipeline
process (which serves the admin API) to report beside its own counters."""

import os
import time

from app import net_stats
from app.shared_state import write_record


def _at(monkeypatch, tmp_path):
    monkeypatch.setattr(net_stats, "_record_path", lambda: str(tmp_path / "api_network.json"))
    monkeypatch.setattr(net_stats, "_previous", None)


def test_the_api_records_its_rate_over_its_interval(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path)
    totals = iter([(1000, 100), (4000, 700)])
    clock = iter([10.0, 13.0])
    monkeypatch.setattr(net_stats, "own_totals", lambda: next(totals))
    monkeypatch.setattr(net_stats.time, "monotonic", lambda: next(clock))
    assert not net_stats.record_api_rate()  # a first sample: no interval yet
    assert net_stats.record_api_rate()
    assert net_stats.api_rates() == {"rxRate": 1000.0, "txRate": 200.0}


def test_a_counter_that_went_backwards_records_nothing(tmp_path, monkeypatch):
    # A restarted container: its counters start again from zero.
    _at(monkeypatch, tmp_path)
    totals = iter([(1000, 100), (10, 1)])
    clock = iter([10.0, 13.0])
    monkeypatch.setattr(net_stats, "own_totals", lambda: next(totals))
    monkeypatch.setattr(net_stats.time, "monotonic", lambda: next(clock))
    net_stats.record_api_rate()
    assert not net_stats.record_api_rate()
    assert net_stats.api_rates() is None


def test_a_stale_record_is_no_rate(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path)
    path = tmp_path / "api_network.json"
    write_record(str(path), {"rxRate": 5.0, "txRate": 1.0})
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert net_stats.api_rates() is None


def test_own_totals_reads_proc_net_dev():
    rx, tx = net_stats.own_totals()
    assert rx >= 0 and tx >= 0


def test_an_unreadable_sample_is_skipped_not_zeroed(tmp_path, monkeypatch):
    # Zeros would make the next good read a lifetime of bytes in a minute.
    _at(monkeypatch, tmp_path)
    totals = iter([(1000, 100), None, (4000, 700)])
    clock = iter([10.0, 13.0])
    monkeypatch.setattr(net_stats, "own_totals", lambda: next(totals))
    monkeypatch.setattr(net_stats.time, "monotonic", lambda: next(clock))
    net_stats.record_api_rate()
    assert not net_stats.record_api_rate()  # unreadable: skipped
    assert net_stats.record_api_rate()
    assert net_stats.api_rates() == {"rxRate": 1000.0, "txRate": 200.0}
