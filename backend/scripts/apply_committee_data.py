"""Apply committee_membership.json / leadership_roles.json to existing rows.

fetch_committee_data.py regenerates the two JSON caches under app/data/,
but Senator/Representative rows only pick up new values through
normalize_members.py during a full pipeline run (Senate ~6h, House
~1-2h). This script applies the current JSON caches directly to already-
stored rows by bioguide_id, so a committee/leadership refresh doesn't
have to wait on the next nightly run. Safe to re-run any time after
fetch_committee_data.py — idempotent, touches only the leadership_title
and committees columns.

Run from the repo:
    python3 backend/scripts/apply_committee_data.py
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Representative, Senator  # noqa: E402
from app.pipeline.transform.committee_data import (  # noqa: E402
    load_committee_membership,
    load_leadership_roles,
)


def main() -> None:
    committees = load_committee_membership()
    leadership = load_leadership_roles()
    if not committees and not leadership:
        print("No committee/leadership data loaded — run fetch_committee_data.py first.")
        sys.exit(1)

    db = SessionLocal()
    try:
        updated = 0
        for model in (Senator, Representative):
            for member in db.query(model).all():
                bioguide_id = member.bioguide_id or ""
                new_title = leadership.get(bioguide_id)
                new_committees = json.dumps(committees.get(bioguide_id, []))
                if member.leadership_title != new_title or member.committees != new_committees:
                    member.leadership_title = new_title
                    member.committees = new_committees
                    updated += 1
        db.commit()
        print(f"Updated {updated} member rows with committee/leadership data.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
