"""The admin network figures cover every backend container: a container
sees only its own interfaces, so the API records its rate for the pipeline
process (which serves the admin API) to report beside its own counters."""

import os
import time

from app import net_stats
from app.shared_state import write_record


def _at(monkeypatch, tmp_path, host="c1"):
    monkeypatch.setattr("app.shared_state.record_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr("socket.gethostname", lambda: host)
    monkeypatch.setattr(net_stats, "_previous", None)
    monkeypatch.setattr(net_stats, "_forgotten", False)


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
    path = tmp_path / "api_network-c1.json"
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


def test_two_containers_overlapping_in_a_rollout_are_summed(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path)
    write_record(str(tmp_path / "api_network-old.json"), {"rxRate": 100.0, "txRate": 10.0})
    write_record(str(tmp_path / "api_network-new.json"), {"rxRate": 50.0, "txRate": 5.0})
    assert net_stats.api_rates() == {"rxRate": 150.0, "txRate": 15.0}


def test_a_long_gone_containers_record_is_deleted(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path)
    old = tmp_path / "api_network-replaced.json"
    write_record(str(old), {"rxRate": 1.0, "txRate": 1.0})
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    assert net_stats.api_rates() is None and not old.exists()


def test_a_stopping_container_forgets_its_record(tmp_path, monkeypatch):
    _at(monkeypatch, tmp_path, host="leaving")
    write_record(str(tmp_path / "api_network-leaving.json"), {"rxRate": 9.0, "txRate": 9.0})
    net_stats.forget_own_record()
    assert net_stats.api_rates() is None


def test_a_recording_in_flight_as_the_container_stops_doesnt_write_it_back(tmp_path, monkeypatch):
    # The recorder's thread keeps running after its loop is cancelled.
    _at(monkeypatch, tmp_path, host="leaving")
    totals = iter([(1000, 100), (4000, 700)])
    clock = iter([10.0, 13.0])
    monkeypatch.setattr(net_stats, "own_totals", lambda: next(totals))
    monkeypatch.setattr(net_stats.time, "monotonic", lambda: next(clock))
    net_stats.record_api_rate()
    net_stats.forget_own_record()
    assert not net_stats.record_api_rate()
    assert net_stats.api_rates() is None
