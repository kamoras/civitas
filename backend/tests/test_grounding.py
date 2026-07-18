"""Tests for deterministic grounding checks on LLM-generated text."""

import pytest

from app.pipeline.analyze.grounding import (
    editorializing_language,
    grounding_violations,
    hedge_and_editorializing_violations,
    hedge_language,
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
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("The 68-32 vote covers 120,000 vouchers", [], id="grounded_numbers_pass"),
            pytest.param("The bill allocates $4.7 billion", ["4.7"], id="fabricated_statistic_flagged"),
            pytest.param("120000 vouchers", [], id="thousands_separator_normalized"),
            pytest.param("a $1.2T package", [], id="currency_and_units_reduce_to_digits"),
            # source has 2026-07-09; "July 9" and "month 7" are the same numbers
            pytest.param("On July 9, 2026", [], id="leading_zero_dates_match"),
            pytest.param("a 0.5 percent cut", ["0.5"], id="decimal_keeps_leading_zero"),
            pytest.param("Senators debated the measure.", [], id="no_numbers_is_clean"),
        ],
    )
    def test_ungrounded_numbers(self, text, expected):
        assert ungrounded_numbers(text, SOURCE) == expected


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
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("costs $4.7 billion", ["4.7"], id="fabricated_money_flagged"),
            pytest.param("supported by 87% of voters", ["87"], id="fabricated_percent_flagged"),
            # "three of the 12 members" — 12 is not statistic-shaped, so long-form
            # prose isn't rejected over phrasing differences.
            pytest.param("three of the 12 members agreed", [], id="contextual_plain_numbers_ignored"),
            pytest.param("the $1.2 trillion package", [], id="grounded_statistic_passes"),
            # A bare year is a statistic even with no $/%/magnitude word nearby —
            # it's exactly what a model invents when padding thin source facts.
            pytest.param("the ban was lifted in 2023", ["2023"], id="fabricated_year_flagged"),
            pytest.param("the 2026 session", [], id="grounded_year_passes"),
            # 1500 falls outside the plausible calendar-year range and has no
            # magnitude context, so it's treated as an ordinary contextual number.
            pytest.param("about 1500 attendees", [], id="four_digit_non_year_not_flagged_as_year_but_may_be_fabricated"),
            pytest.param("a rise of 1.5 degrees", ["1.5"], id="fabricated_magnitude_word_flagged"),
        ],
    )
    def test_ungrounded_statistics(self, text, expected):
        assert ungrounded_statistics(text, SOURCE) == expected


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


class TestHedgeLanguage:
    @pytest.mark.parametrize(
        "text, expected_count",
        [
            pytest.param("Recent reports say the Senate will vote Thursday.", 1, id="recent_reports_say"),
            pytest.param("Recent reports suggest a delay.", 1, id="recent_reports_suggest"),
            pytest.param("Coverage indicates broad support.", 1, id="coverage_indicates"),
            pytest.param("Sources say the deal is close.", 1, id="sources_say"),
            pytest.param("According to reports, the bill stalled.", 1, id="according_to_reports"),
            pytest.param("The Senate voted 68-32 to pass the bill.", 0, id="direct_reporting_is_clean"),
        ],
    )
    def test_hedge_language(self, text, expected_count):
        assert len(hedge_language(text)) == expected_count

    def test_case_insensitive(self):
        assert hedge_language("RECENT REPORTS SAY the bill passed.") != []


class TestEditorializingLanguage:
    @pytest.mark.parametrize(
        "text, expected_count",
        [
            pytest.param("The speech is warranted given the senator's concerns.", 1, id="is_warranted"),
            pytest.param("The vote was justified by prior debate.", 1, id="was_justified"),
            pytest.param("This helps advance the legislation.", 1, id="helps_advance_legislation"),
            pytest.param("This helps move the bill forward.", 1, id="helps_move_bill"),
            pytest.param(
                "The Senate passed the bill 68-32 after weeks of debate.", 0,
                id="plain_reporting_is_clean",
            ),
        ],
    )
    def test_editorializing_language(self, text, expected_count):
        assert len(editorializing_language(text)) == expected_count


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


class TestHedgeAndEditorializingViolations:
    def test_clean_text_no_violations(self):
        assert hedge_and_editorializing_violations("The Senate voted 68-32.") == []

    def test_reports_both_kinds(self):
        problems = hedge_and_editorializing_violations(
            "Sources say the move was warranted."
        )
        assert len(problems) == 2
        assert any("Sources say" in p for p in problems)
        assert any("was warranted" in p for p in problems)

    def test_hedge_only(self):
        problems = hedge_and_editorializing_violations("Coverage indicates a delay.")
        assert len(problems) == 1
        assert "Coverage indicates" in problems[0]

    def test_editorializing_only(self):
        problems = hedge_and_editorializing_violations("The vote was justified.")
        assert len(problems) == 1
        assert "was justified" in problems[0]
