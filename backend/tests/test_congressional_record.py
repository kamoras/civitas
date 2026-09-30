"""Tests for Congressional Record parsing."""

import pytest

from app.pipeline.fetch.congressional_record import parse_speaking_turns


class TestParseSpeakingTurns:
    """Verify extraction of speaker-attributed segments from CREC text."""

    SAMPLE_TEXT = (
        "The Senate met at 10:00 a.m. and was called to order. "
        "Mr. CRUZ. Mr. President, I rise today to speak about the importance "
        "of border security and the urgent need to address the crisis at our "
        "southern border. We must take immediate action to protect American "
        "families and communities from the consequences of an open border. "
        "Mrs. WARREN. Thank you, Mr. Chairman. I want to address the rising "
        "cost of prescription drugs in this country. Families across "
        "Massachusetts are struggling to afford their medications, and we need "
        "to hold pharmaceutical companies accountable for price gouging. "
        "Mr. PRESIDENT. The question is on the motion to proceed. "
        "Ms. COLLINS. I wish to speak briefly about the bipartisan "
        "infrastructure bill and its impact on Maine communities."
    )

    def test_extracts_speakers(self):
        turns = parse_speaking_turns(self.SAMPLE_TEXT)
        speakers = [t["speaker"] for t in turns]
        assert "CRUZ" in speakers
        assert "WARREN" in speakers
        assert "COLLINS" in speakers

    def test_skips_procedural_speakers(self):
        turns = parse_speaking_turns(self.SAMPLE_TEXT)
        speakers = [t["speaker"] for t in turns]
        assert "PRESIDENT" not in speakers

    def test_text_truncated(self):
        # The sample's turns are all under 400 characters; a long one must
        # be cut to the first 400.
        text = "Mr. CRUZ. " + "I rise today to speak about border security. " * 30
        turns = parse_speaking_turns(text)
        assert len(turns) == 1
        assert len(turns[0]["text"]) == 400

    @pytest.mark.parametrize("text", [
        pytest.param("", id="empty_text"),
        pytest.param("The Senate adjourned at 5:00 p.m.", id="no_speakers"),
    ])
    def test_no_speaker_markers_is_empty(self, text):
        assert parse_speaking_turns(text) == []

    def test_hyphenated_name(self):
        text = (
            "Mr. KENNEDY-SMITH. I rise to discuss the proposed legislation "
            "regarding environmental protections and clean energy standards "
            "that will shape the future of energy policy in this country."
        )
        turns = parse_speaking_turns(text)
        assert len(turns) == 1
        assert "KENNEDY" in turns[0]["speaker"]

    def test_short_interjection_skipped(self):
        text = (
            "Mr. SMITH. I agree. "
            "Mrs. JONES. Thank you. That is a very interesting perspective and "
            "I would like to elaborate on the importance of healthcare reform "
            "in rural communities across our great nation."
        )
        turns = parse_speaking_turns(text)
        # "I agree." is too short (<40 chars) and should be skipped
        speakers = [t["speaker"] for t in turns]
        assert "SMITH" not in speakers
        assert "JONES" in speakers


class TestSpeakersTheRecordQualifies:
    BODY = " I rise today to speak about the appropriations bill before us this week."

    def test_a_shared_surname_carries_its_state(self):
        text = f"Mr. SCOTT of Florida.{self.BODY} Mr. SCOTT of South Carolina.{self.BODY}"
        assert [t["speaker"] for t in parse_speaking_turns(text)] == [
            "SCOTT of Florida", "SCOTT of South Carolina",
        ]

    def test_mixed_case_and_two_word_surnames(self):
        text = f"Mr. McGOVERN.{self.BODY} Ms. BLUNT ROCHESTER.{self.BODY} Ms. DeLAURO.{self.BODY}"
        assert [t["speaker"] for t in parse_speaking_turns(text)] == ["McGOVERN", "BLUNT ROCHESTER", "DeLAURO"]

    def test_the_house_parser_reads_them_too(self):
        from app.pipeline.fetch.house_record import parse_house_speaking_turns

        text = f"Mr. JOHNSON of Louisiana.{self.BODY}"
        assert [t["speaker"] for t in parse_house_speaking_turns(text)] == ["JOHNSON of Louisiana"]
