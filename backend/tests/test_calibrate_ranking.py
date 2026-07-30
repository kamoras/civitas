"""Tests for the derivations behind every explore ranking parameter.

Nothing in the ranking is a typed-in number any more, which moves the
burden onto the arithmetic that produces them. These cover the properties
each derivation is supposed to have — especially the ones that make a
signal disappear when the corpus cannot support it, since those are what
keep a prior from inventing an ordering out of nothing.
"""

import pytest

from app.models import ExploreDocument  # noqa: F401  (registers the table)
from app.pipeline import explore_ranking
from app.pipeline.calibrate_ranking import (
    MAX_PAGE_SIZE,
    RRF_K,
    derive_candidate_pool,
    derive_diversity_cap,
    derive_fingerprint,
    derive_prior_weights,
    derive_text_shape,
)


class TestPriorWeights:
    def test_a_signal_the_corpus_cannot_support_gets_no_weight(self):
        # The property the whole authority design leans on: a citation graph
        # with no edges orders nothing, so it must contribute nothing —
        # falling out of the arithmetic rather than needing a special case.
        weights = derive_prior_weights(delta=10.0, coverage={"authority": 0.0})
        assert weights["authority"] == 0.0

    def test_weight_scales_with_how_much_the_signal_distinguishes(self):
        half = derive_prior_weights(10.0, {"freshness": 0.5})["freshness"]
        full = derive_prior_weights(10.0, {"freshness": 1.0})["freshness"]
        # abs tolerance, not relative: the returned weights are rounded to
        # four places, so exact proportionality is not available to assert.
        assert full == pytest.approx(half * 2, abs=1e-4)

    def test_better_retrievers_shrink_the_priors(self):
        # δ is the retrievers' own resolution limit. If they agree closely,
        # there is less room where a prior may reorder without overriding
        # relevance, and the weight must fall accordingly.
        sharp = derive_prior_weights(2.0, {"freshness": 1.0})["freshness"]
        vague = derive_prior_weights(20.0, {"freshness": 1.0})["freshness"]
        assert sharp < vague

    def test_perfectly_agreeing_retrievers_leave_no_room_at_all(self):
        assert derive_prior_weights(0.0, {"freshness": 1.0})["freshness"] == 0.0

    def test_a_full_coverage_prior_matches_the_score_gap_it_is_derived_from(self):
        # w is defined so its whole swing equals the score gap δ ranks buys
        # across both retrieval channels. Restating that here pins the
        # formula against an independent calculation of the same thing.
        delta, channels = 8.0, 2
        gap = 1.0 / (RRF_K + 1) - 1.0 / (RRF_K + 1 + delta)
        expected = channels * (RRF_K + 1) * gap
        assert derive_prior_weights(delta, {"freshness": 1.0})["freshness"] == (
            pytest.approx(round(expected, 4))
        )

    def test_weights_stay_below_a_retrieval_channel(self):
        # A prior that outweighed a retrieval channel would be a sort, not a
        # tie-breaker. Even at total coverage and a very blunt retriever.
        assert derive_prior_weights(50.0, {"freshness": 1.0})["freshness"] < 1.0


class TestCandidatePool:
    def test_a_harsher_filter_demands_a_deeper_pool(self):
        loose = derive_candidate_pool(1.0)["default"]
        harsh = derive_candidate_pool(0.1)["default"]
        assert harsh > loose

    def test_no_filtering_still_covers_a_full_page(self):
        assert derive_candidate_pool(1.0)["default"] >= MAX_PAGE_SIZE

    def test_survival_of_zero_does_not_divide_by_zero(self):
        pool = derive_candidate_pool(0.0)
        assert pool["default"] > 0 and pool["max"] >= pool["default"]


class TestDiversityCap:
    def test_follows_the_corpus_shape(self):
        assert derive_diversity_cap([1, 1, 4, 6, 8]) == 6

    def test_a_corpus_where_no_source_repeats_caps_at_one(self):
        assert derive_diversity_cap([1, 1, 1]) == 1

    def test_empty_corpus(self):
        assert derive_diversity_cap([]) == 1


class TestFingerprint:
    def test_prefix_grows_until_distinct_documents_separate(self):
        shared = "identical opening text " * 30
        docs = [
            {"id": 1, "title": "A", "body": shared + "ending one"},
            {"id": 2, "title": "A", "body": shared + "ending two"},
        ]
        shape = derive_fingerprint(docs)
        # Long enough to reach past the shared opening, or the two collapse
        # into one result.
        assert shape["prefix_chars"] > len(shared) / 2

    def test_a_corpus_of_short_distinct_documents_needs_little_prefix(self):
        docs = [{"id": i, "title": f"Title {i}", "body": f"Body {i}"} for i in range(20)]
        assert derive_fingerprint(docs)["prefix_chars"] <= 100

    def test_empty_corpus_returns_a_usable_shape(self):
        shape = derive_fingerprint([])
        assert shape["prefix_chars"] > 0 and shape["min_chars"] > 0


class TestTextShape:
    def test_snippet_width_tracks_sentence_length(self):
        short = [{"id": 1, "title": "T", "body": "One two. Three four. Five six."}]
        long = [{"id": 1, "title": "T",
                 "body": " ".join(["word"] * 40) + ". " + " ".join(["word"] * 40) + "."}]
        assert (derive_text_shape(short)["snippet_tokens"]
                < derive_text_shape(long)["snippet_tokens"])

    def test_a_term_length_that_appears_everywhere_is_excluded(self):
        # Every document contains "a" and "I"; none of them tells you which
        # document you want (Spärck Jones 1972).
        docs = [
            {"id": i, "title": "", "body": f"a I unique{i} distinct{i} separate{i}"}
            for i in range(20)
        ]
        assert derive_text_shape(docs)["min_term_length"] > 1

    def test_empty_corpus_returns_a_usable_shape(self):
        shape = derive_text_shape([])
        assert shape["snippet_tokens"] > 0 and shape["min_term_length"] >= 1


class TestLoader:
    def test_bundled_calibration_is_present_and_complete(self):
        explore_ranking.reset_cache()
        loaded = explore_ranking.ranking(force_reload=True)
        assert explore_ranking._REQUIRED_KEYS <= set(loaded)
        assert loaded["_source"].startswith("Generated by")

    def test_every_accessor_reads_the_bundled_file(self):
        explore_ranking.reset_cache()
        assert set(explore_ranking.field_weights()) == {"title", "summary", "body"}
        assert set(explore_ranking.fusion_weights()) == {
            "semantic", "keyword", "freshness", "authority"}
        default, maximum = explore_ranking.candidate_pool()
        assert 0 < default <= maximum
        assert explore_ranking.source_diversity_cap() >= 0
        prefix, minimum = explore_ranking.fingerprint_shape()
        assert 0 < minimum <= prefix
        tokens, min_term = explore_ranking.text_shape()
        assert tokens > 0 and min_term >= 1

    def test_override_wins_and_is_restored(self):
        explore_ranking.reset_cache()
        before = explore_ranking.field_weights()
        pinned = {"title": 99.0, "summary": 1.0, "body": 1.0}
        with explore_ranking.override({"field_weights": pinned}):
            assert explore_ranking.field_weights() == pinned
        assert explore_ranking.field_weights() == before

    def test_a_missing_calibration_raises_rather_than_inventing_one(self, monkeypatch):
        # The failure mode that matters: ranking with made-up weights would
        # look like it worked.
        explore_ranking.reset_cache()
        monkeypatch.setattr(explore_ranking, "_load_from_db", lambda: None)
        monkeypatch.setattr(explore_ranking, "_load_bundled", lambda: None)
        with pytest.raises(explore_ranking.RankingCalibrationMissing):
            explore_ranking.ranking(force_reload=True)
        explore_ranking.reset_cache()
