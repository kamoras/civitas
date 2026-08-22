"""app/fact_diff.py — the diff behind an issue's reader-facing "new" marker."""

from unittest.mock import patch

import numpy as np

from app.fact_diff import new_facts_since


class TestNewFactsSince:
    def test_no_previous_facts_treats_everything_as_new(self):
        # The caller (action.py) is responsible for suppressing this case
        # for a never-updated issue — this function itself just answers
        # "what's not in previous", which for an empty previous is all of it.
        assert new_facts_since(["a", "b"], []) == ["a", "b"]

    def test_identical_lists_have_nothing_new(self):
        assert new_facts_since(["a", "b"], ["a", "b"]) == []

    def test_only_the_added_fact_is_new(self):
        assert new_facts_since(["a", "b", "c"], ["a", "b"]) == ["c"]

    def test_order_in_previous_facts_does_not_matter(self):
        assert new_facts_since(["a", "b"], ["b", "a"]) == []

    def test_a_removed_fact_is_not_reported_as_new(self):
        # "new" means added, not "different set" — a fact dropped between
        # versions isn't something to flag on the current list at all.
        assert new_facts_since(["a"], ["a", "b"]) == []

    def test_preserves_current_facts_order_for_the_new_subset(self):
        assert new_facts_since(["z", "a", "m"], ["a"]) == ["z", "m"]

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_a_reworded_claim_is_not_reported_as_new(self, mock_embed):
        # Real production pair (id 597, 0.935 cosine): the LLM rewords the
        # same claim almost every regeneration, so exact-text matching
        # alone rarely suppressed anything — this was the live report
        # ("all the key facts always show as new").
        cosine = 0.935
        mock_embed.return_value = np.array([[cosine, (1 - cosine**2) ** 0.5], [1.0, 0.0]])
        current = ["Curlee stated she faced targeted actions related to her identity during her tenure."]
        previous = ["Curlee stated she faced targeted actions during her tenure."]
        assert new_facts_since(current, previous) == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    def test_a_genuinely_different_claim_still_reports_as_new_even_if_related(self, mock_embed):
        # Real production pair (id 602) scoring 0.764 cosine — related
        # components of a restructured narrative, not the same claim. This
        # sits inside the ambiguous band _FACT_REWORDING_THRESHOLD is
        # deliberately set above, so it must still show as new.
        cosine = 0.764
        mock_embed.return_value = np.array([[cosine, (1 - cosine**2) ** 0.5], [1.0, 0.0]])
        current = ["The decision followed months of discussions with the National Trust."]
        previous = ["The National Trust for Historic Preservation was involved in the dispute."]
        assert new_facts_since(current, previous) == current

    def test_exact_carryover_skips_the_embedding_call_entirely(self):
        # The fast path (byte-identical facts) must never need the model —
        # this is what keeps the common case cheap.
        with patch("app.pipeline.analyze.action_center._embed_texts_sim") as mock_embed:
            assert new_facts_since(["a", "b"], ["a", "b"]) == []
        mock_embed.assert_not_called()
