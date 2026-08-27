"""Tests for the word-geometry/text-sanity helpers shared by every
state's ballot-measure PDF strategy (see ballot_measures_ca.py for the
one real strategy currently registered, and ballot_measures_pdf.py for
the registry these helpers are built to serve more than one of)."""

from app.pipeline.fetch import ballot_measure_pdf_geometry as geo


def _word(text, top, x0, x1):
    return {"text": text, "top": top, "x0": x0, "x1": x1}


def test_split_by_row_gap_cuts_at_the_largest_gap_per_row():
    words = [
        _word("left", 0, 0, 10),
        _word("right", 0, 100, 110),
    ]
    left, right = geo.split_by_row_gap(words)
    assert [w["text"] for w in left] == ["left"]
    assert [w["text"] for w in right] == ["right"]


def test_split_by_row_gap_single_word_row_goes_left():
    words = [_word("solo", 0, 0, 10)]
    left, right = geo.split_by_row_gap(words)
    assert [w["text"] for w in left] == ["solo"]
    assert right == []


def test_lines_from_words_orders_by_row_then_x():
    words = [_word("world", 0, 50, 60), _word("hello", 0, 0, 10)]
    assert geo.lines_from_words(words) == ["hello world"]


def test_clean_text_collapses_whitespace_and_empty_to_none():
    assert geo.clean_text("  a   b\nc ") == "a b c"
    assert geo.clean_text("   ") is None
    assert geo.clean_text(None) is None


def test_looks_corrupted_flags_missing_terminal_punctuation():
    assert geo.looks_corrupted("The state could borrow money") is True


def test_looks_corrupted_flags_internal_repeat():
    assert geo.looks_corrupted("no change in who can marry no change in who marry.") is True


def test_looks_corrupted_false_for_clean_sentence():
    assert geo.looks_corrupted("The state could borrow $10 billion to build schools.") is False


def test_looks_corrupted_does_not_compare_across_fields():
    """The false-positive this deliberately avoids: two independently
    clean sentences (a real yes/no pair) that happen to share a long tail
    should not flag each other — only self-repetition within ONE string
    matters."""
    yes = "The state could borrow $10 billion to build new or renovate existing public school and community college facilities."
    no = "The state could not borrow $10 billion to build new or renovate existing public school and community college facilities."
    assert geo.looks_corrupted(yes) is False
    assert geo.looks_corrupted(no) is False
