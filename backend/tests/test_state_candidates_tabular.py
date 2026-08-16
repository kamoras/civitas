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
            return ["https://example.gov/sov.tsv"]

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
            return [f"https://example.gov/{i}" for i in range(len(payloads))]

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


class TestDistrictTypeColumn:
    """Virginia's export names its federal races "Member, House of
    Representatives (2nd District)" — no "U.S." prefix, and an ordinal
    parse_office doesn't read. Its own DistrictType column is the
    discriminator the label lacks. Rows are the real 2026-08-04 shape.
    """

    _FMT = {
        "delimiter": ",", "encoding": "utf-8",
        "contest_column": "OfficeTitle", "choice_column": "CandidateName",
        "party_column": "Party", "votes_column": "TOTAL_VOTES",
        "district_type_column": "DistrictType", "district_column": "DistrictName",
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

    def test_congressional_rows_are_keyed_by_their_district_number(self):
        tally = tb._tally(tb._rows(self._CSV, self._FMT), self._FMT)
        assert "U.S. House District 2" in tally
        # The zero-padded "02" must not become district 0 or "02".
        assert tb.parse_office("U.S. House District 2") == ("H", 2)

    def test_senate_and_local_rows_keep_their_own_label(self):
        """Only congressional rows are rewritten: the Senate label already
        parses, and a county board race must stay unrecognisable."""
        tally = tb._tally(tb._rows(self._CSV, self._FMT), self._FMT)
        assert "Member, United States Senate" in tally
        assert tb.parse_office("Member County Board (Arlington County)") is None

    @pytest.mark.asyncio
    async def test_yields_the_federal_nominees_only(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return ["https://example.gov/va.csv"]

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
        assert "GeneralPrimary51926" in urls[0]
        assert "Runoff" in urls[1]
        # 2024's General Primary matches the same name pattern and must be
        # excluded by the electionDate year alone.
        assert "2024" not in urls[0]

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
        assert "Democratic" in urls[0] and "Republican" in urls[1]

    @pytest.mark.asyncio
    async def test_blob_name_is_url_quoted(self, monkeypatch):
        """The real blob names contain spaces."""
        monkeypatch.setattr(tb, "_get", self._fake_get())
        urls = await tb._discover_urls(None, "GA", 2026, self._DISCOVERY)
        assert " " not in urls[0]
        assert "%20" in urls[0]

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
        strict = dict(self._DISCOVERY, require_official=True)
        assert await tb._discover_urls(None, "WA", 2026, strict) == []
        # Without the flag the same payload is still usable — the gate is
        # opt-in per state, since a portal that never sets the field would
        # otherwise be permanently withheld.
        assert await tb._discover_urls(None, "WA", 2026, self._DISCOVERY) != []


class TestDiscoverUrl:
    @pytest.mark.asyncio
    async def test_direct_url_substitutes_the_cycle_year(self):
        url = await tb._discover_urls(
            None, "XX", 2026,
            {"mode": "direct_url", "url": "https://example.gov/{year}/results.csv"},
        )
        assert url == ["https://example.gov/2026/results.csv"]

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
        assert url[0].endswith("ENRS/2026_03_03/results_pct_20260303.zip")

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_nothing(self):
        assert await tb._discover_urls(None, "XX", 2026, {"mode": "carrier_pigeon"}) == []


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_returns_federal_nominees_and_excludes_the_state_house_control(
        self, monkeypatch,
    ):
        async def fake_discover(client, state, year, discovery):
            return ["https://example.gov/results.zip"]

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
            return ["https://example.gov/results.zip"]

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
            return ["https://example.gov/results.zip"]

        async def fake_get(client, url, label):
            return _Resp(content=b"x" * (tb.MAX_DOWNLOAD_BYTES + 1))

        monkeypatch.setattr(tb, "_discover_urls", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None


class _Resp:
    def __init__(self, text="", content=b"", json_body=None):
        self.text = text
        self.content = content
        self._json = json_body

    def json(self):
        return self._json
