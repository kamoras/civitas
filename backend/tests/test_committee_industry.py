"""A committee's industry from FEC and SEC records (fec.structured_industry)
outranks any reading of its name."""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch import fec
from app.pipeline.fetch.sec_tickers import SecUnavailable
from app.pipeline.transform import normalize_finance as nf
from app.pipeline.transform.normalize_finance import build_top_donors

# cm.txt columns 0..14, per the FEC data dictionary.
_ROW = "C00000001|AIR TRAFFIC CONTROLLERS PAC|||||||B|Q|||L|AIR TRAFFIC CONTROLLERS ASSOCIATION|"


def test_the_committee_master_keeps_the_organization_type():
    rows = fec.parse_committee_rows(_ROW)
    assert rows["C00000001"]["orgType"] == "L"
    resolved = fec.resolve_connected_orgs(rows)
    assert resolved["C00000001"]["orgType"] == "L"


@pytest.mark.parametrize("meta,industry", [
    ({"type": "X", "designation": "U", "orgType": "L"}, "POLITICAL"),  # a party committee
    ({"type": "Q", "designation": "B", "orgType": "L"}, "LABOR_UNIONS"),
    ({"type": "Q", "designation": "B", "orgType": "C", "sponsorIndustry": "OIL_GAS"}, "OIL_GAS"),
    ({"type": "Q", "designation": "B", "orgType": "T"}, None),  # a trade association: the classifier's
    ({"type": "Q", "designation": "U", "orgType": None, "connectedOrg": None}, "POLITICAL"),  # nonconnected
    ({"type": "Q", "designation": "U", "orgType": None, "connectedOrg": "A SPONSOR"}, None),
    ({"type": "Q", "designation": None, "connectedOrg": None}, None),  # source without the org type
    (None, None),
])
def test_structured_industry(meta, industry):
    assert fec.structured_industry(meta) == industry


@pytest.mark.parametrize("meta,name_industry,industry", [
    # A credit unions' trade association whose name reads as a union.
    ({"type": "Q", "designation": "B", "orgType": "T", "connectedOrg": "A CREDIT UNION LEAGUE"}, "LABOR_UNIONS", "OTHER"),
    ({"type": "Q", "designation": "B", "orgType": "C", "connectedOrg": "A RAILROAD"}, "LABOR_UNIONS", "OTHER"),
    # Any other reading of a trade association's name stands.
    ({"type": "Q", "designation": "B", "orgType": "T", "connectedOrg": "A BANKERS GROUP"}, "FINANCE", "FINANCE"),
    # A labor organization is decided by its type; no type recorded, the name stands.
    ({"type": "Q", "designation": "B", "orgType": "L", "connectedOrg": "A UNION"}, "OTHER", "LABOR_UNIONS"),
    ({"type": "Q", "designation": None, "connectedOrg": None}, "LABOR_UNIONS", "LABOR_UNIONS"),
    (None, "LABOR_UNIONS", "LABOR_UNIONS"),
])
def test_a_union_reading_needs_a_labor_organization(meta, name_industry, industry):
    assert fec.committee_industry(meta, lambda: name_industry) == industry


@pytest.mark.asyncio
async def test_a_corporate_sponsor_takes_its_sec_industry(db_session):
    metas = {
        "C1": {"type": "Q", "designation": "B", "orgType": "C", "connectedOrg": "PHILLIPS 66 COMPANY"},
        "C2": {"type": "Q", "designation": "B", "orgType": "L", "connectedOrg": "SOME UNION"},
    }
    with patch.object(fec, "issuer_industries", new_callable=AsyncMock,
                      return_value=({}, {"PHILLIPS 66 COMPANY": "OIL_GAS"})) as sec:
        await fec._add_sponsor_industries(None, db_session, metas)
    assert sec.await_args.args[3] == ["PHILLIPS 66 COMPANY"]  # corporations only
    assert metas["C1"]["sponsorIndustry"] == "OIL_GAS"
    assert "sponsorIndustry" not in metas["C2"]


@pytest.mark.asyncio
async def test_an_unreachable_sec_leaves_the_name_classifier_to_answer(db_session):
    metas = {"C1": {"orgType": "C", "connectedOrg": "PHILLIPS 66 COMPANY"}}
    with patch.object(fec, "issuer_industries", new_callable=AsyncMock, side_effect=SecUnavailable("down")):
        await fec._add_sponsor_industries(None, db_session, metas)
    assert "sponsorIndustry" not in metas["C1"]


def test_a_union_pac_is_labor_whatever_its_name_reads_like():
    receipt = {
        "contributor_name": "AIR TRAFFIC CONTROLLERS PAC", "contribution_receipt_amount": 5000,
        "memo_text": "", "entity_type": "COM", "contributor_id": "C00000001",
    }
    ai = {"AIR TRAFFIC CONTROLLERS PAC": {"type": "PAC", "industry": "GUNS", "skip": False}}
    donors = build_top_donors(
        [receipt], [], "", ai_classifications=ai,
        committee_meta_map={"C00000001": {"type": "Q", "designation": "B", "orgType": "L", "connectedOrg": None}},
    )
    assert donors[0]["industry"] == "LABOR_UNIONS"


def test_a_joint_fundraiser_is_not_a_donor():
    """Its distributions are the candidate's own fundraising (the FEC
    totals count them as transfers): "... Victory" committees were among
    senators' top donors."""
    meta = {"C1": {"name": "SMITH VICTORY", "type": "N", "designation": "J", "orgType": None, "connectedOrg": None},
            "C2": {"name": "ACME PAC", "type": "Q", "designation": "B", "orgType": "C", "connectedOrg": "ACME CORP",
                   "sponsorIndustry": "MANUFACTURING"}}
    # The payment-processor check and the industry classifier's batch
    # priming are embedding checks, not under test: neither name is a
    # processor, and ACME's industry comes from its sponsor.
    with patch.object(nf, "primed_industry_lookups", lambda names, db=None: contextlib.nullcontext()), \
         patch.object(nf, "skip_entities_batch", lambda names: set()), \
         patch.object(nf, "is_skip_entity", lambda name: False):
        f = nf.normalize_finance(
            {"name": "SMITH, JO", "office": "S"},
            [{"candidate_election_year": 2024, "receipts": 900_000, "contributions": 900_000}], [], [],
            detail={"pacs": {"C1": 400_000, "C2": 50_000}, "committees": meta, "occupations": None, "employers": None},
        )
    names = {d["name"].upper() for d in f["topDonors"]}
    assert "ACME CORP" in names and "SMITH VICTORY" not in names
    assert sum(r["total"] for r in f["industryBreakdown"] if r["industry"] == "POLITICAL") == 0
