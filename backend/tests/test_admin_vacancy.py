"""admin_set_vacancy input validation.

left_office_date stopped being cosmetic when member_lifecycle's purge
started keying an irreversible cascade delete off it — the purge compares
it as a string against a YYYY-MM-DD cutoff, so a typo like "2026" or
"07/01/2026" sorts below every real cutoff. The purge restamps anything
malformed rather than acting on it; this endpoint is the other half of
that defence, rejecting the value before it is ever stored.
"""

import pytest
from fastapi import HTTPException

from app.api.admin import admin_set_vacancy
from app.models import Senator


@pytest.fixture()
def senator(db_session):
    s = Senator(id="jane-doe", bioguide_id="S00001", name="Jane Doe", state="CA", party="D")
    db_session.add(s)
    db_session.commit()
    return s


@pytest.mark.parametrize("bad_date", ["2026", "07/01/2026", "unknown", "2026-13-45", ""])
@pytest.mark.asyncio
async def test_malformed_left_office_date_is_rejected(db_session, senator, bad_date):
    with pytest.raises(HTTPException) as exc:
        await admin_set_vacancy(
            "jane-doe", is_current=False, reason="resigned",
            left_office_date=bad_date, db=db_session,
        )

    assert exc.value.status_code == 400
    assert "YYYY-MM-DD" in exc.value.detail
    # Nothing was written — the seat is untouched.
    assert db_session.query(Senator).filter_by(id="jane-doe").one().is_current is True


@pytest.mark.asyncio
async def test_valid_left_office_date_is_accepted(db_session, senator):
    result = await admin_set_vacancy(
        "jane-doe", is_current=False, reason="resigned",
        left_office_date="2026-03-01", db=db_session,
    )

    assert result["isCurrent"] is False
    assert result["leftOfficeDate"] == "2026-03-01"
    assert result["vacancyReason"] == "resigned"


@pytest.mark.asyncio
async def test_restoring_a_seat_clears_the_removal_clock(db_session, senator):
    await admin_set_vacancy(
        "jane-doe", is_current=False, reason="resigned",
        left_office_date="2026-03-01", db=db_session,
    )

    # reason/left_office_date passed explicitly: called directly rather than
    # through FastAPI, their defaults are Query objects, not None.
    result = await admin_set_vacancy(
        "jane-doe", is_current=True, reason=None, left_office_date=None, db=db_session,
    )

    assert result["isCurrent"] is True
    assert result["leftOfficeDate"] is None
    assert result["vacancyReason"] is None


@pytest.mark.asyncio
async def test_unknown_vacancy_reason_is_rejected(db_session, senator):
    with pytest.raises(HTTPException) as exc:
        await admin_set_vacancy(
            "jane-doe", is_current=False, reason="retired", db=db_session,
        )

    assert exc.value.status_code == 400
