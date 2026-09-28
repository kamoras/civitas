"""Tests for the direct-from-state ballot-measure readers for GA, MS, NH,
NV, OH and UT (ballot_measures_<st>.py).

Every fixture is REAL — each file's `_source` says where it came from,
when, and exactly what was trimmed. PDFs are stored as pdfplumber's own
output (extract_text() per page, or extract_words() where the reader
works from word positions), so no binary PDF is checked in. Where a
state's own site refused this environment (GA: Cloudflare; NV: Imperva),
the document is the state's own file as republished by one of its
counties, and the page the production reader discovers it from could
not be seen — the few tests of that discovery step use a minimal page
written here and say so. Measure counts were cross-checked against news
coverage / Ballotpedia: GA 3, NH 1 amendment (+ the HB 1300 statutory
question), NV 2, OH 1, UT 2, MS none.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.pipeline.fetch import (
    ballot_measures_ga as ga,
    ballot_measures_ms as ms,
    ballot_measures_nh as nh,
    ballot_measures_nv as nv,
    ballot_measures_oh as oh,
    ballot_measures_pdf as pdf,
    ballot_measures_ut as ut,
)
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

HERE = Path(__file__).parent


def _json(name):
    return json.loads((HERE / name).read_text())


def _unpack(page, keys=("text", "x0", "x1", "top")):
    return {"width": page["width"], "words": [dict(zip(keys, w)) for w in page["words"]]}


def test_every_new_state_is_registered_and_resolves():
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    for state in ("GA", "MS", "NH", "NV", "OH", "UT"):
        assert pdf.is_configured(state), state


# ── New Hampshire ────────────────────────────────────────────────────

class TestNewHampshire:
    fx = _json("fixtures_nh_ballot_questions_2026.json")

    def test_finds_the_one_questions_link(self):
        url, ok = nh.find_questions_url(self.fx["details_page"], 2026)
        assert ok
        assert url == "https://mm.nh.gov/files/uploads/sos/docs/questions-on-general-election-ballot-2026-for-web.pdf"

    def test_another_years_page_is_not_this_page(self):
        assert nh.find_questions_url(self.fx["details_page"], 2028) == (None, False)

    def test_both_questions_verbatim(self):
        one, two = nh.parse_questions(self.fx["pdf_text"], 2026)
        assert one["number"] == "1" and two["number"] == "2"
        assert one["title"] == "Questions Relating to Constitutional Amendments proposed by the 2026 General Court"
        assert one["official_summary"].startswith(
            "“Are you in favor of eliminating the office of register of probate by amending articles 71 and 81"
        )
        assert one["official_summary"].endswith(
            "judge.” (Passed by the N.H. House 325 Yes 15 No; Passed by the Senate 23 Yes 1 No) CACR 13"
        )
        assert two["title"] == "Statutory Question required by HB 1300, Chapter 324, 2026"
        # Stored as printed: the placeholders are the state's, not filled in.
        assert two["official_summary"].startswith(
            "“Shall the [name of municipality] limit property tax growth for [name(s) of school district(s)]"
        )
        assert "three- fifths" not in two["official_summary"] and "three-fifths (3/5)" in two["official_summary"]
        for m in (one, two):
            assert m["official_title"] is None
            assert m["yes_means"] is None and m["no_means"] is None and m["fiscal_impact"] is None

    def test_refuses_another_years_document(self):
        assert nh.parse_questions(self.fx["pdf_text"], 2028) is None

    def test_unquoted_numbered_item_or_missing_answer_line_refuses(self):
        text = self.fx["pdf_text"]
        assert nh.parse_questions(text.replace("2. “Shall", "2. Shall"), 2026) is None
        assert nh.parse_questions(text.rsplit("Yes No", 1)[0], 2026) is None

    def test_numbering_gap_refuses(self):
        assert nh.parse_questions(self.fx["pdf_text"].replace("\n2. “", "\n3. “"), 2026) is None

    @pytest.mark.asyncio
    async def test_fetch_paths(self, monkeypatch):
        details = nh.DETAILS_URL.format(year=2026)
        pdf_url = nh.find_questions_url(self.fx["details_page"], 2026)[0]
        pages = {details: (self.fx["details_page"], False)}

        async def get_text_or_missing(client, url, label, **kw):
            assert kw.get("headers") is nh.HEADERS_NO_CONTACT
            return pages.get(url, (None, False))

        async def get_bytes(client, url, label, **kw):
            return self.fx["pdf_text"] if url == pdf_url else None

        monkeypatch.setattr(nh, "get_text_or_missing", get_text_or_missing)
        monkeypatch.setattr(nh, "get_bytes", get_bytes)
        monkeypatch.setattr(nh, "pdf_text", lambda raw: raw)
        result = await nh.fetch_measures(None, 2026)
        assert [(p["number"], u) for p, u in result] == [("1", pdf_url), ("2", pdf_url)]

        # No link on the year's page (2024's page had none): not yet, no deadline.
        pages[details] = (self.fx["details_page"].replace("Questions on the General Election Ballot", "Something"), False)
        with pytest.raises(NotYetPublished) as exc:
            await nh.fetch_measures(None, 2026)
        assert exc.value.deadline_applies is False

        pages[details] = (None, True)  # 404
        with pytest.raises(NotYetPublished):
            await nh.fetch_measures(None, 2026)

        pages[details] = (None, False)  # any other failure
        assert await nh.fetch_measures(None, 2026) is None


# ── Utah ─────────────────────────────────────────────────────────────

class TestUtah:
    fx = _json("fixtures_ut_general_certification_2026.json")
    text = "\n".join(fx["certification_pages"])

    def test_finds_the_general_certification_not_the_primary_ones(self):
        url, ok = ut.find_certification_url(self.fx["current_page"], 2026)
        assert ok and url == "https://vote.utah.gov/wp-content/uploads/2026/09/2026-General-Election-Certification.pdf"
        assert ut.find_certification_url(self.fx["current_page"], 2028) == (None, False)

    def test_amendments_by_letter_with_no_ocr_text_stored(self):
        measures = ut.parse_certification(self.text, 2026)
        assert [(m["number"], m["title"]) for m in measures] == [
            ("A", "Constitutional Amendment A"), ("B", "Constitutional Amendment B"),
        ]
        for m in measures:
            assert m["official_summary"] is None and m["official_title"] is None
            assert m["yes_means"] is None and m["no_means"] is None

    def test_needs_the_no_propositions_statement(self):
        assert ut.parse_certification(self.text.replace("no statewide ballot", "the following statewide ballot"), 2026) is None

    def test_misread_heading_or_count_mismatch_refuses(self):
        assert ut.parse_certification(self.text.replace("Constitutional Amendment B.", "Constitutional Amendrnent B."), 2026) is None
        assert ut.parse_certification(self.text.replace("() For () Against", "", 1), 2026) is None

    def test_refuses_another_years_certification(self):
        assert ut.parse_certification(self.text, 2028) is None

    @pytest.mark.asyncio
    async def test_fetch_paths(self, monkeypatch):
        page = {"html": self.fx["current_page"]}
        cert = ut.find_certification_url(self.fx["current_page"], 2026)[0]

        async def get_text(client, url, label, **kw):
            return page["html"] if url == ut.CURRENT_URL else None

        async def get_bytes(client, url, label, **kw):
            return self.text if url == cert else None

        monkeypatch.setattr(ut, "get_text", get_text)
        monkeypatch.setattr(ut, "get_bytes", get_bytes)
        monkeypatch.setattr(ut, "pdf_text", lambda raw: raw)
        assert [p["number"] for p, _ in await ut.fetch_measures(None, 2026)] == ["A", "B"]

        page["html"] = self.fx["current_page"].replace("2026-General-Election-Certification.pdf", "x.pdf")
        with pytest.raises(NotYetPublished):
            await ut.fetch_measures(None, 2026)

        # A bot challenge carries no "2026 Election Cycle": a failure.
        page["html"] = "<html><head><title>Just a moment...</title></head><body></body></html>"
        assert await ut.fetch_measures(None, 2026) is None


# ── Nevada ───────────────────────────────────────────────────────────

class TestNevada:
    fx = _json("fixtures_nv_ballot_questions_2026.json")

    def test_both_questions_with_the_states_own_framing(self):
        six, seven = nv.parse_booklet(self.fx["pages"], 2026)
        assert (six["number"], seven["number"]) == ("6", "7")
        assert six["official_summary"].startswith(
            "Should the Nevada Constitution be amended to create an individual’s fundamental right to an abortion"
        )
        assert six["official_summary"].endswith("at any point during the pregnancy?")
        assert six["yes_means"].startswith("A “Yes” vote would create a new section of the Nevada Constitution")
        assert six["no_means"] == (
            "A “No” vote would keep the Nevada Constitution in its current form and would not impact the "
            "availability of abortion as a statutory right under Nevada law."
        )
        # A sentence broken by a page footer / stray line still reads whole.
        assert seven["yes_means"].endswith("provide certain information in order to cast a legal ballot.")
        assert seven["no_means"] == "A “No” vote would keep the Nevada Constitution in its current form."
        assert six["fiscal_impact"].startswith("FINANCIAL IMPACT – CANNOT BE DETERMINED")
        assert six["fiscal_impact"].endswith("Legislative Counsel Bureau – August 1, 2024")
        assert "approximately $6,750" in seven["fiscal_impact"]
        assert seven["fiscal_authority"] == "Fiscal Analysis Division of the Legislative Counsel Bureau"
        assert (six["title"], six["origin"]) == ("Amendment to the Nevada Constitution", "Initiative Petition C-05-2023")
        for m in (six, seven):
            assert m["official_title"] is None
            assert "Nevada Secretary of State Page" not in m["fiscal_impact"]

    def test_county_questions_are_never_read(self):
        text = " ".join(str(m) for m in nv.parse_booklet(self.fx["pages"], 2026))
        assert "Churchill" not in text and "Washoe" not in text

    def test_refuses_another_years_booklet(self):
        assert nv.parse_booklet(self.fx["pages"], 2028) is None

    def test_contents_must_match_the_sections(self):
        pages = list(self.fx["pages"])
        pages[3] = pages[3].replace("State Question - No. 7 ...", "State Question - No. 8 ...")
        assert nv.parse_booklet(pages, 2026) is None

    def test_missing_vote_sentence_refuses(self):
        pages = [p.replace("A “No” vote would keep the Nevada Constitution in its current form.", "") for p in self.fx["pages"]]
        assert nv.parse_booklet(pages, 2026) is None

    def test_discovery_from_a_petitions_page(self):
        # SYNTHETIC page: nvsos.gov could not be seen from this
        # environment. It checks only the matching rules.
        page = (
            "<html><head><title>2026 Petitions &amp; General Election Ballot Questions | Nevada Secretary of State"
            "</title></head><body><a href='/files/C-05-2023.pdf'>C-05-2023</a>"
            "<a href='/files/2026-ballot-question-booklet.pdf'>2026 Ballot Question Guide</a></body></html>"
        )
        assert nv.find_booklet_url(page, 2026) == ("https://www.nvsos.gov/files/2026-ballot-question-booklet.pdf", True)
        challenge = "<html><head><script src='/_Incapsula_Resource'></script></head><body></body></html>"
        assert nv.find_booklet_url(challenge, 2026) == (None, False)


# ── Georgia ──────────────────────────────────────────────────────────

class TestGeorgia:
    fx = _json("fixtures_ga_amendments_booklet_2026.json")
    pages = [_unpack(p, ("text", "x0", "x1", "top", "bold")) for p in fx["pages"]]

    def test_three_amendments_verbatim(self):
        one, two, three = ga.parse_booklet(self.pages, 2026)
        assert [m["number"] for m in (one, two, three)] == ["1", "2", "3"]
        assert one["title"] == (
            "Increases the maximum qualifying acreage for assessment and taxation as bona fide conservation use property."
        )
        assert one["official_title"] == (
            "Shall the Constitution of Georgia, for the purpose of protecting family farmland, be amended so as to "
            "increase the maximum acreage to qualify for assessment and taxation as a bona fide conservation use "
            "property from 2,000 acres to 4,000 acres?"
        )
        assert two["official_title"] == (
            "Shall the Constitution of Georgia be amended so as to require that all probate court judges be "
            "elected in nonpartisan elections?"
        )
        # "9-" wrapped at the hyphen before "1-1" reads as the fund's name.
        assert three["title"] == (
            "Authorizes creation of Georgia Next Generation 9-1-1 Fund for 9-1-1 systems expansion, maintenance, and operation."
        )
        assert three["official_summary"].startswith("Summary: This proposal authorizes the General Assembly")
        assert three["official_summary"].endswith("by adding a new subparagraph (s).")
        for m in (one, two, three):
            assert "( )" not in m["official_title"] and "YES" not in m["official_title"]
            assert "copy of this entire" not in m["official_summary"]
            assert m["yes_means"] is None and m["fiscal_impact"] is None

    def test_cover_count_must_match(self):
        pages = [dict(p) for p in self.pages]
        pages[0] = {**pages[0], "words": [
            {**w, "text": "1-4"} if w["text"] == "1-3" else w for w in pages[0]["words"]
        ]}
        assert ga.parse_booklet(pages, 2026) is None

    def test_refuses_another_years_booklet(self):
        assert ga.parse_booklet(self.pages, 2028) is None

    def test_word_across_a_column_edge_refuses(self):
        pages = [dict(p) for p in self.pages]
        words = list(pages[6]["words"])
        words.append({"text": "straddle", "x0": 160.0, "x1": 190.0, "top": 700.0, "bold": False})
        pages[6] = {**pages[6], "words": words}
        assert ga.parse_booklet(pages, 2026) is None

    def test_discovery_link_matching(self):
        # SYNTHETIC page: sos.ga.gov could not be seen from this
        # environment; the hrefs are the real 2026 and 2024 files.
        page = (
            "<html><body><a href='/sites/default/files/2026-09/2026%20Constitutional%20Summaries%20booklet%20FINAL.pdf'>"
            "2026 booklet</a><a href='/sites/default/files/2024-09/Statewide_Const_Amendments_and_Ballot_Questions_"
            "Booklet.pdf'>2024</a></body></html>"
        )
        url, ok = ga.find_booklet_url(page, 2026)
        assert ok and url.endswith("2026-09/2026%20Constitutional%20Summaries%20booklet%20FINAL.pdf")


# ── Ohio ─────────────────────────────────────────────────────────────

class TestOhio:
    fx = _json("fixtures_oh_sample_ballots.json")
    s2026 = [_unpack(p) for p in fx["sample_2026"]]

    def test_finds_this_generals_sample_ballot_not_a_directive(self):
        url, ok = oh.find_sample_ballot_url(self.fx["directives_page"], 2026)
        assert ok and url == "https://www.ohiosos.gov/assets/dir2026-45-official-sample-ballot-november-3-2026.pdf"

    def test_issue_3_verbatim_with_the_ballots_own_yes_no(self):
        (three,) = oh.parse_sample_ballot(self.s2026, 2026)
        assert three["number"] == "3"
        assert three["official_title"] == "TO REQUIRE VOTERS TO PRESENT PHOTO IDENTIFICATION IN ORDER TO VOTE"
        assert three["yes_means"] == "A “YES” vote means approval of the amendment."
        assert three["no_means"] == "A “NO” vote means disapproval of the amendment."
        assert three["origin"] == "Joint Resolution of the General Assembly"
        lines = three["official_summary"].split("\n")
        assert lines[:4] == [
            "Proposed Constitutional Amendment",
            "Proposed by Joint Resolution of the General Assembly",
            "To enact Section 5 of Article V of the Constitution of the State of Ohio",
            "A majority yes vote is required for the adoption of Section 5.",
        ]
        assert lines[5] == (
            "• Require voters to present an approved form of government-issued photo identification in order to vote."
        )
        assert lines[-2:] == ["If approved, the amendment shall take effect immediately.", "SHALL THE AMENDMENT BE APPROVED?"]
        assert "YES” vote" not in three["official_summary"]

    def test_local_templates_are_never_read(self):
        text = str(oh.parse_sample_ballot(self.s2026, 2026))
        assert "LIQUOR" not in text and "TAX LEVY" not in text

    def test_2022_two_issues_side_by_side(self):
        one, two = oh.parse_state_issue_page(_unpack(self.fx["sample_2022_issue_page"]))
        assert one["title"] == "To require courts to consider factors like public safety when setting the amount of bail"
        assert two["title"] == "To prohibit local government from allowing non-electors to vote"
        assert "non-electors" not in one["official_summary"]
        assert one["yes_means"] is None  # 2022's ballot printed no such sentence

    def test_2024_issue_across_three_columns_whole(self):
        (one,) = oh.parse_state_issue_page(_unpack(self.fx["sample_2024_issue_page"]))
        assert one["origin"] == "Initiative Petition"
        items = [ln for ln in one["official_summary"].split("\n") if ln[:1].isdigit()]
        assert len(items) == 10
        # Item 4 ends on the footer's baseline in column 1.
        assert items[3].endswith("willful neglect of duty or gross misconduct.")
        assert one["official_summary"].endswith("SHALL THE AMENDMENT BE APPROVED?")

    def test_2024_issue_page_with_a_candidate_footer_is_still_read(self):
        # 2024's issue page carried the candidate pages' footer. Read by
        # footer alone, that ballot would have been "none".
        page = _unpack(self.fx["sample_2024_issue_page"])
        assert oh.page_kind(page) == "candidates"
        assert oh.parse_sample_ballot([self.s2026[0], page], 2026)[0]["number"] == "1"

    def test_2025_ballot_with_no_state_issue_is_none(self, monkeypatch):
        monkeypatch.setattr(oh, "election_day", lambda year: date(2025, 11, 4))
        assert oh.parse_sample_ballot([_unpack(p) for p in self.fx["sample_2025"]], 2025) == []

    def test_unknown_page_or_wrong_year_refuses(self):
        stray = {"width": 612.0, "words": [{"text": "Something", "x0": 30.0, "x1": 90.0, "top": 900.0}]}
        assert oh.parse_sample_ballot(self.s2026 + [stray], 2026) is None
        assert oh.parse_sample_ballot(self.s2026, 2028) is None

    def test_issue_without_its_question_refuses(self):
        page = self.s2026[2]
        broken = {**page, "words": [w for w in page["words"] if w["text"] not in ("SHALL",)]}
        assert oh.parse_state_issue_page(broken) is None


# ── Mississippi ──────────────────────────────────────────────────────

class TestMississippi:
    fx = _json("fixtures_ms_sample_ballot_2026.json")

    def test_finds_the_sample_ballot(self):
        url, ok = ms.find_sample_ballot_url(self.fx["elections_page"], 2026)
        assert ok and url == "https://www.sos.ms.gov/content/documents/Elections/2026/Sample Ballot 9-9-26.pdf"
        assert ms.find_sample_ballot_url(self.fx["elections_page"], 2028) == (None, True)

    def test_complete_composite_without_a_measure_is_none(self):
        assert ms.confirms_none(self.fx["sample_ballot_pages"], 2026) is True

    def test_anything_like_a_measure_refuses(self):
        pages = list(self.fx["sample_ballot_pages"])
        pages[-1] = pages[-1].replace("END OF BALLOT", "CONSTITUTIONAL AMENDMENT\nYes\nNo\nEND OF BALLOT")
        assert ms.confirms_none(pages, 2026) is False

    def test_incomplete_or_other_election_refuses(self):
        pages = self.fx["sample_ballot_pages"]
        assert ms.confirms_none(pages[:-1], 2026) is False
        assert ms.confirms_none(pages, 2028) is False

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_never_a_measure(self, monkeypatch):
        url = ms.find_sample_ballot_url(self.fx["elections_page"], 2026)[0]

        async def get_text(client, u, label, **kw):
            return self.fx["elections_page"] if u == ms.ELECTIONS_URL else None

        async def get_bytes(client, u, label, **kw):
            return "ballot" if u == url else None

        monkeypatch.setattr(ms, "get_text", get_text)
        monkeypatch.setattr(ms, "get_bytes", get_bytes)
        monkeypatch.setattr(ms, "pdf_pages", lambda raw: self.fx["sample_ballot_pages"])
        assert await ms.fetch_measures(None, 2026) == []
        with pytest.raises(NotYetPublished):
            await ms.fetch_measures(None, 2028)
