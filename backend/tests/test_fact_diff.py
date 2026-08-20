"""app/fact_diff.py — the diff behind an issue's reader-facing "new" marker."""

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
