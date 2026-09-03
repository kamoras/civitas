"""Tests for the shared bulk-results adapter (state_candidates_tabular.py).

The fixture is a real cut of North Carolina's official 2026-03-03 primary
export (fetched live 2026-08-12), keeping the multi-precinct rows that make
aggregation load-bearing, plus an "NC HOUSE OF REPRESENTATIVES DISTRICT
022" contest as a control — a STATE house race whose label looks federal at
a glance and must never be picked up as one.
"""

import io
import os
import zipfile

import pytest

from app.pipeline.fetch import state_candidates_tabular as tb

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_nc_results_export.tsv")
_FORMAT = {
    "delimiter": "\t", "encoding": "utf-8",
    "contest_column": "Contest Name", "choice_column": "Choice",
    "party_column": "Choice Party", "votes_column": "Total Votes",
}


def _fixture_bytes() -> bytes:
    with open(_FIXTURE, "rb") as fh:
        return fh.read()


class TestVotes:
    def test_parses_thousands_separators(self):
        assert tb._votes("1,234") == 1234

    def test_non_numeric_contributes_nothing_rather_than_raising(self):
        assert tb._votes("n/a") == 0
        assert tb._votes("") == 0
        assert tb._votes(None) == 0


class TestRows:
    def test_reads_a_plain_delimited_export(self):
        rows = tb._rows(_fixture_bytes(), _FORMAT)
        assert len(rows) > 100
        assert "Contest Name" in rows[0]

    def test_transparently_unzips_a_single_member_archive(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("results_pct.txt", _fixture_bytes())
        rows = tb._rows(buf.getvalue(), _FORMAT)
        assert len(rows) > 100

    def test_ambiguous_multi_member_archive_is_refused(self):
        """Two candidate members means the configured shape no longer
        matches reality — parsing an arbitrary one would be a guess."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", _fixture_bytes())
            zf.writestr("b.txt", _fixture_bytes())
        assert tb._rows(buf.getvalue(), _FORMAT) is None

    def test_corrupt_zip_returns_none_rather_than_raising(self):
        assert tb._rows(b"PK\x03\x04garbage", _FORMAT) is None


class TestTally:
    def test_sums_a_candidate_across_every_precinct_row(self):
        """These exports are one row per precinct per choice — a single
        row's value is a fraction of the real total."""
        rows = tb._rows(_fixture_bytes(), _FORMAT)
        tally = tb._tally(rows, _FORMAT)
        foxx = tally["US HOUSE OF REPRESENTATIVES DISTRICT 05 (REP)"]["votes"]
        name = next(n for n in foxx if "Foxx" in n)
        single_row = max(
            tb._votes(r["Total Votes"]) for r in rows if r["Choice"] == name
        )
        assert foxx[name] > single_row


def _workbook(rows: list[list[str]]) -> bytes:
    """Minimal real .xlsx: a zip of the two XML parts _xlsx_rows reads,
    with every cell a shared-string reference (t="s"), which is how the
    California workbook actually encodes its text."""
    table = []
    for row in rows:
        for cell in row:
            if cell not in table:
                table.append(cell)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = f"<sst {ns}>" + "".join(f"<si><t>{v}</t></si>" for v in table) + "</sst>"
    body = "".join(
        "<row>" + "".join(
            f'<c t="s"><v>{table.index(c)}</v></c>' for c in row
        ) + "</row>"
        for row in rows
    )
    sheet = f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class TestXlsxRows:
    """An xlsx is a zip of XML, so the standard library reads it — no
    Excel dependency for the states (California among them) that publish
    results in no other machine-readable format."""

    def test_reads_a_workbook_into_header_keyed_rows(self):
        payload = _workbook([
            ["Contest Name", "Candidate Name", "Vote Total"],
            ["United States Representative District 10", "Mark DeSaulnier", "9830"],
        ])
        rows = tb._rows(payload, {"format": "xlsx"})
        assert rows == [{
            "Contest Name": "United States Representative District 10",
            "Candidate Name": "Mark DeSaulnier",
            "Vote Total": "9830",
        }]

    def test_non_workbook_payload_returns_none_rather_than_raising(self):
        assert tb._rows(b"not a zip at all", {"format": "xlsx"}) is None
        assert tb._rows(b"PK\x03\x04corrupt", {"format": "xlsx"}) is None


class TestTopTwo:
    """California runs one all-party contest and advances the top two, so
    a same-party pair is a correct result, not a bug."""

    _FMT = {
        "delimiter": "\t", "encoding": "utf-8",
        "contest_column": "Contest Name", "choice_column": "Candidate Name",
        "party_column": "Party Name", "votes_column": "Vote Total",
    }
    _TSV = (
        "Contest Name\tCandidate Name\tParty Name\tVote Total\n"
        "United States Representative District 4\tMike Thompson\tDemocratic\t900\n"
        "United States Representative District 4\tNiki Jones\tDemocratic\t700\n"
        "United States Representative District 4\tRon Bauer\tRepublican\t300\n"
        "United States Representative District 9\tPat Nolan\tNo Party Preference\t800\n"
        "United States Representative District 9\tAmy Reed\tDemocratic\t600\n"
        "United States Representative District 9\tJoe Katz\tRepublican\t100\n"
    ).encode()

    async def _run(self, monkeypatch, advance_count):
        async def fake_discover(client, state, year, discovery):
            return [{"url": "https://example.gov/sov.tsv", "runoff": False}]

        async def fake_get(client, url, label):
            return _Resp(content=self._TSV)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        return await tb.fetch_confirmed_candidates(
            None, 2026, "CA",
            {"advance_count": advance_count, "format": self._FMT},
        )

    @pytest.mark.asyncio
    async def test_advances_two_of_the_same_party(self, monkeypatch):
        records = await self._run(monkeypatch, 2)
        d4 = [r for r in records if r["district"] == 4]
        assert sorted(r["last_name"] for r in d4) == ["Jones", "Thompson"]
        assert all(r["party"] == "D" for r in d4)

    @pytest.mark.asyncio
    async def test_no_party_preference_candidate_still_advances(self, monkeypatch):
        """Party is incidental under top-two — dropping an NPP leader
        would hide the actual front-runner."""
        records = await self._run(monkeypatch, 2)
        nolan = next(r for r in records if r["last_name"] == "Nolan")
        assert nolan["party"] == ""

    @pytest.mark.asyncio
    async def test_same_data_as_a_party_primary_would_take_only_the_leader(
        self, monkeypatch,
    ):
        """Guards the advance_count switch itself: one-nominee semantics
        must still drop everyone but the winner."""
        records = await self._run(monkeypatch, 1)
        assert [r["last_name"] for r in records if r["district"] == 4] == ["Thompson"]


class TestExcludeChoices:
    """Georgia's workbook appends a per-contest "Total Votes" row in the
    same shape as a candidate — with an empty party and choice id. Left in,
    it doubles the denominator and wins any uncontested race outright."""

    _FMT = dict(_FORMAT, exclude_choices=["Total Votes"])
    _TSV = (
        "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
        "US House of Representatives - District 13 - Rep\tJonathan Chavez\tREP\t25547\n"
        "US House of Representatives - District 13 - Rep\tTotal Votes\t\t25547\n"
    ).encode()

    def test_summary_row_is_not_counted_as_a_candidate(self):
        tally = tb._tally(tb._rows(self._TSV, self._FMT), self._FMT)
        votes = tally["US House of Representatives - District 13 - Rep"]["votes"]
        assert set(votes) == {"Jonathan Chavez"}

    def test_without_exclusion_the_summary_row_would_win(self):
        """Pins why the config exists: the uncontested candidate ties the
        summary row, and a tie yields nobody at all."""
        tally = tb._tally(tb._rows(self._TSV, _FORMAT), _FORMAT)
        votes = tally["US House of Representatives - District 13 - Rep"]["votes"]
        assert "Total Votes" in votes


class TestRunoffOverride:
    """A runoff state's second contest decides every race its primary left
    short of the threshold. Georgia's 2026 Senate is the live case: nobody
    cleared 50% in May, and Collins beat Dooley in the June runoff."""

    _FMT = {
        "delimiter": "\t", "encoding": "utf-8",
        "contest_column": "Contest Name", "choice_column": "Choice",
        "party_column": "Choice Party", "votes_column": "Total Votes",
    }
    _PRIMARY = (
        "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
        "US Senate - Rep\tMike Collins\tREP\t369642\n"
        "US Senate - Rep\tDerek Dooley\tREP\t300000\n"
        "US Senate - Rep\tBuddy Carter\tREP\t229223\n"
    ).encode()
    _RUNOFF = (
        "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
        "US Senate - Rep\tMike Collins\tREP\t500000\n"
        "US Senate - Rep\tDerek Dooley\tREP\t400000\n"
    ).encode()

    async def _run(self, monkeypatch, payloads):
        async def fake_discover(client, state, year, discovery):
            # Last stage is the runoff, as real discovery orders them.
            return [
                {"url": f"https://example.gov/{i}", "runoff": i == len(payloads) - 1 and len(payloads) > 1}
                for i in range(len(payloads))
            ]

        seen = iter(payloads)

        async def fake_get(client, url, label):
            return _Resp(content=next(seen))

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        return await tb.fetch_confirmed_candidates(
            None, 2026, "GA",
            {"runoff_threshold_pct": 50.0, "format": self._FMT},
        )

    @pytest.mark.asyncio
    async def test_primary_alone_confirms_nobody_below_the_threshold(self, monkeypatch):
        assert await self._run(monkeypatch, [self._PRIMARY]) == []

    @pytest.mark.asyncio
    async def test_runoff_resolves_the_seat_the_primary_left_open(self, monkeypatch):
        records = await self._run(monkeypatch, [self._PRIMARY, self._RUNOFF])
        assert [r["last_name"] for r in records] == ["Collins"]

    @pytest.mark.asyncio
    async def test_two_same_day_party_primaries_do_not_erase_each_other(
        self, monkeypatch,
    ):
        """Virginia runs separate Democratic and Republican primary
        elections on the same day. They are different races for the same
        seat, so the second stage must not overwrite the first."""
        dem = (
            "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
            "Member, U.S. House of Representatives District 2\tElaine Luria\tDemocratic\t900\n"
        ).encode()
        rep = (
            "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
            "Member, U.S. House of Representatives District 2\tJen Kiggans\tRepublican\t800\n"
        ).encode()
        records = await self._run(monkeypatch, [dem, rep])
        assert sorted(r["last_name"] for r in records) == ["Kiggans", "Luria"]

    @pytest.mark.asyncio
    async def test_runoff_is_not_itself_thresholded(self, monkeypatch):
        """A runoff is decisive by construction — applying the 50% rule to
        it again would discard the very result that resolves the race."""
        close = (
            "Contest Name\tChoice\tChoice Party\tTotal Votes\n"
            "US Senate - Rep\tMike Collins\tREP\t51\n"
            "US Senate - Rep\tDerek Dooley\tREP\t49\n"
        ).encode()
        records = await self._run(monkeypatch, [self._PRIMARY, close])
        assert [r["last_name"] for r in records] == ["Collins"]


class TestHouseFromColumns:
    """Virginia's export names its federal races "Member, House of
    Representatives (2nd District)" — no "U.S." prefix, and an ordinal
    parse_office doesn't read. Its own DistrictType column is the
    discriminator the label lacks. Rows are the real 2026-08-04 shape.
    """

    _FMT = {
        "delimiter": ",", "encoding": "utf-8",
        "contest_column": "OfficeTitle", "choice_column": "CandidateName",
        "party_column": "Party", "votes_column": "TOTAL_VOTES",
        "house_from_columns": {
            "type_column": "DistrictType", "type_value": "congressional",
            "district_column": "DistrictName",
        },
    }
    _CSV = (
        "CandidateName,TOTAL_VOTES,Party,DistrictType,DistrictName,OfficeTitle\n"
        "Elaine G. Luria,33658,Democratic,congressional,02,"
        '"Member, House of Representatives (2nd District)"\n'
        "Patrick B. Mosolf,566,Democratic,congressional,02,"
        '"Member, House of Representatives (2nd District)"\n'
        "Bert Mizusawa,85,Republican,state,United States Of America,"
        '"Member, United States Senate"\n'
        "Some Supervisor,999,Democratic,county,ARLINGTON COUNTY,"
        '"Member County Board (Arlington County)"\n'
    ).encode()

    def test_the_office_is_resolved_from_the_columns(self):
        tally = tb._tally(tb._rows(self._CSV, self._FMT), self._FMT)
        entry = tally["Member, House of Representatives (2nd District)"]
        # The zero-padded "02" must not become district 0 or "02".
        assert entry["office"] == ("H", 2)

    def test_rows_the_spec_does_not_cover_fall_back_to_the_label(self):
        """Only the configured office is resolved from columns: the Senate
        label already parses on its own, and a county board race must stay
        unrecognisable to both routes."""
        tally = tb._tally(tb._rows(self._CSV, self._FMT), self._FMT)
        assert tally["Member, United States Senate"]["office"] is None
        assert tb.parse_office("Member, United States Senate") == ("S", None)
        assert tally["Member County Board (Arlington County)"]["office"] is None
        assert tb.parse_office("Member County Board (Arlington County)") is None

    @pytest.mark.asyncio
    async def test_yields_the_federal_nominees_only(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return [{"url": "https://example.gov/va.csv", "runoff": False}]

        async def fake_get(client, url, label):
            return _Resp(content=self._CSV)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "VA", {"format": self._FMT},
        )
        assert records == [
            {"office": "H", "district": 2, "party": "D", "last_name": "Luria"},
            {"office": "S", "district": None, "party": "R", "last_name": "Mizusawa"},
        ]

    def test_without_the_column_the_house_label_is_refused(self):
        """Pins why the config exists — and that parse_office still won't
        guess a federal seat out of an unprefixed "House of
        Representatives", which is a state chamber's name in many states."""
        assert tb.parse_office("Member, House of Representatives (2nd District)") is None


class TestColumnJoining:
    """Florida splits across columns what other states keep in one label:
    a contest is (RaceName, PartyCode) and a candidate is (first, last).
    Rows are the real 2026-08-18 file's shape."""

    _FMT = {
        "delimiter": "\t",
        "contest_column": ["RaceName", "PartyCode"],
        "choice_column": ["CanNameFirst", "CanNameLast"],
        "party_column": "PartyCode", "votes_column": "CanVotes",
        "house_from_columns": {
            "type_column": "RaceCode", "type_value": "USR",
            "district_column": "Juris1num",
        },
    }
    _TSV = (
        "RaceCode\tRaceName\tPartyCode\tJuris1num\tCanNameFirst\tCanNameLast\tCanVotes\n"
        "USR\tRepresentative in Congress, District 25\tDEM\t025\tDebbie\tWasserman Schultz\t900\n"
        "USR\tRepresentative in Congress, District 25\tDEM\t025\tJen\tPerelman\t400\n"
        "USR\tRepresentative in Congress, District 25\tREP\t025\tCarla\tSpalding\t700\n"
    ).encode()

    def test_the_party_code_keeps_two_primaries_for_one_seat_apart(self):
        """Without the party column in the key, both parties' primaries
        for a seat tally as ONE race and the bigger one wins the seat
        outright — the Democratic winner here would beat the Republican
        winner and the seat would show a single nominee."""
        tally = tb._tally(tb._rows(self._TSV, self._FMT), self._FMT)
        assert set(tally) == {
            "Representative in Congress, District 25 DEM",
            "Representative in Congress, District 25 REP",
        }

    @pytest.mark.asyncio
    async def test_yields_one_nominee_per_party(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return [tb._stage("https://example.gov/fl.txt")]

        async def fake_get(client, url, label):
            return _Resp(content=self._TSV)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "FL", {"format": self._FMT},
        )
        assert sorted((r["party"], r["last_name"]) for r in records) == [
            ("D", "Schultz"), ("R", "Spalding"),
        ]
        assert all(r["district"] == 25 for r in records)


class TestHeaderlessColumns:
    """Minnesota publishes one semicolon file with NO header row, so the
    columns are named positionally in config."""

    _FMT = {
        "delimiter": ";",
        "columns": ["state", "county", "precinct", "office_id", "office_name",
                    "district", "candidate_id", "candidate", "suffix", "incumbent",
                    "party", "reporting", "total_precincts", "votes", "percent", "total"],
        "contest_column": ["office_name", "party"],
        "choice_column": "candidate", "party_column": "party", "votes_column": "votes",
    }
    _TXT = (
        "MN;01;;0111;U.S. Representative District 8;8;0301;Pete Stauber;;;R;51;51;2273;87.29;2604\n"
        "MN;02;;0111;U.S. Representative District 8;8;0301;Pete Stauber;;;R;51;51;1000;87.29;2604\n"
        "MN;01;;0111;U.S. Representative District 8;8;0302;Anthony Hamilton;;;R;51;51;331;12.71;2604\n"
        "MN;01;;0111;U.S. Representative District 8;8;0401;Luke Gulbranson;;;DFL;51;51;256;16.30;1571\n"
        "MN;01;;0111;U.S. Representative District 8;8;0402;John Munter;;;DFL;51;51;900;8.27;1571\n"
    ).encode()

    def test_positional_columns_are_read(self):
        rows = tb._rows(self._TXT, self._FMT)
        assert rows[0]["candidate"] == "Pete Stauber"
        assert rows[0]["office_name"] == "U.S. Representative District 8"

    @pytest.mark.asyncio
    async def test_votes_sum_across_counties_and_dfl_is_democratic(self, monkeypatch):
        """Minnesota's Democrats are the DFL, and every candidate appears
        once per county."""
        async def fake_discover(client, state, year, discovery):
            return [tb._stage("https://example.gov/mn.txt")]

        async def fake_get(client, url, label):
            return _Resp(content=self._TXT)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(None, 2026, "MN", {"format": self._FMT})
        assert sorted((r["party"], r["last_name"]) for r in records) == [
            ("D", "Munter"), ("R", "Stauber"),
        ]


class TestSkipLines:
    """Hawaii's file opens with its own format banner before the header."""

    def test_the_banner_line_is_dropped(self):
        payload = (b"#FormatVersion 1\n"
                   b"Contest Title\tCandidate Name\tTotal Votes\n"
                   b'"U.S. Representative, Dist I"\t"CASE, Ed"\t63784\n')
        rows = tb._rows(payload, {"delimiter": "\t", "skip_lines": 1})
        assert rows[0]["Candidate Name"] == "CASE, Ed"


class TestPrimaryDateTemplating:
    """A state whose results file is addressed by election date can be
    reached without crawling that state for the date — the national
    calendar supplies it. Minnesota needs exactly this: its files are open
    while the page listing them sits behind a bot manager."""

    _DISCOVERY = {
        "mode": "direct_url",
        "url": "https://files.example.gov/{primary_date_compact}/allraces.txt",
    }

    @pytest.mark.asyncio
    async def test_the_known_primary_date_fills_the_url(self, monkeypatch):
        monkeypatch.setattr(
            "app.pipeline.fetch.state_election_dates.primary_date",
            lambda state, year: "2026-08-11",
        )
        stages = await tb._discover_urls(None, "MN", 2026, self._DISCOVERY)
        assert stages == [tb._stage(
            "https://files.example.gov/20260811/allraces.txt", held="2026-08-11",
        )]

    @pytest.mark.asyncio
    async def test_an_unknown_date_yields_nothing_rather_than_a_broken_url(self, monkeypatch):
        monkeypatch.setattr(
            "app.pipeline.fetch.state_election_dates.primary_date",
            lambda state, year: None,
        )
        assert await tb._discover_urls(None, "MN", 2026, self._DISCOVERY) == []


class TestHtmlTableReader:
    """Maryland publishes results as HTML tables under headings and offers
    no downloadable file at all. A table alone never says which contest it
    is — the heading above it does — so rows carry the heading stack."""

    _PAGE = b"""
    <html><body>
      <h1>Official 2026 Primary Election Results for Representative in Congress</h1>
      <h2>Representative in Congress</h2>
      <h3>District 1</h3>
      <h4>Democratic Candidates - Vote for 1</h4>
      <table>
        <tr><th>Name</th><th>Party</th><th>Total</th></tr>
        <tr><td>Dan Schwartz</td><td>Democratic</td><td>18,317</td></tr>
        <tr><td>Totals</td><td></td><td>18,317</td></tr>
      </table>
      <h4>Republican Candidates - Vote for 1</h4>
      <table>
        <tr><th>Name</th><th>Party</th><th>Total</th></tr>
        <tr><td>Andy Harris</td><td>Republican</td><td>44,000</td></tr>
      </table>
      <h3>District 2</h3>
      <h4>Democratic Candidates - Vote for 1</h4>
      <table>
        <tr><th>Name</th><th>Party</th><th>Total</th></tr>
        <tr><td>Johnny Olszewski</td><td>Democratic</td><td>30,000</td></tr>
      </table>
    </body></html>
    """
    _FMT = {
        "format": "html_table",
        "contest_column": ["heading_2", "heading_3", "heading_4"],
        "choice_column": "Name", "party_column": "Party", "votes_column": "Total",
        "exclude_choices": ["Totals"],
    }

    def test_rows_carry_the_heading_they_appeared_under(self):
        rows = tb._rows(self._PAGE, self._FMT)
        first = rows[0]
        assert first["Name"] == "Dan Schwartz"
        assert first["heading_2"] == "Representative in Congress"
        assert first["heading_3"] == "District 1"

    def test_a_new_heading_clears_the_deeper_ones(self):
        """District 2's rows must not inherit District 1's party heading
        or anything else nested under it."""
        rows = tb._rows(self._PAGE, self._FMT)
        olszewski = next(r for r in rows if r["Name"].startswith("Johnny"))
        assert olszewski["heading_3"] == "District 2"

    @pytest.mark.asyncio
    async def test_both_parties_survive_the_same_district(self, monkeypatch):
        """The party heading is part of the contest key: without it, both
        parties' candidates for a district tally as ONE race and the
        weaker party's nominee is dropped."""
        async def fake_discover(client, state, year, discovery):
            return [tb._stage("https://example.gov/md.html")]

        async def fake_get(client, url, label):
            return _Resp(content=self._PAGE)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "MD", {"format": self._FMT},
        )
        assert sorted((r["district"], r["party"], r["last_name"]) for r in records) == [
            (1, "D", "Schwartz"), (1, "R", "Harris"), (2, "D", "Olszewski"),
        ]

    def test_a_page_that_is_not_a_results_table_yields_nothing(self):
        assert tb._rows(b"<html><body><p>no tables here</p></body></html>",
                        {"format": "html_table"}) is None


class TestLandingPageDiscovery:
    """Florida publishes one dated file per election and no way to list
    them, so the link comes off the page the state keeps current."""

    _DISCOVERY = {
        "mode": "landing_page",
        "page_url": "https://example.gov/Downloads",
        "link_regex": r"https://files\.example\.gov/\d{8}_Results\.txt",
    }

    def _page(self, *names):
        links = "".join(f'<a href="https://files.example.gov/{n}">x</a>' for n in names)
        return f"<html>{links}</html>"

    @pytest.mark.asyncio
    async def test_the_page_url_is_templated_by_cycle_too(self, monkeypatch):
        """A state that files results under a per-cycle path needs the
        PAGE templated, not just the link pattern, or nothing is ever
        found there again."""
        asked = []

        async def fake_get(client, url, label):
            asked.append(url)
            return _Resp(text=self._page("20260818_Results.txt"))

        monkeypatch.setattr(tb, "_get", fake_get)
        await tb._discover_urls(None, "MD", 2026, dict(
            self._DISCOVERY, page_url="https://example.gov/{year}/results/index.html",
        ))
        assert asked == ["https://example.gov/2026/results/index.html"]

    @pytest.mark.asyncio
    async def test_reads_this_cycles_file_off_the_page(self, monkeypatch):
        async def fake_get(client, url, label):
            return _Resp(text=self._page("20260818_Results.txt"))

        monkeypatch.setattr(tb, "_get", fake_get)
        stages = await tb._discover_urls(None, "FL", 2026, self._DISCOVERY)
        assert stages == [tb._stage(
            "https://files.example.gov/20260818_Results.txt", held="2026-08-18",
        )]

    @pytest.mark.asyncio
    async def test_the_general_elections_own_file_is_never_used(self, monkeypatch):
        """After November the page shows the GENERAL's file. Confirming
        nominees from the election they are nominees FOR would be
        backwards — and would quietly start naming November's winners."""
        async def fake_get(client, url, label):
            return _Resp(text=self._page("20261103_Results.txt"))

        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb._discover_urls(None, "FL", 2026, self._DISCOVERY) == []

    @pytest.mark.asyncio
    async def test_with_both_listed_the_primary_wins(self, monkeypatch):
        async def fake_get(client, url, label):
            return _Resp(text=self._page("20261103_Results.txt", "20260818_Results.txt"))

        monkeypatch.setattr(tb, "_get", fake_get)
        stages = await tb._discover_urls(None, "FL", 2026, self._DISCOVERY)
        assert [s["held"] for s in stages] == ["2026-08-18"]


class TestWithheld:
    """One gate for every vendor: a state that asks for it names nobody
    until its own certification flag says so, or its certification
    deadline passes."""

    _GATED = {"require_official": True, "settle_days": 30}

    def test_a_certified_stage_passes(self):
        assert tb._withheld(tb._stage("u", official=True), self._GATED) is False

    def test_an_uncertified_recent_stage_is_withheld(self):
        assert tb._withheld(tb._stage("u", held="2999-01-01"), self._GATED) is True

    def test_a_vendor_with_no_flag_at_all_passes_on_the_deadline(self):
        """Florida publishes no certification flag anywhere — the window
        is the only gate it has."""
        assert tb._withheld(tb._stage("u", held="2000-01-01"), self._GATED) is False
        assert tb._withheld(tb._stage("u", held="2999-01-01"), self._GATED) is True

    def test_a_state_that_does_not_ask_for_the_gate_is_never_withheld(self):
        assert tb._withheld(tb._stage("u", held="2999-01-01"), {}) is False


class TestSosApiReportDiscovery:
    """Georgia's portal hides its workbook behind a three-hop API, and the
    blob filename carries a GUID that changes on every republish — so it
    must be read each run, never hardcoded."""

    _JURISDICTION = {
        "id": "09378a07-e6cf-4f66-be7c-ca4aa534f99a",
        "elections": [
            {"publicElectionId": "GeneralPrimary51926", "electionDate": "2026-05-19",
             "name": [{"languageId": "en", "text": "May 19, 2026 - General Primary"}]},
            {"publicElectionId": "06162026GeneralPrimaryRunoff", "electionDate": "2026-06-16",
             "name": [{"languageId": "en", "text": "June 16th, 2026 General Primary Runoff"}]},
            {"publicElectionId": "GeneralPrimary2024", "electionDate": "2024-05-21",
             "name": [{"languageId": "en", "text": "May 21, 2024 - General Primary"}]},
        ],
    }
    _DISCOVERY = {
        "mode": "sos_api_report",
        "jurisdiction_url": "https://example.gov/api/jurisdictions/Georgia",
        "election_url": "https://example.gov/api/elections/Georgia/{election_id}",
        "cdn_url": "https://example.gov/cdn/{jurisdiction_id}/{blob}",
        "election_name_regex": "General Primary(?!.*Runoff)",
        "runoff_name_regex": "General Primary Runoff",
        "report_name": "Total Votes Excel",
    }

    def _fake_get(self):
        async def fake_get(client, url, label):
            if "jurisdictions" in url:
                return _Resp(json_body=self._JURISDICTION)
            blob = f"Total Votes {url.rsplit('/', 1)[-1]}.xlsx"
            return _Resp(json_body={"publicReportCategories": [
                {"reports": [{"reportName": "Total Votes Excel", "blobName": blob}]},
            ]})
        return fake_get

    @pytest.mark.asyncio
    async def test_returns_primary_then_runoff_for_the_right_cycle(self, monkeypatch):
        monkeypatch.setattr(tb, "_get", self._fake_get())
        urls = await tb._discover_urls(None, "GA", 2026, self._DISCOVERY)

        assert len(urls) == 2
        assert "GeneralPrimary51926" in urls[0]["url"]
        assert urls[0]["runoff"] is False
        assert "Runoff" in urls[1]["url"] and urls[1]["runoff"] is True
        # 2024's General Primary matches the same name pattern and must be
        # excluded by the electionDate year alone.
        assert "2024" not in urls[0]["url"]

    @pytest.mark.asyncio
    async def test_a_list_of_patterns_fetches_every_matching_election(
        self, monkeypatch,
    ):
        """Virginia publishes its two same-day party primaries as separate
        elections, so both must be discovered — one pattern each, rather
        than one loose pattern, so a special election can't slip in."""
        jurisdiction = {"id": "va", "elections": [
            {"publicElectionId": "2026-August-Democratic-Primary",
             "electionDate": "2026-08-04",
             "name": [{"text": "2026 August Democratic Primary"}]},
            {"publicElectionId": "2026-August-Republican-Primary",
             "electionDate": "2026-08-04",
             "name": [{"text": "2026 August Republican Primary"}]},
        ]}

        async def fake_get(client, url, label):
            if "jurisdictions" in url:
                return _Resp(json_body=jurisdiction)
            return _Resp(json_body={"publicReportCategories": [{"reports": [
                {"reportName": "Total Votes Excel",
                 "blobName": f"{url.rsplit('/', 1)[-1]}.csv"},
            ]}]})

        monkeypatch.setattr(tb, "_get", fake_get)
        urls = await tb._discover_urls(None, "VA", 2026, dict(
            self._DISCOVERY,
            election_name_regex=["Democratic Primary", "Republican Primary"],
            runoff_name_regex=None,
        ))

        assert len(urls) == 2
        assert "Democratic" in urls[0]["url"] and "Republican" in urls[1]["url"]
        # Same-day party primaries are both primaries — neither overrides
        # the other, and both are subject to any runoff threshold.
        assert [u["runoff"] for u in urls] == [False, False]

    @pytest.mark.asyncio
    async def test_blob_name_is_url_quoted(self, monkeypatch):
        """The real blob names contain spaces."""
        monkeypatch.setattr(tb, "_get", self._fake_get())
        urls = await tb._discover_urls(None, "GA", 2026, self._DISCOVERY)
        assert " " not in urls[0]["url"]
        assert "%20" in urls[0]["url"]

    @pytest.mark.asyncio
    async def test_a_cycle_with_no_matching_election_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(tb, "_get", self._fake_get())
        assert await tb._discover_urls(None, "GA", 2030, self._DISCOVERY) == []

    @pytest.mark.asyncio
    async def test_uncertified_election_is_refused_when_official_required(
        self, monkeypatch,
    ):
        """The live case: Washington was still counting its 2026-08-04
        primary days later. Confirming a nominee from a count that is still
        moving is exactly the failure this module exists to prevent, so an
        uncertified election yields nothing and lights up on its own at
        certification."""
        async def fake_get(client, url, label):
            if "jurisdictions" in url:
                return _Resp(json_body=self._JURISDICTION)
            return _Resp(json_body={
                "isOfficialResults": False,
                "publicReportCategories": [
                    {"reports": [{"reportName": "Total Votes Excel", "blobName": "x.xlsx"}]},
                ],
            })

        monkeypatch.setattr(tb, "_get", fake_get)
        stages = await tb._discover_urls(None, "WA", 2026, self._DISCOVERY)
        # Discovery reports what the portal said; the gate decides.
        assert stages and all(s["official"] is False for s in stages)
        strict = dict(self._DISCOVERY, require_official=True)
        assert all(tb._withheld(s, strict) for s in stages)
        # Without the flag the same payload is still usable — the gate is
        # opt-in per state, since a portal that never sets the field would
        # otherwise be permanently withheld.
        assert not any(tb._withheld(s, self._DISCOVERY) for s in stages)

    @pytest.mark.asyncio
    async def test_a_withheld_state_is_empty_not_a_failed_fetch(self, monkeypatch):
        """The distinction the pipeline reports on: a state that published
        results nobody has certified yet is HEALTHY and confirms nobody
        ([]), while a state whose file can't be found at all is a failure
        (None). Collapsing the two would report Washington and Virginia as
        broken every run for the weeks between voting and certification."""
        async def withheld(client, state, year, discovery):
            return [{"url": None, "runoff": False}]

        async def nothing(client, state, year, discovery):
            return []

        monkeypatch.setattr(tb, "_discover_urls", withheld)
        assert await tb.fetch_confirmed_candidates(None, 2026, "WA", {}) == []

        monkeypatch.setattr(tb, "_discover_urls", nothing)
        assert await tb.fetch_confirmed_candidates(None, 2026, "WA", {}) is None

    @pytest.mark.asyncio
    async def test_settle_days_releases_an_election_the_portal_never_flagged(
        self, monkeypatch,
    ):
        """Utah's 2026 primary was canvassed and certified on 2026-07-22 —
        the signed state canvass report sits on this same portal — and
        isOfficialResults was still false a month later. Without the
        window, that state confirms nobody forever while looking healthy."""
        async def fake_get(client, url, label):
            if "jurisdictions" in url:
                return _Resp(json_body=self._JURISDICTION)
            return _Resp(json_body={
                "isOfficialResults": False,
                "electionDate": "2026-05-19",
                "publicReportCategories": [
                    {"reports": [{"reportName": "Total Votes Excel", "blobName": "x.xlsx"}]},
                ],
            })

        monkeypatch.setattr(tb, "_get", fake_get)
        strict = dict(self._DISCOVERY, require_official=True, settle_days=30)
        assert await tb._discover_urls(None, "GA", 2026, strict) != []

    def test_an_election_inside_the_window_is_still_withheld(self):
        assert tb._settled({"electionDate": "2999-01-01"}, 30) is False

    def test_no_window_configured_never_releases(self):
        """settle_days is opt-in: without it require_official is absolute."""
        assert tb._settled({"electionDate": "2000-01-01"}, None) is False

    def test_an_unparsable_election_date_does_not_release(self):
        assert tb._settled({"electionDate": None}, 30) is False


class TestDiscoverUrl:
    @pytest.mark.asyncio
    async def test_direct_url_substitutes_the_cycle_year(self):
        url = await tb._discover_urls(
            None, "XX", 2026,
            {"mode": "direct_url", "url": "https://example.gov/{year}/results.csv"},
        )
        assert url == [tb._stage("https://example.gov/2026/results.csv")]

    @pytest.mark.asyncio
    async def test_direct_url_with_no_date_of_its_own_falls_back_to_the_calendar(
        self, monkeypatch,
    ):
        """New Mexico's results URL carries no election id or date — it
        always serves whichever election is current — so it has no way to
        date itself the way a per-cycle path or a dated filename can. The
        national calendar is the only source left standing."""
        monkeypatch.setattr(
            "app.pipeline.fetch.state_election_dates.primary_date",
            lambda state, year: "2026-06-02",
        )
        url = await tb._discover_urls(
            None, "NM", 2026,
            {"mode": "direct_url", "url": "https://example.gov/results.csv",
             "date_from_calendar": True},
        )
        assert url == [tb._stage("https://example.gov/results.csv", held="2026-06-02")]

    @pytest.mark.asyncio
    async def test_s3_listing_picks_the_earliest_matching_key(self, monkeypatch):
        """Within a cycle the first matching election is the primary that
        decides nominees; a later folder is the general or a second
        primary."""
        listing = (
            "<ListBucketResult>"
            "<Contents><Key>ENRS/2026_03_03/results_pct_20260303.zip</Key></Contents>"
            "<Contents><Key>ENRS/2026_11_03/results_pct_20261103.zip</Key></Contents>"
            "<Contents><Key>ENRS/2026_03_03/absentee_20260303.zip</Key></Contents>"
            "</ListBucketResult>"
        )

        async def fake_get(client, url, label):
            return _Resp(text=listing)

        monkeypatch.setattr(tb, "_get", fake_get)
        url = await tb._discover_urls(None, "NC", 2026, {
            "mode": "s3_listing",
            "bucket_url": "https://s3.amazonaws.com/dl.ncsbe.gov",
            "prefix": "ENRS/{year}",
            "file_regex": r"results_pct_\d{8}\.zip",
        })
        assert len(url) == 1
        assert url[0]["url"].endswith("ENRS/2026_03_03/results_pct_20260303.zip")

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_nothing(self):
        assert await tb._discover_urls(None, "XX", 2026, {"mode": "carrier_pigeon"}) == []


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_returns_federal_nominees_and_excludes_the_state_house_control(
        self, monkeypatch,
    ):
        async def fake_discover(client, state, year, discovery):
            return [{"url": "https://example.gov/results.zip", "runoff": False}]

        async def fake_get(client, url, label):
            return _Resp(content=_fixture_bytes())

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "NC", {"runoff_threshold_pct": 30.0, "format": _FORMAT},
        )

        assert {"office": "H", "district": 5, "party": "R", "last_name": "Foxx"} in records
        # "NC HOUSE OF REPRESENTATIVES DISTRICT 022" is a state race whose
        # label looks federal — district 22 must not appear.
        assert all(r["district"] != 22 for r in records)
        assert all(r["office"] in ("S", "H") for r in records)

    @pytest.mark.asyncio
    async def test_undiscoverable_file_returns_none_not_empty(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return []

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return [{"url": "https://example.gov/results.zip", "runoff": False}]

        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_oversized_download_is_refused(self, monkeypatch):
        """A URL that has silently started serving something enormous is a
        config failure, not something to parse."""
        async def fake_discover(client, state, year, discovery):
            return [{"url": "https://example.gov/results.zip", "runoff": False}]

        async def fake_get(client, url, label):
            return _Resp(content=b"x" * (tb.MAX_DOWNLOAD_BYTES + 1))

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None


class TestNewMexico:
    """A real cut of New Mexico's official 2026-06-02 primary export
    (electionresults.sos.nm.gov/resultsCSV.aspx?text=All&type=FED&map=CTY),
    fetched live 2026-09-03 with no login and no session — a plain,
    unauthenticated GET. Kept whole rather than trimmed: it is also what
    proves RaceName alone doesn't carry the House district number (AreaNum
    does), and that a Republican write-in nominee's "(write in)" annotation
    doesn't survive into the recorded surname.
    """

    _CSV = (
        b'RaceID,RaceName,PartyCode,AreaNum,CandidateID,CandidateName,VoteFor,'
        b'CandidateVotes,CandidatePercentage,PrecinctsReporting,'
        b'CandidateAbsenteeVotes,CandidateElectionDayVotes,CandidateEarlyVotes\r\n'
        b'10778,"United States Senator","DEM","",18908,"BEN R LUJAN",1,182360,'
        b'0.841730171844781,2204/2204,29120,76471,76769,\r\n'
        b'10778,"United States Senator","DEM","",18933,"MATT DODSON",1,34289,'
        b'0.158269828155219,2204/2204,4753,17327,12209,\r\n'
        b'10778,"United States Senator","REP","",19598,"LARRY E MARKER (write in)",'
        b'1,31220,1,2204/2204,2333,15209,13678,\r\n'
        b'10779,"United States Representative","DEM","DISTRICT 1",18928,'
        b'"MELANIE ANN STANSBURY",1,79823,1,778/778,15000,29285,35538,\r\n'
        b'10779,"United States Representative","REP","DISTRICT 1",18909,'
        b'"NDIDIAMAKA EKWUA CHARLENE OKPAREKE",1,31809,1,778/778,3523,13570,14716,\r\n'
        b'10780,"United States Representative","DEM","DISTRICT 2",18913,'
        b'"GABRIEL VASQUEZ",1,47123,1,663/663,8600,19629,18894,\r\n'
        b'10780,"United States Representative","REP","DISTRICT 2",18906,'
        b'"GREGORY G CUNNINGHAM",1,26836,0.845334845334845,663/663,2079,13690,11067,\r\n'
        b'10780,"United States Representative","REP","DISTRICT 2",18929,'
        b'"JOSE OROZCO",1,4910,0.154665154665155,663/663,437,2630,1843,\r\n'
        b'10781,"United States Representative","DEM","DISTRICT 3",18912,'
        b'"TERESA LEGER FERNANDEZ",1,68768,1,763/763,8012,34331,26425,\r\n'
        b'10781,"United States Representative","REP","DISTRICT 3",18905,'
        b'"MARTIN ZAMORA",1,32901,1,763/763,1884,18306,12711,\r\n'
    )
    _FMT = {
        "delimiter": ",",
        "contest_column": ["RaceName", "AreaNum", "PartyCode"],
        "choice_column": "CandidateName",
        "party_column": "PartyCode",
        "votes_column": "CandidateVotes",
    }

    @pytest.mark.asyncio
    async def test_confirms_every_real_2026_federal_nominee(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return [tb._stage(
                "https://electionresults.sos.nm.gov/resultsCSV.aspx",
                held="2026-06-02",
            )]

        async def fake_get(client, url, label):
            return _Resp(content=self._CSV)

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "NM", {"format": self._FMT},
        )
        assert sorted(
            (r["office"], r["district"], r["party"], r["last_name"]) for r in records
        ) == [
            ("H", 1, "D", "STANSBURY"),
            ("H", 1, "R", "OKPAREKE"),
            ("H", 2, "D", "VASQUEZ"),
            ("H", 2, "R", "CUNNINGHAM"),
            ("H", 3, "D", "FERNANDEZ"),
            ("H", 3, "R", "ZAMORA"),
            ("S", None, "D", "LUJAN"),
            ("S", None, "R", "MARKER"),
        ]

    def test_house_district_comes_from_area_num_not_the_race_name(self):
        """RaceName alone is just "United States Representative" for all
        three districts — AreaNum is what disambiguates them, and joining
        it into the contest key is what lets parse_office's own district
        matching resolve it with no house_from_columns spec."""
        rows = tb._rows(self._CSV, self._FMT)
        tally = tb._tally(rows, self._FMT)
        assert "United States Representative DISTRICT 2 REP" in tally

    def test_a_write_in_nominees_ballot_annotation_is_not_part_of_the_surname(self):
        rows = tb._rows(self._CSV, self._FMT)
        tally = tb._tally(rows, self._FMT)
        senate = tally["United States Senator REP"]
        assert "LARRY E MARKER (write in)" in senate["votes"]


class _Resp:
    def __init__(self, text="", content=b"", json_body=None):
        self.text = text
        self.content = content
        self._json = json_body

    def json(self):
        return self._json
