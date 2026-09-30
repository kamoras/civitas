"""election_calendar.senate_classes notices the Election run's rewrite of
the refreshed file from another process (AGENTS.md: a module cache of a
file the pipeline rewrites keeps a file_cache stamp of it)."""

import json

import pytest

from app import election_calendar

_BUNDLED = {"1": ["AA"], "2": ["BB"], "3": ["CC"]}


@pytest.fixture
def class_files(tmp_path, monkeypatch):
    """(live, bundled) class files in place of the real ones; the bundled
    one holds _BUNDLED. The module cache is reset on the way in and out."""
    live, bundled = tmp_path / "live.json", tmp_path / "bundled.json"
    bundled.write_text(json.dumps({"classes": _BUNDLED}))
    monkeypatch.setattr(election_calendar, "_CLASS_FILES", (live, bundled))
    election_calendar.reset_senate_classes()
    yield live, bundled
    election_calendar.reset_senate_classes()


def test_a_rewrite_by_another_process_is_seen(class_files):
    live, _ = class_files
    assert election_calendar.senate_classes()[1] == frozenset({"AA"})
    # Written by the pipeline process, which resets only its own cache.
    live.write_text(json.dumps({"classes": {"1": ["ZZ"], "2": ["BB"], "3": ["CC"]}}))
    assert election_calendar.senate_classes()[1] == frozenset({"ZZ"})


@pytest.mark.parametrize("classes", [
    pytest.param([], id="classes_not_a_mapping"),
    # frozenset("CA,NY") would be its characters, not two states.
    pytest.param({"1": "CA,NY", "2": ["BB"], "3": ["CC"]}, id="a_string_where_a_list_of_states_belongs"),
    pytest.param({}, id="no_classes"),
    pytest.param({"1": ["CA"]}, id="missing_two_classes"),
    pytest.param({"1": ["CA"], "2": ["NY"], "3": []}, id="an_empty_class"),
])
def test_a_malformed_refreshed_file_falls_back_to_the_bundled_one(class_files, classes):
    live, _ = class_files
    live.write_text(json.dumps({"classes": classes}))
    assert election_calendar.senate_classes() == {int(k): frozenset(v) for k, v in _BUNDLED.items()}


def test_a_refresh_merges_with_the_classes_as_the_site_reads_them(class_files):
    # A malformed stored file (a string where a list belongs) is not merged
    # as its characters: the bundled copy is what the site shows then.
    from app.pipeline.fetch import senate_classes

    live, _ = class_files
    live.write_text(json.dumps({"classes": {"1": "AL", "2": ["BB"], "3": ["CC"]}}))
    assert senate_classes._stored(live) == {1: {"AA"}, 2: {"BB"}, 3: {"CC"}}


def test_a_refresh_that_cannot_read_the_stored_file_writes_nothing(class_files):
    # Merged with nothing, it would drop every state kept only while its
    # seat is vacant.
    from app.pipeline.fetch import senate_classes

    live, _ = class_files
    live.mkdir()  # there, and unreadable as a file
    with pytest.raises(OSError):
        senate_classes._stored(live)
