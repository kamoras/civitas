"""A box's quoted line (app.sentences.lede): the source's first sentence,
verbatim, or its opening words and an ellipsis when that runs long."""
from app.sentences import lede


def test_the_first_sentence_whole_and_verbatim():
    text = ("The U.S.Census Bureau was established by act of March 6, 1902 (32 Stat. 51). "
            "The major functions are authorized by the Constitution.\r\n\r\nMore.")
    assert lede(text) == "The U.S.Census Bureau was established by act of March 6, 1902 (32 Stat. 51)."


def test_an_initial_or_dotted_abbreviation_does_not_end_it():
    assert lede("The U.S. Fish and Wildlife Service, led by Jo Q. Public, acts. Then more.") == (
        "The U.S. Fish and Wildlife Service, led by Jo Q. Public, acts.")


def test_a_long_sentence_is_cut_at_a_word_with_an_ellipsis():
    text = " ".join(f"w{i}" for i in range(60)) + ". Next."
    got = lede(text, max_words=5)
    assert got == "w0 w1 w2 w3 w4…"


def test_no_text_is_no_line():
    assert lede(None) is None
    assert lede("  \n ") is None
