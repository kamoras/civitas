"""Tests for early_signal.py — drafting a hedged, primary-source-only
ActionIssue from a Senate roll-call vote before press coverage exists."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
import pytest

from app.config import settings
from app.models import ActionIssue, ActionIssueStatus, Senator, SponsoredBill
from app.pipeline.analyze import early_signal as es
from app.pipeline.analyze.action_center import mark_recent_duplicates
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
    @pytest.mark.parametrize("vote, expected", [
        pytest.param(_vote(question="On Passage of the Bill"), True, id="on_passage_of_the_bill"),
        pytest.param(_vote(question="On the Joint Resolution"), True, id="on_the_joint_resolution"),
        pytest.param(_vote(question="On the Nomination"), False, id="nomination"),
        pytest.param(_vote(question="On the Motion to Invoke Cloture"), False, id="cloture"),
        pytest.param(_vote(question="On the Amendment"), False, id="amendment"),
        pytest.param(_house_vote(question="On Passage"), True, id="house_on_passage"),
        pytest.param(_house_vote(question="On Motion to Suspend the Rules and Pass"), True,
                     id="house_suspend_the_rules_and_pass"),
        pytest.param(_house_vote(question="On Motion to Recommit", vote_title="On Motion to Recommit"), False,
                     id="house_motion_to_recommit"),
        pytest.param(_house_vote(question="On Ordering the Previous Question",
                                 vote_title="On Ordering the Previous Question"), False,
                     id="house_previous_question"),
    ])
    def test_is_final_passage(self, vote, expected):
        assert es._is_final_passage(vote) is expected


class TestChamberLabels:
    def test_senate_vote_labels(self):
        assert es._chamber_labels(_vote()) == ("Senate", "senators")

    def test_house_vote_labels(self):
        assert es._chamber_labels(_house_vote()) == ("House of Representatives", "representatives")


class TestVoteUrl:
    def test_senate_vote_url(self):
        url = es._vote_url(_vote(congress=119, session=1, roll_number=42))
        assert url == "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1191/vote_119_1_00042.xml"

    def test_house_vote_url(self):
        url = es._vote_url(_house_vote(year=2026, roll_number=42))
        # The Clerk names a roll below 100 zero-padded; "roll42.xml" is its HTML site.
        assert url == "https://clerk.house.gov/evs/2026/roll042.xml"


@pytest.mark.parametrize("yeas, nays, expected", [
    pytest.param(90, 10, 0.8, id="lopsided"),
    pytest.param(50, 50, 0.0, id="tied"),
    pytest.param(0, 0, 0.0, id="no_yea_nay_votes_is_zero"),
])
def test_vote_margin_ratio(yeas, nays, expected):
    assert es._vote_margin_ratio(_vote(yeas=yeas, nays=nays)) == expected


def _roll_call_signals(db_session, votes, area="DEFENSE", *args):
    """check_roll_call_signals over `votes`, every one classified `area`."""
    # The fixtures' votes are dated 2026-08-30; a run the day after.
    with patch.object(es, "_fetch_recent_votes", return_value=votes), \
            patch.object(es, "classify_policy_area", return_value=(area, 0.9)):
        return es.check_roll_call_signals(db_session, *(args or ("2026-08-31",)))


class TestCheckRollCallSignals:
    def test_procedural_vote_is_rejected(self, db_session):
        created = _roll_call_signals(db_session, [_vote()], "PROCEDURAL")
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_non_final_passage_vote_is_rejected(self, db_session):
        created = _roll_call_signals(db_session, [_vote(question="On the Nomination")])
        assert created == 0
        assert db_session.query(ActionIssue).count() == 0

    def test_qualifying_vote_creates_a_developing_issue(self, db_session):
        created = _roll_call_signals(db_session, [_vote()])
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.status == ActionIssueStatus.DEVELOPING
        assert row.source_type == "senate_roll_call_vote"
        assert row.primary_source_url
        assert row.confirmation_deadline is not None
        assert row.is_current is True

    def test_same_vote_is_not_created_twice(self, db_session):
        _roll_call_signals(db_session, [_vote()])
        created_second_pass = _roll_call_signals(db_session, [_vote()])
        assert created_second_pass == 0
        assert db_session.query(ActionIssue).count() == 1

    def test_qualifying_house_vote_creates_a_developing_issue(self, db_session):
        created = _roll_call_signals(db_session, [_house_vote()])
        assert created == 1
        row = db_session.query(ActionIssue).one()
        assert row.source_type == "house_roll_call_vote"
        assert row.primary_source_url == "https://clerk.house.gov/evs/2026/roll042.xml"

    def test_senate_and_house_votes_sharing_a_roll_number_both_created(self, db_session):
        """recent_roll_call_key is congress-session-rollNumber only — House
        and Senate roll numbers are independent sequences, so a same-
        numbered pair from each chamber must not collide in the per-run
        dedup set."""
        created = _roll_call_signals(db_session, [_vote(roll_number=42), _house_vote(roll_number=42)])
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
        assert _roll_call_signals(db_session, [vote], "EDUCATION", "2026-09-29") == 1
        row = db_session.query(ActionIssue).one()
        assert row.title == "Senate vote on S. 4668: Bill Passed, 77-22"
        assert row.date == "2026-09-29" and row.primary_article_date == "2026-09-28"
        assert "narrow" not in f"{row.title} {row.summary} {row.facts}"
        # With its link and Congress: the issue API shows a bill only with a
        # link, and dropped these until 2026-10-14.
        assert json.loads(row.related_bill_ids) == [{
            "name": "S. 4668", "id": "S.4668", "congress": 119,
            "url": "https://www.congress.gov/bill/119th-congress/senate-bill/4668",
        }]
        assert json.loads(row.facts)[0] == "Tally: 77 yea, 22 nay, 0 not voting."

    @pytest.mark.parametrize("document, measure", [
        pytest.param("H R 9497", "H.R. 9497", id="the_clerk_spelling_is_printed_as_the_record_prints_it"),
        pytest.param("PN1129", "PN1129", id="a_nomination_is_left_as_recorded"),
    ])
    def test_the_measure_is_named_as_the_site_prints_it(self, document, measure):
        vote = _house_vote()
        vote.update(documentName=document, result="Passed")
        title, summary, _ = es._compose_developing_issue(vote)
        assert title == f"House vote on {measure}: Passed, 250-180"
        assert f"for {measure}." in summary


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
        vote = _vote(roll_number=250, yeas=77, nays=22, vote_date="2026-09-28")
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
        assert _roll_call_signals(db_session, [self._vote()], "EDUCATION", "2026-09-29") == 0

    def test_a_vote_whose_bill_an_issue_records_is_not_drafted(self, db_session):
        _reported(db_session, "College sports bill heads to the House", bills=[{"name": "S. 4668", "id": "S.4668"}])
        assert _roll_call_signals(db_session, [self._vote()], "EDUCATION", "2026-09-29") == 0

    def test_an_unrelated_issue_does_not_stop_the_draft(self, db_session):
        self._bill(db_session)
        _reported(db_session, "US, China agree to cut tariffs on $60B worth of products")
        assert _roll_call_signals(db_session, [self._vote()], "EDUCATION", "2026-09-29") == 1

    def test_a_draft_gives_way_once_reporting_covers_its_bill(self, db_session):
        self._bill(db_session)
        _roll_call_signals(db_session, [self._vote()], "EDUCATION", "2026-09-29")
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

    def test_a_retired_draft_stays_off_the_homepage_record(self, db_session):
        # Issue 762 (2026-09-29): retiring flipped is_current, which the
        # homepage's record ignores by design, so the draft stayed listed
        # beside 761. It is marked a duplicate of the covering issue, even
        # once that issue has left the Action Center too.
        self._bill(db_session)
        draft = ActionIssue(
            date="2026-09-28", rank=3, title="Senate vote on S. 4668", summary="The Senate voted 77 in favor.",
            facts="[]", source_urls="[]", source_names="[]", is_current=False,
            status=ActionIssueStatus.DEVELOPING, source_type="senate_roll_call_vote",
        )
        db_session.add(draft)
        news = _reported(db_session, "The Senate passes the Protect College Sports Act, but the bill's future is unclear")
        news.is_current = False
        db_session.commit()
        with patch("app.pipeline.analyze.action_center._embed_texts_sim", return_value=np.eye(2)):
            assert mark_recent_duplicates(db_session) == 1
        assert (draft.duplicate_of_id, news.duplicate_of_id) == (news.id, None)

    def test_a_draft_no_issue_covers_stays_on_the_record(self, db_session):
        self._bill(db_session)
        draft = ActionIssue(
            date="2026-09-28", rank=3, title="Senate vote on S. 4668", summary="", facts="[]", source_urls="[]",
            source_names="[]", is_current=True, status=ActionIssueStatus.DEVELOPING, source_type="senate_roll_call_vote",
        )
        db_session.add(draft)
        _reported(db_session, "US, China agree to cut tariffs on $60B worth of products")
        with patch("app.pipeline.analyze.action_center._embed_texts_sim", return_value=np.eye(2)):
            assert mark_recent_duplicates(db_session) == 0
        assert draft.duplicate_of_id is None


class TestStaleVotes:
    """2026-10-15: the House's rolls of 2026-09-16 went up as that day's
    developing news ("news coverage of the vote has not appeared yet"); the
    poll takes each chamber's latest votes whenever they were cast."""

    @pytest.mark.parametrize("vote_date, today, drafted", [
        pytest.param("2026-09-16", "2026-10-15", 0, id="a_month_old_vote"),
        pytest.param("2026-10-12", "2026-10-15", 0, id="three_days_back"),
        pytest.param("2026-10-13", "2026-10-15", 1, id="two_days_back_is_inside_the_window"),
        pytest.param("October 15, 2026,  09:42 PM", "2026-10-15", 1, id="the_senate_s_date_form"),
        pytest.param("", "2026-10-15", 0, id="no_date_is_not_assumed_new"),
    ])
    def test_a_vote_is_drafted_only_inside_the_confirmation_window(self, db_session, vote_date, today, drafted):
        assert _roll_call_signals(db_session, [_house_vote(vote_date=vote_date)], "DEFENSE", today) == drafted

    def _draft(self, db_session, vote_day, **kw):
        row = ActionIssue(
            date="2026-10-14", rank=999, title="House vote on S 2403: Passed, 401-14", summary="s",
            is_current=True, status=kw.get("status", ActionIssueStatus.DEVELOPING),
            source_type="house_roll_call_vote", primary_source_url=kw.get("url", "https://clerk.house.gov/evs/2026/roll314.xml"),
            primary_article_date=vote_day,
        )
        db_session.add(row)
        db_session.flush()
        return row

    def test_a_current_draft_of_a_stale_vote_is_retired(self, db_session):
        stale, fresh = self._draft(db_session, "2026-09-16"), self._draft(db_session, "2026-10-14", url="u2")
        confirmed = self._draft(db_session, "2026-09-16", url="u3", status=ActionIssueStatus.CONFIRMED)
        assert es.retire_stale_vote_drafts(db_session, "2026-10-15") == 1
        assert stale.is_current is False and stale.status == ActionIssueStatus.DEVELOPING
        assert fresh.is_current is True and confirmed.is_current is True

    def test_a_current_draft_is_reworded_from_its_record(self, db_session):
        # "S 2403" was the Clerk's file spelling, stored before the template
        # printed bills as the site does.
        vote = _house_vote(roll_number=314, vote_date="2026-10-14")
        vote.update(documentName="S 2403", result="Passed")
        row = self._draft(db_session, "2026-10-14")
        assert _roll_call_signals(db_session, [vote], "DEFENSE", "2026-10-15") == 0
        assert row.title.startswith("House vote on S. 2403: Passed")
        assert json.loads(row.related_bill_ids)[0]["id"] == "S.2403"


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

    def test_a_current_draft_is_retitled_by_todays_template(self, db_session):
        # Drafts stored before the title led with the rule kept their
        # agency-list title until they expired.
        with patch.object(es, "_fetch_recent_rules", return_value=[_rule()]):
            es.check_federal_register_signals(db_session)
            row = db_session.query(ActionIssue).one()
            row.title = "Interior Department final rule: Process for Authorizing ..."
            promoted = ActionIssue(date="2026-08-31", rank=1, title="As published", summary="s",
                                   is_current=True, status=ActionIssueStatus.CONFIRMED,
                                   primary_source_url="https://example.com/other")
            db_session.add(promoted)
            db_session.flush()
            es.check_federal_register_signals(db_session)
        assert row.title == f"Final rule: {_rule()['title']}"
        assert promoted.title == "As published"


class TestRuleDraft:
    """A rule's draft is the Federal Register record in a template: nothing
    in it the record doesn't state (the model's drafts were dropped with
    the vote drafts', after one called a 77-22 vote "narrow")."""

    def test_states_the_record_and_nothing_else(self):
        title, summary, facts = es._compose_developing_rule_issue(_rule())
        # The rule's own title leads; the agency opens the summary.
        assert title == "Final rule: Process for Authorizing Seasonal Migratory Game Bird Hunting"
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
        _, summary, _ = es._compose_developing_rule_issue(_rule(agencies=[]))
        assert summary.startswith("A federal agency published the final rule")

    def test_many_agencies_never_push_the_subject_back(self):
        agencies = ["Treasury Department", "Internal Revenue Service", "Labor Department",
                    "Employee Benefits Security Administration", "Health and Human Services Department"]
        title, summary, facts = es._compose_developing_rule_issue(
            _rule(agencies=agencies, title="Transparency in Coverage"))
        assert title == "Final rule: Transparency in Coverage"
        assert summary.startswith(", ".join(agencies) + " published the final rule")
        assert facts[0] == f"Agency: {', '.join(agencies)}."

    def test_an_untitled_rule_is_titled_from_its_abstract_then_its_number(self):
        title, _, _ = es._compose_developing_rule_issue(
            _rule(title="", abstract="This rule sets a fee. It takes effect in May."))
        assert title == "Final rule: This rule sets a fee. It takes effect in May."
        title, _, _ = es._compose_developing_rule_issue(_rule(title="", abstract=""))
        assert title == "Final rule: Federal Register document 2026-17733"


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


class TestRuleAbstractSentences:
    """The summary quotes the abstract by whole sentences: a period inside
    "U.S." or after an initial is not the end of one."""

    @pytest.mark.parametrize("abstract, expected", [
        # The only ". " inside the limit is the one in "U.S.": quoting up to
        # it would present "The rule applies across the U.S." as a sentence.
        pytest.param("The rule applies across the U.S. Fish and Wildlife Service " + "lands and waters " * 30 + "alike.",
                     "", id="a_dotted_abbreviation_does_not_end_the_quote"),
        pytest.param("Rules by John Q. Public and Acme Inc. Holdings take effect. " + "More text " * 60,
                     "Rules by John Q. Public and Acme Inc. Holdings take effect.",
                     id="an_initial_or_a_title_does_not_end_the_quote"),
        pytest.param("It implements 42 U.S.C. 7401 as amended. " + "More text " * 60,
                     "It implements 42 U.S.C. 7401 as amended.",
                     id="a_citation_before_a_number_is_not_a_sentence_end"),
        pytest.param("word " * 200, "", id="no_whole_sentence_within_the_limit_quotes_nothing"),
    ])
    def test_first_sentences(self, abstract, expected):
        assert es._first_sentences(abstract, 400) == expected


def test_a_rule_record_missing_its_number_and_date_leaves_them_out():
    _, _, facts = es._compose_developing_rule_issue(_rule(document_number="", publication_date=""))
    assert facts[1] == "Federal Register document."


@pytest.mark.parametrize("now, congress, house_year", [
    # Jan 2, 2027: the 119th still sits; its House votes are numbered in 2026.
    pytest.param("2027-01-02T15:00:00", 119, 2026, id="outgoing_congress_reads_its_last_year"),
    # Jan 3, 2027 after noon ET: the 120th sits; its votes are 2027's.
    pytest.param("2027-01-03T18:00:00", 120, 2027, id="new_congress_reads_its_own_year"),
    pytest.param("2028-06-01T12:00:00", 120, 2028, id="second_session"),
])
def test_the_roll_call_poll_reads_the_sitting_congress(db_session, freeze_utcnow, monkeypatch, now, congress, house_year):
    freeze_utcnow(datetime.fromisoformat(now))
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", congress)
    seen = {}

    async def senate(_client, _db, *, congress, session_number, **_kw):
        seen.setdefault("senate", []).append((congress, session_number))
        return []

    async def house(_client, _db, *, year, **_kw):
        seen["house"] = year
        return []

    monkeypatch.setattr(es, "fetch_recent_roll_calls", senate)
    monkeypatch.setattr(es, "fetch_recent_house_roll_calls", house)
    es._fetch_recent_votes(db_session)
    assert seen == {"senate": [(congress, 1), (congress, 2)], "house": house_year}
