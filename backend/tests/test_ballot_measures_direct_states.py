"""Tests for the direct-from-state ballot-measure readers for AL, AR, FL,
KY, MD, NC, SC, TN, TX and WV (ballot_measures_<st>.py).

Every fixture is REAL — fetched live 2026-09-28 from the state's own
site and trimmed only where its `_source` note (or HTML comment) says.
PDF-based sources are stored as their pdfplumber extract_text() output,
the same convention ballot_measures_va.py's tests use, so no binary
PDFs are checked in. Measure counts were cross-checked against a second,
non-official source (news coverage of each state's 2026 ballot):
AL 4, AR 4 (three amendments and a bond issue), FL 3, KY 1, MD 3, NC 3,
TN 3, WV 1; SC and TX none.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch.ballot_measure_text import NotYetPublished

from app.pipeline.fetch import (
    ballot_measures_al as al,
    ballot_measures_ar as ar,
    ballot_measures_fl as fl,
    ballot_measures_ky as ky,
    ballot_measures_md as md,
    ballot_measures_nc as nc,
    ballot_measures_pdf as pdf,
    ballot_measures_sc as sc,
    ballot_measures_tn as tn,
    ballot_measures_tx as tx,
    ballot_measures_wv as wv,
)
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state

HERE = Path(__file__).parent


def _json(name):
    return json.loads((HERE / name).read_text())


def _html(name):
    return (HERE / name).read_text()


def _stub_fetch(monkeypatch, module, pages: dict, *, pdfs: dict | None = None):
    """Route a module's get_text/get_bytes to fixture content by URL; a
    URL not in the map fails like a real fetch failure (None). PDFs are
    represented by their extracted text, so pdf_text becomes identity."""
    async def get_text(client, url, label, **kwargs):
        return pages.get(url)

    async def get_bytes(client, url, label):
        return (pdfs or {}).get(url)

    monkeypatch.setattr(module, "get_text", get_text)
    if hasattr(module, "get_bytes"):
        monkeypatch.setattr(module, "get_bytes", get_bytes)
    if hasattr(module, "pdf_text"):
        monkeypatch.setattr(module, "pdf_text", lambda raw: raw)


def test_every_new_state_is_registered_with_a_known_strategy():
    for state in ("AL", "AR", "FL", "KY", "MD", "NC", "SC", "TN", "TX", "WV"):
        assert pdf.is_configured(state), state
        assert source_for_state(state)["source_name"]


# ── Alabama ──────────────────────────────────────────────────────────

class TestAlabama:
    fx = _json("fixtures_al_ballot_statements.json")

    def test_links_come_only_from_this_elections_heading(self):
        links = al.statement_links(self.fx["landing_html"], al.LANDING_URL, 2026)
        assert sorted(links) == ["1", "2", "3", "4"]
        assert links["1"].endswith("/2026-08/FBCStatewideAmendment-1.pdf")
        # No heading for another election: unknown, not "none".
        assert al.statement_links(self.fx["landing_html"], al.LANDING_URL, 2028) is None

    def test_statement_fields_are_lifted_verbatim(self):
        one = al.parse_statement(self.fx["statements"]["1"], "1", 2026)
        assert one["title"].startswith("Proposing an amendment to the Constitution of Alabama of 2022, to provide for the election of a Lieutenant Governor")
        assert one["title"].endswith("Proposed by Act 2026-341.")
        assert one["official_summary"].startswith("Current law generally provides")
        assert one["yes_means"] == "If the majority of voters vote “yes” on Amendment 1, the Alabama Constitution will be changed."
        assert one["no_means"] == "If the majority of voters vote “no” on Amendment 1, the Alabama Constitution will not be changed."
        assert one["fiscal_impact"] == "There are no costs or taxes associated with Amendment 1."
        assert one["fiscal_authority"] == "Alabama Fair Ballot Commission"
        # The summary stops before the state's own yes/no sentences.
        assert "majority of voters" not in one["official_summary"]

    def test_summary_spanning_a_page_break_is_whole(self):
        # Amendment 2's section (4) heading wraps onto page 2.
        two = al.parse_statement(self.fx["statements"]["2"], "2", 2026)
        assert two["official_summary"].startswith("Currently, each county has a board of education")
        assert two["official_summary"].endswith("The Legislature could pass laws to implement approved mergers.")

    def test_all_four_parse(self):
        for n in "1234":
            assert al.parse_statement(self.fx["statements"][n], n, 2026) is not None

    def test_refuses_wrong_election_or_mismatched_number(self):
        assert al.parse_statement(self.fx["statements"]["1"], "1", 2028) is None
        assert al.parse_statement(self.fx["statements"]["1"], "2", 2026) is None

    @pytest.mark.asyncio
    async def test_fetch_end_to_end_and_one_bad_statement_fails_everything(self, monkeypatch):
        links = al.statement_links(self.fx["landing_html"], al.LANDING_URL, 2026)
        pdfs = {links[n]: self.fx["statements"][n] for n in links}
        _stub_fetch(monkeypatch, al, {al.LANDING_URL: self.fx["landing_html"]}, pdfs=pdfs)
        result = await al.fetch_measures(None, 2026)
        assert [p["number"] for p, _ in result] == ["1", "2", "3", "4"]

        pdfs[links["3"]] = "not a statement"
        _stub_fetch(monkeypatch, al, {al.LANDING_URL: self.fx["landing_html"]}, pdfs=pdfs)
        assert await al.fetch_measures(None, 2026) is None


# ── Arkansas ─────────────────────────────────────────────────────────

class TestArkansas:
    fx = _json("fixtures_ar_issue_notices.json")

    def test_reads_only_the_general_assembly_section(self):
        links = ar.issue_links(self.fx["landing_html"], ar.LANDING_URL, 2026)
        assert sorted(links) == ["1", "2", "3", "4"]
        assert ar.issue_links(self.fx["landing_html"], ar.LANDING_URL, 2028) is None

    def test_amendment_uses_popular_name_and_ballot_title(self):
        one = ar.parse_notice(self.fx["notices"]["1"], "1", 2026)
        assert one["title"].startswith('A Constitutional Amendment to be Known as "The Citizens Only Voting Amendment"')
        assert one["official_summary"].startswith('AN AMENDMENT TO THE ARKANSAS CONSTITUTION TO CREATE "THE CITIZENS')
        assert "BE IT RESOLVED" not in one["official_summary"]
        assert one["yes_means"] is None and one["no_means"] is None

    def test_bond_referral_uses_ballot_title_and_question_not_the_choice_labels(self):
        four = ar.parse_notice(self.fx["notices"]["4"], "4", 2026)
        assert four["title"] == "Arkansas Water, Waste Disposal, and Pollution Abatement Facilities Financing Act of 2025."
        assert four["official_summary"].startswith("Shall the Arkansas Natural Resources Commission be authorized")
        assert four["official_summary"].endswith("full faith and credit of the State of Arkansas?")
        # "FOR Issuance ..." is a ballot choice, not an explanation.
        assert four["yes_means"] is None

    def test_citizen_initiative_possible_means_refuse_not_confirm(self):
        html = self.fx["landing_html"]
        # 2026's Attorney General section says "None", so the referrals
        # are the whole ballot (the passing case above). A petition listed
        # there could be on the ballot and nothing here can confirm it —
        # refuse rather than return a possibly incomplete list.
        listed = html.replace(
            "<p>None for the 2026 General Election</p>",
            '<p><a href="/uploads/elections/Some_Initiative.pdf">An Amendment Proposed by Petition</a></p>',
        )
        assert listed != html
        assert ar.issue_links(listed, ar.LANDING_URL, 2026) is None
        # No Attorney General section for the year at all: unknown.
        no_section = html.replace("2026 Proposed Initiatives as Certified by the Attorney General", "Proposed Initiatives")
        assert ar.issue_links(no_section, ar.LANDING_URL, 2026) is None

    def test_real_2024_page_never_listed_its_certified_initiative(self):
        # Archived 2024-10-16, when citizen-initiated Issue 2 (casinos) had
        # been certified sufficient since July 31: the page lists no ballot
        # issue at all, so it can't be the record of initiative
        # certification — and the reader says "not covered", not "none".
        assert "Issue 2" not in self.fx["landing_html_2024_10_16"]
        assert ar.issue_links(self.fx["landing_html_2024_10_16"], ar.LANDING_URL, 2024) is None

    def test_refuses_another_election(self):
        assert ar.parse_notice(self.fx["notices"]["2"], "2", 2028) is None
        assert ar.parse_notice(self.fx["notices"]["2"], "3", 2026) is None


# ── Florida ──────────────────────────────────────────────────────────

class TestFlorida:
    fx = _json("fixtures_fl_initiatives.json")

    def _rows(self):
        return fl.listed_measures(self.fx["results_html"], 2026)

    def test_search_answer_lists_the_three_made_ballot_amendments(self):
        assert fl.search_form_fields(self.fx["search_form_html"])["__RequestVerificationToken"]
        rows = self._rows()
        assert [r["number"] for r in rows] == ["1", "2", "3"]
        assert rows[0]["sponsor"] == "The Florida Legislature/House"
        assert fl.listed_measures(self.fx["results_html"], 2028) == []

    def test_drafter_is_read_from_the_record(self):
        rows = self._rows()
        one = fl.parse_detail(self.fx["details"]["108"], rows[0], 2026)
        three = fl.parse_detail(self.fx["details"]["110"], rows[2], 2026)
        assert one["title_authority"] == "Florida Legislature"
        # Amendment 3's ballot statement was rewritten by the Attorney
        # General under s.101.161(3)(c) — the record says so.
        assert three["title_authority"] == "Florida Attorney General"
        assert three["title"] == "INCREASED HOMESTEAD EXEMPTION; LOWER CAP ON INCREASES IN NON-HOMESTEAD PROPERTY ASSESSMENTS"

    def test_summary_keeps_its_paragraphs_and_skips_the_full_text_link(self):
        three = fl.parse_detail(self.fx["details"]["110"], self._rows()[2], 2026)
        paragraphs = three["official_summary"].split("\n")
        assert paragraphs[0].startswith("This amendment increases the homestead exemption")
        assert paragraphs[-1] == "This amendment takes effect January 1, 2027."
        assert "View Full Text" not in three["official_summary"]
        assert three["fiscal_impact"] is None and three["yes_means"] is None

    def test_detail_must_agree_with_the_row_and_have_made_the_ballot(self):
        rows = self._rows()
        assert fl.parse_detail(self.fx["details"]["108"], rows[1], 2026) is None
        # A record still seeking ballot position (no "Made Ballot" date).
        seeking = {"number": "0", "sponsor": "x"}
        assert fl.parse_detail(self.fx["seeking_detail_html"], seeking, 2028) is None


# ── Kentucky ─────────────────────────────────────────────────────────

class TestKentucky:
    page = _html("fixtures_ky_constitutional_amendments.html")

    def test_the_single_unnumbered_amendment(self):
        [one] = ky.parse_page(self.page, 2026)
        assert one["number"] == ""
        assert one["title"] == "CONSTITUTIONAL AMENDMENT"
        assert one["official_summary"].startswith("Are you in favor of limiting a Governor's ability to grant pardons")
        assert one["official_summary"].endswith("?")
        measure = pdf._to_measure("KY", one, "2026-11-03", ky.PAGE_URL)
        assert measure["id"] == "KY-2026-11-03-CONSTITUTIONAL-AMENDMENT"

    def test_a_page_titled_for_another_year_is_not_this_ballot(self):
        assert ky.parse_page(self.page, 2028) is None


# ── Maryland ─────────────────────────────────────────────────────────

class TestMaryland:
    page = _html("fixtures_md_ballot_questions.html")

    def test_only_statewide_questions(self):
        parsed = md.parse_page(self.page, 2026)
        assert [p["number"] for p in parsed] == ["1", "2", "3"]
        # The Anne Arundel block in the fixture is never read.
        assert not any("Anne Arundel" in (p["official_summary"] or "") for p in parsed)

    def test_yes_no_is_the_explanation_after_the_dash(self):
        one = md.parse_page(self.page, 2026)[0]
        assert one["title"] == "Arbitration Reform for State Employees Act of 2026"
        assert one["yes_means"].startswith("A vote FOR this amendment means that the Governor has to include money")
        assert one["no_means"] == (
            "A vote AGAINST the amendment means that the law about funding agreements "
            "between the State and State employees stays the same."
        )
        assert one["origin"] == "Maryland General Assembly"
        assert one["title_authority"] == "Maryland Secretary of State"

    def test_court_ordered_question_has_no_yes_no_and_says_whose_language_it_is(self):
        three = md.parse_page(self.page, 2026)[2]
        assert three["yes_means"] is None and three["no_means"] is None
        assert three["official_summary"].startswith("Pursuant to the Supreme Court Order dated September 3, 2026")
        assert "Supreme Court of Maryland" in three["title_authority"]

    def test_another_years_page_is_refused(self):
        assert md.parse_page(self.page, 2028) is None


# ── North Carolina ───────────────────────────────────────────────────

class TestNorthCarolina:
    text = _json("fixtures_nc_statewide_referendums.json")["text"]

    def test_three_unnumbered_amendments_keyed_on_their_captions(self):
        parsed = nc.parse_report(self.text, 2026)
        assert [p["title"] for p in parsed] == [
            "CONSTITUTIONAL AMENDMENT - REQUIRE PHOTO ID FOR VOTING",
            "CONSTITUTIONAL AMENDMENT - MAXIMUM INCOME TAX RATE OF 3.5%",
            "CONSTITUTIONAL AMENDMENT - PROPERTY TAX LEVY LIMIT",
        ]
        assert all(p["number"] == "" for p in parsed)
        ids = {pdf._to_measure("NC", p, "2026-11-03", "u")["id"] for p in parsed}
        assert len(ids) == 3

    def test_wrapped_ballot_text_is_rejoined_and_choices_are_not_yes_no(self):
        one = nc.parse_report(self.text, 2026)[0]
        assert one["official_summary"] == (
            "Constitutional amendment to require all voters, not just those presenting to vote "
            "in person, to present photo identification before voting."
        )
        assert one["yes_means"] is None and one["no_means"] is None

    def test_report_for_another_election_or_a_county_is_refused(self):
        assert nc.parse_report(self.text, 2028) is None
        county = self.text.replace("County: ALL COUNTIES", "County: ALAMANCE")
        assert nc.parse_report(county, 2026) is None

    def test_report_url_is_keyed_on_the_election_day(self):
        assert nc.report_url(2026).endswith("/Elections/2026/Candidate%20Filing/statewide_referendums_20261103.pdf")


# ── South Carolina ───────────────────────────────────────────────────

class TestSouthCarolina:
    fx = _json("fixtures_sc_referendums.json")

    def test_picks_the_statewide_general_election(self):
        assert sc.general_election_id(self.fx["elections_2026"], 2026) == "22596"
        assert sc.general_election_id(self.fx["elections_2024"], 2024) == "22114"
        assert sc.general_election_id(self.fx["elections_2026"], 2028) is None

    def test_2026_lists_only_local_questions(self):
        assert sc.statewide_rows(self.fx["results_2026"]) == []

    def test_2024_finds_its_statewide_amendment(self):
        [row] = sc.statewide_rows(self.fx["results_2024"])
        assert row["referendum_id"] == "691"
        parsed = sc.parse_detail(self.fx["detail_691"], row, 2024)
        assert parsed["official_summary"].startswith("Must Section 4, Article II of the Constitution of this State")
        assert parsed["yes_means"] is None
        # Dated for 2024 — never accepted as 2026's.
        assert sc.parse_detail(self.fx["detail_691"], row, 2026) is None

    @pytest.mark.asyncio
    async def test_confirmed_none_needs_the_listing_to_have_rendered(self, monkeypatch):
        async def get_json(client, url, label):
            return self.fx["elections_2026"]

        async def post_text(client, url, label, **kwargs):
            return answer

        monkeypatch.setattr(sc, "get_json", get_json)
        monkeypatch.setattr(sc, "post_text", post_text)
        answer = self.fx["results_2026"]
        assert await sc.fetch_measures(None, 2026) == []
        answer = "<html><body>Error</body></html>"
        assert await sc.fetch_measures(None, 2026) is None


# ── Tennessee ────────────────────────────────────────────────────────

class TestTennessee:
    page = _html("fixtures_tn_proposed_amendments.html")

    def test_reads_only_this_ballots_section(self):
        parsed = tn.parse_page(self.page, 2026)
        assert [p["number"] for p in parsed] == ["1", "2", "3"]
        # The page's 2030 preview (pending a second legislative passage)
        # is not a ballot.
        assert tn.parse_page(self.page, 2030) is None

    def test_state_framing_and_attorney_general_summary(self):
        one = tn.parse_page(self.page, 2026)[0]
        assert one["yes_means"] == "A “yes” vote is a vote to amend the Constitution and adopt the language in the proposed amendment."
        assert one["no_means"] == "A “no” vote is a vote to keep the current language in the Constitution unchanged."
        assert one["official_summary"].startswith("This amendment changes Article I, section 15")
        # The Question (full amended text) is not folded into the summary.
        assert "Shall Article I" not in one["official_summary"]
        assert one["title_authority"] == "Tennessee Attorney General"


# ── Texas ────────────────────────────────────────────────────────────

class TestTexas:
    fx = _json("fixtures_tx_amendment_elections.json")

    def test_2025_propositions_parse(self):
        parsed = tx.parse_results(self.fx["results_2025_11_04"])
        assert [p["number"] for p in parsed] == [str(n) for n in range(1, 18)]
        assert parsed[0]["official_summary"].startswith("The constitutional amendment providing for the creation of the permanent technical institution")

    @pytest.mark.asyncio
    async def test_no_entry_for_this_election_is_confirmed_none(self, monkeypatch):
        _stub_fetch(monkeypatch, tx, {tx.INDEX_URL: self.fx["index_html"]})
        assert await tx.fetch_measures(None, 2026) == []

    @pytest.mark.asyncio
    async def test_an_entry_is_read(self, monkeypatch):
        index = self.fx["index_html"].replace("electionDate=2025-11-04", "electionDate=2026-11-03")
        url = "https://lrl.texas.gov/legis/ConstAmends/results.cfm?electionDate=2026-11-03"
        _stub_fetch(monkeypatch, tx, {tx.INDEX_URL: index, url: self.fx["results_2025_11_04"]})
        assert len(await tx.fetch_measures(None, 2026)) == 17

    @pytest.mark.asyncio
    async def test_an_index_listing_nothing_is_broken_not_none(self, monkeypatch):
        _stub_fetch(monkeypatch, tx, {tx.INDEX_URL: "<html><body><h1>Error</h1></body></html>"})
        assert await tx.fetch_measures(None, 2026) is None


# ── West Virginia ────────────────────────────────────────────────────

class TestWestVirginia:
    fx = _json("fixtures_wv_amendment_notice.json")

    def test_notice_found_in_the_listing(self):
        rows = wv.listing_rows(self.fx["listing_html"])
        notices = [r for r in rows if wv._NOTICE_HEADLINE_RE.search(r["headline"])]
        assert len(rows) == 7 and len(notices) == 1
        assert notices[0]["date"] == "2026-07-23"

    def test_notice_parses_from_the_whole_page(self):
        one = wv.parse_notice(self.fx["notice_html"], 2026)
        assert one["number"] == "1"
        assert one["title"] == "Amendment 1: Citizenship Requirement to Vote in West Virginia Elections Amendment"
        assert one["official_summary"].startswith("This amendment provides that in all elections held in West Virginia")
        assert one["title_authority"] == "West Virginia Legislature (Senate Joint Resolution 9)"
        assert wv.parse_notice(self.fx["notice_html"], 2028) is None

    @pytest.mark.asyncio
    async def test_no_notice_is_not_covered_never_none(self, monkeypatch):
        """A year with no notice is not yet published — never confirmed
        none, and not a failure to alert on either."""
        empty_year_listing = self.fx["listing_html"].replace("Public Notice", "Notice")
        _stub_fetch(monkeypatch, wv, {
            wv.LISTING_URL.format(page=p): empty_year_listing for p in range(wv.MAX_PAGES)
        })
        with pytest.raises(NotYetPublished):
            await wv.fetch_measures(None, 2026)

    async def test_an_unreachable_listing_is_a_failure(self, monkeypatch):
        _stub_fetch(monkeypatch, wv, {})
        assert await wv.fetch_measures(None, 2026) is None


# ── Shared contract ──────────────────────────────────────────────────

def test_to_measure_carries_the_drafter_and_keeps_numeric_ids():
    parsed = {
        "number": "1", "title": "T", "origin": None, "official_summary": "S",
        "fiscal_impact": "F", "yes_means": None, "no_means": None,
        "title_authority": "A", "fiscal_authority": "B",
    }
    m = pdf._to_measure("AL", parsed, "2026-11-03", "u")
    assert m["id"] == "AL-2026-11-03-1"
    assert (m["title_authority"], m["fiscal_authority"]) == ("A", "B")


def test_upsert_stores_the_drafter(db_session):
    from app.models import BallotMeasure
    from app.pipeline import election_pipeline

    parsed = {
        "number": "1", "title": "T", "origin": None, "official_summary": "S",
        "fiscal_impact": "F", "yes_means": None, "no_means": None,
        "title_authority": "Alabama Legislature", "fiscal_authority": "Alabama Fair Ballot Commission",
    }
    item = pdf._to_measure("AL", parsed, "2026-11-03", "u")
    election_pipeline._upsert_measure(db_session, item, item, "Alabama Secretary of State")
    db_session.commit()
    row = db_session.query(BallotMeasure).filter(BallotMeasure.id == "AL-2026-11-03-1").one()
    assert row.title_authority == "Alabama Legislature"
    assert row.fiscal_authority == "Alabama Fair Ballot Commission"


# ── An unrecognised shape is never "none" ────────────────────────────
# Each reader can return [] (a checked "no measures"); these pin that a
# measure present in a shape the reader doesn't know refuses instead of
# silently producing that empty answer.

def test_al_unrecognised_statement_link_refuses():
    html = TestAlabama.fx["landing_html"].replace(
        "Ballot Statement For Statewide Amendment 1<", "Statement on Amendment One<",
    )
    assert al.statement_links(html, al.LANDING_URL, 2026) is None


def test_ar_unrecognised_issue_link_refuses():
    html = TestArkansas.fx["landing_html"].replace(">Issue 1<", ">Issue One<")
    assert ar.issue_links(html, ar.LANDING_URL, 2026) is None


def test_ky_unrecognised_heading_refuses():
    html = TestKentucky.page.replace("underline;\">CONSTITUTIO", "underline;\">PROPOSED CONSTITUTIO")
    assert html != TestKentucky.page
    assert ky.parse_page(html, 2026) is None


def test_md_unrecognised_statewide_heading_refuses():
    html = TestMaryland.page.replace("<h2>Question 2</h2>", "<h2>Statewide Question 2</h2>")
    assert html != TestMaryland.page
    assert md.parse_page(html, 2026) is None


def test_tn_unrecognised_amendment_heading_refuses():
    html = TestTennessee.page.replace("Constitutional Amendment #2", "Amendment Two")
    assert tn.parse_page(html, 2026) is None
