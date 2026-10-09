"""Donor names shown as written, from the FEC's capitals."""

import pytest

from app.pipeline.transform import normalize_finance as nf


@pytest.mark.parametrize(("filed", "shown"), [
    # Names as the FEC files them; the table (app/data/name_casing.json)
    # supplies the words usage writes otherwise.
    ("UCLA", "UCLA"),
    ("AMERICAN FEDERATION OF TEACHERS, AFL-CIO COMMITTEE ON POLITICAL EDUCATION",
     "American Federation Of Teachers, AFL-CIO Committee On Political Education"),
    ("BURNS AND MCDONNELL", "Burns And McDonnell"),
    ("UNITEDHEALTH GROUP INC", "UnitedHealth Group Inc"),
    ("LOUISIANA-PACIFIC CORPORATION", "Louisiana-Pacific Corporation"),
    # The rest of a word after a digit or an apostrophe, and a word cut by one.
    ("21ST CENTURY HEALTHCARE", "21st Century Healthcare"),
    ("INT'L UNION, UNITED AUTOMOBILE WORKERS", "Int'l Union, United Automobile Workers"),
    ("AMERICA'S CREDIT UNIONS", "America's Credit Unions"),
    # Spacing tidied; a name not in capitals is as filed.
    ("RIVER FINANCIAL  CORPORATION", "River Financial Corporation"),
    ("Harvard University", "Harvard University"),
])
def test_names_are_shown_as_written(filed, shown):
    assert nf._clean_donor_name(filed) == shown


def test_without_the_table_each_word_is_capitalized(monkeypatch):
    monkeypatch.setattr(nf, "_name_casing_cache", {})
    assert nf._clean_donor_name("UCLA HEALTH") == "Ucla Health"
