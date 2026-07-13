"""Tests for Congressional Record parsing."""

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

    def test_text_not_empty(self):
        turns = parse_speaking_turns(self.SAMPLE_TEXT)
        for turn in turns:
            assert len(turn["text"]) > 40

    def test_text_truncated(self):
        turns = parse_speaking_turns(self.SAMPLE_TEXT)
        for turn in turns:
            assert len(turn["text"]) <= 400

    def test_empty_text(self):
        assert parse_speaking_turns("") == []

    def test_no_speakers(self):
        assert parse_speaking_turns("The Senate adjourned at 5:00 p.m.") == []

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
