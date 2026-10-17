"""How open comment periods are ordered and shown (app/comment_periods.py)."""
from types import SimpleNamespace

from app.comment_periods import PAPERWORK_TOC_SUBJECT, comment_window, first_sentence, is_paperwork, salience


def test_a_paperwork_notice_is_the_registers_heading_on_a_notice_only():
    assert is_paperwork("Notice", PAPERWORK_TOC_SUBJECT)
    assert is_paperwork("Notice", PAPERWORK_TOC_SUBJECT + ":")  # as the Register writes some
    assert not is_paperwork("Proposed Rule", PAPERWORK_TOC_SUBJECT)
    assert not is_paperwork("Notice", "Privacy Act; Systems of Records")
    assert not is_paperwork("Notice", None)  # not read yet


def test_rules_lead_then_notices_then_paperwork_notices_whatever_their_readers():
    def doc(id_, doc_type, views, toc=None):
        return SimpleNamespace(id=id_, doc_type=doc_type, fr_page_views=views, fr_toc_subject=toc,
                               comments_close_on="2026-11-01")
    docs = [doc(1, "Notice", 9_000, PAPERWORK_TOC_SUBJECT), doc(2, "Notice", 10), doc(3, "Proposed Rule", 5),
            doc(4, "Final Rule", 50)]
    assert [d.id for d in sorted(docs, key=salience)] == [4, 3, 2, 1]
    assert [d.id for d in sorted(docs, key=lambda d: salience(d, {1}))][0] == 1  # an issue links it


def test_the_first_sentence_is_verbatim_and_whole():
    assert first_sentence("The U.S. Fish and Wildlife Service proposes a rule. It would list a beetle.") == (
        "The U.S. Fish and Wildlife Service proposes a rule.")
    assert first_sentence("This rule amends 8 CFR 214.1(l)(2) for visa holders.") == (
        "This rule amends 8 CFR 214.1(l)(2) for visa holders.")
    assert first_sentence("x" * 500) is None  # cut at the stored cap mid-sentence
    assert first_sentence("") is None


def test_the_window_runs_from_publication_to_the_deadline():
    assert comment_window("2026-10-01", "2026-10-31", "2026-10-21") == {
        "opened": "2026-10-01", "closes": "2026-10-31", "totalDays": 30, "daysLeft": 10, "elapsedPercent": 67}
    assert comment_window("2026-10-01", "2026-10-31", "2026-10-31")["daysLeft"] == 0
    assert comment_window(None, "2026-10-31", "2026-10-21") is None
