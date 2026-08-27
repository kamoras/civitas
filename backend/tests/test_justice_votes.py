"""Tests for justice_votes.py's Oyez case-vote fetcher.

Real shape verified live against api.oyez.org (2026-08 audit): a case's
`decisions` array can hold more than one entry — Moyle v. United States
(2023-23-726) has both a "dismissal - improvidently granted" decision (5-4)
and a separate "per curiam" decision (6-3) for the same docket, with some
justices voting differently between the two. Flattening every decision's
votes wrote 2 rows per justice for cases like this — see JusticeVote's and
fetch_case_votes' docstrings.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline.fetch.justice_votes import fetch_case_votes


def _cases_list_response(cases):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=cases)
    return resp


def _case_detail_response(case_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=case_data)
    return resp


def _decided_timeline(date_ms):
    return [{"event": "Decided", "dates": [date_ms]}]


def _vote(name, identifier, vote, opinion="none"):
    return {"member": {"name": name, "identifier": identifier}, "vote": vote, "opinion_type": opinion}


class TestFetchCaseVotes:
    @pytest.mark.asyncio
    async def test_a_case_with_two_decisions_yields_one_row_per_justice(self):
        """Moyle's real shape: dismissal-DIG (5-4) then per curiam (6-3),
        Jackson voting majority in one and minority in the other. Only
        one row per justice should come out, from the LAST decision."""
        case_data = {
            "name": "Moyle v. United States",
            "timeline": _decided_timeline(1700000000),
            "decisions": [
                {
                    "decision_type": "dismissal - improvidently granted",
                    "majority_vote": 5, "minority_vote": 4,
                    "votes": [
                        _vote("John Roberts", "roberts", "majority"),
                        _vote("Ketanji Brown Jackson", "jackson", "majority"),
                    ],
                },
                {
                    "decision_type": "per curiam",
                    "majority_vote": 6, "minority_vote": 3,
                    "votes": [
                        _vote("John Roberts", "roberts", "majority"),
                        _vote("Ketanji Brown Jackson", "jackson", "minority", "dissent"),
                    ],
                },
            ],
        }
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            _cases_list_response([{"docket_number": "23-726", "href": "http://x/case"}]),
            _case_detail_response(case_data),
        ])

        votes = await fetch_case_votes(client, terms=["2023"])

        jackson_votes = [v for v in votes if v["justice_id"] == "jackson"]
        assert len(jackson_votes) == 1
        # The last decision (per curiam) is the one kept.
        assert jackson_votes[0]["vote"] == "minority"
        assert jackson_votes[0]["opinion_type"] == "dissent"
        assert jackson_votes[0]["majority_votes"] == 6
        assert jackson_votes[0]["minority_votes"] == 3

        roberts_votes = [v for v in votes if v["justice_id"] == "roberts"]
        assert len(roberts_votes) == 1

    @pytest.mark.asyncio
    async def test_a_normal_single_decision_case_is_unaffected(self):
        case_data = {
            "name": "A Normal Case",
            "timeline": _decided_timeline(1700000000),
            "decisions": [
                {
                    "decision_type": "majority opinion",
                    "majority_vote": 9, "minority_vote": 0,
                    "votes": [_vote("John Roberts", "roberts", "majority")],
                },
            ],
        }
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            _cases_list_response([{"docket_number": "23-1", "href": "http://x/case"}]),
            _case_detail_response(case_data),
        ])

        votes = await fetch_case_votes(client, terms=["2023"])

        assert len(votes) == 1
        assert votes[0]["justice_id"] == "roberts"
        assert votes[0]["case_id"] == "scotus-2023-23-1"

    @pytest.mark.asyncio
    async def test_a_case_with_no_decisions_yields_nothing(self):
        case_data = {"name": "Undecided", "timeline": _decided_timeline(1700000000), "decisions": []}
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            _cases_list_response([{"docket_number": "23-2", "href": "http://x/case"}]),
            _case_detail_response(case_data),
        ])

        votes = await fetch_case_votes(client, terms=["2023"])
        assert votes == []

    @pytest.mark.asyncio
    async def test_a_case_with_no_decided_date_yields_nothing(self):
        case_data = {
            "name": "Not Yet Decided",
            "timeline": [],
            "decisions": [{"votes": [_vote("John Roberts", "roberts", "majority")]}],
        }
        client = MagicMock()
        client.get = AsyncMock(side_effect=[
            _cases_list_response([{"docket_number": "23-3", "href": "http://x/case"}]),
            _case_detail_response(case_data),
        ])

        votes = await fetch_case_votes(client, terms=["2023"])
        assert votes == []
