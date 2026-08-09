"""Tests for GET /elections/geocode — the Census Bureau geocoder proxy
that resolves a mailing address to state + House district, so a visitor
can auto-select their district instead of using the manual dropdown.

Real response shape verified live against the actual Census endpoint,
2026-08-09 (1600 Pennsylvania Ave NW, Washington DC 20500 -> DC / CD119
"98", the real non-voting-delegate code)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.elections import geocode_address


def _census_response(matches):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"result": {"addressMatches": matches}}
    return resp


def _match(state: str, cd119: str):
    return {
        "addressComponents": {"state": state},
        "geographies": {"119th Congressional Districts": [{"CD119": cd119, "STATE": "13"}]},
    }


async def _call(address: str, matches):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_census_response(matches))
    with patch("app.api.elections.make_async_client") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        return await geocode_address(address, _rl=None), mock_client


class TestGeocodeAddress:
    @pytest.mark.asyncio
    async def test_resolves_state_and_district_for_a_real_match(self):
        result, _ = await _call("100 Peachtree St, Atlanta, GA", [_match("GA", "06")])
        assert result == {"state": "GA", "district": 6}

    @pytest.mark.asyncio
    async def test_at_large_district_is_zero_not_stripped(self):
        result, _ = await _call("1 Main St, Anchorage, AK", [_match("AK", "00")])
        assert result == {"state": "AK", "district": 0}

    @pytest.mark.asyncio
    async def test_no_match_returns_null_state_and_district_not_an_error(self):
        result, _ = await _call("nonsense address that matches nothing", [])
        assert result == {"state": None, "district": None}

    @pytest.mark.asyncio
    async def test_match_with_no_congressional_district_geography_is_null(self):
        match = {"addressComponents": {"state": "GA"}, "geographies": {}}
        result, _ = await _call("some real address", [match])
        assert result == {"state": None, "district": None}

    @pytest.mark.asyncio
    async def test_empty_address_is_a_400_not_a_silent_null(self):
        with pytest.raises(HTTPException) as exc:
            await geocode_address("   ", _rl=None)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_overlong_address_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            await geocode_address("x" * 500, _rl=None)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_network_failure_surfaces_as_502_not_a_500(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.api.elections.make_async_client") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            with pytest.raises(HTTPException) as exc:
                await geocode_address("100 Main St", _rl=None)
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_never_logs_the_address_on_failure(self, caplog):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.api.elections.make_async_client") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            with caplog.at_level("ERROR"):
                with pytest.raises(HTTPException):
                    await geocode_address("100 Very Identifiable Private Lane", _rl=None)
        assert "Very Identifiable Private Lane" not in caplog.text

    @pytest.mark.asyncio
    async def test_passes_the_address_straight_through_to_census(self):
        _, mock_client = await _call("742 Evergreen Terrace, Springfield", [])
        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["params"]["address"] == "742 Evergreen Terrace, Springfield"
