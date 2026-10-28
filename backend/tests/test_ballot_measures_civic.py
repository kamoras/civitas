"""Statewide measures from the VIP ballot (ballot_measures_civic.py), on
the shape Google Civic answered for Michigan on 2026-10-28."""

import pytest

from app.pipeline.fetch import ballot_measures_civic as m
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

P1 = {"kind": "measure", "title": "Proposal 2026-1\n\nA proposal to convene a constitutional convention for the "
      "purpose of drafting a general revision of the state constitution", "subtitle": "PROPOSAL",
      "text": "Shall a convention of elected delegates be convened in 2027 to draft a general revision of the "
      "State Constitution for presentation to the state’s voters for their approval or rejection?"}
P2 = {"kind": "measure", "title": "Proposal 2026-2\n\nA proposed initiated law to prohibit campaign contributions",
      "text": "The proposal would:\n• Prohibit ...\n\nShould this proposal be adopted?"}
LOCAL = {"kind": "measure", "title": "CITY OF LANSING ESSENTIAL SERVICES MILLAGE RESTORATION PROPOSAL", "text": "..."}
SOURCE = {"title_authority": "Michigan Bureau of Elections",
          "origin_when_title_says": {"initiated": "Michigan voters (initiative petition)"}}


def test_only_what_every_district_s_ballot_carries_is_statewide():
    parsed = m.parse_referenda([[P1, P2, LOCAL], [P1, P2], [P2, P1]], SOURCE)
    assert [p["number"] for p in parsed] == ["2026-1", "2026-2"]
    first, second = parsed
    assert first["official_title"].startswith("A proposal to convene") and first["title"] == first["official_title"]
    assert first["official_summary"] == P1["text"]  # verbatim
    assert first["republished_by"] == "the Voting Information Project"
    assert first["title_authority"] == "Michigan Bureau of Elections"
    assert first["origin"] is None and second["origin"] == "Michigan voters (initiative petition)"
    assert all(p["yes_means"] is None and p["fiscal_impact"] is None for p in parsed)


def test_too_few_answers_say_nothing():
    assert m.parse_referenda([[P1], [P1]], SOURCE) == []


@pytest.mark.asyncio
async def test_no_ballot_yet_is_not_yet_published_never_a_confirmed_none(monkeypatch):
    monkeypatch.setattr(m, "is_configured", lambda: True)

    async def no_general(client, state, year):
        return None

    monkeypatch.setattr(m, "general_election_id", no_general)
    with pytest.raises(NotYetPublished):
        await m.fetch_measures(None, 2026, "MI", SOURCE)
