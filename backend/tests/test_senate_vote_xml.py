"""Tests for parse_senate_vote_xml — Senate.gov roll call XML parsing, in
particular the chamber's own result (<vote_result>), which the majority
leader's reconsider-switch exemption in Constituent Alignment relies on
(normalize_votes.MAJORITY_LEADER_TITLES)."""

from app.pipeline.fetch.congress import parse_senate_vote_xml, roll_call_rejected
from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    compute_party_vote_split,
    extract_senator_vote,
    is_reconsider_switch,
    majority_leader_spans,
    stamp_roll_call_outcome,
)


def _member(last, first, party, state, vote, lis):
    return f"""\
    <member>
      <member_full>{last} ({party}-{state})</member_full>
      <last_name>{last}</last_name>
      <first_name>{first}</first_name>
      <party>{party}</party>
      <state>{state}</state>
      <vote_cast>{vote}</vote_cast>
      <lis_member_id>{lis}</lis_member_id>
    </member>
"""


def _vote_xml(*, date, question, result, result_text, members, document="H.R. 5371"):
    return f"""\
<?xml version="1.0" encoding="UTF-8"?><roll_call_vote>
  <congress>119</congress>
  <session>1</session>
  <congress_year>2025</congress_year>
  <vote_number>571</vote_number>
  <vote_date>{date}</vote_date>
  <vote_question_text>{question} {document}</vote_question_text>
  <vote_result_text>{result_text}</vote_result_text>
  <question>{question}</question>
  <vote_title>Motion to Invoke Cloture: Motion to Proceed to {document}</vote_title>
  <majority_requirement>3/5</majority_requirement>
  {f"<vote_result>{result}</vote_result>" if result is not None else ""}
  <document>
    <document_congress>119</document_congress>
    <document_type>H.R.</document_type>
    <document_number>5371</document_number>
    <document_name>{document}</document_name>
    <document_title>A bill making continuing appropriations and extensions for fiscal year 2026, and for other purposes.</document_title>
  </document>
  <count>
    <yeas>49</yeas>
    <nays>45</nays>
    <present/>
    <absent>6</absent>
  </count>
  <members>
{"".join(members)}  </members>
</roll_call_vote>
"""


# Roll 119-1-571 (Oct 14, 2025), trimmed: cloture on the motion to proceed
# to the House CR, rejected 49-45 short of three-fifths. Republicans voted
# Yea; the Majority Leader switched to Nay so he could move to reconsider.
_REJECTED_CLOTURE = _vote_xml(
    date="October 14, 2025,  05:34 PM",
    question="On Cloture on the Motion to Proceed",
    result="Cloture on the Motion to Proceed Rejected",
    result_text="Cloture on the Motion to Proceed Rejected (49-45, 3/5 majority required)",
    members=[
        _member("Alsobrooks", "Angela", "D", "MD", "Nay", "S428"),
        _member("Baldwin", "Tammy", "D", "WI", "Nay", "S354"),
        _member("Barrasso", "John", "R", "WY", "Yea", "S317"),
        _member("Blackburn", "Marsha", "R", "TN", "Yea", "S396"),
        _member("Booker", "Cory", "D", "NJ", "Nay", "S370"),
        _member("Capito", "Shelley", "R", "WV", "Yea", "S372"),
        _member("Schumer", "Charles", "D", "NY", "Nay", "S270"),
        _member("Thune", "John", "R", "SD", "Nay", "S303"),
    ],
)

THUNE = majority_leader_spans("Senate Majority Leader", [
    {"title": "Senate Minority Whip", "start": "2023-01-03", "end": "2025-01-03"},
    {"title": "Senate Majority Leader", "start": "2025-01-03", "end": None},
])
SCHUMER = majority_leader_spans("Senate Minority Leader", [
    {"title": "Senate Majority Leader", "start": "2023-01-03", "end": "2025-01-03"},
    {"title": "Senate Minority Leader", "start": "2025-01-03", "end": None},
])


def _alignment(rc, last, state, party, spans):
    bill = {"billId": rc["documentName"], "partyLeaning": compute_party_vote_split(rc)["label"]}
    stamp_roll_call_outcome(bill, rc)
    vote = extract_senator_vote(rc, "", last, state)
    return _determine_party_alignment(
        party, vote, bill["partyLeaning"],
        reconsider_switch=is_reconsider_switch(bill, spans),
    )


def test_parses_result_fields():
    rc = parse_senate_vote_xml(_REJECTED_CLOTURE, 119, 1, 571)
    assert rc["result"] == "Cloture on the Motion to Proceed Rejected"
    assert rc["resultText"] == "Cloture on the Motion to Proceed Rejected (49-45, 3/5 majority required)"
    assert rc["majorityRequirement"] == "3/5"
    assert rc["rejected"] is True
    assert rc["documentName"] == "H.R. 5371"
    assert len(rc["members"]) == 8


def test_result_text_is_the_fallback_when_bare_result_is_absent():
    xml = _REJECTED_CLOTURE.replace(
        "<vote_result>Cloture on the Motion to Proceed Rejected</vote_result>", "",
    )
    rc = parse_senate_vote_xml(xml, 119, 1, 571)
    assert rc["result"] == "Cloture on the Motion to Proceed Rejected"
    assert rc["rejected"] is True


def test_rejected_is_read_from_the_result_not_the_counts():
    # 49 yeas beat 45 nays, yet cloture failed: three-fifths of the Senate
    # is 60. Counts alone would call this one passed.
    rc = parse_senate_vote_xml(_REJECTED_CLOTURE, 119, 1, 571)
    assert rc["rejected"] is True


def test_result_vocabulary():
    for text in ("Cloture Motion Agreed to", "Bill Passed", "Nomination Confirmed",
                 "Decision of Chair Sustained", "Veto Overridden", "Point of Order Well Taken",
                 # Ends in "not sustained" but records an override that carried.
                 "Veto Not Sustained"):
        assert roll_call_rejected(text) is False, text
    for text in ("Amendment Rejected", "Motion to Table Failed", "Joint Resolution Defeated",
                 "Motion Not Agreed to", "Veto Sustained", "Decision of Chair Not Sustained",
                 "Not Guilty"):
        assert roll_call_rejected(text) is True, text
    for text in ("", None, "Johnson (LA)", "Something New"):
        assert roll_call_rejected(text) is None, text


def test_majority_leaders_switch_on_rejected_cloture_is_not_a_break():
    rc = parse_senate_vote_xml(_REJECTED_CLOTURE, 119, 1, 571)
    assert _alignment(rc, "Thune", "SD", "R", THUNE) is None
    # Without the office, the same Nay is a break.
    assert _alignment(rc, "Thune", "SD", "R", []) is False
    # The minority leader voting with his own caucus is unaffected.
    assert _alignment(rc, "Schumer", "NY", "D", SCHUMER) is True


def test_minority_leaders_real_break_stays_a_break():
    # Roll 119-1-128 (Mar 14, 2025): cloture on the House CR agreed to
    # 62-38; Schumer voted Yea against most of his caucus.
    xml = _vote_xml(
        date="March 14, 2025,  04:27 PM",
        question="On the Cloture Motion",
        result="Cloture Motion Agreed to",
        result_text="Cloture Motion Agreed to (62-38, 3/5 majority required)",
        document="H.R. 1968",
        members=[
            _member("Alsobrooks", "Angela", "D", "MD", "Nay", "S428"),
            _member("Baldwin", "Tammy", "D", "WI", "Nay", "S354"),
            _member("Booker", "Cory", "D", "NJ", "Nay", "S370"),
            _member("Barrasso", "John", "R", "WY", "Yea", "S317"),
            _member("Blackburn", "Marsha", "R", "TN", "Yea", "S396"),
            _member("Capito", "Shelley", "R", "WV", "Yea", "S372"),
            _member("Schumer", "Charles", "D", "NY", "Yea", "S270"),
            _member("Thune", "John", "R", "SD", "Yea", "S303"),
        ],
    )
    rc = parse_senate_vote_xml(xml, 119, 1, 128)
    assert rc["rejected"] is False
    assert _alignment(rc, "Schumer", "NY", "D", SCHUMER) is False
    assert _alignment(rc, "Thune", "SD", "R", THUNE) is True


def test_majority_leaders_nay_on_a_motion_that_passed_stays_a_break():
    xml = _REJECTED_CLOTURE.replace(
        "Cloture on the Motion to Proceed Rejected", "Cloture on the Motion to Proceed Agreed to",
    )
    rc = parse_senate_vote_xml(xml, 119, 1, 571)
    assert rc["rejected"] is False
    assert _alignment(rc, "Thune", "SD", "R", THUNE) is False
