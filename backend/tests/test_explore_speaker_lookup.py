"""Linking a Congressional Record speaker to a member: only when the
surname names exactly one, sitting members first."""

from app.pipeline.explore_pipeline import _SpeakerLookup, _speaker_surname

ROWS = [
    ("scott-rick", "Rick Scott", "FL", True),
    ("scott-tim", "Tim Scott", "SC", True),
    ("vanhollen-chris", "Chris Van Hollen", "MD", True),
    ("lujan-ben", "Ben Ray Luján", "NM", True),
    ("casey-robert", "Robert P., Jr. Casey", "PA", False),
    ("cruz-ted", "Ted Cruz", "TX", True),
    ("cruz-old", "Old Cruz", "TX", False),
]


def test_a_shared_surname_needs_its_state():
    lookup = _SpeakerLookup(ROWS)
    assert lookup.get("SCOTT") is None
    assert lookup.get("SCOTT of Florida") == "scott-rick"
    assert lookup.get("SCOTT of South Carolina") == "scott-tim"
    assert lookup.get("SCOTT of Nowhere") is None


def test_surnames_the_last_word_misses():
    lookup = _SpeakerLookup(ROWS)
    assert lookup.get("VAN HOLLEN") == "vanhollen-chris"
    assert lookup.get("LUJAN") == "lujan-ben"
    assert lookup.get("CASEY") == "casey-robert"  # departed, but nobody sitting shares it


def test_a_sitting_member_is_not_displaced_by_a_departed_one():
    assert _SpeakerLookup(ROWS).get("CRUZ") == "cruz-ted"


def test_document_name_drops_the_state():
    assert _speaker_surname("SCOTT of Florida") == "Scott"
    assert _speaker_surname("VAN HOLLEN") == "Van Hollen"
