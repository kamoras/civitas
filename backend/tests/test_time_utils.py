"""time_utils.eastern_date: the one way a stored (naive UTC) timestamp
becomes a calendar date on the site."""

from datetime import date, datetime, timezone

import pytest

from app.time_utils import eastern_date


@pytest.mark.parametrize("stamp, expected", [
    pytest.param(datetime(2026, 10, 15, 1, 30), date(2026, 10, 14), id="evening_edt_is_the_day_before_utc"),
    pytest.param(datetime(2026, 10, 15, 4, 0), date(2026, 10, 15), id="edt_midnight"),
    pytest.param(datetime(2026, 12, 15, 4, 30), date(2026, 12, 14), id="evening_est"),
    pytest.param(datetime(2026, 12, 15, 5, 0), date(2026, 12, 15), id="est_midnight"),
    pytest.param(datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc), date(2026, 10, 14), id="aware_utc"),
])
def test_eastern_date(stamp, expected):
    assert eastern_date(stamp) == expected
