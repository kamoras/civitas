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
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


_VOTE = '<?xml version="1.0"?><rollcall-vote><vote-metadata/></rollcall-vote>'
# What the House Clerk serves, with a 200, for a roll that doesn't exist yet.
_SANITIZE_ERROR = '<xml>Error sanitizing file "roll400.xml". Please try again.</xml>'


class _FakeClient:
    """A vote document for any roll number <= max_valid; past it, a 404 or
    the Clerk's 200 error body (`missing`); every request raises when
    `down`."""

    def __init__(self, max_valid: int, missing: str = "404", down: bool = False):
        self.max_valid = max_valid
        self.missing = missing
        self.down = down

    async def get(self, url: str, timeout: float):
        if self.down:
            raise OSError("connection refused")
        roll = int(url.removesuffix(".xml").rsplit("roll", 1)[-1])
        if roll <= self.max_valid:
            return _FakeResponse(200, _VOTE)
        if self.missing == "sanitize":
            return _FakeResponse(200, _SANITIZE_ERROR)
        return _FakeResponse(404, "<html>Not found</html>")


def _url_for_roll(roll: int) -> str:
    return f"https://example.test/roll{roll}.xml"


_PROBES = [500, 300, 200, 150, 100, 75, 50, 25, 10]


@pytest.mark.parametrize("missing", ["404", "sanitize"])
@pytest.mark.parametrize("max_valid", [
    # Between the 200 and 300 probe points, so the narrow forward search
    # from 200 must walk up to find it exactly.
    pytest.param(217, id="exact_highest_between_probe_points"),
    pytest.param(300, id="exact_probe_hit_needs_no_narrow_search"),
    pytest.param(0, id="no_valid_roll_call_returns_zero"),
])
@pytest.mark.asyncio
async def test_finds_the_highest_valid_roll_call(max_valid, missing):
    client = _FakeClient(max_valid, missing=missing)
    assert await _find_highest_roll_call(client, _url_for_roll, _PROBES, "rollcall-vote") == max_valid


@pytest.mark.asyncio
async def test_an_unreachable_site_is_none_not_zero():
    client = _FakeClient(217, down=True)
    assert await _find_highest_roll_call(client, _url_for_roll, _PROBES, "rollcall-vote") is None
