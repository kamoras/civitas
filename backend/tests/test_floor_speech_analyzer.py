"""Tests for floor speech advocacy analysis."""

from app.pipeline.analyze.floor_speech_analyzer import analyze_floor_advocacy


class TestAnalyzeFloorAdvocacy:
    """Verify floor advocacy analysis for Promise Persistence scoring."""

    def test_empty_remarks(self):
        result = analyze_floor_advocacy([], [])
        assert result["advocacyCoverage"] == 0.0
        assert result["totalRemarks"] == 0
