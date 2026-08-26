"""Tests for scripts/remove_phantom_house_races.py — the one-time
cleanup for House Race rows created before election_pipeline._sync_roster
validated district numbers against real apportionment."""

from app.models import Candidate, Race, ScoreSnapshot


class TestRemovePhantomHouseRaces:
    def test_a_phantom_district_is_removed_with_its_candidates(self, db_session):
        from scripts import remove_phantom_house_races as script

        db_session.add(Race(id="2026-HOUSE-GA-23", cycle_year=2026, office="H", state="GA", district=23))
        db_session.add(Candidate(id="bad1", race_id="2026-HOUSE-GA-23", name="", party="UNK"))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        script.main()

        assert db_session.query(Race).filter(Race.id == "2026-HOUSE-GA-23").first() is None
        assert db_session.query(Candidate).filter(Candidate.id == "bad1").first() is None

    def test_a_real_district_is_left_alone(self, db_session):
        from scripts import remove_phantom_house_races as script

        db_session.add(Race(id="2026-HOUSE-CA-12", cycle_year=2026, office="H", state="CA", district=12))
        db_session.add(Candidate(id="good1", race_id="2026-HOUSE-CA-12", name="Real Candidate", party="DEM"))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        script.main()

        assert db_session.query(Race).filter(Race.id == "2026-HOUSE-CA-12").first() is not None
        assert db_session.query(Candidate).filter(Candidate.id == "good1").first() is not None

    def test_senate_races_are_never_touched(self, db_session):
        # Senate races have no district at all — must not be mistaken
        # for a phantom House district.
        from scripts import remove_phantom_house_races as script

        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA", district=None))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        script.main()

        assert db_session.query(Race).filter(Race.id == "2026-SEN-GA").first() is not None

    def test_associated_score_snapshots_are_also_removed(self, db_session):
        from scripts import remove_phantom_house_races as script

        db_session.add(Race(id="2026-HOUSE-NY-28", cycle_year=2026, office="H", state="NY", district=28))
        db_session.add(Candidate(id="bad2", race_id="2026-HOUSE-NY-28", name="", party="UNK"))
        db_session.add(ScoreSnapshot(
            entity_type="candidate", entity_id="bad2", date="2026-08-01", overall_score=0.0,
        ))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        script.main()

        assert db_session.query(ScoreSnapshot).filter(ScoreSnapshot.entity_id == "bad2").first() is None
