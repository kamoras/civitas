"""Tests for the primary-date reads (state_election_dates.py).

The FEC's national calendar is the one source that answers "when is this
state's primary" WITHOUT anyone having written an adapter for that state,
so it is what makes every state's status reportable at all. Rows below are
the real shape of api.open.fec.gov's election-dates response.
"""

import pytest

from app.pipeline.fetch import state_election_dates as dates


def _payload(results, pages=1, count=None):
    return {"results": results, "pagination": {
        "pages": pages, "count": len(results) * pages if count is None else count,
    }}


@pytest.fixture
def dates_file(tmp_path, monkeypatch):
    path = tmp_path / "dates.json"
    monkeypatch.setattr(dates, "_PATH", str(path))
    monkeypatch.setattr(dates, "_cache", None)
    return path


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
        assert await dates.fetch_fec_calendar(None, 2026) == ({
            "GA": {"primary": "2026-05-19", "runoff": "2026-06-16"},
        }, True)

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
        calendar, _complete = await dates.fetch_fec_calendar(None, 2026)
        assert calendar["TX"]["primary"] == "2026-03-03"

    async def test_non_federal_rows_are_ignored(self, monkeypatch):
        async def fake_fetch(client, url):
            return _payload([
                {"election_state": "NC", "election_date": "2026-03-03",
                 "election_type_full": "Primary election", "office_sought": "P"},
            ])

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        assert await dates.fetch_fec_calendar(None, 2026) == ({}, True)

    async def test_a_failed_read_yields_nothing_rather_than_raising(self, monkeypatch):
        async def fake_fetch(client, url):
            return None

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        assert await dates.fetch_fec_calendar(None, 2026) == ({}, False)

    async def test_a_later_page_failing_leaves_the_read_incomplete(self, monkeypatch):
        """What page 1 held is still true, but a state whose Senate general
        sat on the failed page must not read as having none."""
        async def fake_fetch(client, url):
            if "page=2" in url:
                return None
            return _payload([
                {"election_state": "AZ", "election_date": "2026-11-03",
                 "election_type_full": "General election", "office_sought": "S"},
            ], pages=3)

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        calendar, complete = await dates.fetch_fec_calendar(None, 2026)
        assert calendar == {"AZ": {"senate": "2026-11-03"}} and complete is False

    async def test_more_pages_than_it_will_read_is_incomplete(self, monkeypatch):
        async def fake_fetch(client, url):
            return _payload([
                {"election_state": "AZ", "election_date": "2026-08-04",
                 "election_type_full": "Primary election", "office_sought": "H"},
            ], pages=dates._FEC_MAX_PAGES + 1)

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", fake_fetch)
        _calendar, complete = await dates.fetch_fec_calendar(None, 2026)
        assert complete is False

    async def test_a_read_short_of_the_endpoints_own_count_is_incomplete(self, monkeypatch):
        """A page with no pagination, or rows missing from a page that
        came back fine, can't say what is absent — and a complete read
        retracts Senate elections."""
        row = {"election_state": "AZ", "election_date": "2026-11-03",
               "election_type_full": "General election", "office_sought": "S"}

        async def no_pagination(client, url):
            return {"results": [row]}

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", no_pagination)
        assert (await dates.fetch_fec_calendar(None, 2026))[1] is False

        async def short(client, url):
            return _payload([row], count=240)

        monkeypatch.setattr("app.pipeline.fetch.fec._fetch_with_retry", short)
        assert (await dates.fetch_fec_calendar(None, 2026))[1] is False


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
    def test_a_later_read_does_not_drop_what_an_earlier_one_knew(self, dates_file):
        """A state's feed may give its primary on one read and its runoff on
        another — the two have to accumulate."""
        dates.save("GA", 2026, {"primary": "2026-05-19", "runoff": "2026-06-16"})
        dates.save("GA", 2026, {"primary": "2026-05-19"})
        assert dates.primary_date("GA", 2026) == "2026-05-19"
        assert dates.all_dates()["2026-GA"]["runoff"] == "2026-06-16"

    def test_the_states_own_primary_wins_and_neither_source_erases_the_other(self, dates_file):
        """They used to share one key, so whichever wrote last that night
        was the date shown."""
        dates.save("NC", 2026, {"primary": "2026-03-03"})
        dates.save_calendar(2026, {"NC": {"primary": "2026-03-10"}}, complete=True, read_on="2026-09-01")
        assert dates.primary_date("NC", 2026) == "2026-03-03"
        dates.save("NC", 2026, {"primary": "2026-03-03"})
        assert dates.all_dates()["2026-NC"]["fec_primary"] == "2026-03-10"
        dates.save_calendar(2026, {"GA": {"primary": "2026-05-19"}}, complete=False, read_on="x")
        assert dates.primary_date("GA", 2026) == "2026-05-19"  # the calendar alone still answers

    def test_a_write_reads_the_file_not_a_stale_copy(self, dates_file):
        """Another process (or thread) wrote since this one last read: its
        change must survive this one's write."""
        dates.save("GA", 2026, {"primary": "2026-05-19"})
        stale = dates._cache
        import json
        on_disk = json.loads(dates_file.read_text())
        on_disk["2026-TX"] = {"primary": "2026-03-03"}
        dates_file.write_text(json.dumps(on_disk))
        dates._cache = stale  # this process never saw TX
        dates.save("AZ", 2026, {"primary": "2026-08-04"})
        assert dates.primary_date("TX", 2026) == "2026-03-03"
        assert json.loads(dates_file.read_text())["2026-TX"] == {"primary": "2026-03-03"}

    def test_a_write_that_fails_says_so_and_changes_nothing(self, tmp_path, monkeypatch):
        from app.atomic_write import NotSaved

        blocked = tmp_path / "not-a-dir"
        blocked.write_text("")
        monkeypatch.setattr(dates, "_PATH", str(blocked / "dates.json"))
        monkeypatch.setattr(dates, "_cache", None)
        with pytest.raises(NotSaved):
            dates.save("TX", 2026, {"primary": "2026-03-03"})
        assert dates.primary_date("TX", 2026) is None  # not served as if saved


class TestCalendarRetraction:
    def test_only_a_complete_read_marks_the_calendar_or_retracts_a_senate_race(self, dates_file):
        """An incomplete read can't say what is absent — the roster deletes
        a Senate race the calendar doesn't list."""
        dates.save_calendar(2026, {"AZ": {"senate": "2026-11-03"}}, complete=False, read_on="d1")
        assert dates.senate_election_known("AZ", 2026) is None  # not read in full yet
        dates.save_calendar(
            2026, {"AZ": {"senate": "2026-11-03"}, "GA": {"senate": "2026-11-03"}},
            complete=True, read_on="d2",
        )
        assert dates.senate_election_known("GA", 2026) is True
        dates.save_calendar(2026, {"AZ": {"senate": "2026-11-03"}}, complete=False, read_on="d3")
        assert dates.senate_election_known("GA", 2026) is True  # a partial read retracts nothing
        dates.save_calendar(2026, {"AZ": {"senate": "2026-11-03"}}, complete=True, read_on="d4")
        assert dates.senate_election_known("GA", 2026) is False  # dropped from a full read: gone
        assert dates.senate_election_known("AZ", 2026) is True

    def test_a_retraction_leaves_the_states_own_dates(self, dates_file):
        dates.save("GA", 2026, {"primary": "2026-05-19", "runoff": "2026-06-16"})
        dates.save_calendar(2026, {"GA": {"senate": "2026-11-03", "runoff": "2026-06-16"}},
                            complete=True, read_on="d1")
        dates.save_calendar(2026, {}, complete=True, read_on="d2")
        assert dates.all_dates()["2026-GA"] == {
            "primary": "2026-05-19", "runoff": "2026-06-16", "state_feed": True,
        }

    def test_a_legacy_calendar_date_stops_shadowing_the_calendar(self, dates_file):
        """The calendar used to write the state's own keys. Such a value,
        left on disk, would otherwise pass for the state's own claim
        forever and hide every later FEC correction."""
        import json
        dates_file.write_text(json.dumps({"2026-OK": {"primary": "2026-06-16", "runoff": "2026-08-25"}}))
        dates.save_calendar(2026, {"OK": {"primary": "2026-06-23"}}, complete=False, read_on="d1")
        assert dates.primary_date("OK", 2026) == "2026-06-16"  # a partial read changes nothing old
        dates.save_calendar(2026, {"OK": {"primary": "2026-06-23"}}, complete=True, read_on="d2")
        assert dates.primary_date("OK", 2026) == "2026-06-23"
        assert dates.all_dates()["2026-OK"] == {"fec_primary": "2026-06-23"}
