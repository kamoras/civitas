"""Tests for deterministic grounding checks on LLM-generated text."""

from app.pipeline.analyze.grounding import (
    grounding_violations,
    repeated_sentences,
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

    def test_appositive_role_fabrication_flagged(self):
        # The exact production failure: a role description set off by
        # commas, not a title word directly prefixing the name — the form
        # ungrounded_titled_names didn't cover until this was found.
        text = (
            "The Senate Republican leader, Chuck Schumer, has said Graham's "
            "death has made a hard month harder for the Senate agenda."
        )
        assert ungrounded_titled_names(text, SOURCE) == ["Chuck Schumer"]

    def test_appositive_role_grounded_by_source(self):
        source = SOURCE + " Senate Majority Leader John Thune spoke afterward."
        text = "The Senate Majority Leader, John Thune, praised the vote."
        assert ungrounded_titled_names(text, source) == []

    def test_appositive_without_trailing_comma_not_matched(self):
        # Guards against over-matching an ordinary sentence start following
        # some unrelated use of a role word.
        assert ungrounded_titled_names(
            "He is the chair. Whitfield spoke next.", SOURCE
        ) == []


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

    def test_fabricated_year_flagged(self):
        # A bare year is a statistic even with no $/%/magnitude word nearby —
        # it's exactly what a model invents when padding thin source facts.
        assert ungrounded_statistics("the ban was lifted in 2023", SOURCE) == ["2023"]

    def test_grounded_year_passes(self):
        assert ungrounded_statistics("the 2026 session", SOURCE) == []

    def test_four_digit_non_year_not_flagged_as_year_but_may_be_fabricated(self):
        # 1500 falls outside the plausible calendar-year range and has no
        # magnitude context, so it's treated as an ordinary contextual number.
        assert ungrounded_statistics("about 1500 attendees", SOURCE) == []

    def test_fabricated_magnitude_word_flagged(self):
        assert ungrounded_statistics("a rise of 1.5 degrees", SOURCE) == ["1.5"]


class TestRepeatedSentences:
    def test_no_repetition_is_clean(self):
        text = "The Senate passed the bill. The House will vote next week."
        assert repeated_sentences(text) == []

    def test_verbatim_repeat_flagged(self):
        text = (
            "The proposal is currently under review by the agency. "
            "Something else happened in between. "
            "The proposal is currently under review by the agency."
        )
        assert repeated_sentences(text) == ["the proposal is currently under review by the agency"]

    def test_short_repeated_phrase_ignored(self):
        text = "He said no. Later, he said no."
        assert repeated_sentences(text) == []

    def test_whitespace_differences_still_match(self):
        text = (
            "The  agreement   includes specific targets for both countries. "
            "Filler sentence goes here now. "
            "The agreement includes specific targets for both countries."
        )
        assert repeated_sentences(text) == ["the agreement includes specific targets for both countries"]


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
