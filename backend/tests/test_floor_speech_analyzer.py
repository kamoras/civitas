"""Tests for floor speech advocacy analysis."""

from app.pipeline.analyze.floor_speech_analyzer import (
    _classify_remark_categories,
    analyze_floor_advocacy,
)


class TestClassifyRemarkCategories:
    """Verify keyword-based category matching."""

    def test_healthcare_keywords(self):
        cats = _classify_remark_categories(
            "We must lower prescription drug costs for Medicare recipients"
        )
        assert "healthcare" in cats

    def test_defense_keywords(self):
        cats = _classify_remark_categories(
            "Our military troops deserve better veteran healthcare"
        )
        assert "defense" in cats
        assert "healthcare" in cats

    def test_environment_keywords(self):
        cats = _classify_remark_categories(
            "Climate change threatens our renewable energy future"
        )
        assert "environment" in cats

    def test_no_match(self):
        cats = _classify_remark_categories(
            "I thank the distinguished senator for yielding time"
        )
        assert len(cats) == 0

    def test_multiple_categories(self):
        cats = _classify_remark_categories(
            "The economy needs jobs in clean energy and infrastructure"
        )
        assert "economy" in cats
        assert "energy" in cats or "environment" in cats

    def test_case_insensitive(self):
        cats = _classify_remark_categories("HEALTHCARE REFORM IS CRITICAL")
        assert "healthcare" in cats


class TestAnalyzeFloorAdvocacy:
    """Verify floor advocacy analysis for Promise Persistence scoring."""

    def test_empty_remarks(self):
        result = analyze_floor_advocacy([], [])
        assert result["advocacyCoverage"] == 0.0
        assert result["totalRemarks"] == 0

    def test_full_coverage(self):
        """Senator speaks about all their promised categories."""
        remarks = [
            {"text": "We need healthcare reform now", "title": "", "date": "2025-02-01"},
            {"text": "The economy must create more jobs for workers", "title": "", "date": "2025-02-02"},
            {"text": "Climate change requires clean energy investment", "title": "", "date": "2025-02-03"},
        ]
        promises = [
            {"promiseText": "Lower drug costs", "category": "healthcare", "alignment": "kept"},
            {"promiseText": "Create jobs", "category": "economy", "alignment": "partial"},
            {"promiseText": "Fight climate change", "category": "environment", "alignment": "unclear"},
        ]
        result = analyze_floor_advocacy(remarks, promises)
        assert result["advocacyCoverage"] == 1.0
        assert result["totalRemarks"] == 3

    def test_partial_coverage(self):
        """Senator speaks about only some of their promised categories."""
        remarks = [
            {"text": "Healthcare is the most important issue for families", "title": "", "date": "2025-02-01"},
        ]
        promises = [
            {"promiseText": "Lower drug costs", "category": "healthcare", "alignment": "kept"},
            {"promiseText": "Secure the border", "category": "immigration", "alignment": "unclear"},
        ]
        result = analyze_floor_advocacy(remarks, promises)
        assert result["advocacyCoverage"] == 0.5
        assert "healthcare" in result["advocatedCategories"]

    def test_remarks_beyond_promises(self):
        """Senator speaks on topics not in their promises — still counted."""
        remarks = [
            {"text": "We must strengthen our military defense capabilities", "title": "", "date": "2025-02-01"},
        ]
        promises = [
            {"promiseText": "Lower drug costs", "category": "healthcare", "alignment": "kept"},
        ]
        result = analyze_floor_advocacy(remarks, promises)
        assert result["advocacyCoverage"] == 0.0
        assert "defense" in result["advocatedCategories"]
        assert result["totalRemarks"] == 1

    def test_other_category_excluded(self):
        """Promises with 'other' category are excluded from coverage calculation."""
        remarks = [
            {"text": "We need healthcare reform", "title": "", "date": "2025-02-01"},
        ]
        promises = [
            {"promiseText": "Lower drug costs", "category": "healthcare", "alignment": "kept"},
            {"promiseText": "Something vague", "category": "other", "alignment": "unclear"},
        ]
        result = analyze_floor_advocacy(remarks, promises)
        assert result["advocacyCoverage"] == 1.0

    def test_title_included_in_classification(self):
        """The granule title should also be used for category matching."""
        remarks = [
            {"text": "I yield my time", "title": "IMMIGRATION REFORM ACT", "date": "2025-02-01"},
        ]
        promises = [
            {"promiseText": "Secure the border", "category": "immigration", "alignment": "unclear"},
        ]
        result = analyze_floor_advocacy(remarks, promises)
        assert result["advocacyCoverage"] == 1.0
