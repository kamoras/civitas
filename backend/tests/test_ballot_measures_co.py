"""Tests for Colorado's ballot-measure PDF strategy: parsing the
Legislative Council's "Blue Book", "Quick Ballot Reference Guide"
section.

fixtures_co_bluebook_page6.json is REAL — page.extract_words() output
from the real 2024 general-election Blue Book, page index 6, which
carries two full measures (Amendments G and H) back to back — enough to
exercise the per-measure boundary logic (a title/badge that must not
pick up the PREVIOUS measure's "YES"/"NO" vote-means badges, and a
vote-means zone that must not run into the NEXT measure's title).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import ballot_measures_co as co

FIXTURE = json.loads((Path(__file__).parent / "fixtures_co_bluebook_page6.json").read_text())


def _fake_page(words):
    return SimpleNamespace(extract_words=lambda extra_attrs=None: words)


def test_parses_both_measures_on_the_page():
    results = co.parse_page(_fake_page(FIXTURE))
    assert [r["number"] for r in results] == ["G", "H"]


def test_first_measure_does_not_pick_up_the_next_measures_badge():
    """The regression this guards: title-sized "YES"/"NO" vote-means
    badges are as large as a real title, and an earlier version let
    measure G's window absorb measure H's badge/title text."""
    g = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "G")
    assert g["title"] == "Modify Property Tax Exemption for Veterans with Disabilities"
    assert "Judicial" not in g["title"]


def test_vote_means_zone_does_not_bleed_into_next_measures_title():
    """The regression this guards: the vote-means paragraph has no
    marker of its own end, so an earlier version's NO text ran straight
    into the next measure's title ("...total. Judicial Discipline H
    Confidentiality Procedures and")."""
    g = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "G")
    assert g["no_means"].endswith("100 percent permanent and total.")
    assert "Judicial" not in g["no_means"]


def test_yes_no_and_summary_match_the_real_source_text():
    h = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "H")
    assert h["origin"] == "the legislature"
    assert h["official_summary"].startswith("Shall there be an amendment")
    # The Blue Book's whole sentence, its lead-in included (§7).
    assert h["yes_means"] == (
        "A “yes” vote on Amendment H creates an independent adjudicative board made up of citizens, "
        "lawyers, and judges to conduct judicial misconduct hearings and "
        "impose disciplinary actions, and allows more information to be "
        "shared earlier with the public."
    )
    assert h["no_means"] == (
        "A “no” vote on Amendment H means that a select panel of judges will continue to conduct "
        "judicial misconduct hearings and recommend disciplinary actions, "
        "and cases remain confidential unless public sanctions are "
        "recommended at the end of the process."
    )


def test_no_fiscal_impact_field_in_this_section():
    """Colorado's quick-reference section never publishes one — verified
    real, not a parsing gap (see module docstring)."""
    for r in co.parse_page(_fake_page(FIXTURE)):
        assert r["fiscal_impact"] is None


def test_page_with_no_measures_returns_empty():
    assert co.parse_page(_fake_page([])) == []


def test_a_measure_without_its_ballot_title_section_fails_the_document():
    """The regression: a measure whose "Ballot Title" row wasn't found was
    skipped (2022's referred-amendment sub-format was documented as
    "safely dropped"), publishing the Blue Book one measure short."""
    words = [w for w in FIXTURE if w["text"] != "Title"]
    with pytest.raises(ValueError):
        co.parse_page(_fake_page(words))


def test_a_blue_book_with_no_measure_is_a_failure_never_none():
    with pytest.raises(ValueError):
        co.parse_document([_fake_page([])])


def test_the_ballot_title_is_attributed_to_whoever_wrote_it():
    """The headline is the Legislative Council's; the quoted Ballot Title
    of a referred measure is the General Assembly's."""
    for r in co.parse_page(_fake_page(FIXTURE)):
        assert r["origin"] == "the legislature"
        assert r["title_authority"] == "Colorado General Assembly"
        assert r.get("official_title") is None
    assert co._ballot_title_drafter("citizen initiative") == "Colorado Title Board"
    assert co._ballot_title_drafter(None) is None


# ── 2026: real pages, and the whole-document rules ──────────────────────
#
# fixtures_co_bluebook_2026_page9/11.json are REAL page.extract_words()
# output from the 2026 Blue Book (page indexes 9 and 11): Amendment 87,
# whose YES column runs three lines past its NO column across a gutter
# narrower than find_column_boundary's 15pt minimum, and Propositions 133
# and 134, where 133's YES text repeats "child sex trafficking to" itself.
PAGE_87 = json.loads((Path(__file__).parent / "fixtures_co_bluebook_2026_page9.json").read_text())
PAGE_133 = json.loads((Path(__file__).parent / "fixtures_co_bluebook_2026_page11.json").read_text())


def test_a_narrow_gutter_is_split_at_the_no_badge():
    (m87,) = co.parse_page(_fake_page(PAGE_87))
    assert m87["yes_means"] == (
        "A “yes” vote on Amendment 87 creates a graduated state income tax that increases revenue; uses the additional money for "
        "K-12 education, health care, and early childhood care and education; and exempts additional "
        "revenue collected from the state’s constitutional revenue limit."
    )
    assert m87["no_means"] == "A “no” vote on Amendment 87 keeps the current constitutional requirement for a flat state income tax rate."


def test_a_phrase_the_official_text_repeats_is_kept():
    m133 = next(m for m in co.parse_page(_fake_page(PAGE_133)) if m["number"] == "133")
    assert m133["yes_means"].endswith("expands child sex trafficking to include buying sexual activity with a minor.")
    assert m133["no_means"].startswith("A “no” vote on Proposition 133 keeps the existing felony criminal penalties")


def _analysis_page(count: int):
    """A Blue Book analysis page: its own header, and each measure opening
    "Placed on the ballot by ..." without a Ballot Title section."""
    words, top = [{"text": "Amendment", "x0": 45, "x1": 100, "top": 20, "size": 15}], 60
    for _ in range(count):
        for i, text in enumerate("Placed on the ballot by citizen initiative".split()):
            words.append({"text": text, "x0": 115 + 40 * i, "x1": 150 + 40 * i, "top": top, "size": 9})
        for i, text in enumerate("Amendment 81 proposes amending the Colorado Constitution to:".split()):
            words.append({"text": text, "x0": 45 + 50 * i, "x1": 90 + 50 * i, "top": top + 20, "size": 13})
        top += 200
    return _fake_page(words)


def test_only_the_quick_reference_guide_pages_are_read():
    """Analysis sections also open with "Placed on the ballot by"; read as
    measures they made every whole Blue Book fail (2022, 2024, 2026)."""
    measures = co.parse_document([_fake_page(FIXTURE), _analysis_page(1), _analysis_page(1)])
    assert [m["number"] for m in measures] == ["G", "H"]


def test_a_guide_short_of_the_documents_analyses_is_refused():
    with pytest.raises(ValueError, match="2 guide measures but 3 analysis sections"):
        co.parse_document([_fake_page(FIXTURE), _analysis_page(2), _analysis_page(1)])


def test_a_tables_wrapped_column_headers_are_read_column_by_column():
    """Amendment 87's ballot title carries a table whose headers wrap over
    three lines; read row by row they interleaved into "Current Average
    Proposed Change in Average Income Income Tax ..."."""
    (m87,) = co.parse_page(_fake_page(PAGE_87))
    assert (
        "Change in Income Taxes Owed by Income Category Income Categories "
        "Current Average Income Tax Owed Proposed Average Income Tax Owed "
        "Proposed Change in Average Income Tax Owed if Passed + or ‑ "
        "$25,000 or less $59 $50 -$9 $25,001 - $50,000"
    ) in m87["official_summary"]
    # Reordered, never dropped: the same words as the zone read row by row.
    zone = [w for w in PAGE_87 if 180 < w["top"] < 630]
    assert sorted(m87["official_summary"].split()) == sorted(" ".join(co.lines_from_words(zone)).split())


def test_body_text_without_a_table_reads_row_by_row():
    h = next(r for r in co.parse_page(_fake_page(FIXTURE)) if r["number"] == "H")
    assert h["official_summary"].startswith("Shall there be an amendment")
