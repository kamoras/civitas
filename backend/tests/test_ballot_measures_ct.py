"""Tests for Connecticut's ballot-measure strategy (ballot_measures_ct.py).

fixtures_ct_sample_ballots.json is REAL, fetched live 2026-09-28 from
portal.ct.gov (Secretary of the State):

- ballot_2026_eastford: extract_text() of Eastford's official November 3,
  2026 sample ballot — one sheet, offices only, no question column.
- ballot_2026_burlington: Burlington's 2026 sample ballot, which carries
  a LOCAL question (Regional School District 10 bonds) in the "Vote on
  the Questions(s)" column.
- ballot_2024_eastford: Eastford's November 5, 2024 sample ballot, which
  carries 2024's statewide no-excuse-absentee-voting amendment.
- index_hrefs: every town-ballots link on the Secretary's sample-ballot
  index page (town-ballots/ballots).
"""

import json
from pathlib import Path

from app.pipeline.fetch import ballot_measures_ct as ct

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ct_sample_ballots.json").read_text())
INDEX_HTML = "".join(f'<a href="{h}">x</a>' for h in FIXTURE["index_hrefs"])


class TestElectionPageUrl:
    def test_picks_this_years_november_page_whatever_its_slug_shape(self):
        assert ct.election_page_url(INDEX_HTML, 2026).endswith("/2026-november-town-election-ballots")
        assert ct.election_page_url(INDEX_HTML, 2024).endswith("/2024-general-election-sample-ballots")

    def test_primary_special_and_ppp_pages_are_never_chosen(self):
        for year in (2024, 2026):
            url = ct.election_page_url(INDEX_HTML, year)
            assert not any(w in url for w in ("primary", "special", "ppp"))

    def test_no_page_for_the_year_is_none(self):
        assert ct.election_page_url(INDEX_HTML, 2030) is None


class TestQuestionFreeBallot:
    def test_2026_eastford_proves_no_statewide_question(self):
        assert ct.is_question_free_state_ballot(FIXTURE["ballot_2026_eastford"], 2026)

    def test_a_local_question_proves_nothing(self):
        assert not ct.is_question_free_state_ballot(FIXTURE["ballot_2026_burlington"], 2026)

    def test_2024_statewide_amendment_is_seen(self):
        assert not ct.is_question_free_state_ballot(FIXTURE["ballot_2024_eastford"], 2024)

    def test_a_ballot_for_another_year_does_not_count(self):
        assert not ct.is_question_free_state_ballot(FIXTURE["ballot_2026_eastford"], 2028)

    def test_a_multi_sheet_ballot_does_not_count(self):
        text = FIXTURE["ballot_2026_eastford"].replace("Sheet1of1", "Sheet1of2")
        assert not ct.is_question_free_state_ballot(text, 2026)


class TestFetchMeasures:
    def _patch(self, monkeypatch, ballots):
        page = "".join(f'<a href="/-/media/{name}.pdf?rev=1&amp;hash=2">{name}</a>' for name in ballots)

        async def fake_text(client, limiter, url, label, **kw):
            return INDEX_HTML if url == ct.INDEX_URL else page

        async def fake_bytes(client, limiter, url, label, **kw):
            return url.encode()
        monkeypatch.setattr(ct, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(ct, "fetch_bytes_with_retry", fake_bytes)
        monkeypatch.setattr(
            ct, "_extract_text", lambda raw: FIXTURE[raw.decode().rsplit("/", 1)[1].split(".pdf")[0]],
        )

    async def test_a_question_free_ballot_after_a_local_one_is_confirmed_none(self, monkeypatch):
        self._patch(monkeypatch, ["ballot_2026_burlington", "ballot_2026_eastford"])
        assert await ct.fetch_measures(None, 2026) == []

    async def test_only_ballots_with_questions_is_not_concluded(self, monkeypatch):
        self._patch(monkeypatch, ["ballot_2026_burlington"])
        assert await ct.fetch_measures(None, 2026) is None

    async def test_statewide_question_year_is_not_concluded(self, monkeypatch):
        self._patch(monkeypatch, ["ballot_2024_eastford"])
        assert await ct.fetch_measures(None, 2024) is None
