"""Bring stored Constituent Alignment onto the current reference at startup.

A release that changes the statistic Constituent Alignment's reference is
measured on (stamped CONSTITUENT_REFERENCE_STATISTIC — v6.15's unweighted
break rate over full-confidence members, v6.16's logit expectation and
per-party residual scale) leaves two things behind until the
next nightly run: the persisted /data/constituent_reference.json, which
CONSTITUENT_REFERENCE.load() then skips in favour of the bundled prior, and
every member's stored score. The API's "show the math" breakdown recomputes
from the first and the pages read the second, so for up to a day a profile's
number and its breakdown disagreed.

Everything the score needs is stored: the party-labeled votes, party and
caucus, state and district, and the Voteview ideal points in
/data/member_ideal_points.json. So on startup, when a chamber's persisted
reference was measured on a different statistic, this re-measures it from
the database, rescores that chamber's stored Constituent Alignment and its
vote-part status, and persists the reference only once those scores are
committed. Once the persisted reference is current it does nothing. Same
shape as les_rescore.
"""

import json
import logging
from collections import defaultdict

from app.pipeline.analyze.party_line_record import load_record

logger = logging.getLogger(__name__)


def _stale_chambers() -> list[str]:
    """Chambers whose persisted reference exists but was measured on another
    statistic. A chamber with no persisted entry was scored against the
    bundled prior, which is what the breakdown reads too: nothing to fix."""
    from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE, _read_json

    persisted = _read_json(CONSTITUENT_REFERENCE.live_path)
    return [
        c for c in ("senate", "house")
        if persisted.get(c) and not CONSTITUENT_REFERENCE.usable(persisted.get(c))
    ]


def _member_dict(row, votes: list[dict], chamber: str) -> dict:
    """The calculate_scores-shaped fields Constituent Alignment reads."""
    return {
        "state": row.state or "",
        "party": row.party or "I",
        "district": row.district if chamber == "house" else None,
        "bioguideId": row.bioguide_id,
        "votingRecord": {
            "keyVotes": votes,
            "recentVotes": [],
            "effectiveParty": row.caucus_party or row.party,
            "partyLineRecord": load_record(row.party_line_record),
        },
    }


def rescore_stale_constituent_alignment(session_factory, *, house_lines: int | None) -> list[str]:
    """Rescore each chamber whose persisted Constituent Alignment reference
    predates the current statistic. Returns the chambers rescored. Never
    raises.

    `house_lines` is the Congress whose district lines the caller holds in
    effect for the rescore (district_pvi.current_lines — main's lifespan).
    Each rescored representative records it (district_lines_congress) in
    the same commit as their new score, so the score breakdown — and the
    overlap check measured from it just below — recompute on the lines the
    score used. Recorded afterwards, the overlap check would read members
    still recorded on older lines on those, beside scores rewritten on the
    current ones, and a House run committing in between (another backend,
    mid-rollout) would have its members stamped with this rescore's
    lines."""
    from app.models import HousePipelineRun, PipelineRun, Representative, Senator
    from app.pipeline.analyze.ground_truth import _vote_query_for
    from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
    from app.pipeline.analyze.score_calculator import (
        _constituent_alignment_core,
        compute_constituent_reference,
        constituent_reference_inputs,
    )
    from app.pipeline.run_tracker import run_in_progress
    from app.pipeline.transform.normalize_votes import stored_vote

    done: list[str] = []
    for chamber in _stale_chambers():
        model, run = (Senator, PipelineRun) if chamber == "senate" else (Representative, HousePipelineRun)
        db = session_factory()
        try:
            if run_in_progress(db, run):
                # A run in progress writes a fresh reference and scores itself.
                logger.info("Constituent Alignment rescore (%s) skipped — a pipeline run is in progress", chamber)
                continue
            vote_model, fk_col = _vote_query_for(model)
            rows = db.query(model).filter(model.is_current.is_(True)).all()
            votes: dict[str, list[dict]] = defaultdict(list)
            for row_id, member_id, bill_id, with_party in (
                db.query(vote_model.id, fk_col, vote_model.bill_id, vote_model.voted_with_party)
                .filter(vote_model.voted_with_party.isnot(None))
                .filter(fk_col.in_([r.id for r in rows]))
                .all()
            ):
                votes[member_id].append(stored_vote(row_id, bill_id, with_party))
            members = [_member_dict(r, votes[r.id], chamber) for r in rows]
            ref = compute_constituent_reference(constituent_reference_inputs(members))
            if ref is None:
                # Scoring against the prior would move every score for no
                # gain; the next pipeline run measures and rescores.
                logger.warning(
                    "Constituent Alignment rescore (%s): too few full-confidence members to measure a reference",
                    chamber,
                )
                continue
            reference = {**CONSTITUENT_REFERENCE.load(), chamber: ref}
            for row, m in zip(rows, members):
                core = _constituent_alignment_core(
                    m["votingRecord"], [], {}, m["state"], m["party"],
                    district=m["district"], bioguide_id=m["bioguideId"], reference=reference,
                )
                row.score_constituent_alignment = core["score"]
                try:
                    confidence = json.loads(row.score_confidence or "{}")
                except (TypeError, ValueError):
                    confidence = {}
                if isinstance(confidence, dict):
                    confidence["constituentAlignmentVotePart"] = core["vote_part_status"]
                    row.score_confidence = json.dumps(confidence)
                if chamber == "house":
                    row.district_lines_congress = house_lines
            db.commit()
            # Persisted only once the scores it describes are committed: a
            # current-statistic reference is what marks the chamber done, so
            # writing it first would strand the scores on a failure.
            CONSTITUENT_REFERENCE.write(chamber, ref)
            done.append(chamber)
            # The overlap check reads these breakdowns; re-measure it so
            # /about/scores doesn't show the pre-rescore reading until the
            # next nightly run. Never raises.
            from app.pipeline.analyze.signal_overlap import record_signal_overlap

            record_signal_overlap(db, chamber)
            logger.info(
                "Constituent Alignment rescore (%s): %d members moved to the current reference",
                chamber, len(rows),
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Constituent Alignment rescore (%s) failed (non-fatal) — retried next startup", chamber,
            )
        finally:
            db.close()
    return done
