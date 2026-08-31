"""Departure detection and eventual removal for members of Congress.

Two stages, both driven from the nightly Senate/House pipelines, mirroring
the monitor lifecycle in analyze/action_center.py (dormant -> closed ->
deleted) with the same "thresholds live next to the code that applies
them" convention:

1. ``reconcile_roster`` — a member in the database but absent from
   tonight's Congress.gov roster has left office. Marks ``is_current``
   False and stamps ``left_office_date``. Until this existed, vacancy was
   manual-only (admin panel) and so in practice never recorded, which
   left stage 2 with nothing to act on. Reversible: a member who
   reappears on the roster is restored, because a one-night fetch gap
   should not permanently retire anyone.

2. ``purge_departed_members`` — a member whose ``left_office_date`` is
   more than ``RETIREMENT_GRACE_DAYS`` old is deleted outright, along
   with every row that references them. The grace period exists so a
   seat mid-special-election still shows constituents who used to hold
   it; once a successor has been seated for months, the departed member
   is no longer part of the current picture.

PRESIDENTS ARE NEVER SUBJECT TO EITHER STAGE. Neither is the Supreme
Court. Both functions take an explicit chamber and only ever touch
Senator/Representative rows — a former president is permanent site
content (they remain rankable against the historical field, which is the
only comparison that means anything for that office; see
president_service.get_president_leaderboard), and there is no roster feed
to reconcile them against in the first place.
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    ActionIssue,
    BskySenatorSpotlight,
    ExploreDocument,
    Representative,
    ScoreSnapshot,
    Senator,
)
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

CHAMBER_SENATE = "senate"
CHAMBER_HOUSE = "house"

# How long a departed member stays on the site before removal. Sized to
# outlast the seat being refilled: Senate vacancies are filled by
# gubernatorial appointment in weeks, and House special elections
# generally run 3-5 months from vacancy to swearing-in. 180 days clears
# the slow end of that range, so in the normal case a member is only
# removed once their successor has been serving for a while.
RETIREMENT_GRACE_DAYS = 180

# A roster smaller than this fraction of the members currently serving is
# treated as a broken fetch, not as mass departure, and reconciliation is
# skipped entirely. Congress.gov returning a partial page must never be
# able to retire the whole chamber. Normal turnover is safe: even at the
# start of a new Congress the roster is REPLACED, not shrunk — ~435 names
# arrive, some of them new — so a legitimate 60-80 member freshman class
# passes this check comfortably.
_MIN_ROSTER_FRACTION = 0.9

# Departures above this in one night are legitimate at a turnover but odd
# any other time, so they get logged at warning level for a human to eyeball.
_UNUSUAL_DEPARTURE_COUNT = 10

_MODELS = {CHAMBER_SENATE: Senator, CHAMBER_HOUSE: Representative}
# ScoreSnapshot.entity_type as written by senate_pipeline/house_pipeline.
_SNAPSHOT_ENTITY = {CHAMBER_SENATE: "senator", CHAMBER_HOUSE: "representative"}

_AUTO_VACANCY_REASON = "left office"


def _model_for(chamber: str):
    try:
        return _MODELS[chamber]
    except KeyError:
        raise ValueError(
            f"chamber must be one of {sorted(_MODELS)}, got {chamber!r} — "
            "presidents and justices are deliberately not covered here"
        ) from None


def _today_str(today: str | None) -> str:
    return today or utcnow().strftime("%Y-%m-%d")


def _alert(subject: str, body: str, *, dedupe_key: str) -> None:
    """Best-effort ops alert. Imported lazily and never allowed to raise —
    this module runs mid-pipeline and an alerting failure must not take the
    nightly run down with it (same lazy-import pattern as scheduler.py)."""
    try:
        from app.ops_alerts import send_ops_alert
        send_ops_alert(subject, body, dedupe_key=dedupe_key)
    except Exception:
        logger.exception("Failed to send ops alert: %s", subject)


def _is_iso_date(value: str | None) -> bool:
    """True for a real YYYY-MM-DD date. The purge compares dates as
    strings, which is only sound for that exact format."""
    if not value:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def reconcile_roster(
    db: Session,
    chamber: str,
    roster_bioguide_ids: set[str],
    *,
    today: str | None = None,
) -> dict:
    """Mark members absent from tonight's roster as no longer serving.

    ``roster_bioguide_ids`` is every bioguide id in the freshly-fetched
    chamber roster. Callers MUST pass the complete roster — never a
    filtered subset (e.g. senate_pipeline's ``senator_filter`` runs),
    since every member not in the set is a departure candidate.

    Returns a summary dict; commits nothing (the caller's transaction owns it).
    """
    model = _model_for(chamber)
    today = _today_str(today)
    roster = {b for b in roster_bioguide_ids if b}

    rows = db.query(model).all()
    serving = [m for m in rows if m.is_current]

    if not roster:
        logger.warning("Roster reconciliation skipped for %s — empty roster", chamber)
        return {"status": "skipped", "reason": "empty roster", "departed": [], "restored": []}

    floor = _MIN_ROSTER_FRACTION * len(serving)
    if len(roster) < floor:
        # Partial/failed fetch. Bailing out costs a day of staleness;
        # proceeding could retire most of a chamber.
        logger.error(
            "Roster reconciliation skipped for %s — roster has %d members but "
            "%d are currently serving (floor %.0f); treating as a failed fetch",
            chamber, len(roster), len(serving), floor,
        )
        # Alerted, not just logged: fetch_senators caches whatever it got,
        # and its pagination loop breaks silently on a mid-run failure, so
        # one truncated response can keep this skipping every night with
        # nothing but a log line to show for it. Same reasoning as
        # scheduler.py's alert on a skipped pipeline step.
        _alert(
            f"{chamber.title()} roster reconciliation skipped",
            f"Tonight's {chamber} roster had {len(roster)} members but "
            f"{len(serving)} are recorded as currently serving. This looks "
            "like a truncated or failed Congress.gov fetch, so no member was "
            "retired. Departures will go undetected until it recovers; if "
            "this repeats, check the cached roster.",
            dedupe_key=f"roster-skipped-{chamber}-{today}",
        )
        return {
            "status": "skipped",
            "reason": f"roster too small ({len(roster)} < {floor:.0f})",
            "departed": [],
            "restored": [],
        }

    departed: list[str] = []
    restored: list[str] = []
    unmatchable = 0

    for m in rows:
        if not m.bioguide_id:
            # Name-derived ids can't be matched against the roster with any
            # confidence, so these are left alone rather than guessed at.
            if m.is_current:
                unmatchable += 1
            continue

        on_roster = m.bioguide_id in roster

        if m.is_current and not on_roster:
            m.is_current = False
            m.vacancy_reason = _AUTO_VACANCY_REASON
            m.left_office_date = today
            departed.append(m.id)
        elif not m.is_current and on_roster:
            # Back on the roster — a transient fetch gap, a correction, or a
            # returning member. Clear the vacancy so the purge clock stops.
            m.is_current = True
            m.vacancy_reason = None
            m.left_office_date = None
            restored.append(m.id)

    if unmatchable:
        logger.warning(
            "%d serving %s member(s) have no bioguide_id and were skipped by "
            "roster reconciliation", unmatchable, chamber,
        )
    if departed:
        log = logger.warning if len(departed) >= _UNUSUAL_DEPARTURE_COUNT else logger.info
        log("Marked %d %s member(s) as departed: %s", len(departed), chamber, ", ".join(departed))
    if restored:
        logger.info("Restored %d %s member(s) to serving: %s", len(restored), chamber, ", ".join(restored))

    return {"status": "ok", "departed": departed, "restored": restored, "unmatchable": unmatchable}


def purge_departed_members(
    db: Session,
    chamber: str,
    *,
    today: str | None = None,
    grace_days: int = RETIREMENT_GRACE_DAYS,
) -> dict:
    """Delete members whose ``left_office_date`` is on or before
    ``today - grace_days``.

    A member marked not-current but carrying no usable ``left_office_date``
    — an older manual admin vacancy, or a malformed value — gets today's
    date stamped rather than being deleted immediately or ignored forever.
    The clock starts now and they are removed on a later run.

    Returns a summary dict; commits nothing.
    """
    model = _model_for(chamber)
    today = _today_str(today)
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=grace_days)).strftime("%Y-%m-%d")

    candidates = db.query(model).filter(model.is_current == False).all()  # noqa: E712

    purged: list[str] = []
    stamped: list[str] = []

    for m in candidates:
        if not _is_iso_date(m.left_office_date):
            # No date, or one that isn't YYYY-MM-DD. Both get today's date
            # rather than a deletion: the cutoff test below is a string
            # comparison, and a malformed value sorts arbitrarily against
            # it — "2026" and "07/01/2026" both compare BELOW any real
            # cutoff and would delete the member (and every donor, vote,
            # promise, bill and trade of theirs) on the next run. Restamping
            # is self-healing; the clock simply starts now.
            if m.left_office_date:
                logger.warning(
                    "Unparseable left_office_date %r on %s — restamping as %s "
                    "instead of purging", m.left_office_date, m.id, today,
                )
            m.left_office_date = today
            stamped.append(m.id)
            continue
        if m.left_office_date > cutoff:
            continue

        _purge_member_traces(db, m.id, chamber)
        # ORM delete, not a bulk query delete: SQLite runs without
        # PRAGMA foreign_keys=ON (see database.py's pragma list), so the
        # ON DELETE CASCADE on donors/votes/promises/bills/trades is
        # inert and it's the relationship-level cascade that actually
        # clears the child rows.
        db.delete(m)
        purged.append(m.id)

    if stamped:
        logger.info(
            "Stamped left_office_date on %d %s member(s) with no departure date: %s",
            len(stamped), chamber, ", ".join(stamped),
        )
    if purged:
        logger.warning(
            "Purged %d %s member(s) departed before %s: %s",
            len(purged), chamber, cutoff, ", ".join(purged),
        )

    return {"status": "ok", "purged": purged, "stamped": stamped, "cutoff": cutoff}


def _purge_member_traces(db: Session, member_id: str, chamber: str) -> None:
    """Clear references to a member that no foreign key would catch.

    Everything hanging off a real FK (donors, votes, lobbying matches,
    promises, sponsored bills, stock trades) goes with the ORM cascade.
    These four don't have one and would otherwise dangle: a stale entry in
    an action issue renders a contact chip linking to a 404, and an orphan
    snapshot keeps feeding the trend series of an id nothing else knows.
    """
    db.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == _SNAPSHOT_ENTITY[chamber],
        ScoreSnapshot.entity_id == member_id,
    ).delete(synchronize_session=False)

    # Filtered on (senator_id, chamber) together, not id alone — the table
    # holds both chambers (see bluesky_spotlight._pick_politician), keyed
    # exactly this way so a senator and a representative that happen to
    # share an id string are never conflated. An id-only filter here would
    # delete a live, different-chamber member's spotlight row by accident.
    db.query(BskySenatorSpotlight).filter(
        BskySenatorSpotlight.senator_id == member_id,
        BskySenatorSpotlight.chamber == chamber,
    ).delete(synchronize_session=False)

    # Explore documents are kept — a floor speech is a government record
    # that outlives the member's term, and it still reads correctly under
    # politician_name. Only the link is severed, so the explore page stops
    # pointing at a profile that no longer exists. The matching vec_explore
    # metadata is left as-is: it is only ever read as a search filter, and
    # nothing can ask for a purged member's id once the profile is gone.
    db.query(ExploreDocument).filter(
        ExploreDocument.politician_id == member_id,
    ).update({ExploreDocument.politician_id: None}, synchronize_session=False)

    _strip_from_action_issues(db, member_id)


def _strip_from_action_issues(db: Session, member_id: str) -> None:
    """Remove a member from every action issue's related_senators blob."""
    # LIKE prefilter so this touches only the handful of issues that
    # actually name the member, rather than rewriting the whole table.
    issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.related_senators.like(f'%"{member_id}"%'))
        .all()
    )
    for issue in issues:
        try:
            entries = json.loads(issue.related_senators or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and e.get("id") == member_id)]
        if len(kept) != len(entries):
            issue.related_senators = json.dumps(kept)
