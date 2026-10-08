"""Splitting the Congressional Record into member speeches, from the
Record's own layout. Fixtures are real GovInfo granule HTML, trimmed
(tests/fixtures/crec/)."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from app.pipeline.fetch import congressional_record as cr
from app.pipeline.fetch.congressional_record import parse_granule_speeches, speech_granules

FIXTURES = Path(__file__).parent / "fixtures" / "crec"


def _parse(name: str, title: str, floor: str | None = None) -> list[dict]:
    return parse_granule_speeches((FIXTURES / name).read_text(), title, floor)[0]


def _summary(speeches: list[dict]) -> list[tuple]:
    return [(s["speaker"], s["heading"], s["opens"]) for s in speeches]


class TestSenateFloorSession:
    """A Senate session granule: a speech under the Record's own heading,
    the leader's quorum call business, the leader's speech under another."""

    speeches = _parse("senate_legislative_session.htm", "LEGISLATIVE SESSION")

    def test_each_turn_sits_under_the_heading_the_record_printed_over_it(self):
        assert _summary(self.speeches) == [
            ("GRASSLEY", "Healthcare Fraud", True),
            ("THUNE", "Healthcare Fraud", False),  # rescinding the quorum call after it
            ("THUNE", "Democrat Party", True),
        ]

    def test_text_is_whole_and_is_only_the_members(self):
        grassley = self.speeches[0]["text"]
        assert len(grassley) > 2_000  # the old parse kept 400 characters
        assert grassley.endswith("I suggest the absence of a quorum.")
        # The presiding officer's and the clerk's words that follow are not his.
        assert "clerk" not in grassley and "PRESIDING OFFICER" not in grassley
        # Pagination is not text.
        assert "[[Page" not in grassley
        # Paragraphs are kept.
        assert grassley.count("\n") >= 10

    def test_quoted_bill_text_read_by_the_clerk_is_nobodys_speech(self):
        assert all("A bill (S. 4668)" not in s["text"] for s in self.speeches)


class TestHouseDebate:
    """A suspension debate: a motion, the bill text the clerk reads, the
    chair, General Leave, then the debate."""

    speeches = _parse("house_suspension_debate.htm",
                      "STOPPING POLITICAL DISCRIMINATION IN DISASTER ASSISTANCE ACT")

    def test_turns_and_headings(self):
        assert [s["speaker"] for s in self.speeches] == [
            "PERRY", "PERRY", "PERRY", "GRAVES", "PERRY", "STANTON", "PERRY", "STANTON",
        ]
        assert self.speeches[0]["heading"] == "STOPPING POLITICAL DISCRIMINATION IN DISASTER ASSISTANCE ACT"
        assert self.speeches[0]["opens"]
        # The bill's own title ("H.R. 1342"), printed centered over the bill
        # text, is a document's heading, not the Record's.
        assert {s["heading"] for s in self.speeches[1:]} == {"General Leave"}

    def test_the_bill_text_is_not_the_movers_speech(self):
        assert "Be it enacted" not in self.speeches[0]["text"]

    def test_a_time_stamp_does_not_split_a_speech(self):
        perry = self.speeches[4]["text"]
        assert "{time}" not in perry
        assert "The inspector general found that" in perry
        assert perry.endswith("I reserve the balance of my time.")


class TestOneMemberSeveralTributes:
    """Three tributes by one member in one granule, each under the Record's
    own heading — they used to share the first one's title."""

    speeches = _parse("house_three_tributes.htm", "NATIONAL POLICE WEEK: TEXAS HONOR GUARD")

    def test_each_tribute_is_its_own_speech_under_its_own_heading(self):
        assert _summary(self.speeches) == [
            ("De La CRUZ", "NATIONAL POLICE WEEK: TEXAS HONOR GUARD", True),
            ("De La CRUZ", "Police Chief Jaime Ayala", True),
            ("De La CRUZ", "Honoring Sergeant Joshua Roque", True),
        ]

    def test_the_permission_note_and_the_rule_are_not_text(self):
        assert all("was recognized to address the House" not in s["text"] for s in self.speeches)
        assert all("____" not in s["text"] for s in self.speeches)


def test_a_statement_submitted_rather_than_spoken_is_read():
    """The Record marks it with bullets, where a spoken turn has its
    two-space indent."""
    (speech,) = _parse("senate_submitted_statement.htm", "TRIBUTE TO JOE McDONNELL")
    assert speech["speaker"] == "SHEEHY" and speech["opens"]
    assert "Joe is a native of Ridgewood" in speech["text"]
    assert "bullet" not in speech["text"]


def test_a_leader_going_on_to_another_subject_is_a_new_speech():
    """Designated once, then a new heading and "Mr. President, now on ..."."""
    speeches = _parse("senate_leader_topics.htm", "LEADER REMARKS")
    assert _summary(speeches)[1:] == [
        ("SCHUMER", "Tribute to Lee Saunders", True),
        ("SCHUMER", "China", True),
    ]
    assert speeches[2]["text"].startswith("Mr. President, now on the Xi Jinping visit")
    assert "Lee" not in speeches[2]["text"]


def test_a_subject_in_a_granule_of_its_own_is_the_floor_holders():
    first = (FIXTURES / "senate_leader_granule_1.htm").read_text()
    second = (FIXTURES / "senate_leader_granule_2.htm").read_text()
    speeches, holder = parse_granule_speeches(first, "William Pulte (Executive Session)", "SCHUMER")
    assert _summary(speeches) == [("SCHUMER", "William Pulte", True)]
    assert holder == "SCHUMER"
    speeches, _ = parse_granule_speeches(second, "Nomination of David M. Prouty (Executive Session)", holder)
    assert [s["speaker"] for s in speeches] == ["SCHUMER"]
    # With nobody holding the floor, undesignated words are nobody's.
    assert parse_granule_speeches(second, "Nomination of David M. Prouty (Executive Session)")[0] == []


def test_an_article_put_in_the_record_is_not_a_heading_or_speech():
    speeches = _parse("house_inserted_article.htm", "PROVIDING FOR CONSIDERATION OF H.R. 7567")
    assert [s["speaker"] for s in speeches] == ["McGOVERN", "McGOVERN"]
    assert all(s["heading"] == "PROVIDING FOR CONSIDERATION OF H.R. 7567" for s in speeches)
    assert all("Gasoline prices" not in s["text"] for s in speeches)


class TestDesignations:
    BODY = " I rise today to speak about the appropriations bill before us this week."

    @pytest.mark.parametrize("line, speaker", [
        ("  Mr. SMITH of Florida.", "SMITH of Florida"),
        ("  Mr. McDONALD.", "McDONALD"),
        ("  Ms. VAN WEST.", "VAN WEST"),
        ("  Ms. De La PAZ.", "De La PAZ"),
        ("  Mr. O'BRIEN.", "O'BRIEN"),
        ("  Mr. DesROCHES.", "DesROCHES"),
    ])
    def test_the_record_prints_these_designations(self, line, speaker):
        html = f"<pre>\n{line}{self.BODY}\n</pre>"
        assert [s["speaker"] for s in parse_granule_speeches(html)[0]] == [speaker]

    def test_a_misprinted_officer_is_not_a_member(self):
        html = "<pre>\n  Mr. SPEAKER. Members are reminded to direct their remarks to the Chair.\n</pre>"
        assert parse_granule_speeches(html)[0] == []

    def test_an_address_to_the_chair_is_not_a_designation(self):
        html = f"<pre>\n  Mr. President.{self.BODY}\n</pre>"
        assert parse_granule_speeches(html)[0] == []


class TestSpeechGranules:
    MODS = (FIXTURES / "package_mods.xml").read_text()

    def test_only_granules_with_a_speaking_member_of_that_chamber(self):
        # The pledge (no speaker) is left out; so is Extensions of Remarks,
        # which MODS files under the House but is no floor speech.
        assert speech_granules(self.MODS, "SENATE") == [
            ("CREC-2026-09-24-pt1-PgS4959-6", "LEGISLATIVE SESSION"),
            ("CREC-2026-09-24-pt1-PgS4974-2", "TRIBUTE TO MAJOR GENERAL GREGORY C. PORTER"),
        ]
        assert speech_granules(self.MODS, "HOUSE") == []


class TestFetchDay:
    MODS = (FIXTURES / "package_mods.xml").read_text()
    PAGE = (FIXTURES / "senate_legislative_session.htm").read_text()

    def _run(self, monkeypatch, pages):
        async def mods(client, package_id):
            return self.MODS

        async def htm(client, url):
            return pages.get(url.rsplit("/", 2)[-2])

        monkeypatch.setattr(cr, "fetch_package_mods", mods)
        monkeypatch.setattr(cr, "_fetch_htm", htm)
        return asyncio.run(cr.fetch_day_speeches(None, "CREC-2026-09-24"))

    def test_speeches_carry_their_granule_and_its_own_page(self, monkeypatch):
        day = self._run(monkeypatch, {
            "CREC-2026-09-24-pt1-PgS4959-6": self.PAGE,
            "CREC-2026-09-24-pt1-PgS4974-2": "<pre></pre>",
        })
        assert day["House"] == []
        first = day["Senate"][0]
        assert first["speaker"] == "GRASSLEY" and first["date"] == "2026-09-24"
        assert first["url"] == ("https://www.govinfo.gov/app/details/CREC-2026-09-24/"
                                "CREC-2026-09-24-pt1-PgS4959-6")

    def test_a_granule_that_could_not_be_fetched_fails_the_whole_day(self, monkeypatch):
        assert self._run(monkeypatch, {"CREC-2026-09-24-pt1-PgS4959-6": self.PAGE}) is None


class TestPackageIndex:
    """GovInfo's collection lists packages by last modification: a
    reprocessed 1996 issue came back in a 60-day window."""

    def test_only_this_congress_and_the_window(self, monkeypatch, freeze_utcnow):
        freeze_utcnow(datetime(2026, 10, 7, 12))
        pages = [{"packages": [{"packageId": p} for p in (
            "CREC-1996-07-10", "CREC-2026-09-24", "CREC-2026-07-01", "CREC-2026-10-06", "FR-2026-10-06",
        )]}]

        async def fake(client, url):
            return pages.pop(0)

        monkeypatch.setattr(cr, "_fetch_json", fake)
        assert asyncio.run(cr.fetch_crec_packages(None, 60)) == ["CREC-2026-10-06", "CREC-2026-09-24"]

    def test_an_unreadable_index_is_none_not_empty(self, monkeypatch):
        async def fake(client, url):
            return None

        monkeypatch.setattr(cr, "_fetch_json", fake)
        assert asyncio.run(cr.fetch_crec_packages(None, 60)) is None
