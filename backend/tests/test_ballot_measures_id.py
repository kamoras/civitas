"""Tests for Idaho's voter-pamphlet strategy (ballot_measures_id.py).

fixtures_id_voter_pamphlet_2026.json is REAL — pdfplumber
extract_words(extra_attrs=["fontname", "size"]) output for pages 4-7,
11, 13 and 14 of archive.voteidaho.gov/download/2026_Voter_Pamphlet.pdf,
fetched 2026-09-28 (subset-font prefixes stripped, "bottom" dropped,
rounded to 0.1pt). Trimmed to what the parser reads: argument bodies
and full texts are cut, but page 11 keeps its first rows ("Be it enacted
by the People of the State of Idaho:") and page 13 keeps the "ABSENTEE
VOTING" heading that ends Proposition One's block.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.pipeline.fetch import ballot_measures_id as idaho
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.ballot_measures_pdf import discover_pdf_url_checked

FIXTURE = json.loads((Path(__file__).parent / "fixtures_id_voter_pamphlet_2026.json").read_text())


def _pages(fixture=FIXTURE):
    return [SimpleNamespace(extract_words=lambda extra_attrs=None, w=p["words"]: w) for p in fixture]


def _measures(fixture=FIXTURE):
    return {m["number"]: m for m in idaho.parse_document(_pages(fixture))}


def test_all_four_real_questions_are_found():
    assert list(_measures()) == [
        "House Joint Resolution 4", "House Joint Resolution 6", "Proposition One", "House Bill 932",
    ]


def test_amendment_keeps_question_statement_and_idahos_own_yes_no():
    hjr4 = _measures()["House Joint Resolution 4"]
    # Two drafters, two fields — never one summary under a joint label.
    assert hjr4["official_title"].startswith("Shall Section 26, Article III of the Constitution")
    assert hjr4["official_title"].endswith("?")
    assert hjr4["title_authority"] == "Idaho Legislature"
    assert hjr4["official_summary"].startswith(idaho.STATEMENT_HEADING + ": This proposed constitutional amendment would give")
    assert "Shall Section 26" not in hjr4["official_summary"]
    assert hjr4["yes_means"] == (
        "A YES vote would give the Legislature exclusive authority to legalize marijuana, "
        "narcotics, or other psychoactive substances in the State of Idaho."
    )
    assert hjr4["no_means"] == (
        "A NO vote would make no change to Idaho’s Constitution, which currently allows "
        "changes to Idaho’s controlled substances laws using the initiative process."
    )
    assert hjr4["origin"] == "Idaho Legislature"


def test_yes_no_columns_split_where_the_no_label_sits():
    # HJR 6's YES text starts one row BELOW its NO text on the real page.
    hjr6 = _measures()["House Joint Resolution 6"]
    assert hjr6["yes_means"] == "A YES vote would designate English the official state language of Idaho."
    assert hjr6["no_means"].startswith("A NO vote would make no change to Idaho’s Constitution")


def test_initiative_titles_fiscal_and_drafter_are_read_not_assumed():
    prop = _measures()["Proposition One"]
    assert prop["title"] == "Reproductive Freedom & Privacy Act"
    # The pamphlet's heading name is not a ballot title; the Attorney
    # General's short and long ballot titles are the quoted text.
    assert prop.get("official_title") is None
    assert prop["official_summary"].startswith("Short Ballot Title: Measure creating right to abortion")
    assert "Long Ballot Title: The measure seeks to change Idaho’s laws" in prop["official_summary"]
    assert prop["fiscal_impact"].startswith("The State estimates the initiative would increase state expenditures")
    assert prop["fiscal_impact"].endswith("readily available data to support.")
    # The sponsor's FUNDING SOURCE STATEMENT is not the state's fiscal statement.
    assert "No funding source is required" not in prop["fiscal_impact"]
    assert prop["fiscal_authority"] == "Idaho Division of Fiscal Management"
    assert prop["origin"] == "Idaho voters (initiative petition)"
    assert prop["yes_means"].startswith("A YES vote would support creating a right to abortion")
    assert prop["no_means"].startswith("A NO vote would support making no change")


def test_advisory_question_reads_both_option_columns_in_order():
    adv = _measures()["House Bill 932"]
    s = adv["official_summary"]
    order = [s.index(f"({c})") for c in "abcdef"]
    assert order == sorted(order)
    assert "bolt-action rifle (.30-06)" in s
    # The purpose follows under the pamphlet's heading as printed.
    assert " BRIEF STATEMENT OF PURPOSE: " in s
    assert adv["yes_means"] is None and adv["no_means"] is None


def test_a_proposition_without_the_enacting_clause_gets_no_assumed_origin():
    trimmed = [p for p in FIXTURE if p["page"] != 11]
    assert _measures(trimmed)["Proposition One"]["origin"] is None


def test_recognised_heading_with_missing_sections_fails_the_document():
    broken = [dict(p) for p in FIXTURE]
    broken[0] = {**broken[0], "words": [w for w in broken[0]["words"] if w["top"] < 200]}
    with pytest.raises(ValueError):
        idaho.parse_document(_pages(broken))


def test_unrecognised_measure_heading_fails_rather_than_being_skipped():
    renamed = [dict(p) for p in FIXTURE]
    renamed[0] = {**renamed[0], "words": [
        {**w, "text": "Referendum"} if w["text"] == "Amendment" and w["size"] > 15 else w
        for w in renamed[0]["words"]
    ]}
    with pytest.raises(ValueError):
        idaho.parse_document(_pages(renamed))


# The page voteidaho.gov redirected every address to (302) from 2026-10-21,
# trimmed from the real archive.voteidaho.gov/index.html: its dropdown and
# its pamphlet card, both linking the current pamphlet.
MAINTENANCE_PAGE = """
<p>We're performing some maintenance on our main website. We've designed this page to get you to your
destination as quickly as possible while the update is ongoing.</p>
<li><a class="dropdown-item" href="https://archive.voteidaho.gov/download/2026_Voter_Pamphlet.pdf" target="_blank">Voter Pamphlet [PDF]</a></li>
<h5 class="card-title">2026 Voter Pamphlet</h5>
<p class="card-text">Download this PDF for details about Proposition One and other measures on the ballot for November 3rd.</p>
<a href="https://archive.voteidaho.gov/download/2026_Voter_Pamphlet.pdf" class="btn btn-outline-primary">Voter Pamphlet [PDF]</a>
"""


async def _discover_behind_redirect(target, landing_page=MAINTENANCE_PAGE):
    def handler(request):
        if request.url.host == "voteidaho.gov":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text=landing_page)

    source = source_for_state("ID")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await discover_pdf_url_checked(
            client, source["landing_page_url"], 2026, tuple(source["keyword"]), exclude=tuple(source["exclude"]),
        )


@pytest.mark.asyncio
async def test_registry_follows_the_sites_redirect_to_the_pamphlet():
    """2026-10-21: the registered general-election page answered 302 to a
    maintenance page on archive.voteidaho.gov; reading the redirect as an
    unreadable page failed Idaho with all four measures still linked."""
    url, complete = await _discover_behind_redirect("https://archive.voteidaho.gov/index.html")
    assert url == "https://archive.voteidaho.gov/download/2026_Voter_Pamphlet.pdf"
    assert complete


@pytest.mark.asyncio
async def test_a_redirect_off_the_states_site_is_not_read():
    """A redirect to another host is not the state's page: nothing on it is
    taken, and the crawl is incomplete (a failure), never "not published"."""
    assert await _discover_behind_redirect("https://example.com/index.html") == (None, False)


@pytest.mark.asyncio
async def test_a_redirected_page_without_the_link_is_not_a_complete_read():
    """A moved page that lands on a homepage says nothing about the document
    the registered page would have linked: no link there is a failure, not
    "not published yet"."""
    landing = '<a href="https://voteidaho.gov/register">Register to vote</a>'
    assert await _discover_behind_redirect("https://archive.voteidaho.gov/index.html", landing) == (None, False)
