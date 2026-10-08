"""Which Record turns are speeches, and their titles."""

from pathlib import Path

import numpy as np
import pytest

from app.pipeline.analyze import floor_speech
from app.pipeline.analyze.floor_speech import speech_flags, titled_speeches
from app.pipeline.fetch.congressional_record import parse_granule_speeches

FIXTURES = Path(__file__).parent / "fixtures" / "crec"


def _turn(speaker, heading, opens, granule="G1", title="SECTION TITLE"):
    return {"speaker": speaker, "text": "...", "heading": heading, "opens": opens,
            "granule_id": granule, "granule_title": title}


class TestTitles:
    def test_a_speech_that_opens_a_heading_takes_it(self):
        (s,) = titled_speeches([_turn("A", "Tribute to a Constituent", True)], [True])
        assert s["title"] == "Tribute to a Constituent"

    def test_a_reply_is_remarks_on_the_speech_it_answers(self):
        """Not the other member's title: that is how one member came to be
        credited with another's tribute."""
        turns = [_turn("A", "Artificial Intelligence", True), _turn("B", "Artificial Intelligence", False)]
        assert [s["title"] for s in titled_speeches(turns, [True, True])] == [
            "Artificial Intelligence", "Remarks on Artificial Intelligence",
        ]

    def test_a_heading_only_floor_business_opened_is_no_topic(self):
        """House "General Leave": its heading sits over the whole debate."""
        turns = [
            _turn("A", "BILL TITLE", True, title="BILL TITLE"),      # the motion
            _turn("A", "General Leave", True, title="BILL TITLE"),   # the request
            _turn("B", "General Leave", False, title="BILL TITLE"),  # the debate
        ]
        out = titled_speeches(turns, [False, False, True])
        assert [(s["speaker"], s["title"]) for s in out] == [("B", "Remarks on BILL TITLE")]

    def test_topics_do_not_cross_granules(self):
        turns = [_turn("A", "Iran", True, granule="G1"),
                 _turn("B", "Other Business", False, granule="G2", title="OTHER SECTION")]
        assert titled_speeches(turns, [True, True])[1]["title"] == "Remarks on OTHER SECTION"


class _Encoder:
    """Stands in for the sentence model: every procedural prototype and any
    paragraph containing "consent" on one axis, everything else on the
    other."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        procedural = set(floor_speech.PROCEDURAL_PROTOTYPES)
        return np.array([[1.0, 0.0] if t in procedural or "consent" in t else [0.0, 1.0] for t in texts])


class TestFloorBusinessArithmetic:
    def test_a_turn_of_procedural_paragraphs_is_business(self):
        turn = "Mr. President, I ask unanimous consent that the order for the quorum call be rescinded."
        assert speech_flags([turn], _Encoder()) == [False]

    def test_speech_paragraphs_count_and_business_ones_do_not(self):
        long_words = "x" * floor_speech.MIN_SPEECH_CHARS
        short_words = "x" * (floor_speech.MIN_SPEECH_CHARS - 1)
        consent = "I ask unanimous consent to " + "y" * 200
        assert speech_flags([f"{consent}\n{long_words}", f"{consent}\n{short_words}"], _Encoder()) == [True, False]

    def test_no_text_no_model(self):
        assert speech_flags([]) == []


@pytest.mark.slow
class TestOnTheSimilarityModel:
    """The calibrated prototypes and thresholds on real Record turns."""

    def test_real_turns(self):
        speeches = parse_granule_speeches(
            (FIXTURES / "senate_legislative_session.htm").read_text(), "LEGISLATIVE SESSION")[0]
        debate = parse_granule_speeches(
            (FIXTURES / "house_suspension_debate.htm").read_text(),
            "STOPPING POLITICAL DISCRIMINATION IN DISASTER ASSISTANCE ACT")[0]
        flags = speech_flags([s["text"] for s in speeches + debate])
        assert flags[:3] == [True, False, True]  # speech, quorum call rescinded, speech
        by_speaker = list(zip([s["speaker"] for s in debate], flags[3:]))
        assert by_speaker == [
            ("PERRY", False),    # moves to suspend the rules
            ("PERRY", False),    # general leave
            ("PERRY", False),    # yields himself time, then 5 minutes to a colleague
            ("GRAVES", True),
            ("PERRY", True),     # "I yield myself such time", then a speech
            ("STANTON", True),
            ("PERRY", False),    # reserves his time
            ("STANTON", False),  # yields 2 minutes
        ]
