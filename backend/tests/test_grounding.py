"""Tests for deterministic grounding checks on LLM-generated text."""

from app.pipeline.analyze.grounding import (
    grounding_violations,
    ungrounded_numbers,
    ungrounded_statistics,
    ungrounded_titled_names,
)

SOURCE = (
    "Senate votes 68-32 to pass the funding bill. Susan Collins said the "
    "$1.2 trillion package includes 120,000 new housing vouchers. The vote "
    "happened on 2026-07-09. Factory fire kills at least 28."
)


class TestUngroundedNumbers:
    def test_grounded_numbers_pass(self):
        assert ungrounded_numbers("The 68-32 vote covers 120,000 vouchers", SOURCE) == []

    def test_fabricated_statistic_flagged(self):
        assert ungrounded_numbers("The bill allocates $4.7 billion", SOURCE) == ["4.7"]

    def test_thousands_separator_normalized(self):
        assert ungrounded_numbers("120000 vouchers", SOURCE) == []

    def test_currency_and_units_reduce_to_digits(self):
        assert ungrounded_numbers("a $1.2T package", SOURCE) == []

    def test_leading_zero_dates_match(self):
        # source has 2026-07-09; "July 9" and "month 7" are the same numbers
        assert ungrounded_numbers("On July 9, 2026", SOURCE) == []

    def test_decimal_keeps_leading_zero(self):
        assert ungrounded_numbers("a 0.5 percent cut", SOURCE) == ["0.5"]

    def test_no_numbers_is_clean(self):
        assert ungrounded_numbers("Senators debated the measure.", SOURCE) == []


class TestUngroundedTitledNames:
    def test_titled_name_grounded_by_untitled_source(self):
        # "Sen. Collins" is fine when source says "Susan Collins" without title
        assert ungrounded_titled_names("Sen. Collins praised the vote", SOURCE) == []

    def test_invented_official_flagged(self):
        out = ungrounded_titled_names("Senator Whitfield objected", SOURCE)
        assert out == ["Senator Whitfield"]

    def test_untitled_names_not_checked(self):
        # Bare names aren't titled-official claims; other validators own those.
        assert ungrounded_titled_names("Whitfield objected", SOURCE) == []


class TestUngroundedStatistics:
    def test_fabricated_money_flagged(self):
        assert ungrounded_statistics("costs $4.7 billion", SOURCE) == ["4.7"]

    def test_fabricated_percent_flagged(self):
        assert ungrounded_statistics("supported by 87% of voters", SOURCE) == ["87"]

    def test_contextual_plain_numbers_ignored(self):
        # "three of the 12 members" — 12 is not statistic-shaped, so long-form
        # prose isn't rejected over phrasing differences.
        assert ungrounded_statistics("three of the 12 members agreed", SOURCE) == []

    def test_grounded_statistic_passes(self):
        assert ungrounded_statistics("the $1.2 trillion package", SOURCE) == []


class TestGroundingViolations:
    def test_clean_text_no_violations(self):
        assert grounding_violations("Collins backed the 68-32 vote.", SOURCE) == []

    def test_reports_both_kinds(self):
        problems = grounding_violations(
            "Rep. Alvarez said the bill adds $9.9 billion.", SOURCE
        )
        assert len(problems) == 2
        assert any("9.9" in p for p in problems)
        assert any("Alvarez" in p for p in problems)
