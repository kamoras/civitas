"""stamp_motion_type: what a roll call decided, on key and recent votes in
both chambers (the lobbying link shows a member's passage vote, or says which
motion the vote shown was)."""

from unittest.mock import patch

from app.pipeline.analyze import bill_learning


def test_stamps_from_the_chambers_question():
    vote = {}
    with patch.object(bill_learning, "classify_motion_type", return_value="procedural") as classify:
        bill_learning.stamp_motion_type(vote, {"question": "On Motion to Recommit"})
    assert vote["motionType"] == "procedural"
    classify.assert_called_once_with("On Motion to Recommit")


def test_keeps_a_type_already_known_and_leaves_a_missing_question_unknown():
    known = {"motionType": "cloture"}
    with patch.object(bill_learning, "classify_motion_type") as classify:
        bill_learning.stamp_motion_type(known, {"question": "On Passage"})
        missing = {}
        bill_learning.stamp_motion_type(missing, {})
    assert known["motionType"] == "cloture" and missing["motionType"] is None
    classify.assert_not_called()
