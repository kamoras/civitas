"""Tests for automatic source discovery (state_source_crawler.py).

Every fixture below is the real shape of a state's published export,
reduced: Virginia's per-locality CSV, Georgia's workbook rows, Florida's
election-night file, Washington's top-two workbook. The inference rules
are the dangerous part of this module — a wrong column is a confidently
wrong nominee — so they are pinned against the shapes that actually
exist, including the ones that fooled an earlier version.
"""

import pytest

from app.pipeline.fetch import state_source_crawler as crawler


def _rows(header: str, *lines: str) -> list[dict]:
    cols = header.split("|")
    return [dict(zip(cols, line.split("|"))) for line in lines]


class TestInferFormatFromLabels:
    """The straightforward case: the contest label names the office."""

    _ROWS = _rows(
        "Office Name|Ballot Name|Choice ID|Party|Total",
        "US Senate - Rep|Mike Collins|2|REP|369642",
        "US Senate - Rep|Derek Dooley|3|REP|300000",
        "US Senate - Rep|Total Votes||‌|669642",
        "US House of Representatives - District 13 - Dem|Marcye Scott|6|Dem|25547",
        "US House of Representatives - District 13 - Dem|Ana Ruiz|7|Dem|11000",
        "US House of Representatives - District 13 - Dem|Total Votes||‌|36547",
    )

    def test_finds_every_role(self):
        fmt = crawler.infer_format(self._ROWS)
        assert fmt["contest_column"] == "Office Name"
        assert fmt["choice_column"] == "Ballot Name"
        assert fmt["votes_column"] == "Total"
        assert fmt["party_column"] == "Party"

    def test_an_id_column_is_not_mistaken_for_the_candidate(self):
        """"Choice ID" holds as few values per contest as "Ballot Name" —
        fewer, since the summary row has a name but no id — so cardinality
        alone picks the wrong one."""
        assert crawler.infer_format(self._ROWS)["choice_column"] != "Choice ID"

    def test_summary_rows_are_excluded(self):
        assert "Total Votes" in crawler.infer_format(self._ROWS)["exclude_choices"]


class TestInferFormatFromColumns:
    """Virginia: the House label is one parse_office must refuse, and the
    Senate label in the same file is one it accepts."""

    _ROWS = _rows(
        "CandidateName|TOTAL_VOTES|Party|LocalityName|DistrictType|DistrictName|OfficeTitle|ElectionName",
        "Elaine G. Luria|900|Democratic|ACCOMACK|congressional|02|Member, House of Representatives (2nd District)|2026 August Democratic Primary",
        "Elaine G. Luria|700|Democratic|ALBEMARLE|congressional|02|Member, House of Representatives (2nd District)|2026 August Democratic Primary",
        "Patrick B. Mosolf|100|Democratic|ACCOMACK|congressional|02|Member, House of Representatives (2nd District)|2026 August Democratic Primary",
        "Patrick B. Mosolf|80|Democratic|ALBEMARLE|congressional|02|Member, House of Representatives (2nd District)|2026 August Democratic Primary",
        "Tom S. P. Perriello|500|Democratic|ACCOMACK|congressional|05|Member, House of Representatives (5th District)|2026 August Democratic Primary",
        "Suzanne K. Krzyzanowski|300|Democratic|ALBEMARLE|congressional|05|Member, House of Representatives (5th District)|2026 August Democratic Primary",
        "Some Supervisor|50|Democratic|ARLINGTON|county|ARLINGTON COUNTY|Member County Board (Arlington County)|2026 August Democratic Primary",
    )

    def test_reads_the_office_from_the_district_columns(self):
        spec = crawler.infer_format(self._ROWS)["house_from_columns"]
        assert spec == {
            "type_column": "DistrictType",
            "type_value": "congressional",
            "district_column": "DistrictName",
        }

    def test_picks_the_office_label_not_a_name_or_a_locality(self):
        """The contest is what stays CONSTANT within one district. Both
        CandidateName and LocalityName vary inside a district, and
        ElectionName never varies at all."""
        fmt = crawler.infer_format(self._ROWS)
        contest = fmt["contest_column"]
        assert (contest[0] if isinstance(contest, list) else contest) == "OfficeTitle"

    def test_picks_the_candidate_not_the_locality(self):
        """Both are name-like and both repeat once per contest — but every
        contest lists the same localities and its own candidates."""
        assert crawler.infer_format(self._ROWS)["choice_column"] == "CandidateName"

    def test_gives_up_rather_than_guess_when_nothing_identifies_an_office(self):
        rows = _rows(
            "Thing|Who|Count",
            "Best Dog|Rex|12",
            "Best Dog|Fido|8",
        )
        assert crawler.infer_format(rows) is None


class TestInferFormatTopTwo:
    """Washington's party column is free-text candidate PREFERENCE, which
    is both low-cardinality and name-like — an earlier version picked it
    as the candidate column."""

    _ROWS = _rows(
        "Office Name|Ballot Name|Party|Total",
        "U.S. Representative Congressional District 5|Ann Marie Danimus|(Prefers Democratic Party)|900",
        "U.S. Representative Congressional District 5|Michael Baumgartner|(Prefers Republican Party)|800",
        "U.S. Representative Congressional District 7|Pramila Jayapal|(Prefers Democratic Party)|700",
        "U.S. Representative Congressional District 7|Some Challenger|(Prefers Democratic Party)|100",
    )

    def test_the_preference_column_is_the_party_not_the_candidate(self):
        fmt = crawler.infer_format(self._ROWS)
        assert fmt["choice_column"] == "Ballot Name"
        assert fmt["party_column"] == "Party"


class TestContestKeying:
    """Florida keeps the party only in its own column, so the contest key
    has to carry it — otherwise both parties' primaries for a seat tally
    as ONE race and the bigger primary takes the seat outright."""

    _ROWS = _rows(
        "RaceCode|RaceName|PartyCode|Juris1num|CanNameLast|CanVotes|ElectionDate",
        "USR|Representative in Congress, District 25|DEM|025|Wasserman Schultz|900|08/18/2026",
        "USR|Representative in Congress, District 25|DEM|025|Perelman|400|08/18/2026",
        "USR|Representative in Congress, District 25|REP|025|Spalding|700|08/18/2026",
        "USR|Representative in Congress, District 9|DEM|009|Soto|600|08/18/2026",
        "USR|Representative in Congress, District 9|REP|009|Justice|300|08/18/2026",
    )

    def test_the_party_joins_the_contest_key(self):
        fmt = crawler.infer_format(self._ROWS)
        assert fmt["contest_column"] == ["RaceName", "PartyCode"]

    def test_the_election_date_column_is_found(self):
        """A file whose URL carries no date can still date itself, and a
        source that can't be dated can never clear the certification
        gate."""
        assert crawler.infer_format(self._ROWS)["held_column"] == "ElectionDate"


class TestVotesColumn:
    """A candidate filing list is not a results file, and both have a
    numeric column. Nebraska's is "Vote For" — the number of seats to
    elect — which an earlier version read as votes, making every
    candidate tied."""

    def test_a_constant_seat_count_is_not_a_vote_count(self):
        rows = _rows(
            "Office|Candidate Name|Party (if applicable)|Vote For",
            "United States Representative District 1|Mike Flood|Republican|1",
            "United States Representative District 1|Carol Blood|Democratic|1",
            "United States Representative District 2|Don Bacon|Republican|1",
        )
        assert crawler.infer_format(rows) is None

    def test_real_vote_counts_are_accepted(self):
        rows = _rows(
            "Office|Candidate Name|Party|Votes",
            "United States Representative District 1|Mike Flood|Republican|48210",
            "United States Representative District 1|Carol Blood|Democratic|21004",
            "United States Representative District 2|Don Bacon|Republican|39118",
        )
        assert crawler.infer_format(rows)["votes_column"] == "Votes"


class TestElectionPatterns:
    """Patterns are built from the election's own name with everything
    that dates it removed, so the same pattern finds the equivalent
    election in the next cycle."""

    def test_strips_the_date_and_keeps_what_the_election_is(self):
        assert crawler._name_pattern("May 19, 2026 - General Primary") == r"General\s+Primary"
        assert crawler._name_pattern("2026 Utah Primary Election") == r"Utah\s+Primary\s+Election"

    def test_two_same_day_party_primaries_become_two_patterns(self):
        patterns = crawler._election_patterns(
            ["2026 August Democratic Primary", "2026 August Republican Primary"],
        )
        assert len(patterns) == 2
        assert any("Democratic" in p for p in patterns)

    def test_a_primary_pattern_will_not_match_its_own_runoff(self):
        """"General Primary" is a prefix of "General Primary Runoff", so
        without the guard the runoff gets taken for the primary and
        thresholded as one."""
        import re
        pattern = crawler._election_patterns(["May 19, 2026 - General Primary"])
        assert re.search(pattern, "June 16th, 2026 General Primary Runoff") is None
        assert re.search(pattern, "May 19, 2026 - General Primary")


class TestGeneralise:
    def test_every_changing_number_becomes_a_wildcard(self):
        """Next cycle's file is this one with different numbers in it —
        the election date, the state's internal election id, the year."""
        import re
        pattern = crawler._generalise(
            "https://x.gov/files/20260818_ElecResultsFL.txt",
        )
        assert re.fullmatch(pattern, "https://x.gov/files/20280822_ElecResultsFL.txt")


class TestShapeOf:
    def test_reads_the_delimiter_from_the_bytes(self):
        assert crawler._shape_of(b"a\tb\tc\n1\t2\t3") == {"delimiter": "\t"}
        assert crawler._shape_of(b"a,b,c\n1,2,3") == {"delimiter": ","}
        assert crawler._shape_of(b"a|b|c\n1|2|3") == {"delimiter": "|"}

    def test_recognises_a_workbook(self):
        assert crawler._shape_of(b"PK\x03\x04" + b"x" * 20 + b"xl/worksheets/sheet1.xml") == {
            "format": "xlsx",
        }


class TestLooksFederal:
    def test_rejects_a_district_no_state_has(self):
        assert crawler._looks_federal([{"office": "H", "district": 240, "party": "R",
                                        "last_name": "X"}]) is False

    def test_rejects_nothing_at_all(self):
        assert crawler._looks_federal([]) is False
        assert crawler._looks_federal(None) is False

    def test_accepts_a_plausible_slate(self):
        assert crawler._looks_federal([
            {"office": "S", "district": None, "party": "D", "last_name": "Turek"},
            {"office": "H", "district": 4, "party": "R", "last_name": "McGowan"},
        ]) is True


class TestRobots:
    """A weekly sweep of fifty government sites is what robots.txt is
    for, and a crawler that ignores it earns a block that takes the whole
    feature down."""

    @pytest.mark.asyncio
    async def test_a_disallowed_path_is_not_read(self, monkeypatch):
        class _Resp:
            text = "User-agent: *\nDisallow: /private/"

        async def fake_get(client, url, label, timeout=20.0, probe=False):
            return _Resp()

        monkeypatch.setattr(crawler, "_get", fake_get)
        monkeypatch.setattr(crawler, "_robots", {})
        assert await crawler._allowed(None, "https://x.gov/private/results.csv") is False
        assert await crawler._allowed(None, "https://x.gov/elections/results.csv") is True

    @pytest.mark.asyncio
    async def test_no_robots_file_means_permitted(self, monkeypatch):
        """What the standard says absence means."""
        async def fake_get(client, url, label, timeout=20.0, probe=False):
            return None

        monkeypatch.setattr(crawler, "_get", fake_get)
        monkeypatch.setattr(crawler, "_robots", {})
        assert await crawler._allowed(None, "https://x.gov/anything") is True


@pytest.mark.asyncio
class TestDiscoverSourceSafety:
    async def test_a_file_that_parses_into_nothing_federal_is_not_adopted(
        self, monkeypatch,
    ):
        """The whole point of proving a source by parsing it: a page full
        of downloadable CSVs is not a page full of election results."""
        async def fake_pages(client, state, cycle):
            return [("https://x.gov/data", "https://x.gov/data/dogs.csv")]

        async def fake_clarity(client, state, cycle):
            return None

        async def fake_portal(client, state, cycle):
            return None

        async def fake_read(client, url, label):
            return _rows("Thing|Who|Count", "Best Dog|Rex|12"), {"delimiter": "|"}

        monkeypatch.setattr(crawler, "_probe_pages", fake_pages)
        monkeypatch.setattr(crawler, "_probe_clarity", fake_clarity)
        monkeypatch.setattr(crawler, "_probe_enhanced_voting", fake_portal)
        monkeypatch.setattr(crawler, "_read", fake_read)
        assert await crawler.discover_source(None, "ZZ", 2026) is None
