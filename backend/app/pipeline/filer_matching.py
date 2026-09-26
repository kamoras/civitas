"""Match a disclosure filer's name to a sitting senator or representative.

Shared by the STOCK Act trade ingest and the annual holdings ingest
(stock_pipeline.py): both read filings indexed by the filer's printed name,
and both must skip a filing rather than guess when the name is ambiguous.
"""

from sqlalchemy.orm import Session

from app.models import Representative, Senator


def match_senator(db: Session, last: str, first: str) -> Senator | None:
    if not last:
        return None
    candidates = (
        db.query(Senator)
        .filter(Senator.is_current == True, Senator.name.ilike(f"%{last}%"))  # noqa: E712
        .all()
    )
    if len(candidates) == 1:
        return candidates[0]
    if first:
        for c in candidates:
            if first.lower() in c.name.lower():
                return c
    # Ambiguous (multiple same-last-name matches, none disambiguated by
    # first name) — skip rather than guess which one filed the PTR.
    return None


def match_representative(db: Session, last: str, first: str, state_district: str) -> Representative | None:
    if not last:
        return None
    state = state_district[:2] if state_district else None
    # The House FD index supplies the FULL district ("CA27"), and
    # Representative.district exists — so filter on it. Previously only the
    # state was used, leaving same-state same-surname pairs to a fragile
    # first-name substring match that silently skipped the filing every run
    # whenever the formal filing name differed from the display name
    # ("Michael" vs "Mike"). District makes the match exact for all 435
    # voting seats.
    district: int | None = None
    if state_district and len(state_district) > 2 and state_district[2:].isdigit():
        district = int(state_district[2:])

    query = db.query(Representative).filter(
        Representative.is_current == True, Representative.name.ilike(f"%{last}%")  # noqa: E712
    )
    if state:
        query = query.filter(Representative.state == state)
    if district is not None:
        query = query.filter(Representative.district == district)
    candidates = query.all()
    if len(candidates) == 1:
        return candidates[0]
    if first:
        for c in candidates:
            if first.lower() in c.name.lower():
                return c
    # Ambiguous (multiple same-last-name matches, none disambiguated by
    # first name) — skip rather than guess which one filed the PTR.
    return None
