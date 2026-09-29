"""Tests for early_signal.py — drafting a hedged, primary-source-only
ActionIssue from a Senate roll-call vote before press coverage exists."""

import json
from datetime import timedelta
from unittest.mock import patch

from app.models import ActionIssue, ActionIssueStatus, Senator, SponsoredBill
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
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)):
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
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)):
            es.check_roll_call_signals(db_session)
            created_second_pass = es.check_roll_call_signals(db_session)
        assert created_second_pass == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_qualifying_house_vote_creates_a_developing_issue(self, db_session):
        with patch.object(es, "_fetch_recent_votes", return_value=[_house_vote()]), \
                patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)):
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
        ), patch.object(es, "classify_policy_area", return_value=("DEFENSE", 0.9)):
            created = es.check_roll_call_signals(db_session)
        assert created == 2
        source_types = {row.source_type for row in db_session.query(ActionIssue).all()}
        assert source_types == {"senate_roll_call_vote", "house_roll_call_vote"}

    def test_the_draft_is_the_vote_record_in_a_template(self, db_session):
        """2026-09-28: the model's draft of S. 4668's passage called 77-22
        "a narrow" vote. The draft now states only the record: measure,
        question, result, tally, date. The issue carries the refresh's
        date, never the Senate's raw one, which sorted after every ISO date
        and hid the rest of the Action Center."""
        vote = _vote(
            roll_number=250, yeas=77, nays=22, vote_date="September 28, 2026,  09:42 PM",
            document_title="A bill to protect the name, image, and likeness rights of student athletes.",
        )
        vote.update(documentName="S. 4668", result="Bill Passed")
        with patch.object(es, "_fetch_recent_votes", return_value=[vote]), \
                patch.object(es, "classify_policy_area", return_value=("EDUCATION", 0.9)):
            assert es.check_roll_call_signals(db_session, "2026-09-29") == 1
        row = db_session.query(ActionIssue).one()
        assert row.title == "Senate vote on S. 4668: Bill Passed, 77-22"
        assert row.date == "2026-09-29" and row.primary_article_date == "2026-09-28"
        assert "narrow" not in f"{row.title} {row.summary} {row.facts}"
        assert json.loads(row.related_bill_ids) == [{"name": "S. 4668", "id": "S.4668"}]
        assert json.loads(row.facts)[0] == "Tally: 77 yea, 22 nay, 0 not voting."


def _reported(db_session, title, summary="", bills=None):
    row = ActionIssue(
        date="2026-09-29", rank=1, title=title, summary=summary, facts="[]", source_urls="[]",
        source_names='["NPR"]', is_current=True, status=ActionIssueStatus.CONFIRMED,
        related_bill_ids=json.dumps(bills or []),
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestCoveredVotes:
    """2026-09-28: news of S. 4668's passage ("the Protect College Sports
    Act") became an issue first; the roll-call draft then made a second,
    thinner issue about the same vote."""

    def _vote(self):
        vote = _vote(roll_number=250, yeas=77, nays=22)
        vote.update(documentName="S. 4668", result="Bill Passed")
        return vote

    def _bill(self, db_session):
        db_session.add(Senator(id="S1", name="A Senator", state="TX", party="R", is_current=True))
        db_session.add(SponsoredBill(senator_id="S1", bill_id="S.4668", title="Protect College Sports Act of 2026",
                                     congress=119))
        db_session.commit()

    def test_a_vote_whose_bill_an_issue_names_by_short_title_is_not_drafted(self, db_session):
        self._bill(db_session)
        _reported(db_session, "The Senate passes the Protect College Sports Act, but the bill's future is unclear")
        with patch.object(es, "_fetch_recent_votes", return_value=[self._vote()]), \
                patch.object(es, "classify_policy_area", return_value=("EDUCATION", 0.9)):
            assert es.check_roll_call_signals(db_session, "2026-09-29") == 0

    def test_a_vote_whose_bill_an_issue_records_is_not_drafted(self, db_session):
        _reported(db_session, "College sports bill heads to the House", bills=[{"name": "S. 4668", "id": "S.4668"}])
        with patch.object(es, "_fetch_recent_votes", return_value=[self._vote()]), \
                patch.object(es, "classify_policy_area", return_value=("EDUCATION", 0.9)):
            assert es.check_roll_call_signals(db_session, "2026-09-29") == 0

    def test_an_unrelated_issue_does_not_stop_the_draft(self, db_session):
        self._bill(db_session)
        _reported(db_session, "US, China agree to cut tariffs on $60B worth of products")
        with patch.object(es, "_fetch_recent_votes", return_value=[self._vote()]), \
                patch.object(es, "classify_policy_area", return_value=("EDUCATION", 0.9)):
            assert es.check_roll_call_signals(db_session, "2026-09-29") == 1

    def test_a_draft_gives_way_once_reporting_covers_its_bill(self, db_session):
        self._bill(db_session)
        with patch.object(es, "_fetch_recent_votes", return_value=[self._vote()]), \
                patch.object(es, "classify_policy_area", return_value=("EDUCATION", 0.9)):
            es.check_roll_call_signals(db_session, "2026-09-29")
        draft = db_session.query(ActionIssue).one()
        assert es.retire_covered_developing_issues(db_session) == 0
        news = _reported(db_session, "Senate passes college sports bill", "The Protect College Sports Act passed 77-22.")
        assert es.retire_covered_developing_issues(db_session) == 1
        assert draft.is_current is False and news.is_current is True

    def test_a_draft_from_before_it_recorded_its_bill_is_matched_by_its_text(self, db_session):
        self._bill(db_session)
        legacy = ActionIssue(
            date="2026-09-29", rank=4, title="Senate vote on S. 4668", summary="The Senate voted 77 in favor.",
            facts="[]", source_urls="[]", source_names="[]", is_current=True,
            status=ActionIssueStatus.DEVELOPING, source_type="senate_roll_call_vote",
        )
        db_session.add(legacy)
        db_session.commit()
        _reported(db_session, "The Senate passes the Protect College Sports Act")
        assert es.retire_covered_developing_issues(db_session) == 1


class TestCheckFederalRegisterSignals:
    def test_qualifying_rule_creates_a_developing_issue(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]):
            created = es.check_federal_register_signals(db_session, "2026-08-31")
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.status == ActionIssueStatus.DEVELOPING
        assert row.source_type == "federal_register_significant_rule"
        assert row.primary_source_url == _rule()["htmlUrl"]
        assert row.confirmation_deadline is not None
        assert row.is_current is True
        assert row.date == "2026-08-31"

    def test_missing_document_number_is_skipped(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule(document_number="")]):
            created = es.check_federal_register_signals(db_session)
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_same_rule_is_not_created_twice(self, db_session):
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]):
            es.check_federal_register_signals(db_session)
            created_second_pass = es.check_federal_register_signals(db_session)
        assert created_second_pass == 0
        assert db_session.query(ActionIssue).count() == 1


class TestRuleDraft:
    """A rule's draft is the Federal Register record in a template: nothing
    in it the record doesn't state (the model's drafts were dropped with
    the vote drafts', after one called a 77-22 vote "narrow")."""

    def test_states_the_record_and_nothing_else(self):
        title, summary, facts = es._compose_developing_rule_issue(_rule())
        assert title == "Interior Department final rule: Process for Authorizing Seasonal Migratory Game Bird Hunting"
        assert summary == (
            'Interior Department published the final rule "Process for Authorizing Seasonal Migratory Game '
            'Bird Hunting" in the Federal Register on 2026-08-31. The Service is changing the administrative '
            "process. This is from the Federal Register; news coverage of the rule has not appeared yet."
        )
        assert facts == [
            "Agency: Interior Department.",
            "Federal Register document 2026-17733, published 2026-08-31.",
            "Abstract: The Service is changing the administrative process.",
        ]

    def test_a_long_abstract_is_quoted_by_whole_sentences(self):
        abstract = "First sentence. " + "Second sentence runs on " * 30 + "and ends."
        _, summary, facts = es._compose_developing_rule_issue(_rule(abstract=abstract))
        assert "First sentence. This is from the Federal Register" in summary
        assert facts[-1] == f"Abstract: {' '.join(abstract.split())}"

    def test_missing_agencies_falls_back(self):
        title, _, _ = es._compose_developing_rule_issue(_rule(agencies=[]))
        assert title.startswith("A federal agency final rule:")


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
