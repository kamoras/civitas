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
            return "https://example.gov/sov.tsv"

        async def fake_get(client, url, label):
            return _Resp(content=self._TSV)

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
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


class TestDiscoverUrl:
    @pytest.mark.asyncio
    async def test_direct_url_substitutes_the_cycle_year(self):
        url = await tb._discover_url(
            None, "XX", 2026,
            {"mode": "direct_url", "url": "https://example.gov/{year}/results.csv"},
        )
        assert url == "https://example.gov/2026/results.csv"

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
        url = await tb._discover_url(None, "NC", 2026, {
            "mode": "s3_listing",
            "bucket_url": "https://s3.amazonaws.com/dl.ncsbe.gov",
            "prefix": "ENRS/{year}",
            "file_regex": r"results_pct_\d{8}\.zip",
        })
        assert url.endswith("ENRS/2026_03_03/results_pct_20260303.zip")

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_none(self):
        assert await tb._discover_url(None, "XX", 2026, {"mode": "carrier_pigeon"}) is None


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_returns_federal_nominees_and_excludes_the_state_house_control(
        self, monkeypatch,
    ):
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return _Resp(content=_fixture_bytes())

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
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
            return None

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_oversized_download_is_refused(self, monkeypatch):
        """A URL that has silently started serving something enormous is a
        config failure, not something to parse."""
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return _Resp(content=b"x" * (tb.MAX_DOWNLOAD_BYTES + 1))

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None


class _Resp:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content
