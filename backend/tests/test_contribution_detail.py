"""The complete contribution detail behind top donors and the industry
breakdown (v6.26).

Until 2026-10 both were built from one page of 100 receipts each, which
covered under 1% of a large campaign's money ($300K of one Senate campaign's $34M
of itemized individual money, none of its $3.4M from PACs). Now: every
committee contribution from the FEC's bulk file, itemized individual money
by occupation, the employee side of donors by employer.
"""

import io
import zipfile
from unittest.mock import patch

import pytest

from app.pipeline.analyze.score_calculator import _top_donor_concentration
from app.pipeline.fetch import fec
from app.pipeline.transform import normalize_finance as nf
from app.pipeline.transform.occupation_industry import industry_of_occupation, normalize_title

FINANCIALS = [{
    "candidate_election_year": 2024, "receipts": 10_000_000, "contributions": 10_000_000,
    "other_political_committee_contributions": 600_000,
    "individual_itemized_contributions": 4_000_000, "individual_unitemized_contributions": 5_400_000,
}]
COMMITTEES = {
    "C1": {"name": "JPMORGAN CHASE & CO. FEDERAL PAC", "type": "Q", "designation": "B",
           "connectedOrg": "JPMORGAN CHASE & CO."},
    "C2": {"name": "NRSC", "type": "Y", "designation": "U", "connectedOrg": None},
}
AI = {"JPMORGAN CHASE & CO.": {"type": "PAC", "industry": "FINANCE", "skip": False},
      "GOOGLE": {"type": "Org/Employees", "industry": "TECH", "skip": False}}


def _detail(**overrides):
    detail = {
        "pacs": {"C1": 500_000, "C2": 100_000},
        "committees": COMMITTEES,
        "occupations": [
            {"occupation": "ATTORNEY", "total": 1_000_000},
            {"occupation": "PHYSICIAN", "total": 500_000},
            {"occupation": "RETIRED", "total": 2_000_000},
            {"occupation": "CEO", "total": 500_000},
        ],
        "employers": [{"employer": "GOOGLE", "total": 80_000}, {"employer": "RETIRED", "total": 2_000_000}],
    }
    return {**detail, **overrides}


@pytest.fixture(autouse=True)
def _no_model():
    # The employer-status filter is an embedding check; stub it as the one
    # FEC convention these fixtures use.
    with patch.object(nf, "classify_employer_skips_batch", lambda names: {n for n in names if n == "RETIRED"}):
        yield


def _breakdown(detail):
    f = nf.normalize_finance({"name": "DOE, JANE", "office": "S"}, FINANCIALS, [], [], [],
                             ai_classifications=AI, detail=detail)
    return {row["industry"]: row["total"] for row in f["industryBreakdown"]}, f


def test_occupations_put_itemized_money_in_industries():
    by, _ = _breakdown(_detail())
    assert by["LAWYERS"] == 1_000_000 and by["HEALTHCARE"] == 500_000
    # Retired donors and chief executives (spread across every industry)
    # stay unattributed rather than guessed.
    assert by["LARGE_INDIVIDUAL"] == 2_500_000


def test_every_committee_that_gave_is_counted_by_its_registration():
    by, _ = _breakdown(_detail())
    assert by["FINANCE"] == 500_000  # the sponsor's industry
    assert by["POLITICAL"] == 100_000  # a party committee, whatever its name


def test_occupation_money_never_exceeds_the_itemized_total():
    inflated = [{"occupation": "ATTORNEY", "total": 8_000_000}]  # twice the itemized total
    by, _ = _breakdown(_detail(occupations=inflated))
    assert by["LAWYERS"] == 4_000_000 and "LARGE_INDIVIDUAL" not in by


def test_top_donors_come_from_the_complete_detail():
    _, f = _breakdown(_detail())
    donors = {d["name"].upper(): d for d in f["topDonors"]}
    assert donors["JPMORGAN CHASE & CO."]["total"] == 500_000
    assert donors["JPMORGAN CHASE & CO."]["isCommittee"] and donors["NRSC"]["type"] == "Party/Ideological"
    assert donors["GOOGLE"]["total"] == 80_000 and "RETIRED" not in donors


def test_null_and_an_occupation_in_the_employer_field_are_not_donors():
    """Live 2026-10-03: "Null" was a top donor on 354 scorecards, usually
    rank 1, and "Owner", "President", "Attorney" sat among the
    organizations."""
    employers = [
        {"employer": "GOOGLE", "total": 80_000},
        {"employer": "NULL", "total": 206_450},
        {"employer": "ATTORNEY", "total": 40_000},  # as occupation: $1M
        {"employer": "OWNER", "total": 30_000},  # never an occupation here
    ]
    _, f = _breakdown(_detail(employers=employers))
    donors = {d["name"].upper() for d in f["topDonors"]}
    assert "GOOGLE" in donors
    assert "NULL" not in donors and "ATTORNEY" not in donors
    # Only this committee's own occupation field marks a value: with no
    # donor listing OWNER as their occupation, it is left as filed.
    assert "OWNER" in donors


def test_an_organization_also_written_as_an_occupation_is_kept():
    # A few donors writing their employer in both boxes doesn't make it a job.
    assert nf._employer_values_not_organizations(
        [{"employer": "GOOGLE", "total": 80_000}],
        [{"occupation": "GOOGLE", "total": 2_000}],
    ) == set()
    assert nf._employer_values_not_organizations(
        [{"employer": "Null", "total": 5}, {"employer": "PRESIDENT", "total": 10}],
        [{"occupation": "PRESIDENT", "total": 900}],
    ) == {"NULL", "PRESIDENT"}
    # Occupations unreadable: only the missing-value text is known.
    assert nf._employer_values_not_organizations(
        [{"employer": "NULL", "total": 5}, {"employer": "PRESIDENT", "total": 10}], None,
    ) == {"NULL"}


def test_a_missing_source_falls_back_to_the_samples_not_to_zero():
    # Occupations unreadable: the breakdown uses the sampled receipts.
    receipt = {"contributor_employer": "GOOGLE", "contribution_receipt_amount": 3_000, "memo_text": ""}
    f = nf.normalize_finance({"name": "DOE, JANE", "office": "S"}, FINANCIALS, [receipt], [], [],
                             ai_classifications=AI, detail=_detail(occupations=None))
    by = {row["industry"]: row["total"] for row in f["industryBreakdown"]}
    assert by["TECH"] == 3_000


def test_a_small_gift_campaign_has_a_measurable_low_concentration():
    """A campaign of small gifts listed only a few dozen big donors, which used
    to read as too few to measure, a neutral 50. Over all outside money the top ten are
    a sliver, which is the point."""
    funding = {"totalContributions": 33_229_708, "topDonors": [{"total": 16_000, "type": "Org/Employees"}] * 48}
    share, _, pool = _top_donor_concentration(funding)
    assert pool == 33_229_708 and share < 0.005


def test_own_money_is_not_outside_money():
    funding = {"totalContributions": 1_000_000, "topDonors": [
        {"total": 500_000, "type": "Self-Funded"}, {"total": 50_000, "type": "PAC"}]}
    share, _, pool = _top_donor_concentration(funding)
    assert pool == 500_000 and share == 0.1


# ── The bulk file ──

PAS2 = "\n".join([
    "C1|N|Q2|P2024|1|24K|CCM|DOE FOR SENATE|X|NV|1|||1|5000|C9|S0NV00001|a|1|||1",
    "C1|N|Q2|P2024|1|24K|CCM|DOE FOR SENATE|X|NV|1|||1|-1000|C9|S0NV00001|a|1|||2",  # refund
    "C2|N|Q2|P2024|1|24E|ORG|AD BUY|X|NV|1|||1|90000|C9|S0NV00001|a|1|||3",  # independent spending
    "C3|N|Q2|P2024|1|24Z|CCM|DOE FOR SENATE|X|NV|1|||1|250|C9|S0NV00001|a|1|||4",  # in kind
])


def test_the_bulk_file_keeps_contributions_and_nets_refunds():
    parsed = fec.parse_committee_contributions(PAS2.splitlines())
    assert parsed == {"S0NV00001": {"C1": 4000.0, "C3": 250.0}}


def test_a_missing_cycle_is_unknown_not_zero():
    data = {2022: {"S0NV00001": {"C1": 100.0}}, 2024: {"S0NV00001": {"C1": 50.0, "C4": -10.0}}}
    assert fec.candidate_committee_contributions(data, "S0NV00001", [2022, 2024]) == {"C1": 150.0}
    assert fec.candidate_committee_contributions(data, "S0NV00001", [2020, 2022]) is None
    assert fec.candidate_committee_contributions(None, "S0NV00001", [2022]) is None


async def test_the_bulk_download_is_parsed_and_cached(db_session):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("itpas2.txt", PAS2)
    calls = []

    class Client:
        async def get(self, url, **kw):
            calls.append(url)

            class R:
                content = buf.getvalue()

                def raise_for_status(self):
                    pass
            return R()

    first = await fec.fetch_committee_contributions(Client(), db_session, [2024])
    again = await fec.fetch_committee_contributions(Client(), db_session, [2024])
    assert first == again == {2024: {"S0NV00001": {"C1": 4000.0, "C3": 250.0}}}
    assert calls == ["https://www.fec.gov/files/bulk-downloads/2024/pas224.zip"]


# ── Occupations ──

@pytest.mark.parametrize("occupation,industry", [
    ("ATTORNEY", "LAWYERS"), ("Registered Nurse", "HEALTHCARE"), ("REALTOR", "REAL_ESTATE"),
    ("PROFESSOR", "EDUCATION"), ("software engineer", "TECH"),
    # The data gives no industry: spread across all of them, or no job.
    ("CEO", None), ("RETIRED", None), ("NOT EMPLOYED", None), ("HOMEMAKER", None), ("", None),
])
def test_occupations_are_read_against_the_census_and_onet_data(occupation, industry):
    assert industry_of_occupation(occupation) == industry


def test_titles_meet_in_the_singular():
    assert normalize_title("Registered Nurses") == normalize_title("REGISTERED NURSE") == "REGISTERED NURSE"
    assert normalize_title("Business") == "BUSINESS"  # -ss is not a plural
