"""Tests for Illinois's ballot-measure strategy (ballot_measures_il.py).

fixtures_il_voters_guide.json is REAL — trimmed copies of the State
Board of Elections' Illinois Voters' Guide, fetched live 2026-09-28: the
home page's hidden WebForms fields and election dropdown, and the
"Questions of Public Policy" page after selecting the 2026, 2024 and
2022 general elections.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_il as il

FIXTURE = json.loads((Path(__file__).parent / "fixtures_il_voters_guide.json").read_text())


class TestElectionOption:
    def test_finds_the_real_2026_general(self):
        assert il.election_option_value(FIXTURE["home"], 2026) == "71"

    def test_unlisted_year_is_none(self):
        assert il.election_option_value(FIXTURE["home"], 2030) is None

    def test_postback_carries_view_state_and_target(self):
        form = il.postback_form(FIXTURE["home"], "71")
        assert form["__VIEWSTATE"]
        assert form["__EVENTVALIDATION"]
        assert form["__EVENTTARGET"] == "ctl00$MainContent$ddlElection"
        assert form["ctl00$MainContent$ddlElection"] == "71"


class TestQuestionsPage:
    def test_boards_own_none_statement_is_empty(self):
        assert il.parse_questions_page(FIXTURE["questions_2026"]) == []

    @pytest.mark.parametrize("key", ["questions_2024", "questions_2022"])
    def test_listed_question_without_a_reader_is_never_none(self, key):
        # 2024 (three advisory questions) and 2022 (one amendment) each
        # list a PDF. Returning [] there would publish "no measures".
        assert il.parse_questions_page(FIXTURE[key]) is None

    def test_home_page_bounce_is_a_failure(self):
        # A lost session lands back on the home page, which has no
        # questions panel at all.
        assert il.parse_questions_page(FIXTURE["home"]) is None


class TestFetchMeasures:
    async def test_end_to_end_confirmed_none(self, monkeypatch):
        pages = {il.HOME_URL: FIXTURE["home"], il.QUESTIONS_URL: FIXTURE["questions_2026"]}

        async def fake_text(client, limiter, url, label, **kw):
            return pages[url]

        async def fake_post(client, limiter, method, url, **kw):
            assert method == "POST"
            assert kw["data"]["ctl00$MainContent$ddlElection"] == "71"
            return object()

        monkeypatch.setattr(il, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(il, "fetch_with_retry", fake_post)
        assert await il.fetch_measures(None, 2026) == []

    async def test_failed_postback_is_none(self, monkeypatch):
        async def fake_text(client, limiter, url, label, **kw):
            return FIXTURE["home"]

        async def fake_post(*a, **kw):
            return None

        monkeypatch.setattr(il, "fetch_text_with_retry", fake_text)
        monkeypatch.setattr(il, "fetch_with_retry", fake_post)
        assert await il.fetch_measures(None, 2026) is None
