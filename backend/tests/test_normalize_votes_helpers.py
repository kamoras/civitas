"""Tests for normalize_votes helper functions: roll-call resolution and the
rcKey-first vote lookup added in the 2026-07 platform review."""

from app.pipeline.transform.normalize_votes import (
    find_house_roll_call,
    normalize_recent_votes,
)


class TestFindHouseRollCall:
    def test_year_extracted_from_url(self):
        actions = [{
            "text": "On passage Passed House. Roll no. 123",
            "recordedVotes": [{
                "chamber": "House",
                "rollNumber": 123,
                "url": "https://clerk.house.gov/evs/2026/roll123.xml",
            }],
        }]
        assert find_house_roll_call(actions) == {"year": 2026, "rollCallNumber": 123}

    def test_missing_url_year_falls_back_to_vote_date(self):
        """House Clerk roll numbers restart every year, so guessing a fixed
        year resolves to a REAL, different roll call — the fallback must
        come from the vote's own date, never a hardcoded year."""
        actions = [{
            "text": "On passage Passed House. Roll no. 45",
            "actionDate": "2026-03-14",
            "recordedVotes": [{
                "chamber": "House",
                "rollNumber": 45,
                "url": "https://clerk.house.gov/some-other-format",
            }],
        }]
        assert find_house_roll_call(actions) == {"year": 2026, "rollCallNumber": 45}

    def test_no_year_anywhere_returns_none(self):
        actions = [{
            "text": "On passage Passed House. Roll no. 45",
            "recordedVotes": [{
                "chamber": "House",
                "rollNumber": 45,
                "url": "https://clerk.house.gov/some-other-format",
            }],
        }]
        assert find_house_roll_call(actions) is None


class TestRcKeyLookup:
    def test_two_votes_on_same_document_resolve_distinct_roll_calls(self):
        """Cloture + confirmation on one nomination share a documentName
        (billId); the rcKey join must resolve each classified vote to ITS
        roll call, not whichever one was stored last."""
        cloture_rc = {
            "members": [{"lastName": "Doe", "state": "NY",
                         "voteCast": "Yea", "firstName": "Jane", "party": "D"}],
            "voteDate": "2026-05-01",
        }
        confirm_rc = {
            "members": [{"lastName": "Doe", "state": "NY",
                         "voteCast": "Nay", "firstName": "Jane", "party": "D"}],
            "voteDate": "2026-05-02",
        }
        rc_map = {"119-1-101": cloture_rc, "119-1-102": confirm_rc}
        classified = [
            {"billId": "PN230", "rcKey": "119-1-101", "billName": "Nomination X",
             "date": "2026-05-01", "policyArea": "PROCEDURAL", "policyAreas": [],
             "partyAlignmentWeight": 0.0, "stance": "nomination",
             "partyLeaning": "bipartisan", "description": ""},
            {"billId": "PN230", "rcKey": "119-1-102", "billName": "Nomination X",
             "date": "2026-05-02", "policyArea": "PROCEDURAL", "policyAreas": [],
             "partyAlignmentWeight": 0.0, "stance": "nomination",
             "partyLeaning": "bipartisan", "description": ""},
        ]
        votes = normalize_recent_votes(classified, rc_map, "Doe", "NY", "D")
        assert [v["vote"] for v in votes] == ["Yea", "Nay"]
