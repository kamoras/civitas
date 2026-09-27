"""Relabel a chamber's stored partisan-depth profiles against the whole chamber.

party_platform.finalize_partisan_depth needs every member of a chamber at
once (the ideology prior is fitted over the chamber; depth is a tercile
within the member's own party). Reading the profiles back from the database
rather than from one run's results means a filtered run is still compared
with everyone. Shared by the Senate and House pipelines — the House used to
compute no partisan depth at all.
"""

import json
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def finalize_stored_partisan_depth(db: Session, model) -> None:
    """`model` is Senator or Representative. Never aborts the run: the
    per-member provisional labels stay if this fails."""
    from app.pipeline.analyze.party_platform import finalize_partisan_depth

    try:
        rows = (
            db.query(model)
            .filter(model.is_current.is_(True), model.partisan_depth.isnot(None))
            .all()
        )
        profiles = []
        for row in rows:
            profile = json.loads(row.partisan_depth)
            profile.setdefault("evalParty", row.party)
            profiles.append((row, profile))
        finalize_partisan_depth([p for _, p in profiles])
        for row, profile in profiles:
            row.partisan_depth = json.dumps(profile)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Partisan-depth finalization failed (%s) — provisional labels kept",
            model.__tablename__, exc_info=True,
        )
