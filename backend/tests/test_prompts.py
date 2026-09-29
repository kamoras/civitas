"""Tests for parse_explore_document_summary — the plain-text
SUMMARY/KEY POINTS/IMPACT splitter shared by explore.py's streaming
summary endpoint (re-parsed on every chunk) and its cache-write path
(parsed once at the end).
"""

from app.pipeline.analyze.prompts import parse_explore_document_summary


class TestParseExploreDocumentSummary:
    def test_full_text_with_all_three_sections(self):
        text = (
            "SUMMARY: The bill funds highway repairs in three states.\n"
            "KEY POINTS:\n"
            "- Allocates $2B over five years\n"
            "- Requires state matching funds\n"
            "IMPACT: Commuters in affected states see fewer road closures."
        )
        result = parse_explore_document_summary(text)
        assert result["summary"] == "The bill funds highway repairs in three states."
        assert result["keyPoints"] == [
            "Allocates $2B over five years",
            "Requires state matching funds",
        ]
        assert result["impact"] == "Commuters in affected states see fewer road closures."

    def test_impact_omitted_per_prompt_instruction(self):
        text = (
            "SUMMARY: A resolution honoring a retiring public servant.\n"
            "KEY POINTS:\n"
            "- Recognizes 30 years of service"
        )
        result = parse_explore_document_summary(text)
        assert result["summary"] == "A resolution honoring a retiring public servant."
        assert result["keyPoints"] == ["Recognizes 30 years of service"]
        assert result["impact"] == ""

    def test_partial_stream_before_key_points_marker_arrives(self):
        """Mid-stream text (marker not yet emitted) must still parse as a
        usable partial summary — this is the exact shape the frontend
        re-parses after every chunk while the LLM is still generating."""
        text = "SUMMARY: The bill funds highway repairs"
        result = parse_explore_document_summary(text)
        assert result["summary"] == "The bill funds highway repairs"
        assert result["keyPoints"] == []
        assert result["impact"] == ""

    def test_no_markers_at_all_treated_as_pure_summary(self):
        result = parse_explore_document_summary("SUMMARY: Just a short summary.")
        assert result["summary"] == "Just a short summary."
        assert result["keyPoints"] == []
        assert result["impact"] == ""

    def test_key_points_lines_not_starting_with_dash_are_ignored(self):
        text = "SUMMARY: Text.\nKEY POINTS:\nsome preamble the model added\n- Real point\nIMPACT: X"
        result = parse_explore_document_summary(text)
        assert result["keyPoints"] == ["Real point"]


class TestCutOff:
    """A generation stopped at its token limit ends mid-sentence: the
    section it was writing is dropped."""

    def test_in_the_impact(self):
        text = "SUMMARY: Whole.\nKEY POINTS:\n- One\n- Two\nIMPACT: Half a sen"
        assert parse_explore_document_summary(text, cut_off=True) == {
            "summary": "Whole.", "keyPoints": ["One", "Two"], "impact": "",
        }

    def test_in_the_key_points(self):
        text = "SUMMARY: Whole.\nKEY POINTS:\n- One\n- Tw"
        assert parse_explore_document_summary(text, cut_off=True)["keyPoints"] == ["One"]

    def test_in_the_summary_leaves_nothing_usable(self):
        assert parse_explore_document_summary("SUMMARY: Half a", cut_off=True)["summary"] == ""

    def test_decided_by_the_sections_as_parsed_not_by_marker_order(self):
        # No KEY POINTS: everything is summary, so the summary was cut.
        text = "SUMMARY: foo. IMPACT: bar ba"
        assert parse_explore_document_summary(text, cut_off=True)["summary"] == ""
        # An IMPACT: inside the summary doesn't make the key points whole.
        text = "SUMMARY: see IMPACT: x.\nKEY POINTS:\n- One\n- Tw"
        assert parse_explore_document_summary(text, cut_off=True)["keyPoints"] == ["One"]

    def test_cut_partway_through_a_marker_keeps_every_whole_section(self):
        text = "SUMMARY: Complete sentence.\nKEY POI"
        assert parse_explore_document_summary(text, cut_off=True) == {
            "summary": "Complete sentence.", "keyPoints": [], "impact": "",
        }
        text = "SUMMARY: Whole.\nKEY POINTS:\n- One\n- Two\nIMPA"
        assert parse_explore_document_summary(text, cut_off=True)["keyPoints"] == ["One", "Two"]

    def test_cut_just_after_a_key_point_ended_keeps_that_point(self):
        text = "SUMMARY: s.\nKEY POINTS:\n- a\n- b\n"
        assert parse_explore_document_summary(text, cut_off=True)["keyPoints"] == ["a", "b"]
        text = "SUMMARY: s.\nKEY POINTS:\n"
        assert parse_explore_document_summary(text, cut_off=True) == {
            "summary": "s.", "keyPoints": [], "impact": "",
        }

    def test_a_line_break_does_not_end_the_summary_or_the_impact(self):
        # Prose that can run to another paragraph: stopped after one, the
        # section is still unfinished.
        assert parse_explore_document_summary("SUMMARY: The bill does X.\n", cut_off=True)["summary"] == ""
        text = "SUMMARY: s.\nKEY POINTS:\n- a\nIMPACT: First paragraph.\n"
        assert parse_explore_document_summary(text, cut_off=True) == {
            "summary": "s.", "keyPoints": ["a"], "impact": "",
        }
