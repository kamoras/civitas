"""Tests for direct parsing of California's own Voter Information Guide PDF.

fixtures_ca_vig_page4.json is REAL — page.extract_words() output (text,
top, x0, x1 only) from the real 2024 general-election VIG PDF, page index
4, fetched directly during development. Not synthesized: this is the same
page that carries Proposition 2 (parses cleanly end to end, including
yes_means/no_means) and Proposition 3 (a real row-gap misclassification on
a wrapped line — see _looks_corrupted's docstring — verified here to be
safely dropped rather than shipped scrambled).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import ballot_measures_ca as ca

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ca_vig_page4.json").read_text())


def _fake_page(words):
    return SimpleNamespace(extract_words=lambda: words)


# ── real-page parsing ──────────────────────────────────────────────────


def test_parses_two_real_propositions_from_one_page():
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    assert [r["number"] for r in results] == ["2", "3"]


def test_prop2_parses_cleanly_including_yes_no_framing():
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    prop2 = next(r for r in results if r["number"] == "2")
    assert prop2["origin"] == "the Legislature"
    assert prop2["official_summary"].startswith("Authorizes $10 billion")
    assert prop2["fiscal_impact"].startswith("Increased state costs")
    assert prop2["yes_means"] == (
        "The state could borrow $10 billion to build new or renovate "
        "existing public school and community college facilities."
    )
    assert prop2["no_means"] == (
        "The state could not borrow $10 billion to build new or renovate "
        "existing public school and community college facilities."
    )


def test_prop3_yes_no_framing_dropped_as_corrupted_not_guessed():
    """Prop 3's yes_means/no_means genuinely misclassify on the real PDF
    (see _looks_corrupted). The rest of the proposition is unaffected —
    only the two suspect fields are dropped, not the whole record."""
    results = ca.parse_quick_reference_page(_fake_page(FIXTURE))
    prop3 = next(r for r in results if r["number"] == "3")
    assert prop3["yes_means"] is None
    assert prop3["no_means"] is None
    assert prop3["official_summary"].startswith("Amends California Constitution")


def test_page_with_no_quick_reference_content_returns_empty():
    assert ca.parse_quick_reference_page(_fake_page([])) == []


# ── corruption detection ────────────────────────────────────────────────


def test_looks_corrupted_flags_missing_terminal_punctuation():
    assert ca._looks_corrupted("The state could borrow money") is True


def test_looks_corrupted_flags_internal_repeat():
    assert ca._looks_corrupted("no change in who can marry no change in who marry.") is True


def test_looks_corrupted_false_for_clean_sentence():
    assert ca._looks_corrupted("The state could borrow $10 billion to build schools.") is False


def test_looks_corrupted_does_not_compare_across_fields():
    """The false-positive this deliberately avoids: Prop 2's real
    yes_means/no_means legitimately share a long tail ("...build new or
    renovate existing public school and community college facilities.")
    differing only in "could"/"could not" — each is independently clean,
    so neither should be flagged just because the other looks similar."""
    yes = "The state could borrow $10 billion to build new or renovate existing public school and community college facilities."
    no = "The state could not borrow $10 billion to build new or renovate existing public school and community college facilities."
    assert ca._looks_corrupted(yes) is False
    assert ca._looks_corrupted(no) is False


# ── raw+detail shape for election_pipeline._upsert_measure ─────────────


def test_to_measure_produces_upsert_ready_shape():
    parsed = {
        "number": "2", "title": "T", "origin": "the Legislature",
        "official_summary": "S", "fiscal_impact": "F",
        "yes_means": "Y", "no_means": "N",
    }
    measure = ca._to_measure(parsed, "2026-11-03", "https://example.com/vig.pdf")
    assert measure["id"] == "CA-2026-11-03-2"
    assert measure["state"] == "CA"
    assert measure["election_date"] == "2026-11-03"
    assert measure["official_title"] == "T"
    assert measure["source_url"] == "https://example.com/vig.pdf"


def test_to_measure_falls_back_to_proposition_number_when_title_missing():
    parsed = {
        "number": "7", "title": "", "origin": None, "official_summary": "S",
        "fiscal_impact": None, "yes_means": None, "no_means": None,
    }
    measure = ca._to_measure(parsed, "2026-11-03", "https://example.com/vig.pdf")
    assert measure["title"] == "Proposition 7"


# ── fetch orchestration ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_ca_measures_returns_cached_result_without_a_fetch(db_session):
    from app.pipeline.cache import api_cache_set

    api_cache_set(db_session, "ca_vig", "ca-vig-2026", {"measures": [{"id": "CA-x"}]})

    async def fail_get(*a, **kw):
        raise AssertionError("should not fetch — cache hit")

    client = SimpleNamespace(get=fail_get)
    result = await ca.fetch_ca_measures(client, db_session, 2026, "2026-11-03")
    assert result == [{"id": "CA-x"}]


@pytest.mark.asyncio
async def test_fetch_ca_measures_returns_none_on_http_failure(db_session):
    import httpx

    class FakeResponse:
        status_code = 403

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=self)

    async def fake_get(*a, **kw):
        return FakeResponse()

    client = SimpleNamespace(get=fake_get)
    result = await ca.fetch_ca_measures(client, db_session, 2026, "2026-11-03")
    assert result is None
