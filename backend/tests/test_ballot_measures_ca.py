"""Tests for California's ballot-measure strategy (ballot_measures_ca.py),
which reads the Secretary of State's Official Voter Information Guide as
HTML.

fixtures_ca_voter_guide_html.json is REAL (see its _source): the 2026
guide's propositions index and both pages of Props 1, 3 and 45, and the
archived 2024 guide's index and both pages of its Prop 2, fetched live
2026-09-28.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ca as ca
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

FX = json.loads((Path(__file__).parent / "fixtures_ca_voter_guide_html.json").read_text())
CURRENT = "https://voterguide.sos.ca.gov/"
ARCHIVE_2024 = "https://vigarchive.sos.ca.gov/2024/general/"


def _prop(number, prefix="current"):
    ts = ca.parse_title_summary(FX[f"{prefix}_{number}_title_summary"], number)
    quick = ca.parse_quick_reference(FX[f"{prefix}_{number}_quick"], number)
    return ca.combine(number, ts, quick)


class TestIndex:
    def test_both_real_indexes_list_the_same_propositions(self):
        props = ca.proposition_links(FX["current_index"], CURRENT + "propositions/")
        assert ca.quick_reference_numbers(FX["current_quick_reference_index"]) == list(props)
        archived = ca.proposition_links(FX["archive_2024_index"], ARCHIVE_2024 + "propositions/")
        assert ca.quick_reference_numbers(FX["archive_2024_quick_reference_index"]) == list(archived)

    def test_the_2026_guide_lists_its_fourteen_propositions(self):
        links = ca.proposition_links(FX["current_index"], CURRENT + "propositions/")
        assert list(links) == ["1", "2", "3", "4", "5", "37", "38", "39", "40", "41", "42", "43", "44", "45"]
        assert links["37"] == "https://voterguide.sos.ca.gov/propositions/37/"

    def test_the_banner_dates_the_guide(self):
        assert ca.names_general_election(FX["current_index"], 2026)
        assert not ca.names_general_election(FX["current_index"], 2024)
        assert ca.names_general_election(FX["archive_2024_index"], 2024)
        links = ca.proposition_links(FX["archive_2024_index"], ARCHIVE_2024 + "propositions/")
        assert list(links) == ["2", "3", "4", "5", "6", "32", "33", "34", "35", "36"]

    def test_an_index_entry_it_cant_read_refuses_the_guide(self):
        html = FX["current_index"].replace('href="/propositions/40/"', 'href="/propositions/forty/"', 1)
        assert html != FX["current_index"]
        assert ca.proposition_links(html, CURRENT + "propositions/") is None


class TestProposition:
    def test_attorney_generals_title_and_summary_and_the_laos_fiscal_estimate(self):
        three = _prop("3")
        assert three["number"] == "3"
        assert three["official_title"] == three["title"] == (
            "PROVIDES PERMANENT FUNDING FOR SCHOOLS AND HEALTH CARE BY EXTENDING EXISTING TAX ON HIGH INCOMES. "
            "INITIATIVE CONSTITUTIONAL AMENDMENT."
        )
        assert three["title_authority"] == "California Attorney General"
        assert three["official_summary"].startswith(
            "• Makes permanent the existing 2012 voter-approved tax rates for high-income Californians"
        )
        assert three["fiscal_impact"] == (
            "• Maintains $5 billion to $15 billion of annual state income tax revenue by making a temporary "
            "tax increase on high-income earners permanent instead of letting it expire in 2031."
        )
        assert three["fiscal_authority"] == "California Legislative Analyst's Office"
        assert three["origin"] == "Petition Signatures"

    def test_the_states_own_yes_no_sentences_verbatim(self):
        three = _prop("3")
        assert three["yes_means"] == (
            "An income tax increase on high-income earners in place since 2012 would become permanent "
            "instead of expiring in 2031."
        )
        assert three["no_means"] == "An income tax increase on high-income earners in place since 2012 would expire in 2031."

    def test_real_characters_no_ligature_damage(self):
        """The PDF guide's text layer read "infation", "fnance"; the HTML is
        the words the state wrote."""
        for n in ("1", "3", "45"):
            p = _prop(n)
            text = " ".join(v for v in p.values() if isinstance(v, str))
            for damaged in ("infation", "fnance", "efect", "ofer", "signifcant"):
                assert damaged not in text, (n, damaged)

    def test_a_nested_list_keeps_its_items_and_their_nesting(self):
        one = _prop("1")
        lines = one["official_summary"].split("\n")
        assert lines[0].startswith("• Authorizes $11.25 billion in state general obligation bonds")
        assert lines[1] == "• Allocates funds:"
        assert lines[2] == "– $5.1 billion to build and rehabilitate multifamily rental housing;"
        assert lines[-1] == "– $200 million to support local housing programs."
        assert one["origin"] == "the Legislature"

    def test_the_archived_2024_guide_reads_the_same_way(self):
        two = _prop("2", prefix="archive_2024")
        assert two["title"] == "AUTHORIZES BONDS FOR PUBLIC SCHOOL AND COMMUNITY COLLEGE FACILITIES. LEGISLATIVE STATUTE."
        assert two["yes_means"] == (
            "The state could borrow $10 billion to build new or renovate existing public school and "
            "community college facilities."
        )
        assert two["fiscal_impact"].startswith("• Increased state costs of about $500 million annually")

    def test_a_page_for_another_proposition_is_refused(self):
        assert ca.parse_title_summary(FX["current_3_title_summary"], "4") is None
        assert ca.parse_quick_reference(FX["current_3_quick"], "4") is None
        # Titles that disagree between a proposition's two pages.
        ts = ca.parse_title_summary(FX["current_3_title_summary"], "3")
        quick = ca.parse_quick_reference(FX["current_45_quick"].replace(">45<", ">3<"), "3")
        assert ca.combine("3", ts, quick) is None

    def test_a_page_that_doesnt_name_its_drafter_is_refused(self):
        no_ag = FX["current_3_title_summary"].replace("PREPARED BY THE ATTORNEY GENERAL", "PREPARED BY STAFF")
        assert ca.parse_title_summary(no_ag, "3") is None
        no_lao = FX["current_3_title_summary"].replace("LEGISLATIVE ANALYST", "STAFF")
        assert ca.parse_title_summary(no_lao, "3") is None

    def test_a_missing_vote_sentence_is_refused(self):
        no_no = FX["current_3_quick"].replace("A NO vote on this measure means", "A NO vote means")
        assert no_no != FX["current_3_quick"]
        assert ca.parse_quick_reference(no_no, "3") is None


def _serve(monkeypatch, pages: dict, missing: set = frozenset()):
    async def get_text(client, url, label, **kw):
        return pages.get(url)

    async def get_text_or_missing(client, url, label):
        if url in missing:
            return None, True
        return pages.get(url), False

    monkeypatch.setattr(ca, "get_text", get_text)
    monkeypatch.setattr(ca, "get_text_or_missing", get_text_or_missing)


def _one_qrg_index(numbers):
    """The real 2026 Quick Reference Guide index cut down to `numbers`."""
    import re

    html = FX["current_quick_reference_index"]
    for m in re.finditer(r'<li><a href="/quick-reference-guide/(\d+)\.htm">.*?</li>', html, re.S):
        if m.group(1) not in numbers:
            html = html.replace(m.group(0), "")
    return html


def _one_prop_index(numbers):
    """The real 2026 index cut down to `numbers`' entries."""
    import re

    html = FX["current_index"]
    for m in re.finditer(r'<li>\s*<a href="/propositions/(\d+)/">.*?</li>', html, re.S):
        if m.group(1) not in numbers:
            html = html.replace(m.group(0), "")
    return html


class TestFetch:
    async def test_every_listed_proposition_is_read(self, monkeypatch):
        index = _one_prop_index({"1", "3", "45"})
        pages = {
            CURRENT + "propositions/": index,
            CURRENT + "quick-reference-guide/": _one_qrg_index({"1", "3", "45"}),
        }
        for n in ("1", "3", "45"):
            pages[CURRENT + f"propositions/{n}/"] = FX[f"current_{n}_quick"]
            pages[CURRENT + f"propositions/{n}/title-summary.htm"] = FX[f"current_{n}_title_summary"]
        _serve(monkeypatch, pages)
        results = await ca.fetch_measures(None, 2026)
        assert [p["number"] for p, _ in results] == ["1", "3", "45"]
        assert results[0][1] == CURRENT + "propositions/1/title-summary.htm"

    async def test_one_proposition_unreadable_refuses_the_state(self, monkeypatch):
        """Fail-closed completeness: the guide's index lists it, so a
        guide read without it is not the ballot."""
        index = _one_prop_index({"1", "3", "45"})
        pages = {
            CURRENT + "propositions/": index,
            CURRENT + "quick-reference-guide/": _one_qrg_index({"1", "3", "45"}),
        }
        for n in ("1", "3"):
            pages[CURRENT + f"propositions/{n}/"] = FX[f"current_{n}_quick"]
            pages[CURRENT + f"propositions/{n}/title-summary.htm"] = FX[f"current_{n}_title_summary"]
        _serve(monkeypatch, pages)
        assert await ca.fetch_measures(None, 2026) is None

    async def test_a_proposition_missing_from_one_of_the_two_indexes_refuses(self, monkeypatch):
        """The regression: the final "read == listed" check compared the
        propositions index with itself and could never fail — a
        proposition missing from that one index went unnoticed. The Quick
        Reference Guide's own index is the independent second count."""
        pages = {
            CURRENT + "propositions/": _one_prop_index({"1", "3"}),
            CURRENT + "quick-reference-guide/": _one_qrg_index({"1", "3", "45"}),
        }
        for n in ("1", "3", "45"):
            pages[CURRENT + f"propositions/{n}/"] = FX[f"current_{n}_quick"]
            pages[CURRENT + f"propositions/{n}/title-summary.htm"] = FX[f"current_{n}_title_summary"]
        _serve(monkeypatch, pages)
        assert await ca.fetch_measures(None, 2026) is None
        # ... and an unreachable second index is a failure too.
        del pages[CURRENT + "quick-reference-guide/"]
        assert await ca.fetch_measures(None, 2026) is None

    async def test_an_earlier_year_is_read_from_the_archive(self, monkeypatch):
        pages = {
            CURRENT + "propositions/": FX["current_index"],
            ARCHIVE_2024 + "propositions/": FX["archive_2024_index"],
        }
        _serve(monkeypatch, pages)
        assert await ca.find_guide(None, 2024) == ARCHIVE_2024

    async def test_no_guide_for_the_year_yet_is_not_yet_published(self, monkeypatch):
        """The current guide is another election's and the archive has none
        (404) for this year: not posted yet — not a failure, never none."""
        _serve(monkeypatch, {CURRENT + "propositions/": FX["current_index"]},
               missing={"https://vigarchive.sos.ca.gov/2028/general/propositions/"})
        with pytest.raises(NotYetPublished):
            await ca.find_guide(None, 2028)

    async def test_an_unreachable_page_is_a_failure(self, monkeypatch):
        _serve(monkeypatch, {})
        assert await ca.fetch_measures(None, 2026) is None
        # Current guide readable but the archive is down (not a 404).
        _serve(monkeypatch, {CURRENT + "propositions/": FX["current_index"]})
        assert await ca.find_guide(None, 2028) is None


def test_registered_as_the_html_strategy():
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources
    from app.pipeline.fetch import ballot_measures_pdf as pdf

    sources.invalidate_cache()
    assert sources.source_for_state("CA")["strategy"] == "ca_voter_guide_html"
    assert pdf.MULTI_DOCUMENT_STRATEGIES["ca_voter_guide_html"] is ca.fetch_measures
    assert "ca_quick_reference" not in pdf.STRATEGIES
