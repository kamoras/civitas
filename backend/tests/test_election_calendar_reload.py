"""election_calendar.senate_classes notices the Election run's rewrite of
the refreshed file from another process (AGENTS.md: a module cache of a
file the pipeline rewrites keeps a file_cache stamp of it)."""

import json

import pytest

from app import election_calendar


def test_a_rewrite_by_another_process_is_seen(tmp_path, monkeypatch):
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": ["XX"], "3": ["YY"]}}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[1] == frozenset({"AA"})
    # Written by the pipeline process, which resets only its own cache.
    live.write_text(json.dumps({"classes": {"1": ["BB"], "2": ["XX"], "3": ["YY"]}}))
    assert election_calendar.senate_classes()[1] == frozenset({"BB"})
    election_calendar.reset_senate_classes()


def test_a_malformed_refreshed_file_falls_back_to_the_bundled_one(tmp_path, monkeypatch):
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": ["XX"], "3": ["YY"]}}))
    live.write_text(json.dumps({"classes": []}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[1] == frozenset({"AA"})
    election_calendar.reset_senate_classes()


def test_a_string_where_a_list_of_states_belongs_falls_back(tmp_path, monkeypatch):
    # frozenset("CA,NY") would be its characters, not two states.
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["CA"], "2": ["XX"], "3": ["YY"]}}))
    live.write_text(json.dumps({"classes": {"1": "CA,NY", "2": ["XX"], "3": ["YY"]}}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[1] == frozenset({"CA"})
    election_calendar.reset_senate_classes()


@pytest.mark.parametrize("classes", [{}, {"1": ["CA"]}, {"1": ["CA"], "2": ["NY"], "3": []}])
def test_a_file_missing_a_class_falls_back(tmp_path, monkeypatch, classes):
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": ["BB"], "3": ["CC"]}}))
    live.write_text(json.dumps({"classes": classes}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    assert election_calendar.senate_classes()[3] == frozenset({"CC"})
    election_calendar.reset_senate_classes()


def test_a_refresh_merges_with_the_classes_as_the_site_reads_them(tmp_path, monkeypatch):
    # A malformed stored file (a string where a list belongs) is not merged
    # as its characters: the bundled copy is what the site shows then.
    from app.pipeline.fetch import senate_classes

    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": ["BB"], "3": ["CC"]}}))
    live.write_text(json.dumps({"classes": {"1": "AL", "2": ["BB"], "3": ["CC"]}}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    assert senate_classes._stored(live) == {1: {"AA"}, 2: {"BB"}, 3: {"CC"}}


def test_a_refresh_that_cannot_read_the_stored_file_writes_nothing(tmp_path, monkeypatch):
    # Merged with nothing, it would drop every state kept only while its
    # seat is vacant.
    from app.pipeline.fetch import senate_classes

    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": {"1": ["AA"], "2": ["BB"], "3": ["CC"]}}))
    live.mkdir()  # there, and unreadable as a file
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    with pytest.raises(OSError):
        senate_classes._stored(live)
