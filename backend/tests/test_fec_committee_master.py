"""The FEC committee master file: parsing, the political-committee rule,
and the per-committee API fallback for anything the file lacks."""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import (
    committee_master_cycles,
    is_political_committee,
    parse_committee_master,
    resolve_committee_meta,
)

# Real rows from cm26.txt (2026-09), pipe-delimited per the FEC dictionary.
CM_ROWS = "\n".join([
    "C00000059|HALLMARK CARDS, INC. PAC (HALLPAC)|KLEIN, CASSIE MS.|2501 MCGEE, MD853||KANSAS CITY|MO|64108|B|Q|UNK|M|C|HALLMARK CARDS, INC.|",
    "C00027466|NRSC|BROGHAMER, KRISTI|425 2ND STREET NE||WASHINGTON|DC|20002|U|Y|REP|M||YOUNG VICTORY COMMITTEE|S4AK00214",
    "C00104299|JPMORGAN CHASE & CO. FEDERAL POLITICAL ACTION COMMITTEE|BRADEN, EILEEN G.|875 15TH STREET, NW|9TH FLOOR|WASHINGTON|DC|20005|B|Q||M|C|JPMORGAN CHASE & CO.|",
    "C00001461|ALASKA STATE MEDICAL ASSOCIATION POLITICAL ACTION COMMITTEE (ALPAC)|POWELL, ELI MR.|4107 LAUREL STREET||ANCHORAGE|AK|99508|U|Q|NAT|Q|M|NONE|",
    "short|line",
])


def test_parse_reads_type_designation_and_connected_org():
    master = parse_committee_master(CM_ROWS)
    assert master["C00104299"] == {"type": "Q", "designation": "B", "connectedOrg": "JPMORGAN CHASE & CO."}
    assert master["C00027466"]["type"] == "Y"
    assert "short" not in master


def test_political_committees_by_registration():
    master = parse_committee_master(CM_ROWS)
    assert is_political_committee(master["C00027466"])       # party (Y)
    assert not is_political_committee(master["C00104299"])   # corporate SSF
    assert is_political_committee({"type": "N", "designation": "D"})  # leadership PAC
    assert is_political_committee({"type": "S", "designation": "P"})  # a senate campaign
    assert not is_political_committee(None)


def test_cycles_cover_the_oldest_senate_window_and_the_current_cycle():
    # A 2020 election's window starts in 2016.
    assert committee_master_cycles(date(2026, 9, 27)) == [2026, 2024, 2022, 2020, 2018, 2016]
    assert committee_master_cycles(date(2025, 3, 1)) == [2026, 2024, 2022, 2020, 2018, 2016]


@pytest.mark.asyncio
async def test_api_is_asked_only_for_committees_the_master_lacks(db_session):
    master = parse_committee_master(CM_ROWS)
    party = {"type": "Y", "designation": "U", "connectedOrg": None}
    with patch("app.pipeline.fetch.fec.fetch_committee_meta", new=AsyncMock(return_value=party)) as api:
        metas = await resolve_committee_meta(None, db_session, {"C00104299", "C09999999"}, master)
    assert {cid: m["type"] for cid, m in metas.items()} == {"C00104299": "Q", "C09999999": "Y"}
    # The fallback's designation reaches the political-committee rule too.
    assert is_political_committee(metas["C09999999"])
    api.assert_awaited_once()


def test_connected_org_only_for_a_sponsored_pac():
    master = parse_committee_master(CM_ROWS)
    # The NRSC row's column holds a joint-fundraising partner, not a sponsor.
    assert master["C00027466"]["connectedOrg"] is None
    # The FEC form's placeholder.
    assert master["C00001461"]["connectedOrg"] is None
    assert master["C00000059"]["connectedOrg"] == "HALLMARK CARDS, INC."
