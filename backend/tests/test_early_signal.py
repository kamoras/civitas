"""Tests for early_signal.py — drafting a hedged, primary-source-only
ActionIssue from a Senate roll-call vote before press coverage exists."""

from datetime import timedelta
from unittest.mock import patch

from app.models import ActionIssue, ActionIssueStatus
from app.pipeline.analyze import early_signal as es
from app.time_utils import utcnow


def _vote(
    congress=119, session=1, roll_number=42, question="On Passage of the Bill",
    vote_title="On Passage", document_title="A bill to do a thing",
    vote_date="2026-08-30", yeas=60, nays=40,
) -> dict:
    members = (
        [{"voteCast": "Yea"} for _ in range(yeas)]
        + [{"voteCast": "Nay"} for _ in range(nays)]
    )
    return {
        "congress": congress,
        "session": session,
        "rollNumber": roll_number,
        "voteTitle": vote_title,
        "voteDate": vote_date,
        "question": question,
        "documentTitle": document_title,
        "documentName": "S.1",
        "members": members,
    }


def _house_vote(
    year=2026, congress=119, session=1, roll_number=42, question="On Passage",
    vote_title="On Passage", document_title="A bill to do a thing",
    vote_date="2026-08-30", yeas=250, nays=180,
) -> dict:
    members = (
        [{"voteCast": "Yea"} for _ in range(yeas)]
        + [{"voteCast": "Nay"} for _ in range(nays)]
    )
    return {
        "year": year,
        "congress": congress,
        "session": session,
        "rollNumber": roll_number,
        "voteTitle": vote_title,
        "voteDate": vote_date,
        "question": question,
        "documentTitle": document_title,
        "documentName": "H.R.1",
        "members": members,
        "chamber": "House",
    }


def _rule(
    title="Process for Authorizing Seasonal Migratory Game Bird Hunting",
    abstract="The Service is changing the administrative process.",
    document_number="2026-17733",
    html_url="https://www.federalregister.gov/documents/2026/08/31/2026-17733/process",
    publication_date="2026-08-31",
    agencies=None,
) -> dict:
    return {
        "title": title,
        "abstract": abstract,
        "documentNumber": document_number,
        "htmlUrl": html_url,
        "publicationDate": publication_date,
        "agencies": agencies if agencies is not None else ["Interior Department"],
    }


class TestIsFinalPassage:
    def test_on_passage_of_the_bill_matches(self):
        assert es._is_final_passage(_vote(question="On Passage of the Bill")) is True

    def test_on_the_joint_resolution_matches(self):
        assert es._is_final_passage(_vote(question="On the Joint Resolution")) is True

    def test_nomination_does_not_match(self):
        assert es._is_final_passage(_vote(question="On the Nomination")) is False

    def test_cloture_does_not_match(self):
        assert es._is_final_passage(
            _vote(question="On the Motion to Invoke Cloture")
        ) is False

    def test_amendment_does_not_match(self):
        assert es._is_final_passage(_vote(question="On the Amendment")) is False

    def test_house_on_passage_matches(self):
        assert es._is_final_passage(_house_vote(question="On Passage")) is True

    def test_house_suspend_the_rules_and_pass_matches(self):
        assert es._is_final_passage(
            _house_vote(question="On Motion to Suspend the Rules and Pass")
        ) is True

    def test_house_motion_to_recommit_does_not_match(self):
        assert es._is_final_passage(
            _house_vote(question="On Motion to Recommit", vote_title="On Motion to Recommit")
        ) is False

    def test_house_previous_question_does_not_match(self):
        assert es._is_final_passage(
            _house_vote(
                question="On Ordering the Previous Question",
                vote_title="On Ordering the Previous Question",
            )
        ) is False


class TestChamberLabels:
    def test_senate_vote_labels(self):
        assert es._chamber_labels(_vote()) == ("Senate", "senators")

    def test_house_vote_labels(self):
        assert es._chamber_labels(_house_vote()) == ("House of Representatives", "representatives")


class TestVoteUrl:
    def test_senate_vote_url(self):
        url = es._vote_url(_vote(congress=119, session=1, roll_number=42))
        assert url == es._senate_vote_url(119, 1, 42)

    def test_house_vote_url(self):
        url = es._vote_url(_house_vote(year=2026, roll_number=42))
        assert url == "https://clerk.house.gov/evs/2026/roll42.xml"


class TestVoteMarginRatio:
    def test_lopsided_vote(self):
        assert es._vote_margin_ratio(_vote(yeas=90, nays=10)) == 0.8

    def test_tied_vote(self):
        assert es._vote_margin_ratio(_vote(yeas=50, nays=50)) == 0.0

    def test_no_yea_nay_votes_is_zero(self):
        vote = _vote(yeas=0, nays=0)
        assert es._vote_margin_ratio(vote) == 0.0


class TestCheckRollCallSignals:
    def _mock_llm_result(self, title="Senate passes the bill", summary="text", facts=None):
        return {"title": title, "summary": summary, "facts": facts or ["A fact stated in the record."]}

    def test_procedural_vote_is_rejected(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("PROCEDURAL", 0.9)):
            created = es.check_roll_call_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_non_final_passage_vote_is_rejected(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_vote(question="On the Nomination")]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)):
            created = es.check_roll_call_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_qualifying_vote_creates_a_developing_issue(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            created = es.check_roll_call_signals(db_session)
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.status == ActionIssueStatus.DEVELOPING
        assert row.source_type == "senate_roll_call_vote"
        assert row.primary_source_url
        assert row.confirmation_deadline is not None
        assert row.is_current is True

    def test_same_vote_is_not_created_twice(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            es.check_roll_call_signals(db_session)
            created_second_pass = es.check_roll_call_signals(db_session)
        assert created_second_pass == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_qualifying_house_vote_creates_a_developing_issue(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_house_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            created = es.check_roll_call_signals(db_session)
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.source_type == "house_roll_call_vote"
        assert row.primary_source_url == "https://clerk.house.gov/evs/2026/roll42.xml"

    def test_senate_and_house_votes_sharing_a_roll_number_both_created(self, db_session):
        """recent_roll_call_key is congress-session-rollNumber only — House
        and Senate roll numbers are independent sequences, so a same-
        numbered pair from each chamber must not collide in the per-run
        dedup set."""
        with patch.object(
            es, "_fetch_recent_votes",
            return_value=[_vote(roll_number=42), _house_vote(roll_number=42)],
        ), patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            created = es.check_roll_call_signals(db_session)
        assert created == 2
        source_types = {row.source_type for row in db_session.query(ActionIssue).all()}
        assert source_types == {"senate_roll_call_vote", "house_roll_call_vote"}

    def test_generation_that_never_grounds_creates_nothing(self, db_session):
        """A generation that keeps fabricating a number outside the vote
        record must not create a row, not just a low-quality one."""
        bad_result = {
            "title": "Senate passes the bill",
            "summary": "The Senate voted 999-1 on the measure.",
            "facts": ["The vote passed 999-1."],
        }
        with patch.object(es, "_fetch_recent_votes", return_value=[_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)), \
                patch.object(es, "call_llm", return_value=bad_result):
            created = es.check_roll_call_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0


class TestCheckFederalRegisterSignals:
    def _mock_llm_result(self, title="Interior changes hunting process", summary="text", facts=None):
        return {"title": title, "summary": summary, "facts": facts or ["A fact stated in the record."]}

    def test_qualifying_rule_creates_a_developing_issue(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            created = es.check_federal_register_signals(db_session)
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.status == ActionIssueStatus.DEVELOPING
        assert row.source_type == "federal_register_significant_rule"
        assert row.primary_source_url == _rule()["htmlUrl"]
        assert row.confirmation_deadline is not None
        assert row.is_current is True

    def test_missing_document_number_is_skipped(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule(document_number="")]):
            created = es.check_federal_register_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_same_rule_is_not_created_twice(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]), \
                patch.object(es, "call_llm", return_value=self._mock_llm_result()):
            es.check_federal_register_signals(db_session)
            created_second_pass = es.check_federal_register_signals(db_session)
        assert created_second_pass == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_generation_that_never_grounds_creates_nothing(self, db_session):
        bad_result = {
            "title": "Interior changes hunting process",
            "summary": "The rule affects 10 million acres nationwide.",
            "facts": ["It affects 10 million acres."],
        }
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]), \
                patch.object(es, "call_llm", return_value=bad_result):
            created = es.check_federal_register_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0


class TestRuleSourceText:
    def test_includes_agencies_title_and_abstract(self):
        text = es._rule_source_text(_rule())
        assert "2026-17733" in text
        assert "Interior Department" in text
        assert "Process for Authorizing Seasonal Migratory Game Bird Hunting" in text
        assert "changing the administrative process" in text

    def test_missing_agencies_falls_back(self):
        text = es._rule_source_text(_rule(agencies=[]))
        assert "an unspecified agency" in text


class TestExpireStaleDevelopingIssues:
    def _make_developing_row(self, db_session, deadline_hours_from_now: float) -> ActionIssue:
        row = ActionIssue(
            date="2026-08-30", rank=999, title="A developing story", summary="s",
            is_current=True, status=ActionIssueStatus.DEVELOPING,
            source_type="senate_roll_call_vote", primary_source_url="https://example.com/vote.xml",
            confirmation_deadline=utcnow() + timedelta(hours=deadline_hours_from_now),
        )
        db_session.add(row)
        db_session.flush()
        return row

    def test_past_deadline_is_retired(self, db_session):
        row = self._make_developing_row(db_session, deadline_hours_from_now=-1)
        expired = es.expire_stale_developing_issues(db_session, utcnow())
        assert expired == 1
        assert row.is_current is False
        # Never deletes, never changes status — same "render the true
        # state" mechanic as BallotMeasure.status / _retire_untouched_issues.
        assert row.status == ActionIssueStatus.DEVELOPING

    def test_within_deadline_is_left_alone(self, db_session):
        row = self._make_developing_row(db_session, deadline_hours_from_now=24)
        expired = es.expire_stale_developing_issues(db_session, utcnow())
        assert expired == 0
        assert row.is_current is True

    def test_confirmed_issue_is_never_touched(self, db_session):
        """A CONFIRMED issue must never be expired even if it happens to
        carry a stale confirmation_deadline from before promotion."""
        row = ActionIssue(
            date="2026-08-30", rank=1, title="A confirmed story", summary="s",
            is_current=True, status=ActionIssueStatus.CONFIRMED,
            confirmation_deadline=utcnow() - timedelta(hours=1),
        )
        db_session.add(row)
        db_session.flush()
        expired = es.expire_stale_developing_issues(db_session, utcnow())
        assert expired == 0
        assert row.is_current is True
