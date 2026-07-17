"""Unit tests for bluesky_poster helpers.

All tests are fast (no LLM, no network) — they exercise _sanitize and
_validate_facts which are pure functions with no external dependencies.
"""


import pytest

from app.pipeline.analyze.bluesky_poster import _sanitize
from app.pipeline.analyze.action_center import _validate_facts


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_clean_text_unchanged(self):
        text = "Senate passes major healthcare bill. Provisions take effect next year."
        assert _sanitize(text, 240) == text

    def test_trailing_hashtags_converted(self):
        result = _sanitize("Ukraine intensifies campaign. #Ukraine #War", 240)
        assert "#" not in result
        assert "Ukraine" in result
        assert "War" in result

    def test_inline_hashtags_keep_word(self):
        # #rates and #inflation should become plain words, not vanish
        result = _sanitize("Fed pauses #rates hikes as #inflation cools.", 240)
        assert "#" not in result
        assert "rates" in result
        assert "inflation" in result
        assert result.endswith("cools.")

    def test_truncates_at_sentence_boundary(self):
        text = "Senate passes major climate bill. The legislation includes new emissions targets for industrial facilities. Additional provisions address renewable energy subsidies."
        result = _sanitize(text, 80)
        assert result == "Senate passes major climate bill."
        assert len(result) <= 80

    def test_falls_back_to_word_boundary_when_no_sentence(self):
        # No period anywhere — should trim to last space
        text = "A very long run-on sentence that never ends and keeps going and going and going past the budget"
        result = _sanitize(text, 40)
        assert len(result) <= 40
        assert not result.endswith(" ")  # no trailing space
        assert " " not in result[result.rfind(" ") + 1:]  # ends on a complete word

    def test_under_budget_no_truncation(self):
        text = "Short sentence."
        assert _sanitize(text, 240) == "Short sentence."

    def test_hashtag_then_truncation(self):
        # Hashtags stripped first, then length enforced
        text = "Fed signals rate pause. #Fed #Rates The economy continues to adjust to prior hikes."
        result = _sanitize(text, 50)
        assert "#" not in result
        assert len(result) <= 50
        assert result.endswith(".")

    def test_empty_string(self):
        assert _sanitize("", 240) == ""

    def test_only_hashtags(self):
        result = _sanitize("#Ukraine #War #Politics", 240)
        assert "#" not in result
        # Words are preserved
        assert "Ukraine" in result


# ---------------------------------------------------------------------------
# _validate_facts
# ---------------------------------------------------------------------------

class TestValidateFacts:

    # --- Self-referential comparison detection ---

    @pytest.mark.parametrize(
        "fact",
        [
            pytest.param(
                "Meta's market value overtakes that of Meta Platforms, Tesla, and Micron.",
                id="drops_meta_surpasses_meta",
            ),
            pytest.param(
                "Micron's market value has surpassed Meta Platforms, Tesla, and Micron.",
                id="drops_micron_surpasses_micron",
            ),
            # "Apple" root "apple" appears on both sides
            pytest.param(
                "Apple's revenue exceeds Apple's previous record by 10%.",
                id="drops_apple_beats_apple",
            ),
        ],
    )
    def test_self_referential_comparison_dropped(self, fact):
        assert _validate_facts([fact]) == []

    def test_keeps_apple_surpasses_microsoft(self):
        facts = ["Apple surpassed Microsoft in market cap for the first time since 2021."]
        result = _validate_facts(facts)
        assert len(result) == 1
        assert "Apple" in result[0]

    def test_keeps_fact_without_comparison_verb(self):
        facts = ["The Federal Reserve signaled it may pause rate hikes this year."]
        result = _validate_facts(facts)
        assert result == facts

    def test_keeps_resolved_event_fact(self):
        facts = ["Weinstein's New York rape charge was dropped after an overturned conviction."]
        result = _validate_facts(facts)
        assert result == facts

    def test_mixed_good_and_bad_facts(self):
        facts = [
            "Meta's market cap surpasses Meta Platforms.",           # bad
            "Senate passed a budget resolution with 51 votes.",      # good
            "Apple beat Samsung in global smartphone shipments.",     # good — distinct
        ]
        result = _validate_facts(facts)
        assert len(result) == 2
        assert any("Senate" in f for f in result)
        assert any("Apple" in f for f in result)
        assert not any("Meta's market cap surpasses Meta" in f for f in result)

    # --- Input type handling ---

    def test_non_list_returns_empty(self):
        assert _validate_facts("not a list") == []
        assert _validate_facts({"key": "val"}) == []
        assert _validate_facts(None) == []

    def test_empty_list_returns_empty(self):
        assert _validate_facts([]) == []

    def test_skips_non_string_items(self):
        facts = [42, None, "Valid fact about the Senate vote.", {"bad": "entry"}]
        result = _validate_facts(facts)
        assert result == ["Valid fact about the Senate vote."]

    def test_strips_whitespace_from_facts(self):
        facts = ["  Fact with leading spaces.  "]
        result = _validate_facts(facts)
        assert result == ["Fact with leading spaces."]

    # --- Edge cases for comparison detection ---

    def test_comparison_verb_with_distinct_entities_kept(self):
        # "surpass" with two clearly different capitalized entities
        facts = ["Biden's approval rating surpassed Trump's for the first time this quarter."]
        result = _validate_facts(facts)
        assert len(result) == 1

    def test_no_capitalized_words_comparison_kept(self):
        # Comparison verb but no proper nouns to detect self-reference
        facts = ["Inflation exceeded expectations for the third consecutive month."]
        result = _validate_facts(facts)
        assert result == facts
