"""Unit tests for bluesky_poster helpers.

All tests are fast (no LLM, no network) — they exercise strip_hashtags_and_truncate and
_validate_facts which are pure functions with no external dependencies.
"""


import pytest

from unittest.mock import patch

from app.pipeline.analyze.bluesky_poster import _is_near_duplicate
from app.pipeline.analyze.bluesky_utils import strip_hashtags_and_truncate
from app.pipeline.analyze.grounding import validate_facts as _validate_facts


# ---------------------------------------------------------------------------
# strip_hashtags_and_truncate
# ---------------------------------------------------------------------------

class TestStripHashtagsAndTruncate:
    def test_clean_text_unchanged(self):
        text = "Senate passes major healthcare bill. Provisions take effect next year."
        assert strip_hashtags_and_truncate(text, 240) == text

    def test_trailing_hashtags_converted(self):
        result = strip_hashtags_and_truncate("Ukraine intensifies campaign. #Ukraine #War", 240)
        assert "#" not in result
        assert "Ukraine" in result
        assert "War" in result

    def test_inline_hashtags_keep_word(self):
        # #rates and #inflation should become plain words, not vanish
        result = strip_hashtags_and_truncate("Fed pauses #rates hikes as #inflation cools.", 240)
        assert "#" not in result
        assert "rates" in result
        assert "inflation" in result
        assert result.endswith("cools.")

    def test_truncates_at_sentence_boundary(self):
        text = "Senate passes major climate bill. The legislation includes new emissions targets for industrial facilities. Additional provisions address renewable energy subsidies."
        result = strip_hashtags_and_truncate(text, 80)
        assert result == "Senate passes major climate bill."
        assert len(result) <= 80

    def test_falls_back_to_word_boundary_when_no_sentence(self):
        # No period anywhere — should trim to last space
        text = "A very long run-on sentence that never ends and keeps going and going and going past the budget"
        result = strip_hashtags_and_truncate(text, 40)
        assert len(result) <= 40
        assert not result.endswith(" ")  # no trailing space
        assert " " not in result[result.rfind(" ") + 1:]  # ends on a complete word

    def test_under_budget_no_truncation(self):
        text = "Short sentence."
        assert strip_hashtags_and_truncate(text, 240) == "Short sentence."

    def test_hashtag_then_truncation(self):
        # Hashtags stripped first, then length enforced
        text = "Fed signals rate pause. #Fed #Rates The economy continues to adjust to prior hikes."
        result = strip_hashtags_and_truncate(text, 50)
        assert "#" not in result
        assert len(result) <= 50
        assert result.endswith(".")

    def test_empty_string(self):
        assert strip_hashtags_and_truncate("", 240) == ""

    def test_only_hashtags(self):
        result = strip_hashtags_and_truncate("#Ukraine #War #Politics", 240)
        assert "#" not in result
        # Words are preserved
        assert "Ukraine" in result

    def test_no_space_or_punctuation_in_range_keeps_full_trim(self):
        # 2026-08 cleanup: this consolidates two inline copies (in
        # bluesky_spotlight.py) that computed this fallback as
        # `trimmed[:trimmed.rfind(" ")]` — when there's no space at all,
        # rfind returns -1, so that silently evaluated to `trimmed[:-1]`,
        # dropping the last character instead of keeping the whole trim.
        text = "a" * 50
        result = strip_hashtags_and_truncate(text, 40)
        assert result == "a" * 40


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
    """Audit M9: the poster's suppression path must increment the run
    counters, and every path that marks an issue posted pins its facts."""

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

    def test_near_duplicate_suppression_increments_counter(self, db_session, bluesky_configured):
        from datetime import datetime, timezone

        from app.pipeline.analyze import action_metrics, bluesky_poster

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
        with patch.object(bluesky_poster, "_compose_new_post", return_value=prior_text):
            posted = bluesky_poster.process_issues_for_bluesky([fresh], db_session)

        assert posted == 0
        assert fresh.bsky_posted_at is not None  # marked handled, not published
        assert bluesky_configured == []
        assert action_metrics.snapshot().get("bsky_posts_suppressed_near_duplicate") == 1
        # These facts were judged to have nothing new to say, so they become
        # the baseline the repost gate measures against — otherwise the same
        # suppressed update is regenerated and re-suppressed on every later
        # article-date advance.
        assert fresh.bsky_posted_facts == fresh.facts

    def test_publishing_pins_the_repost_baseline_to_what_was_posted(self, db_session, bluesky_configured):
        """The upstream repost gate needs "what have readers been told", and
        the `facts` column can't answer it — every hourly refresh overwrites
        it whether or not anything was posted."""
        from app.models import BroadcastPost
        from app.pipeline.analyze import bluesky_poster

        issue = self._issue()
        db_session.add(issue)
        db_session.commit()

        text = "The House passed the defense bill 216-212."
        with patch.object(bluesky_poster, "_compose_new_post", return_value=text):
            posted = bluesky_poster.process_issues_for_bluesky([issue], db_session)

        assert posted == 1
        assert issue.bsky_last_post_text == text
        assert issue.bsky_posted_facts == issue.facts
        post = db_session.query(BroadcastPost).one()
        assert (post.kind, post.title, post.text, post.bsky_status) == (
            "issue", "House passes defense bill", text, "sent")
        assert post.url.startswith("https://civitas-research.org/issue/")
        assert bluesky_configured == [(text, post.url)]

    def test_bluesky_refusing_the_post_still_publishes_it(self, db_session, bluesky_configured):
        """Feed readers have been told, so the baseline is pinned and the
        issue isn't published a second time; the Bluesky send is retried by
        app.broadcast, not by republishing the issue."""
        from app.models import BroadcastPost
        from app.pipeline.analyze import bluesky_poster

        bluesky_configured.ok = False
        issue = self._issue()
        db_session.add(issue)
        db_session.commit()

        with patch.object(bluesky_poster, "_compose_new_post", return_value="Some post text."):
            posted = bluesky_poster.process_issues_for_bluesky([issue], db_session)

        assert posted == 1
        assert issue.bsky_posted_at is not None
        assert issue.bsky_posted_facts == issue.facts
        assert db_session.query(BroadcastPost).one().bsky_status == "failed"

    def test_published_to_the_feed_without_a_bluesky_account(self, db_session, bluesky_outbox):
        from app.models import BroadcastPost
        from app.pipeline.analyze import bluesky_poster

        issue = self._issue()
        db_session.add(issue)
        db_session.commit()

        with patch.object(bluesky_poster, "_compose_new_post", return_value="Some post text."):
            assert bluesky_poster.process_issues_for_bluesky([issue], db_session) == 1

        assert db_session.query(BroadcastPost).one().bsky_status == "off"
        assert bluesky_outbox == []

    def test_the_mark_and_the_post_are_committed_together(self, db_session):
        """A crash right after publishing must not leave the issue unmarked,
        or the next run publishes it again."""
        from app.models import ActionIssue, BroadcastPost
        from app.pipeline.analyze import bluesky_poster

        issue = self._issue()
        db_session.add(issue)
        db_session.commit()

        seen = {}
        real_publish = bluesky_poster.broadcast.publish

        def publish_then_crash(db, **kw):
            real_publish(db, **kw)
            db.expire_all()
            seen["marked"] = db.get(ActionIssue, issue.id).bsky_posted_at is not None
            raise RuntimeError("crash after the commit")

        with patch.object(bluesky_poster, "_compose_new_post", return_value="Some post text."), \
                patch.object(bluesky_poster.broadcast, "publish", publish_then_crash):
            with pytest.raises(RuntimeError):
                bluesky_poster.process_issues_for_bluesky([issue], db_session)

        assert seen["marked"] is True
        assert db_session.query(BroadcastPost).count() == 1

    @pytest.mark.parametrize("publishes", [True, False])
    def test_every_path_that_sets_posted_at_also_sets_posted_facts(
        self, db_session, bluesky_configured, publishes,
    ):
        """bsky_posted_at set with bsky_posted_facts still NULL must only
        ever mean "row predates the column".

        database._backfill_bsky_posted_facts relies on exactly that: it
        seeds any such row on startup and is safe to re-run because the
        poster writes both in the same commit. A future path that marks an
        issue handled without pinning the baseline would silently turn
        that repair into a baseline overwrite on every deploy, so the
        invariant is asserted here rather than left implicit in the two
        tests above.
        """
        from datetime import datetime, timezone

        from app.pipeline.analyze import bluesky_poster

        issue = self._issue()
        db_session.add(issue)
        db_session.commit()

        # publishes=True is the publish path; False routes through
        # near-duplicate suppression, the other way posted_at gets set.
        prior = "The House passed the defense bill 216-212."
        if not publishes:
            db_session.add(self._issue(
                bsky_posted_at=datetime.now(timezone.utc),
                bsky_last_post_text=prior,
            ))
            db_session.commit()

        with patch.object(bluesky_poster, "_compose_new_post", return_value=prior):
            bluesky_poster.process_issues_for_bluesky([issue], db_session)

        if issue.bsky_posted_at is not None:
            assert issue.bsky_posted_facts is not None


class TestComposeNewPost:
    """The issue post is the verified lede, verbatim — not model prose.

    The LLM used to write it from the issue's title, summary and facts, and
    four live failures were caught by grounding checks (a figure not in the
    facts, "former President" for the sitting one, "a U.S. president" for a
    named one). The full story was moved off model prose after three more got
    PAST those same checks (issues 748, 750, 751); the post now carries no
    model-written word, so none of those can be produced at all."""

    def _issue(self, **overrides):
        from app.models import ActionIssue
        import json as _json
        defaults = dict(
            date="2026-07-24", rank=1, is_current=True,
            title="Trump calls for EU investigation into tech fines",
            summary="Trump said EU fines against major tech companies should be reversed.",
            facts=_json.dumps(["The EU fined three companies this year."]),
            source_names=_json.dumps(["AP News"]),
            bsky_posted_at=None,
            primary_article_date="2026-07-24",
        )
        defaults.update(overrides)
        return ActionIssue(**defaults)

    def test_the_post_is_the_lede_verbatim_and_calls_no_model(self):
        from app.pipeline.analyze import bluesky_poster

        assert not hasattr(bluesky_poster, "call_llm")
        text = bluesky_poster._compose_new_post(self._issue(), "2026-07-24")
        assert text == "Trump said EU fines against major tech companies should be reversed."

    def test_a_lede_too_long_falls_back_to_the_headline_rather_than_being_cut(self):
        from app.pipeline.analyze import bluesky_poster

        long_lede = "Trump said " + "EU fines against major tech companies should be reversed, " * 6 + "officials said."
        assert len(long_lede) > bluesky_poster.MAX_POST_CHARS
        text = bluesky_poster._compose_new_post(self._issue(summary=long_lede), "2026-07-24")
        assert text == "Trump calls for EU investigation into tech fines"

    def test_nothing_that_fits_posts_nothing_and_is_counted(self):
        from app.pipeline.analyze import action_metrics, bluesky_poster

        too_long = "x" * (bluesky_poster.MAX_POST_CHARS + 1)
        action_metrics.reset()
        assert bluesky_poster._compose_new_post(self._issue(summary=too_long, title=too_long), "2026-07-24") is None
        assert action_metrics.snapshot().get("bsky_posts_skipped_too_long") == 1

    def test_hashtags_become_words(self):
        from app.pipeline.analyze import bluesky_poster

        text = bluesky_poster._compose_new_post(self._issue(summary="The #Senate passed the bill."), "2026-07-24")
        assert text == "The Senate passed the bill."


    def test_a_repost_leads_with_the_fact_the_last_post_lacked(self):
        # The repost gate released this because the facts gained something;
        # the lede is unchanged, and posting it again would only be
        # suppressed as a near-duplicate — the update would never post.
        import json as _json

        from app.pipeline.analyze import bluesky_poster

        issue = self._issue(
            facts=_json.dumps(["The EU fined three companies this year.", "The Commission said it would appeal."]),
            bsky_posted_facts=_json.dumps(["The EU fined three companies this year."]),
            bsky_last_post_text="Trump said EU fines against major tech companies should be reversed.",
        )
        assert bluesky_poster._compose_new_post(issue, "2026-07-24") == "The Commission said it would appeal."

    def test_a_first_post_ignores_facts_and_uses_the_lede(self):
        import json as _json

        from app.pipeline.analyze import bluesky_poster

        issue = self._issue(facts=_json.dumps(["The Commission said it would appeal."]), bsky_posted_facts=None)
        assert bluesky_poster._compose_new_post(issue, "2026-07-24") == (
            "Trump said EU fines against major tech companies should be reversed."
        )

    def test_a_repost_with_no_new_fact_falls_back_to_the_lede(self):
        import json as _json

        from app.pipeline.analyze import bluesky_poster

        facts = _json.dumps(["The EU fined three companies this year."])
        issue = self._issue(facts=facts, bsky_posted_facts=facts)
        assert bluesky_poster._compose_new_post(issue, "2026-07-24").startswith("Trump said EU fines")


class TestStalenessPhrasing:
    """2026-07 live case: a post opened with 'On 2026-07-24' the day after
    the event, when 'Yesterday:' was both available and accurate. Only the
    one phrasing that's actually true is ever used — and it is added by
    code, not by asking a model to."""

    def _issue(self, article_date):
        from app.models import ActionIssue
        return ActionIssue(
            date="2026-07-25", rank=1, is_current=True,
            title="Trump calls for EU investigation into tech fines",
            summary="Trump said EU fines against major tech companies should be reversed.",
            facts="[]", source_names="[]", bsky_posted_at=None,
            primary_article_date=article_date,
        )

    def test_exactly_one_day_stale_opens_with_yesterday(self):
        from app.pipeline.analyze import bluesky_poster

        text = bluesky_poster._compose_new_post(self._issue("2026-07-24"), "2026-07-25")
        assert text == "Yesterday: Trump said EU fines against major tech companies should be reversed."

    def test_multiple_days_stale_opens_with_the_date(self):
        from app.pipeline.analyze import bluesky_poster

        text = bluesky_poster._compose_new_post(self._issue("2026-07-20"), "2026-07-25")
        assert text == "On July 20: Trump said EU fines against major tech companies should be reversed."

    def test_not_stale_has_no_prefix(self):
        from app.pipeline.analyze import bluesky_poster

        text = bluesky_poster._compose_new_post(self._issue("2026-07-25"), "2026-07-25")
        assert text == "Trump said EU fines against major tech companies should be reversed."

    def test_unreadable_or_missing_date_has_no_prefix(self):
        from app.pipeline.analyze import bluesky_poster

        assert bluesky_poster._staleness_prefix(None, "2026-07-25") == ""
        assert bluesky_poster._staleness_prefix("July 20", "2026-07-25") == ""
