"""Tests for race_relevance — the semantic about-ness gate.

The embedding model itself is not loaded here: these cover the parts
that decide behaviour (the derived threshold, the descriptor, the
fail-closed paths). Separation quality was measured against the live
corpus and is recorded in the module docstring.
"""

import json

import pytest

from app.models import Candidate, Race
from app.pipeline.analyze import race_relevance as rr


@pytest.fixture(autouse=True)
def _clear_cache():
    rr.reset_cache()
    yield
    rr.reset_cache()


class TestOtsuThreshold:
    """Otsu 1979: the cut maximising between-class variance. No parameter,
    no labels — the corpus decides, which is the whole reason it is used
    here instead of a number someone typed."""

    def test_separates_two_clusters_completely(self):
        """The property that matters is not where the cut lands but that
        it puts every low value on one side and every high value on the
        other — that is what "maximises between-class variance" buys."""
        low = [0.05 + i * 0.0001 for i in range(400)]
        high = [0.60 + i * 0.0001 for i in range(400)]
        cut = rr.otsu_threshold(low + high)
        assert max(low) < cut <= min(high)

    def test_refuses_a_sample_too_small_to_threshold(self):
        assert rr.otsu_threshold([0.1, 0.9]) is None


class TestRaceDescriptor:
    def test_a_senate_race_reads_as_one(self, db_session):
        race = Race(id="2026-SEN-CT", cycle_year=2026, office="S", state="CT")
        db_session.add(race)
        db_session.add(Candidate(id="S1", race_id="2026-SEN-CT", name="MURPHY, CHRIS", party="DEM"))
        db_session.commit()
        text = rr.race_descriptor(race)
        assert "CT" in text and "Senate" in text and "MURPHY, CHRIS" in text

    def test_an_at_large_house_seat_is_labelled(self, db_session):
        race = Race(id="2026-HOUSE-AK-0", cycle_year=2026, office="H", state="AK", district=0)
        db_session.add(race)
        db_session.commit()
        assert "at-large" in rr.race_descriptor(race)


class TestThreshold:
    def test_falls_back_to_the_measured_bootstrap(self, db_session):
        assert rr.threshold(db_session) == rr.BOOTSTRAP_THRESHOLD

    def test_prefers_a_stored_calibration(self, db_session):
        from app.pipeline.cache import api_cache_set
        api_cache_set(db_session, rr._CACHE_NAMESPACE, rr._CACHE_KEY,
                      json.dumps({"threshold": 0.42, "sample_size": 900}))
        db_session.commit()
        assert rr.threshold(db_session) == 0.42

    def test_unreadable_calibration_does_not_crash_the_gate(self, db_session):
        from app.pipeline.cache import api_cache_set
        api_cache_set(db_session, rr._CACHE_NAMESPACE, rr._CACHE_KEY, "not json")
        db_session.commit()
        assert rr.threshold(db_session) == rr.BOOTSTRAP_THRESHOLD


class TestCalibrateAndStore:
    def test_a_corpus_too_small_keeps_the_previous_threshold(self, db_session):
        """Stale gates better than nothing — same failure mode as
        explore_ranking.calibrate_and_store."""
        assert rr.calibrate_and_store(db_session) is None
        assert rr.threshold(db_session) == rr.BOOTSTRAP_THRESHOLD


class TestIsRelevant:
    def test_an_empty_item_is_never_relevant(self, db_session, monkeypatch):
        """Fail closed: no text means no basis to judge, so no post."""
        race = Race(id="2026-SEN-CT", cycle_year=2026, office="S", state="CT")
        db_session.add(race)
        db_session.commit()

        class _Empty:
            title = None
            summary = None

        called = []
        monkeypatch.setattr(rr, "score_pairs", lambda *a: called.append(1) or [1.0])
        assert rr.is_relevant(_Empty(), race, db_session) is False
        assert not called  # short-circuits before loading any model
