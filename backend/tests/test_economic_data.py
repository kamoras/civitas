"""Tests for economic_data.calculate_jobs_created's Blinder-Watson
attribution window (2026-07, platform-review O9).

The GDP component of Effectiveness excludes a term's first calendar year
(Blinder & Watson 2016 — year-1 outcomes primarily reflect the
predecessor); jobs used to be counted from inauguration January, so the
two components of one score used opposite attribution rules, and a
sitting president (no term-end January yet) silently got no jobs
component at all — scored on a different basis than every completed term
in the same ranking.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.pipeline.fetch.economic_data import calculate_jobs_created, fetch_employment_data


def _series(points: dict[tuple[int, int], float]) -> list[dict]:
    """BLS-shaped series data: {(year, month): thousands}."""
    return [
        {"year": str(y), "period": f"M{m:02d}", "value": str(v)}
        for (y, m), v in points.items()
    ]


class TestJobsAttributionWindow:
    def test_baseline_is_second_year_january_not_inauguration(self):
        # Jobs boom in year 1 (predecessor-attributed), flat afterward:
        # under the old inauguration-January baseline this term claims
        # +2.0M; under Blinder-Watson attribution it claims 0.
        data = _series({
            (2021, 1): 140_000,   # inauguration January
            (2022, 1): 142_000,   # second-year January (baseline)
            (2025, 1): 142_000,   # term-end January
        })
        assert calculate_jobs_created(data, 2021, 2025) == 0.0

    def test_completed_term_uses_term_end_january(self):
        data = _series({
            (2022, 1): 140_000,
            (2025, 1): 146_500,
        })
        assert calculate_jobs_created(data, 2021, 2025) == 6.5

    def test_in_progress_term_falls_back_to_latest_month(self):
        # No 2029 January exists — the incumbent's endpoint is the latest
        # available month instead of silently returning None.
        data = _series({
            (2026, 1): 150_000,
            (2026, 5): 151_200,
        })
        assert calculate_jobs_created(data, 2025, 2029) == 1.2

    def test_too_young_term_returns_none(self):
        # Latest data predates the second-year-January baseline — there is
        # no attributed window yet, so no jobs figure (never a guess).
        data = _series({
            (2025, 3): 150_000,
            (2025, 11): 150_800,
        })
        assert calculate_jobs_created(data, 2025, 2029) is None

    def test_missing_baseline_january_returns_none(self):
        data = _series({(2025, 1): 146_000})
        assert calculate_jobs_created(data, 2021, 2025) is None


class TestFetchEmploymentDataYearCap:
    """end_year is capped at the current year (BLS has no future data) —
    must come from the project's canonical UTC clock, not a local-
    timezone-dependent call, so the cap can't drift by a year depending
    on the container's timezone right at a year boundary."""

    async def test_end_year_beyond_current_year_is_capped(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": []}]},
        }
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.pipeline.fetch.economic_data.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            await fetch_employment_data(mock_client, start_year=2020, end_year=2030)

        sent_payload = mock_client.post.call_args.kwargs["json"]
        assert sent_payload["endyear"] == "2026"

    async def test_end_year_within_current_year_is_unchanged(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": []}]},
        }
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.pipeline.fetch.economic_data.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            await fetch_employment_data(mock_client, start_year=2020, end_year=2023)

        sent_payload = mock_client.post.call_args.kwargs["json"]
        assert sent_payload["endyear"] == "2023"
