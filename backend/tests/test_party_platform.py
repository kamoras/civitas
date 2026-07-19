"""Tests for party_platform.py's refine_with_vote_data.

refine_with_vote_data had zero direct test coverage before this file,
despite being the function both senate_pipeline.py and house_pipeline.py
rely on to prefer a real roll-call split over content-only classification.
This specifically covers the 2026-07 sponsored-bills fix: a bill that is
both sponsored (content-classified) and later voted on (roll-call split
available) must show the vote-refined label, not the raw content label,
matching what "recent votes" and "key bills" already did.
"""

from app.pipeline.analyze.party_platform import refine_with_vote_data


class TestRefineWithVoteData:
    def test_vote_split_overrides_content_label(self):
        """The scenario this fix addresses: a sponsored bill's content-only
        classification said "bipartisan," but the bill later got a real
        floor vote that split cleanly along party lines. The real split
        must win — this is what makes a sponsored-bill entry consistent
        with the same bill's label elsewhere on the scorecard (key bills/
        recent votes), instead of the two disagreeing."""
        assert refine_with_vote_data("bipartisan", "R") == "R"
        assert refine_with_vote_data("bipartisan", "D") == "D"

    def test_vote_split_overrides_even_a_confident_content_label(self):
        """Content analysis is never authoritative once real vote data
        exists — a bill read as strongly partisan by content but passed
        with genuine bipartisan majorities must not keep the content label
        (this was the 2026-06 bug that pinned House IV at ~87-89 for
        every rep — see the function's own docstring)."""
        assert refine_with_vote_data("R", "bipartisan") == "bipartisan"
        assert refine_with_vote_data("D", "bipartisan") == "bipartisan"

    def test_no_vote_data_falls_back_to_content(self):
        """No roll call exists for this bill (the common case for
        sponsored bills, which mostly never reach a floor vote) — content
        classification is the only signal available and must be used
        as-is, not silently dropped to a default."""
        assert refine_with_vote_data("D", None) == "D"
        assert refine_with_vote_data("bipartisan", None) == "bipartisan"
