"""Input fingerprinting for incremental member analysis.

The pipeline re-derives all 535 members from scratch every night. Most of
them have not changed: no new votes, no new FEC filings, no platform
edits. Re-running LLM synthesis, lobbying matching, and per-bill policy
classification over byte-identical inputs produces byte-identical output
at full cost, and that cost is now measured in hours.

This module hashes the inputs to one member's analysis so a run can
recognise that case and skip it.

Two properties make the skip safe:

1. **A skipped member keeps its stored scorecard**, and both snapshot
   recorders iterate every row in the table rather than only the members
   the run touched. So a skip does not punch a hole in the score trends —
   the member still gets today's point from its existing scores.

2. **The fingerprint folds in the analysis code hash** the pipeline
   already computes to decide whether to clear the LLM and learned-
   classification caches. Any edit to analyze/, transform/, assemble/,
   scoring, or config_definitions changes that hash and therefore
   invalidates every member's fingerprint at once. The failure mode worth
   fearing is a stale scorecard produced by a silently-changed algorithm,
   and reusing the existing signal closes it without a hand-maintained
   version constant that someone will eventually forget to bump.

The asymmetry to keep in mind when changing this: a fingerprint that is
too *sensitive* costs a wasted re-derivation, which is merely slow. A
fingerprint that is too *coarse* — one that misses an input which really
does affect the output — serves stale data, which for a transparency
project is the actual harm. When in doubt, include the field.
"""

import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.models import MemberAnalysisFingerprint
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Bump to force a full re-derivation independent of any code change —
# e.g. after fixing a bug in what this module feeds into the hash, where
# the analysis code itself is untouched but the old fingerprints are
# wrong. Changing it invalidates every member on the next run.
FINGERPRINT_SCHEMA_VERSION = 1


def compute_fingerprint(inputs: dict, code_hash: str) -> str:
    """Stable SHA-256 over one member's analysis inputs.

    `sort_keys` makes dict ordering irrelevant; list order is preserved
    and therefore significant. That is the conservative direction — a
    reordered donor list re-derives rather than being treated as
    unchanged.

    `default=str` keeps the hash from raising on datetimes and Decimals
    that reach it from the fetch layer. It is deliberately lossy for
    exotic objects, so callers should pass plain data.
    """
    blob = json.dumps(inputs, sort_keys=True, default=str, separators=(",", ":"))
    payload = f"{FINGERPRINT_SCHEMA_VERSION}|{code_hash}|{blob}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_fingerprints(db: Session, entity_type: str) -> dict[str, str]:
    """Every stored fingerprint for an entity type, in one query.

    Loaded up front rather than queried per member: 535 point lookups
    against SQLite while the pipeline is also writing is exactly the
    contention this change exists to reduce.
    """
    try:
        rows = (
            db.query(MemberAnalysisFingerprint)
            .filter(MemberAnalysisFingerprint.entity_type == entity_type)
            .all()
        )
        return {r.entity_id: r.fingerprint for r in rows}
    except Exception:
        # No fingerprints means no skips, which is the pre-existing
        # behaviour. Never let this optimisation break a run.
        logger.warning("Could not load %s fingerprints — running full", entity_type, exc_info=True)
        return {}


def record_fingerprint(db: Session, entity_type: str, entity_id: str, fingerprint: str) -> None:
    """Store the fingerprint for a member that was just fully analysed.

    Only call this after the member's analysis has been persisted. A
    fingerprint recorded for output that failed to save would skip the
    member on the next run and leave the failure permanent.
    """
    try:
        row = (
            db.query(MemberAnalysisFingerprint)
            .filter_by(entity_type=entity_type, entity_id=entity_id)
            .one_or_none()
        )
        if row is None:
            row = MemberAnalysisFingerprint(entity_type=entity_type, entity_id=entity_id)
            db.add(row)
        row.fingerprint = fingerprint
        row.computed_at = utcnow()
        db.commit()
    except Exception:
        logger.warning(
            "Could not record fingerprint for %s/%s — it will re-derive next run",
            entity_type, entity_id, exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            logger.debug("Fingerprint rollback failed", exc_info=True)


def clear_fingerprints(db: Session, entity_type: str | None = None) -> int:
    """Drop stored fingerprints so the next run re-derives everything.

    Used by the admin force-full-run path and by data resets.
    """
    query = db.query(MemberAnalysisFingerprint)
    if entity_type is not None:
        query = query.filter(MemberAnalysisFingerprint.entity_type == entity_type)
    count = query.delete()
    db.commit()
    return count
