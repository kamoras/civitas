"""Tests for the primary-date reads (state_election_dates.py).

The FEC's national calendar is the one source that answers "when is this
state's primary" WITHOUT anyone having written an adapter for that state,
so it is what makes every state's status reportable at all. Rows below are
the real shape of api.open.fec.gov's election-dates response.
"""

import pytest

from app.pipeline.fetch import state_election_dates as dates


def _payload(results, pages=1):
    return {"results": results, "pagination": {"pages": pages}}


@pytest.mark.asyncio
class TestFecCalendar:
    async def test_reads_a_primary_and_its_runoff(self, monkeypatch):
        async def fake_fetch(client, url):
            return _payload([
                {"election_state": "GA", "election_date": "2026-05-19",
                 "election_type_full": "Primary election", "office_sought": "H"},
                {"election_state": "GA", "election_date": "2026-06-16",
                 "election_type_full": "Runoff", "office_sought": "H"},
            ])

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        assert await dates.fetch_fec_calendar(None, 2026) == {
            "GA": {"primary": "2026-05-19", "runoff": "2026-06-16"},
        }

    async def test_a_special_election_is_not_a_states_primary(self, monkeypatch):
        """A special primary is a different race on its own schedule —
        folding one in would report a state's regular primary as whenever
        its last vacancy happened to be filled."""
        async def fake_fetch(client, url):
            return _payload([
                {"election_state": "TX", "election_date": "2026-01-31",
                 "election_type_full": "Special primary", "office_sought": "H"},
                {"election_state": "TX", "election_date": "2026-03-03",
                 "election_type_full": "Primary election", "office_sought": "H"},
            ])

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        calendar = await dates.fetch_fec_calendar(None, 2026)
        assert calendar["TX"]["primary"] == "2026-03-03"

    async def test_non_federal_rows_are_ignored(self, monkeypatch):
        async def fake_fetch(client, url):
            return _payload([
                {"election_state": "NC", "election_date": "2026-03-03",
                 "election_type_full": "Primary election", "office_sought": "P"},
            ])

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        assert await dates.fetch_fec_calendar(None, 2026) == {}

    async def test_a_failed_read_yields_nothing_rather_than_raising(self, monkeypatch):
        async def fake_fetch(client, url):
            return None

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        assert await dates.fetch_fec_calendar(None, 2026) == {}


@pytest.mark.asyncio
class TestDiscoverDatesClarity:
    """discover_dates's own clarity branch had no test coverage at all
    before this — added alongside the landing_page discovery mode
    (state_candidates_clarity.py) to confirm West Virginia's empty
    elections.json degrades honestly rather than silently."""

    async def test_reads_the_primary_off_a_populated_elections_json(self, monkeypatch):
        class _Resp:
            def json(self):
                return [{"Date": "6/30/2026 12:00:00 AM", "ElectionName": "2026 Primary"}]

        async def fake_get(client, url, label):
            return _Resp()

        monkeypatch.setattr("app.pipeline.fetch.state_candidates_clarity._get", fake_get)
        result = await dates.discover_dates(None, 2026, "CO", {"strategy": "clarity"})
        assert result == {"primary": "2026-06-30"}

    async def test_empty_elections_json_with_no_landing_page_config_is_a_quiet_unknown(self, monkeypatch, caplog):
        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr("app.pipeline.fetch.state_candidates_clarity._get", fake_get)
        result = await dates.discover_dates(None, 2026, "XX", {"strategy": "clarity"})
        assert result == {}

    async def test_empty_elections_json_with_landing_page_config_logs_the_gap(self, monkeypatch, caplog):
        # West Virginia's real shape: elections.json is empty, but its
        # source entry carries a landing_page discovery block (used by
        # state_candidates_clarity.py for candidate confirmation) --
        # this must be distinguishable in logs from a plain unknown.
        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr("app.pipeline.fetch.state_candidates_clarity._get", fake_get)
        source = {
            "strategy": "clarity",
            "discovery": {"mode": "landing_page", "page_url": "x", "link_regex": "y"},
        }
        with caplog.at_level("INFO"):
            result = await dates.discover_dates(None, 2026, "WV", source)
        assert result == {}
        assert any("WV" in r.message for r in caplog.records)


class TestSaveMerges:
    def test_a_later_read_does_not_drop_what_an_earlier_one_knew(self, tmp_path, monkeypatch):
        """The national calendar supplies a runoff the state's own feed may
        not mention, and vice versa — the two have to accumulate."""
        monkeypatch.setattr(dates, "_PATHS", (str(tmp_path / "dates.json"),))
        monkeypatch.setattr(dates, "_cache", None)
        dates.save("GA", 2026, {"primary": "2026-05-19", "runoff": "2026-06-16"})
        dates.save("GA", 2026, {"primary": "2026-05-19"})
        assert dates.primary_date("GA", 2026) == "2026-05-19"
        assert dates.all_dates()["2026-GA"]["runoff"] == "2026-06-16"
