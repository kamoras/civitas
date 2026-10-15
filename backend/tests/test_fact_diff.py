"""app/fact_diff.py — the diff behind an issue's reader-facing "new" marker."""

from unittest.mock import patch

import numpy as np

import json

from app.fact_diff import new_facts_since, stored_exact_new_facts, stored_new_facts
from app.models import ActionIssue
from app.pipeline.analyze.action_center import backfill_new_facts


class TestNewFactsSince:
    def test_no_previous_facts_treats_everything_as_new(self):
        # The caller (action.py) is responsible for suppressing this case
        # for a never-updated issue — this function itself just answers
        # "what's not in previous", which for an empty previous is all of it.
        assert new_facts_since(["a", "b"], []) == ["a", "b"]

    @patch("app.fact_diff._embed")
    def test_only_the_added_fact_is_new(self, mock_embed):
        # Real bug this guards against (flaky under coverage
        # instrumentation, confirmed live 2026-09-06): "a"/"b"/"c" are
        # non-empty, so remaining=["c"] and previous_facts=["a","b"]
        # both being truthy skips the early return and falls through to
        # a REAL, unmocked embedding call — this test's actual intent
        # (add-only detection) has nothing to do with embedding
        # similarity, and letting real model output decide the result
        # is fragile regardless of margin. Mocked orthogonal here so
        # only the set-membership logic is under test.
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        assert new_facts_since(["a", "b", "c"], ["a", "b"]) == ["c"]

    def test_order_in_previous_facts_does_not_matter(self):
        assert new_facts_since(["a", "b"], ["b", "a"]) == []

    def test_a_removed_fact_is_not_reported_as_new(self):
        # "new" means added, not "different set" — a fact dropped between
        # versions isn't something to flag on the current list at all.
        assert new_facts_since(["a"], ["a", "b"]) == []

    @patch("app.fact_diff._embed")
    def test_preserves_current_facts_order_for_the_new_subset(self, mock_embed):
        # Same unmocked-real-model fragility as test_only_the_added_
        # fact_is_new above -- mocked orthogonal so only order
        # preservation is under test.
        mock_embed.return_value = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        assert new_facts_since(["z", "a", "m"], ["a"]) == ["z", "m"]

    @patch("app.fact_diff._embed")
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

    @patch("app.fact_diff._embed")
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
        # Identical lists have nothing new, and the fast path (byte-identical
        # facts) must never need the model — this is what keeps the common
        # case cheap.
        with patch("app.fact_diff._embed") as mock_embed:
            assert new_facts_since(["a", "b"], ["a", "b"]) == []
        mock_embed.assert_not_called()


class TestStoredNewFacts:
    def test_a_never_updated_issue_stores_nothing_new_without_the_model(self):
        with patch("app.fact_diff._embed", side_effect=AssertionError("model used")):
            assert stored_new_facts(json.dumps(["a"]), "[]") == "[]"
            assert stored_new_facts(json.dumps(["a"]), None) == "[]"

    @patch("app.fact_diff._embed")
    def test_stores_the_added_fact(self, mock_embed):
        mock_embed.return_value = np.array([[0.0, 1.0], [1.0, 0.0]])
        assert json.loads(stored_new_facts(json.dumps(["a", "c"]), json.dumps(["a"]))) == ["c"]


class TestStoredExactNewFacts:
    def test_template_facts_are_new_exactly_when_their_text_is_without_the_model(self):
        with patch("app.fact_diff._embed", side_effect=AssertionError("model used")):
            assert json.loads(stored_exact_new_facts(
                json.dumps(["Count: 51%", "Precincts: 80%"]), json.dumps(["Count: 49%", "Precincts: 80%"])
            )) == ["Count: 51%"]
            assert stored_exact_new_facts(json.dumps(["Count: 51%"]), "[]") == "[]"


class TestBackfillNewFacts:
    @patch("app.fact_diff._embed")
    def test_fills_only_rows_written_before_the_column(self, mock_embed, db_session):
        mock_embed.return_value = np.array([[0.0, 1.0], [1.0, 0.0]])
        old = ActionIssue(date="2026-10-01", rank=1, title="Old row",
                          facts=json.dumps(["a", "c"]), previous_facts=json.dumps(["a"]), new_facts=None)
        fresh = ActionIssue(date="2026-10-01", rank=2, title="Never updated",
                            facts=json.dumps(["a"]), previous_facts="[]", new_facts=None)
        done = ActionIssue(date="2026-10-01", rank=3, title="Already stored",
                           facts=json.dumps(["a"]), previous_facts=json.dumps(["x"]), new_facts='["kept"]')
        db_session.add_all([old, fresh, done])
        db_session.commit()

        assert backfill_new_facts(db_session) == 2

        assert json.loads(old.new_facts) == ["c"]
        assert fresh.new_facts == "[]"
        assert done.new_facts == '["kept"]'
