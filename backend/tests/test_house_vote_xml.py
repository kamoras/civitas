"""Tests for parse_house_vote_xml — clerk.house.gov roll call XML parsing,
in particular the action-date extraction early_signal.py's ActionIssue.date
relies on."""

from app.pipeline.fetch.congress import parse_house_vote_xml

_SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
  <vote-metadata>
    <congress>119</congress>
    <session>2</session>
    <action-date>22-Jul-2026</action-date>
    <vote-question>On Passage</vote-question>
    <legis-num>H.R. 1</legis-num>
    <vote-desc>A bill to do a thing</vote-desc>
  </vote-metadata>
  <vote-data>
    <recorded-vote>
      <legislator name-id="A000001" sort-field="Smith" party="D" state="CA">John</legislator>
      <vote>Yea</vote>
    </recorded-vote>
    <recorded-vote>
      <legislator name-id="B000002" sort-field="Jones" party="R" state="TX">Jane</legislator>
      <vote>Nay</vote>
    </recorded-vote>
  </vote-data>
</rollcall-vote>
"""


def test_parses_action_date_into_iso_format():
    result = parse_house_vote_xml(_SAMPLE_XML, year=2026, roll_number=42)
    assert result["voteDate"] == "2026-07-22"


def test_parses_core_fields():
    result = parse_house_vote_xml(_SAMPLE_XML, year=2026, roll_number=42)
    assert result["chamber"] == "House"
    assert result["congress"] == 119
    assert result["session"] == 2
    assert result["rollNumber"] == 42
    assert len(result["members"]) == 2


def test_unrecognized_date_format_degrades_to_empty_string():
    bad_xml = _SAMPLE_XML.replace("22-Jul-2026", "2026-07-22")
    result = parse_house_vote_xml(bad_xml, year=2026, roll_number=42)
    assert result["voteDate"] == ""


# Trimmed from clerk.house.gov/evs/2026/roll231.xml (H.Res. 1398, a rule
# that failed 198-224; Majority Leader Scalise switched to No so he could
# move to reconsider, the Speaker voted Aye).
_FAILED_RULE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
<vote-metadata>
<majority>R</majority>
<congress>119</congress>
<session>2nd</session>
<chamber>U.S. House of Representatives</chamber>
<rollcall-num>231</rollcall-num>
<legis-num>H RES 1398</legis-num>
<vote-question>On Agreeing to the Resolution</vote-question>
<vote-type>RECORDED VOTE</vote-type>
<vote-result>Failed</vote-result>
<action-date>30-Jun-2026</action-date>
<vote-desc>Providing for consideration of the bills H.R. 8800, H.R. 8595, and H.R. 8884</vote-desc>
</vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="A000370" sort-field="Adams" party="D" state="NC" role="legislator">Adams</legislator><vote>No</vote></recorded-vote>
<recorded-vote><legislator name-id="A000055" sort-field="Aderholt" party="R" state="AL" role="legislator">Aderholt</legislator><vote>Aye</vote></recorded-vote>
<recorded-vote><legislator name-id="A000371" sort-field="Aguilar" party="D" state="CA" role="legislator">Aguilar</legislator><vote>No</vote></recorded-vote>
<recorded-vote><legislator name-id="A000379" sort-field="Alford" party="R" state="MO" role="legislator">Alford</legislator><vote>Aye</vote></recorded-vote>
<recorded-vote><legislator name-id="J000294" sort-field="Jeffries" party="D" state="NY" role="legislator">Jeffries</legislator><vote>No</vote></recorded-vote>
<recorded-vote><legislator name-id="J000299" sort-field="Johnson (LA)" party="R" state="LA" role="speaker">Johnson (LA)</legislator><vote>Aye</vote></recorded-vote>
<recorded-vote><legislator name-id="S001176" sort-field="Scalise" party="R" state="LA" role="legislator">Scalise</legislator><vote>No</vote></recorded-vote>
</vote-data>
</rollcall-vote>
"""


def test_parses_the_chambers_own_result():
    result = parse_house_vote_xml(_FAILED_RULE_XML, year=2026, roll_number=231)
    assert result["result"] == "Failed"
    assert result["rejected"] is True
    assert result["voteDate"] == "2026-06-30"


def test_missing_result_is_unknown_not_passed():
    # _SAMPLE_XML carries no <vote-result> at all.
    result = parse_house_vote_xml(_SAMPLE_XML, year=2026, roll_number=42)
    assert result["result"] == ""
    assert result["rejected"] is None


def test_passed_result_is_not_rejected():
    xml = _FAILED_RULE_XML.replace("<vote-result>Failed", "<vote-result>Passed")
    assert parse_house_vote_xml(xml, year=2026, roll_number=231)["rejected"] is False


def test_failed_rule_is_not_a_break_for_the_majority_leader_only():
    """End to end from the clerk's XML: Scalise's switch to No is not a
    break; the Speaker's Aye is a vote with party; and the same No without
    the office is a break."""
    from app.pipeline.transform.normalize_votes import (
        _determine_party_alignment,
        compute_party_vote_split,
        extract_representative_vote,
        is_reconsider_switch,
        majority_leader_spans,
        stamp_roll_call_outcome,
    )

    rc = parse_house_vote_xml(_FAILED_RULE_XML, year=2026, roll_number=231)
    bill = {"billId": "HouseRC-2026-231", "partyLeaning": compute_party_vote_split(rc)["label"]}
    stamp_roll_call_outcome(bill, rc)
    assert bill["partyLeaning"] == "R"

    def alignment(bioguide, last, title, tenures):
        vote = extract_representative_vote(rc, bioguide, last, "LA")
        normalized = "Yea" if vote.upper() in ("AYE", "YEA") else "Nay"
        spans = majority_leader_spans(title, tenures)
        return _determine_party_alignment(
            "R", normalized, bill["partyLeaning"],
            reconsider_switch=is_reconsider_switch(bill, spans),
        )

    scalise = [{"title": "House Majority Leader", "start": "2025-01-03", "end": None}]
    speaker = [{"title": "Speaker of the House", "start": "2023-10-25", "end": None}]
    assert alignment("S001176", "Scalise", "House Majority Leader", scalise) is None
    assert alignment("J000299", "Johnson (LA)", "Speaker of the House", speaker) is True
    assert alignment("S001176", "Scalise", None, []) is False  # same No, no office
