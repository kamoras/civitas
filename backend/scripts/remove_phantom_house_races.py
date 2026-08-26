"""Remove House Race rows created before the district-apportionment
validation in election_pipeline._sync_roster existed (see
fix/validate-house-district-apportionment).

2026-08-26 audit: 461 Race rows for 435 real seats. Four carried a
district number that doesn't exist for that state (FL-59, GA-23, IL-51,
NY-28), and some states carried a spurious null-district row — all
populated with garbage-looking FEC filings (empty candidate names, party
"UNK"). _sync_roster now rejects these going forward; this is the
one-time cleanup for rows already created.

Uses the same district_pvi.json real-435-seat map _sync_roster's fix
checks against, so this stays correct if that map is ever regenerated —
no hardcoded state/district list here.

Run from the repo:
    python3 backend/scripts/remove_phantom_house_races.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Candidate, Race, RaceCoverageItem, ScoreSnapshot  # noqa: E402
from app.pipeline.analyze.score_calculator import get_district_pvi_map  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        real_districts = set(get_district_pvi_map())
        house_races = db.query(Race).filter(Race.office == "H").all()
        phantom = [r for r in house_races if f"{r.state}-{r.district}" not in real_districts]

        if not phantom:
            print(f"Checked {len(house_races)} House races — no phantom districts found.")
            return

        for race in phantom:
            candidate_ids = [
                c.id for c in db.query(Candidate.id).filter(Candidate.race_id == race.id).all()
            ]
            n_snapshots = 0
            if candidate_ids:
                n_snapshots = (
                    db.query(ScoreSnapshot)
                    .filter(ScoreSnapshot.entity_type == "candidate", ScoreSnapshot.entity_id.in_(candidate_ids))
                    .delete(synchronize_session=False)
                )
            n_candidates = (
                db.query(Candidate).filter(Candidate.race_id == race.id).delete(synchronize_session=False)
            )
            n_coverage = (
                db.query(RaceCoverageItem).filter(RaceCoverageItem.race_id == race.id).delete(synchronize_session=False)
            )
            print(
                f"Removing {race.id} (state={race.state}, district={race.district}): "
                f"{n_candidates} candidate(s), {n_coverage} coverage item(s), {n_snapshots} snapshot(s)"
            )
            db.delete(race)

        db.commit()
        print(f"Removed {len(phantom)} phantom House race(s) out of {len(house_races)} checked.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
