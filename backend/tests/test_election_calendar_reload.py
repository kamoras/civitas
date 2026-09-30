"""election_calendar.senate_classes notices the Election run's rewrite of
the refreshed file from another process (AGENTS.md: a module cache of a
file the pipeline rewrites keeps a file_cache stamp of it)."""

import json

from app import election_calendar


def test_a_rewrite_by_another_process_is_seen(tmp_path, monkeypatch):
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": [], "3": []}}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[1] == frozenset({"AA"})
    # Written by the pipeline process, which resets only its own cache.
    live.write_text(json.dumps({"classes": {"1": ["BB"], "2": [], "3": []}}))
    assert election_calendar.senate_classes()[1] == frozenset({"BB"})
    election_calendar.reset_senate_classes()


def test_a_malformed_refreshed_file_falls_back_to_the_bundled_one(tmp_path, monkeypatch):
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": [], "3": []}}))
    live.write_text(json.dumps({"classes": []}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[1] == frozenset({"AA"})
    election_calendar.reset_senate_classes()
