"""Tests for _find_highest_roll_call — the probe-then-narrow search shared
by fetch_recent_roll_calls (Senate) and fetch_recent_house_roll_calls
(House). Extracted out of two ~30-line near-duplicate implementations;
this pins the shared search behavior directly rather than through either
chamber's full fetch pipeline.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.congress import _find_highest_roll_call

# _find_highest_roll_call calls the module's shared rate limiter
# (CONGRESS_RPS=1.2 by default) before every probe — without patching it
# out, ~9 probes would make this test sleep for several real seconds.
pytestmark = pytest.mark.usefixtures("_no_rate_limit")


@pytest.fixture()
def _no_rate_limit():
    with patch("app.pipeline.fetch.congress._rate_limiter.acquire", new=AsyncMock(return_value=None)):
        yield


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeClient:
    """Returns 200 for any roll number <= max_valid, else 404."""

    def __init__(self, max_valid: int):
        self.max_valid = max_valid

    async def get(self, url: str, timeout: float):
        roll = int(url.rstrip(".xml").rsplit("roll", 1)[-1])
        return _FakeResponse(200 if roll <= self.max_valid else 404)


def _url_for_roll(roll: int) -> str:
    return f"https://example.test/roll{roll}.xml"


@pytest.mark.asyncio
async def test_finds_exact_highest_between_probe_points():
    # Highest valid roll is 217 — between the 200 and 300 probe points, so
    # the narrow forward search from 200 must walk up to find it exactly.
    client = _FakeClient(max_valid=217)
    result = await _find_highest_roll_call(
        client, _url_for_roll, [500, 300, 200, 150, 100, 75, 50, 25, 10],
    )
    assert result == 217


@pytest.mark.asyncio
async def test_no_valid_roll_call_returns_zero():
    client = _FakeClient(max_valid=0)
    result = await _find_highest_roll_call(
        client, _url_for_roll, [500, 300, 200, 150, 100, 75, 50, 25, 10],
    )
    assert result == 0


@pytest.mark.asyncio
async def test_exact_probe_hit_needs_no_narrow_search():
    client = _FakeClient(max_valid=300)
    result = await _find_highest_roll_call(
        client, _url_for_roll, [500, 300, 200, 150, 100, 75, 50, 25, 10],
    )
    assert result == 300
