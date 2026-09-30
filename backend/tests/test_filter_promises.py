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
    analysis="Senator voted Yea on H.R. 3 to lower drug costs.",
    related_votes=None,
    related_bills=None,
    party_alignment=None,
):
    """Build a mock CampaignPromise ORM-like object."""
    return SimpleNamespace(
        promise_text=text,
        category=category,
        alignment=alignment,
        analysis=analysis,
        related_votes=json.dumps(related_votes if related_votes is not None else ["H.R. 3"]),
        related_bills=json.dumps(related_bills if related_bills is not None else []),
        party_alignment=party_alignment,
    )


class TestFilterPromises:
    """Tests for the read-time promise quality filter."""

    def test_passthrough_valid_promise(self):
        promises = [_make_promise()]
        result = _filter_promises(promises)
        assert len(result) == 1
        assert result[0].alignment == "kept"
        assert result[0].promise_text == "Lower drug costs"

    @pytest.mark.parametrize("analysis", [
        pytest.param("Senator has received funding from healthcare PACs.", id="filler_funding"),
        pytest.param("This is, a political PAC that supports healthcare.", id="filler_political_pac"),
        pytest.param(None, id="none_analysis"),
    ])
    def test_analysis_reduced_to_empty(self, analysis):
        result = _filter_promises([_make_promise(analysis=analysis, related_votes=["H.R. 3"])])
        assert len(result) == 1
        assert result[0].analysis == ""

    @pytest.mark.parametrize("alignment, analysis, related_votes, expected", [
        pytest.param("broken", "Senator voted Yea on HR.1, which aligns with this promise.", None, "kept",
                     id="broken_corrected_when_analysis_says_kept"),
        pytest.param("kept", "Senator voted against the bill, contradicting this pledge.", None, "broken",
                     id="kept_corrected_when_analysis_says_broken"),
        pytest.param("kept", "Senator supports the bill but voted against the final version.", None, "unclear",
                     id="contradictory_signals_downgraded"),
        # A 'kept' promise whose analysis doesn't cite a bill becomes 'unclear'.
        pytest.param("kept", "Senator supports healthcare expansion.", [], "unclear",
                     id="kept_without_bill_ref_downgraded"),
        pytest.param(None, "Senator voted Yea on H.R. 3 to lower drug costs.", None, "unclear",
                     id="none_alignment_reads_as_unclear"),
    ])
    def test_alignment_label_corrected(self, alignment, analysis, related_votes, expected):
        promise = _make_promise(alignment=alignment, analysis=analysis, related_votes=related_votes)
        result = _filter_promises([promise])
        assert result[0].alignment == expected

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
        # Also the JSON related_votes column read back as a list.
        promises = [
            _make_promise(text="Lower healthcare costs for families", related_votes=["HR.1"]),
            _make_promise(text="Strengthen national defense spending", related_votes=["HR.2"]),
        ]
        result = _filter_promises(promises)
        assert result[0].related_votes == ["HR.1"]
        assert result[1].related_votes == ["HR.2"]

    def test_empty_bill_sets_not_flagged_as_duplicate(self):
        """Two promises with empty bill sets should NOT trigger the duplicate guard."""
        promises = [
            _make_promise(text="Expand renewable energy funding", related_votes=[], alignment="unclear", analysis="No related votes found."),
            _make_promise(text="Protect public lands from development", related_votes=[], alignment="unclear", analysis="No related votes found."),
        ]
        result = _filter_promises(promises)
        assert result[0].alignment == "unclear"
        assert result[1].alignment == "unclear"

    def test_empty_input(self):
        assert _filter_promises([]) == []

    def test_error_page_promise_filtered(self):
        """Promises scraped from 404 pages should be removed entirely."""
        promises = [
            _make_promise(
                text="404 Error Page Requested Page Not Found (404). Search Senate.gov",
                analysis="The senator's voting record does not align.",
            ),
        ]
        result = _filter_promises(promises)
        assert len(result) == 0

    def test_no_related_votes_field(self):
        p = _make_promise(alignment="unclear", analysis="No related legislation found.")
        p.related_votes = None
        result = _filter_promises([p])
        assert result[0].related_votes == []
