"""Wisconsin's certified canvass of its 2026 partisan primary
(state_candidates_canvass_summary_pdf.py), state offices included.

Both fixtures are REAL, off the Wisconsin Elections Commission's
"Canvass Results for 2026 Partisan Primary" (WEC Canvass Reporting
System, generated 2026-08-27, 79 pages), fetched 2026-09-28:

fixtures_wi_canvass_2026.txt -- the module's own text extraction of the
file, cut to: pages 1-4 whole (all five executive offices), Congressional
Districts 1 and 2, State Senate District 9 (a Democratic column holding only
SCATTERING), Assembly Districts 3-5 (a write-in winner, wrapped
Constitution headings, a Wisconsin Green nominee) and Assembly District
74 (Chanz Green, a surname that is also a party).

fixtures_wi_canvass_pages.pdf -- pages 32 and 41 of the PDF itself,
copied out unchanged: Christine M. Sinicki's name sits 4pt ABOVE her own
vote count and Elizabeth McCrank's 6pt BELOW hers; plain text
extraction left both Winner lines nameless.
"""

from pathlib import Path

from app.pipeline.fetch import state_candidates_canvass_summary_pdf as wi

FIXTURES = Path(__file__).parent
LINES = (FIXTURES / "fixtures_wi_canvass_2026.txt").read_text().splitlines()
PAGES = (FIXTURES / "fixtures_wi_canvass_pages.pdf").read_bytes()


def _state(records):
    return {
        (r["office"], r["district"], r["party"], r["last_name"])
        for r in records if r["office"] not in ("S", "H")
    }


class TestExecutiveOffices:
    def test_the_five_offices_and_their_marked_winners(self):
        got = {t for t in _state(wi.parse_canvass(LINES, True)) if t[0] not in ("upper", "lower")}
        assert got == {
            ("governor", None, "R", "Tom Tiffany"),
            # The state's mark, not the vote order: Crowley 315,413 over
            # Francesca Hong's 311,740, listed fourth.
            ("governor", None, "D", "David Crowley"),
            ("lt_governor", None, "R", "David Varnam"),
            ("lt_governor", None, "D", "Sarah Godlewski"),
            ("attorney_general", None, "R", "Eric Toney"),
            ("attorney_general", None, "D", "Josh Kaul"),
            ("secretary_of_state", None, "R", "Jay Schroeder"),
            ("secretary_of_state", None, "D", "JoCasta Zamarripa"),
            # "Pete Karas Wisconsin" / "Green": the party wrapped.
            ("secretary_of_state", None, "G", "Pete Karas"),
            ("treasurer", None, "R", "John S. Leiber"),
            ("treasurer", None, "D", "Yee Leng Xiong"),
        }

    def test_nothing_state_level_without_the_opt_in(self):
        records = wi.parse_canvass(LINES)
        assert {r["office"] for r in records} == {"H"}
        assert {(r["district"], r["party"], r["display_name"]) for r in records} == {
            (1, "R", "Bryan Steil"), (1, "D", "Mitchell Berman"), (2, "D", "Mark Pocan"),
        }


class TestLegislature:
    def test_both_chambers(self):
        got = {t for t in _state(wi.parse_canvass(LINES, True)) if t[0] in ("upper", "lower")}
        assert got == {
            ("upper", "9", "R", "Amy Binsfeld"),
            # The write-in marker is an annotation, not part of the name.
            ("lower", "3", "R", "Ron Tusler"),
            ("lower", "3", "D", "Michael J. Goodwin"),
            ("lower", "4", "R", "David Steffen"),
            ("lower", "4", "D", "Alexia Unertl"),
            ("lower", "5", "R", "Joy Goeben"),
            ("lower", "5", "D", "Justin Schumacher"),
            ("lower", "5", "G", "David Schupbach"),
            # One trailing party is stripped, not every party-like word.
            ("lower", "74", "R", "Chanz Green"),
            ("lower", "74", "D", "Paul Johnson"),
        }

    def test_a_wrapped_party_heading_is_read_from_the_next_line(self):
        lines = [
            "Office REPRESENTATIVE TO THE ASSEMBLY DISTRICT 3 Total Votes: 6,657",
            "Party: REPRESENTATIVE TO THE ASSEMBLY DISTRICT 3 - Total Votes: 3",
            "Constitution",
            "Winner 3 100% Jane Q. Example Constitution",
        ]
        assert wi.parse_canvass(lines, True) == [
            {"office": "lower", "district": "3", "party": "C", "last_name": "Jane Q. Example"},
        ]

    def test_a_wrapped_heading_followed_by_anything_else_names_no_party(self):
        lines = [
            "Office REPRESENTATIVE TO THE ASSEMBLY DISTRICT 3 Total Votes: 6,657",
            "Party: REPRESENTATIVE TO THE ASSEMBLY DISTRICT 3 - Total Votes: 3",
            "Winner 3 100% Jane Q. Example Constitution",
        ]
        assert wi.parse_canvass(lines, True) == []


def _word(text, x0, top):
    return {"text": text, "x0": x0, "top": top}


class TestLineGrouping:
    def test_a_name_printed_off_its_count_still_reaches_the_winner_line(self):
        records = wi.parse_canvass(wi._lines(PAGES), True)
        assert _state(records) == {
            ("lower", "20", "R", "Kyle Cleary"),
            ("lower", "20", "D", "Christine M. Sinicki"),
            ("lower", "21", "R", "Dylan Pfaffenbach"),
            ("lower", "21", "D", "Daniel J. Bukiewicz"),
            ("lower", "35", "R", "Calvin Callahan"),
            ("lower", "35", "D", "Elizabeth McCrank"),
            ("lower", "36", "R", "Jeffrey L. Mursau"),
        }

    def test_a_wrapped_party_keeps_its_order(self):
        # Matthew Arndt's row: "Wisconsin" beside the name, "Green" 9pt
        # under it in the same column.
        words = [
            _word("Winner", 47, 400), _word("62", 142, 400), _word("100%", 184, 400),
            _word("Matthew", 263, 401), _word("Arndt", 300, 401),
            _word("Wisconsin", 461, 400), _word("Green", 461, 409),
        ]
        assert wi._page_lines(words) == ["Winner 62 100% Matthew Arndt Wisconsin Green"]

    def test_a_name_between_two_rows_joins_neither(self):
        # Equidistant from two counts: which row it belongs to is not
        # knowable, so both stay nameless rather than one taking a
        # stranger's name.
        words = [
            _word("Winner", 47, 400), _word("50", 142, 400), _word("60%", 184, 400),
            _word("Jane", 263, 405), _word("Example", 290, 405),
            _word("40", 142, 410), _word("40%", 184, 410),
        ]
        assert wi._page_lines(words) == ["Winner 50 60%", "Jane Example", "40 40%"]


class TestWinnerName:
    def test_one_party_label_is_stripped(self):
        assert wi._winner_name("Chanz Green Republican") == "Chanz Green"
        assert wi._winner_name("Pete Karas Wisconsin") == "Pete Karas"
        assert wi._winner_name("Matthew Arndt Wisconsin Green") == "Matthew Arndt"

    def test_scattering_and_a_lost_name_name_nobody(self):
        assert wi._winner_name("SCATTERING") is None
        assert wi._winner_name("Democrat") is None
        assert wi._winner_name("") is None
