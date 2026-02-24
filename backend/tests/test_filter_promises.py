"""Tests for the senator service promise quality filters.

These test the _filter_promises function which runs at read time to fix
data quality issues from LLM output that was persisted to the database.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.senator_service import _filter_promises


def _make_promise(
    text="Lower drug costs",
    category="healthcare",
    alignment="kept",
    analysis="Senator voted for healthcare expansion.",
    related_votes=None,
):
    """Build a mock CampaignPromise ORM-like object."""
    return SimpleNamespace(
        promise_text=text,
        category=category,
        alignment=alignment,
        analysis=analysis,
        related_votes=json.dumps(related_votes or []),
    )


class TestFilterPromises:
    """Tests for the read-time promise quality filter."""

    def test_passthrough_valid_promise(self):
        promises = [_make_promise()]
        result = _filter_promises(promises)
        assert len(result) == 1
        assert result[0].alignment == "kept"
        assert result[0].promise_text == "Lower drug costs"

    def test_filler_analysis_stripped(self):
        promises = [
            _make_promise(analysis="Senator has received funding from healthcare PACs."),
        ]
        result = _filter_promises(promises)
        assert result[0].analysis == ""

    def test_filler_political_pac_stripped(self):
        promises = [
            _make_promise(analysis="This is, a political PAC that supports healthcare."),
        ]
        result = _filter_promises(promises)
        assert result[0].analysis == ""

    def test_broken_label_corrected_when_analysis_says_kept(self):
        promises = [
            _make_promise(
                alignment="broken",
                analysis="Senator voted Yea on HR.1, which aligns with this promise.",
            ),
        ]
        result = _filter_promises(promises)
        assert result[0].alignment == "kept"

    def test_kept_label_corrected_when_analysis_says_broken(self):
        promises = [
            _make_promise(
                alignment="kept",
                analysis="Senator voted against the bill, contradicting this pledge.",
            ),
        ]
        result = _filter_promises(promises)
        assert result[0].alignment == "broken"

    def test_contradictory_signals_downgraded(self):
        promises = [
            _make_promise(
                alignment="kept",
                analysis="Senator supports the bill but voted against the final version.",
            ),
        ]
        result = _filter_promises(promises)
        assert result[0].alignment == "unclear"

    def test_duplicate_bill_sets_downgraded(self):
        promises = [
            _make_promise(
                text="Lower drug costs",
                related_votes=["HR.1", "HR.2"],
            ),
            _make_promise(
                text="Expand Medicare",
                related_votes=["HR.1", "HR.2"],
            ),
        ]
        result = _filter_promises(promises)
        for p in result:
            assert p.alignment == "unclear"
            assert p.related_votes == []

    def test_unique_bill_sets_preserved(self):
        promises = [
            _make_promise(text="Healthcare", related_votes=["HR.1"]),
            _make_promise(text="Defense", related_votes=["HR.2"]),
        ]
        result = _filter_promises(promises)
        assert result[0].related_votes == ["HR.1"]
        assert result[1].related_votes == ["HR.2"]

    def test_empty_bill_sets_not_flagged_as_duplicate(self):
        """Two promises with empty bill sets should NOT trigger the duplicate guard."""
        promises = [
            _make_promise(text="Promise A", related_votes=[]),
            _make_promise(text="Promise B", related_votes=[]),
        ]
        result = _filter_promises(promises)
        assert result[0].alignment == "kept"
        assert result[1].alignment == "kept"

    def test_empty_input(self):
        assert _filter_promises([]) == []

    def test_none_analysis_handled(self):
        p = _make_promise(analysis=None)
        result = _filter_promises([p])
        assert len(result) == 1
        assert result[0].analysis == ""

    def test_none_alignment_handled(self):
        p = _make_promise(alignment=None)
        result = _filter_promises([p])
        assert len(result) == 1

    def test_related_votes_deserialized(self):
        p = _make_promise(related_votes=["HR.1", "S.200"])
        result = _filter_promises([p])
        assert result[0].related_votes == ["HR.1", "S.200"]

    def test_no_related_votes_field(self):
        p = _make_promise()
        p.related_votes = None
        result = _filter_promises([p])
        assert result[0].related_votes == []
