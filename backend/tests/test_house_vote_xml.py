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
