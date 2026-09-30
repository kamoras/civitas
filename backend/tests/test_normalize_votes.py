"""Tests for vote normalization and party alignment logic."""

import pytest

from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    _infer_caucus_from_cosponsorship,
    _infer_caucus_from_votes,
    _infer_caucus_party,
    compute_party_split,
    extract_senator_vote,
    is_reconsider_switch,
    majority_leader_spans,
    normalize_recent_votes,
    normalize_votes,
    stamp_roll_call_outcome,
    vote_date_iso,
)


class TestPartyAlignment:
    """Party alignment determination logic."""

    @pytest.mark.parametrize(
        "party, vote, bill_leaning, expected",
        [
            pytest.param("R", "Yea", "R", True, id="republican_yea_on_republican_bill"),
            pytest.param("R", "Nay", "R", False, id="republican_nay_on_republican_bill"),
            pytest.param("R", "Yea", "D", False, id="republican_yea_on_democratic_bill"),
            pytest.param("R", "Nay", "D", True, id="republican_nay_on_democratic_bill"),
            pytest.param("D", "Yea", "D", True, id="democrat_yea_on_democratic_bill"),
            pytest.param("D", "Nay", "D", False, id="democrat_nay_on_democratic_bill"),
            pytest.param("I", "Yea", "R", None, id="independent_always_none_yea"),
            pytest.param("I", "Nay", "D", None, id="independent_always_none_nay"),
            pytest.param("R", "Not Voting", "R", None, id="not_voting_always_none"),
            pytest.param("R", "Yea", "bipartisan", None, id="bipartisan_always_none"),
            pytest.param("D", "Yea", None, None, id="no_party_leaning_is_none"),
        ],
    )
    def test_party_alignment(self, party, vote, bill_leaning, expected):
        assert _determine_party_alignment(party, vote, bill_leaning) is expected


class TestComputePartySplit:
    """Roll call party split computation."""

    def _make_members(self, r_yea, r_nay, d_yea, d_nay):
        members = []
        for _ in range(r_yea):
            members.append({"party": "R", "voteCast": "Yea"})
        for _ in range(r_nay):
            members.append({"party": "R", "voteCast": "Nay"})
        for _ in range(d_yea):
            members.append({"party": "D", "voteCast": "Yea"})
        for _ in range(d_nay):
            members.append({"party": "D", "voteCast": "Nay"})
        return {"members": members}

    @pytest.mark.parametrize("r_yea, r_nay, d_yea, d_nay, expected", [
        pytest.param(40, 5, 3, 42, "R", id="republican_bill"),
        pytest.param(2, 43, 40, 5, "D", id="democratic_bill"),
        pytest.param(30, 15, 35, 10, "bipartisan", id="bipartisan_bill"),
        pytest.param(2, 0, 1, 0, None, id="insufficient_data_returns_none"),
    ])
    def test_party_split(self, r_yea, r_nay, d_yea, d_nay, expected):
        data = self._make_members(r_yea=r_yea, r_nay=r_nay, d_yea=d_yea, d_nay=d_nay)
        assert compute_party_split(data) == expected


class TestExtractSenatorVote:
    """Senator vote extraction from roll call data."""

    def test_match_by_name_and_state(self):
        data = {
            "members": [
                {"lastName": "CRUZ", "state": "TX", "voteCast": "Yea"},
                {"lastName": "WARREN", "state": "MA", "voteCast": "Nay"},
            ]
        }
        assert extract_senator_vote(data, "Cruz", "TX") == "Yea"
        assert extract_senator_vote(data, "Warren", "MA") == "Nay"

    def test_case_insensitive(self):
        data = {"members": [{"lastName": "cruz", "state": "tx", "voteCast": "Nay"}]}
        assert extract_senator_vote(data, "CRUZ", "TX") == "Nay"

    def test_not_found_returns_none(self):
        data = {"members": [{"lastName": "SMITH", "state": "OH", "voteCast": "Yea"}]}
        assert extract_senator_vote(data, "Jones", "CA") is None

    def test_empty_data_returns_none(self):
        assert extract_senator_vote(None) is None
        assert extract_senator_vote({}) is None
        assert extract_senator_vote({"members": []}) is None

    def test_multi_word_last_name(self):
        """Multi-word last names like 'Cortez Masto' and 'Van Hollen' must match."""
        data = {
            "members": [
                {"lastName": "Cortez Masto", "state": "NV", "voteCast": "Yea"},
                {"lastName": "Van Hollen", "state": "MD", "voteCast": "Nay"},
                {"lastName": "Blunt Rochester", "state": "DE", "voteCast": "Yea"},
            ]
        }
        assert extract_senator_vote(data, "Cortez Masto", "NV") == "Yea"
        assert extract_senator_vote(data, "Van Hollen", "MD") == "Nay"
        assert extract_senator_vote(data, "Blunt Rochester", "DE") == "Yea"

    def test_unicode_accent_normalization(self):
        """Accented characters (e.g. Luján) must match unaccented form (Lujan)."""
        data = {
            "members": [
                {"lastName": "Lujan", "state": "NM", "voteCast": "Yea"},
            ]
        }
        assert extract_senator_vote(data, "Luján", "NM") == "Yea"
        assert extract_senator_vote(data, "Lujan", "NM") == "Yea"


class TestNormalizeVotes:
    """Full vote normalization pipeline."""

    def test_basic_vote_counting(self):
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "description": ""},
            {"billId": "hr2", "billName": "Bill 2", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "description": ""},
        ]
        votes = {"hr1": "Yea", "hr2": "Nay"}

        result = normalize_votes("B001", bills, votes, "D")

        assert result["totalVotes"] == 2
        assert len(result["keyVotes"]) == 2
        assert result["keyVotes"][0]["vote"] == "Yea"
        assert result["keyVotes"][1]["vote"] == "Nay"

    def test_party_loyalty_calculation(self):
        bills = [
            {"billId": f"hr{i}", "billName": f"Bill {i}", "policyArea": "DEFENSE",
             "stance": "x", "partyLeaning": "R", "partySplit": "R", "description": ""}
            for i in range(10)
        ]
        votes = {f"hr{i}": "Yea" if i < 8 else "Nay" for i in range(10)}

        result = normalize_votes("B001", bills, votes, "R")
        assert result["votedWithPartyCount"] == 8
        assert result["votedAgainstPartyCount"] == 2
        assert result["partyLoyaltyPct"] == 80.0

    def test_a_bills_content_lean_never_makes_a_vote_a_break(self):
        """With or against the party is how the parties actually voted on
        the roll call (partySplit). A vote whose split is unknown has no
        party label, whatever the bill's content lean: voting for a bill
        that reads Democratic is not a break for a Republican."""
        bills = [{"billId": "hr1", "billName": "Bill 1", "policyArea": "HEALTHCARE",
                  "stance": "x", "partyLeaning": "D", "description": ""}]
        result = normalize_votes("B001", bills, {"hr1": "Yea"}, "R")
        assert result["keyVotes"][0]["votedWithParty"] is None
        assert result["votedAgainstPartyCount"] == 0

    def test_no_votes_on_bills(self):
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "DEFENSE",
             "stance": "x", "partyLeaning": "R", "description": ""},
        ]
        result = normalize_votes("B001", bills, {}, "R")
        assert result["totalVotes"] == 0
        assert len(result["keyVotes"]) == 0
        assert result["partyLoyaltyPct"] == 0.0  # no votes = 0% measurable loyalty

    def test_independent_caucus_inference(self):
        """Independents who vote mostly with D should get effective party D."""
        bills = [
            {"billId": f"d{i}", "billName": f"D Bill {i}", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "partySplit": "D", "description": ""}
            for i in range(8)
        ] + [
            {"billId": f"r{i}", "billName": f"R Bill {i}", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "partySplit": "R", "description": ""}
            for i in range(3)
        ]
        # Senator votes Yea on D bills, Nay on R bills
        votes = {f"d{i}": "Yea" for i in range(8)}
        votes.update({f"r{i}": "Nay" for i in range(3)})

        result = normalize_votes("B001", bills, votes, "I")
        assert result["effectiveParty"] == "D"
        assert result["votedWithPartyCount"] > 0

    def test_independent_caucus_with_cosponsorship(self):
        """Cosponsorship data strengthens caucus inference for Independents."""
        bills = [
            {"billId": f"d{i}", "billName": f"D Bill {i}", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "description": ""}
            for i in range(6)
        ] + [
            {"billId": f"r{i}", "billName": f"R Bill {i}", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "description": ""}
            for i in range(3)
        ]
        votes = {f"d{i}": "Yea" for i in range(6)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        cosponsor = {"d_cosponsored": 15, "r_cosponsored": 2}

        result = normalize_votes("B001", bills, votes, "I", cosponsorship_profile=cosponsor)
        assert result["effectiveParty"] == "D"


class TestInferCaucusFromVotes:
    """Vote-based caucus inference (sub-function)."""

    def test_mostly_democratic_votes(self):
        bills = [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(7)
        ] + [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(3)
        ]
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        party, d, r = _infer_caucus_from_votes(bills, votes)
        assert party == "D"
        assert d > r

    def test_mostly_republican_votes(self):
        bills = [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(7)
        ] + [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(3)
        ]
        votes = {f"r{i}": "Yea" for i in range(7)}
        votes.update({f"d{i}": "Nay" for i in range(3)})
        party, d, r = _infer_caucus_from_votes(bills, votes)
        assert party == "R"
        assert r > d

    def test_insufficient_data(self):
        bills = [
            {"billId": "d0", "partyLeaning": "D"},
            {"billId": "r0", "partyLeaning": "R"},
        ]
        votes = {"d0": "Yea", "r0": "Nay"}
        party, _, _ = _infer_caucus_from_votes(bills, votes)
        assert party is None

    def test_bipartisan_ignored(self):
        bills = [
            {"billId": f"b{i}", "partyLeaning": "bipartisan"} for i in range(20)
        ]
        votes = {f"b{i}": "Yea" for i in range(20)}
        party, _, _ = _infer_caucus_from_votes(bills, votes)
        assert party is None


class TestInferCaucusFromCosponsorship:
    """Cosponsorship-based caucus inference."""

    @pytest.mark.parametrize(
        "d_cosponsored, r_cosponsored, expected_party",
        [
            pytest.param(20, 3, "D", id="strongly_democratic_cosponsorship"),
            pytest.param(2, 15, "R", id="strongly_republican_cosponsorship"),
            pytest.param(1, 1, None, id="insufficient_data"),
            pytest.param(0, 0, None, id="empty_profile"),
            pytest.param(10, 10, None, id="equal_cosponsorship"),
        ],
    )
    def test_infer_caucus(self, d_cosponsored, r_cosponsored, expected_party):
        profile = {"d_cosponsored": d_cosponsored, "r_cosponsored": r_cosponsored}
        party, d, r = _infer_caucus_from_cosponsorship(profile)
        assert party == expected_party
        assert d == d_cosponsored
        assert r == r_cosponsored


class TestInferCaucusPartyCombined:
    """Combined caucus inference using votes + cosponsorship."""

    def _make_party_bills(self, d_count, r_count):
        bills = [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(d_count)
        ] + [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(r_count)
        ]
        return bills

    def test_votes_only_backward_compat(self):
        """Without cosponsorship data (omitted or None), behaves like the old
        function."""
        bills = self._make_party_bills(7, 3)
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        assert _infer_caucus_party(bills, votes) == "D"
        assert _infer_caucus_party(bills, votes, None) == "D"

    def test_cosponsorship_reinforces_votes(self):
        """When both signals agree, result is the agreed party (the Sanders
        pattern: an Independent who caucuses with Democrats)."""
        bills = self._make_party_bills(7, 3)
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        cosponsor = {"d_cosponsored": 15, "r_cosponsored": 2}
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_cosponsorship_alone_sufficient(self):
        """With strong cosponsorship but weak voting data, cosponsorship wins."""
        bills = self._make_party_bills(2, 2)
        votes = {f"d{i}": "Yea" for i in range(2)}
        votes.update({f"r{i}": "Nay" for i in range(2)})
        cosponsor = {"d_cosponsored": 20, "r_cosponsored": 1}
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_disagreement_strong_margin_picks_winner(self):
        """When signals disagree but one is much stronger, it wins."""
        bills = self._make_party_bills(3, 7)
        votes = {f"d{i}": "Nay" for i in range(3)}
        votes.update({f"r{i}": "Yea" for i in range(7)})
        # Votes say R, but cosponsorship strongly says D
        cosponsor = {"d_cosponsored": 25, "r_cosponsored": 2}
        # Combined: D = 3 + 25*1.5 = 40.5, R = 7 + 2*1.5 = 10
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_disagreement_weak_margin_returns_none(self):
        """When signals disagree with similar strength, returns None."""
        # Yea on D bills → d_support, Yea on R bills → r_support
        # So votes: d_support=4, r_support=6 → vote_party=R
        bills = self._make_party_bills(4, 6)
        votes = {f"d{i}": "Yea" for i in range(4)}
        votes.update({f"r{i}": "Yea" for i in range(6)})
        # Cosponsorship leans D → cosponsor_party=D (disagrees with votes)
        cosponsor = {"d_cosponsored": 6, "r_cosponsored": 4}
        # Combined: D = 4 + 6*1.5 = 13, R = 6 + 4*1.5 = 12
        # Margin = 1/25 = 0.04 < 0.2 threshold → None
        result = _infer_caucus_party(bills, votes, cosponsor)
        assert result is None


class TestMajorityLeaderReconsiderSwitch:
    """The majority leader switches to Nay on a motion about to fail so they
    can move to reconsider (Senate Rule XIII; House Rule XIX cl. 2). That
    Nay is not a break with party — but only for the majority leader, only
    while they hold the office, and only when the chamber recorded the
    motion as rejected."""

    THUNE_TENURES = [
        {"title": "Senate Minority Whip", "chamber": "senate", "start": "2023-01-03", "end": "2025-01-03"},
        {"title": "Senate Majority Leader", "chamber": "senate", "start": "2025-01-03", "end": None},
    ]
    SCHUMER_TENURES = [
        {"title": "Senate Majority Leader", "chamber": "senate", "start": "2023-01-03", "end": "2025-01-03"},
        {"title": "Senate Minority Leader", "chamber": "senate", "start": "2025-01-03", "end": None},
    ]

    @staticmethod
    def _rc(leaning="R", rejected=True, date="October 14, 2025,  05:34 PM", bill_id="HR5371"):
        bill = {"billId": bill_id, "billName": "Continuing appropriations",
                "policyArea": "BUDGET", "partyLeaning": leaning, "description": ""}
        stamp_roll_call_outcome(bill, {"rejected": rejected, "voteDate": date})
        bill["partySplit"] = leaning  # the split a real roll call's members would give
        return bill

    def _alignment(self, bill, vote, party="R", title="Senate Majority Leader", tenures=None):
        spans = majority_leader_spans(title, tenures if tenures is not None else self.THUNE_TENURES)
        return _determine_party_alignment(
            party, vote, bill["partyLeaning"],
            reconsider_switch=is_reconsider_switch(bill, spans),
        )

    @pytest.mark.parametrize("rc_kwargs, vote, expected", [
        pytest.param({}, "Nay", None, id="nay_on_rejected_motion_is_not_a_break"),
        pytest.param({"rejected": False}, "Nay", False, id="nay_on_passed_motion_stays_a_break"),
        pytest.param({"rejected": None}, "Nay", False, id="unknown_result_is_not_exempted"),
        pytest.param({}, "Yea", True, id="yea_with_party_still_counts_with_party"),
        pytest.param({"leaning": "D"}, "Nay", True, id="nay_on_other_partys_rejected_motion_counts_with_party"),
        # The mirror case: the other party's motion carried against the
        # leader's own party, and the leader switched to Yea — the
        # prevailing side — to be able to move to reconsider.
        pytest.param({"leaning": "D", "rejected": False}, "Yea", None,
                     id="yea_on_motion_carried_over_own_party_is_not_a_break"),
        pytest.param({"rejected": False}, "Yea", True, id="yea_on_carried_motion_own_party_backed_counts_with_party"),
        # Thune was minority whip in 2024, not majority leader.
        pytest.param({"date": "June 4, 2024,  11:00 AM"}, "Nay", False, id="vote_outside_tenure_is_a_break"),
    ])
    def test_majority_leader(self, rc_kwargs, vote, expected):
        assert self._alignment(self._rc(**rc_kwargs), vote) is expected

    @pytest.mark.parametrize("rc_kwargs, vote, expected", [
        pytest.param({"leaning": "R", "rejected": False, "date": "March 14, 2025,  01:30 PM"}, "Yea", False,
                     id="mirror_case_needs_the_majority_leader"),
        # Schumer's March 2025 CR cloture: a real break, and it stays one.
        pytest.param({"leaning": "D", "rejected": True, "date": "March 14, 2025,  01:30 PM"}, "Nay", False,
                     id="minority_leader_is_never_exempted"),
        # Schumer led the majority in 2023-24; his current title is minority
        # leader, but the tenure dates decide.
        pytest.param({"leaning": "D", "date": "2024-02-07"}, "Nay", None,
                     id="vote_inside_a_past_majority_leader_tenure_is_exempted"),
        pytest.param({"leaning": "D", "date": "2025-01-03"}, "Nay", False,
                     id="handover_day_belongs_to_the_new_role"),
    ])
    def test_former_majority_leader_now_minority_leader(self, rc_kwargs, vote, expected):
        bill = self._rc(**rc_kwargs)
        assert self._alignment(
            bill, vote, party="D", title="Senate Minority Leader", tenures=self.SCHUMER_TENURES,
        ) is expected

    def test_speaker_is_not_exempted(self):
        bill = self._rc(date="2026-06-30")
        assert self._alignment(bill, "Nay", title="Speaker of the House", tenures=[
            {"title": "Speaker of the House", "chamber": "house", "start": "2023-10-25", "end": None},
        ]) is False

    def test_assistant_majority_leader_is_a_different_office(self):
        assert majority_leader_spans("Assistant Senate Majority Leader", [
            {"title": "Assistant Senate Majority Leader", "start": "2021-01-20", "end": None},
        ]) == []

    def test_missing_tenure_data_falls_back_to_current_title(self):
        assert majority_leader_spans("House Majority Leader", None)
        assert self._alignment(self._rc(date="2026-06-30"), "Nay",
                               title="House Majority Leader", tenures=[]) is None

    def test_no_title_and_no_tenure_means_no_spans(self):
        assert majority_leader_spans(None, None) == []

    def test_vote_date_formats(self):
        assert vote_date_iso("October 14, 2025,  05:34 PM") == "2025-10-14"
        assert vote_date_iso("2026-06-30") == "2026-06-30"
        assert vote_date_iso("") is None
        assert vote_date_iso("sometime") is None

    def test_aggregate_counts_skip_the_switch(self):
        spans = majority_leader_spans("Senate Majority Leader", self.THUNE_TENURES)
        bills = [self._rc(bill_id=f"rej{i}") for i in range(3)]
        bills += [self._rc(rejected=False, bill_id=f"pass{i}") for i in range(2)]
        bills += [self._rc(rejected=False, bill_id=f"yea{i}") for i in range(5)]
        votes = {f"rej{i}": "Nay" for i in range(3)}
        votes.update({f"pass{i}": "Nay" for i in range(2)})
        votes.update({f"yea{i}": "Yea" for i in range(5)})

        result = normalize_votes("T000250", bills, votes, "R", leader_spans=spans)
        assert result["votedWithPartyCount"] == 5
        assert result["votedAgainstPartyCount"] == 2  # only the Nays on passed motions
        by_id = {v["billId"]: v for v in result["keyVotes"]}
        assert by_id["rej0"]["votedWithParty"] is None
        assert by_id["rej0"]["reconsiderSwitch"] is True
        assert by_id["pass0"]["reconsiderSwitch"] is False

        # Without the leader's spans the same record shows five breaks.
        plain = normalize_votes("X000001", bills, votes, "R")
        assert plain["votedAgainstPartyCount"] == 5

    def test_recent_votes_path_applies_the_same_rule(self):
        spans = majority_leader_spans("Senate Majority Leader", self.THUNE_TENURES)
        bill = self._rc()
        bill["rcKey"] = "119-1-571"
        rc_map = {"119-1-571": {"members": [
            {"lastName": "Thune", "state": "SD", "party": "R", "voteCast": "Nay"},
        ]}}
        votes = normalize_recent_votes([bill], rc_map, "Thune", "SD", "R", leader_spans=spans)
        assert votes[0]["votedWithParty"] is None
        assert votes[0]["reconsiderSwitch"] is True

        votes = normalize_recent_votes([bill], rc_map, "Thune", "SD", "R")
        assert votes[0]["votedWithParty"] is False


def test_partisan_depth_ignores_reconsider_switch_votes():
    from app.pipeline.analyze.party_platform import _alignments_from_votes

    vote = {"vote": "Nay", "policyArea": "BUDGET", "partyLeaning": "R", "policyAreas": []}
    assert _alignments_from_votes({"keyVotes": [vote, vote]})[0]["alignment"] == "D"
    switched = {**vote, "reconsiderSwitch": True}
    assert _alignments_from_votes({"keyVotes": [switched, switched]}) == []
