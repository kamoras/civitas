"""Unit tests for bluesky_poster helpers.

All tests are fast (no LLM, no network) — they exercise _sanitize and
_validate_facts which are pure functions with no external dependencies.
"""


import pytest

from unittest.mock import patch

from app.pipeline.analyze.bluesky_poster import _is_near_duplicate, _sanitize
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
# _is_near_duplicate
# ---------------------------------------------------------------------------

class TestIsNearDuplicate:
    def test_identical_text_is_duplicate(self):
        post = "The Senate passed the funding bill on a 68-32 vote."
        assert _is_near_duplicate(post, [post]) is True

    def test_reworded_same_story_is_duplicate(self):
        prior = "The Senate passed the funding bill on a 68-32 vote Thursday."
        candidate = "On Thursday the Senate passed the funding bill by a 68-32 vote."
        assert _is_near_duplicate(candidate, [prior]) is True

    def test_different_story_not_duplicate(self):
        prior = "The Senate passed the funding bill on a 68-32 vote."
        candidate = "The Supreme Court heard arguments on the new immigration rule."
        assert _is_near_duplicate(candidate, [prior]) is False

    def test_genuine_update_with_new_content_not_duplicate(self):
        # Same topic but a materially new development introduces enough new
        # vocabulary to clear the threshold.
        prior = "The Senate advanced the funding bill in committee this week."
        candidate = (
            "The House rejected the funding bill 210-225 after the Senate "
            "amendment on immigration enforcement failed a procedural motion."
        )
        assert _is_near_duplicate(candidate, [prior]) is False

    def test_checks_all_prior_texts(self):
        candidate = "The Senate passed the funding bill on a 68-32 vote."
        priors = [
            "The Supreme Court heard arguments on the immigration rule.",
            "The Senate passed the funding bill on a 68-32 vote Thursday.",
        ]
        assert _is_near_duplicate(candidate, priors) is True

    def test_empty_candidate_not_duplicate(self):
        assert _is_near_duplicate("", ["some prior post text here"]) is False

    def test_no_priors_not_duplicate(self):
        assert _is_near_duplicate("A brand new post about a new topic.", []) is False


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

    # --- Meta-fact detection (fact describes the coverage, not an event) ---

    @pytest.mark.parametrize(
        "fact",
        [
            # Verbatim examples spotted on real 2026-07-19 production output
            pytest.param(
                "No specific dates or names of the bills were provided in the articles.",
                id="drops_no_dates_provided_in_articles",
            ),
            pytest.param(
                "Specific details about security protocols were mentioned but not "
                "expanded in the articles.",
                id="drops_not_expanded_in_articles",
            ),
            pytest.param(
                "No formal policy changes or legal actions were reported in the coverage.",
                id="drops_not_reported_in_coverage",
            ),
        ],
    )
    def test_meta_fact_about_coverage_dropped(self, fact):
        assert _validate_facts([fact]) == []

    def test_keeps_fact_that_mentions_report_as_a_real_document(self):
        # "report" appears, but as the actual event (a report was released),
        # not as a meta-reference to "the articles"/"the coverage" itself.
        facts = ["The inspector general released a report finding no wrongdoing."]
        result = _validate_facts(facts)
        assert result == facts


class TestProcessIssuesMetrics:
    """Audit M9: the poster's suppression and grounding-rejection paths
    must increment the run counters."""

    def _issue(self, **overrides):
        from app.models import ActionIssue
        import json as _json
        defaults = dict(
            date="2026-07-22", rank=1, is_current=True,
            title="House passes defense bill",
            summary="The House passed the bill 216-212.",
            facts=_json.dumps(["The House passed the defense bill 216-212."]),
            source_names=_json.dumps(["AP News"]),
            bsky_posted_at=None,
        )
        defaults.update(overrides)
        return ActionIssue(**defaults)

    def test_near_duplicate_suppression_increments_counter(self, db_session, monkeypatch):
        from datetime import datetime, timezone

        from app.pipeline.analyze import action_metrics, bluesky_poster

        monkeypatch.setattr(bluesky_poster.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_poster.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        prior_text = "The House passed the defense bill 216-212 on Thursday afternoon."
        prior = self._issue(
            title="Defense bill passes House",
            bsky_posted_at=datetime.now(timezone.utc),
            bsky_last_post_text=prior_text,
        )
        db_session.add(prior)
        fresh = self._issue()
        db_session.add(fresh)
        db_session.commit()

        action_metrics.reset()
        with patch.object(bluesky_poster, "_generate_new_post", return_value=prior_text):
            posted = bluesky_poster.process_issues_for_bluesky([fresh], db_session)

        assert posted == 0
        assert fresh.bsky_posted_at is not None  # marked handled, not published
        assert action_metrics.snapshot().get("bsky_posts_suppressed_near_duplicate") == 1

    def test_grounding_rejection_increments_counter(self, db_session):
        from app.pipeline.analyze import action_metrics, bluesky_poster

        issue = self._issue()
        # Post invents a figure not in the issue's material — grounding
        # rejects it on both attempts, so no post text is returned.
        with patch.object(
            bluesky_poster, "call_llm",
            return_value={"post": "The House passed the defense bill with $900 billion in new spending."},
        ):
            action_metrics.reset()
            text = bluesky_poster._generate_new_post(issue, "2026-07-22")

        assert text is None
        assert action_metrics.snapshot().get("bsky_post_grounding_rejections") == 2

    def test_former_president_status_hallucination_rejected(self, db_session):
        # 2026-07 live case: the post described "former President Donald
        # Trump" while the issue's material said "President Trump" — the
        # model's stale training data demoting the sitting president. The
        # grounding gate must reject it, not publish it.
        import json as _json

        from app.pipeline.analyze import action_metrics, bluesky_poster

        issue = self._issue(
            title="President Trump announces new tariffs",
            summary="President Trump announced tariffs on steel imports.",
            facts=_json.dumps(["President Trump announced tariffs on steel imports."]),
        )
        with patch.object(
            bluesky_poster, "call_llm",
            return_value={"post": "Former President Donald Trump announced tariffs on steel imports."},
        ):
            action_metrics.reset()
            text = bluesky_poster._generate_new_post(issue, "2026-07-22")

        assert text is None
        assert action_metrics.snapshot().get("bsky_post_grounding_rejections") == 2

    def test_genuinely_former_official_still_posts(self, db_session):
        # The permissive side: when the issue's own material calls someone
        # "former", the post repeating it is grounded and publishes.
        import json as _json

        from app.pipeline.analyze import bluesky_poster

        issue = self._issue(
            title="Former President Obama criticizes ruling",
            summary="Former President Barack Obama criticized the court ruling.",
            facts=_json.dumps(["Former President Barack Obama criticized the ruling."]),
        )
        with patch.object(
            bluesky_poster, "call_llm",
            return_value={"post": "Former President Barack Obama criticized the court ruling."},
        ):
            text = bluesky_poster._generate_new_post(issue, "2026-07-22")

        assert text == "Former President Barack Obama criticized the court ruling."
