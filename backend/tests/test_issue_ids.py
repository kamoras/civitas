"""app/issue_ids.py — the public/private id bijection ActionIssue's share
links depend on. Uniqueness has to hold by construction, not by luck, so
these pin the round trip and the no-collision property directly rather than
trusting a probabilistic token generator."""

from app.issue_ids import from_public_id, to_public_id


class TestToPublicId:
    def test_round_trips(self):
        for issue_id in (0, 1, 2, 42, 999_999, 2**31):
            assert from_public_id(to_public_id(issue_id)) == issue_id

    def test_never_collides_across_a_realistic_range(self):
        ids = range(20_000)
        assert len({to_public_id(i) for i in ids}) == len(ids)

    def test_always_letter_prefixed_so_it_cannot_look_like_a_legacy_id(self):
        # get_action_issue (api/action.py) treats an all-digits path segment
        # as a pre-public-id link and looks it up by the raw int id instead —
        # that fallback is only safe if a real public id can never be
        # all-digits.
        for issue_id in (0, 1, 100, 123_456):
            assert not to_public_id(issue_id).isdigit()


class TestFromPublicId:
    def test_rejects_a_bare_number(self):
        assert from_public_id("42") is None

    def test_rejects_non_hex_suffix(self):
        assert from_public_id("iNoSuchIssue") is None

    def test_rejects_wrong_length(self):
        assert from_public_id("iabc") is None
