"""The FEC committee master file: parsing, the political-committee rule,
and the per-committee API fallback for anything the file lacks."""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.fec import (
    committee_master_cycles,
    fetch_committee_master,
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
    "C00013342|UNITED MINE WORKERS OF AMERICA - COAL MINERS POLITICAL ACTION COMMITTEE|X|18354 QUANTICO GATEWAY DRIVE|SUITE 200|TRIANGLE|VA|22172|B|Q||M|L|UNITED MINE WORKERS OF AMERICA - COAL MINERS POLITICAL ACTION COMMITTEE|",
    "short|line",
])

# A sponsor chain (cm26, 2026-09): the Ohio bankers' PAC names the ABA's
# PAC, whose sponsor is the association, which also files in its own name
# (type E). Two PACs naming each other are a loop.
CHAIN_ROWS = "\n".join([
    "C00074799|OHIO BANKERS LEAGUE POLITICAL ACTION COMMITTEE (FEDERAL)|X|A||C|OH|1|U|Q||M|T|AMERICAN BANKERS ASSOCIATION PAC (BANKPAC)|",
    "C00035253|OREGON BANKERS ASSOCIATION POLITICAL ACTION COMMITTEE|X|A||C|OR|1|U|Q||M|T|AMERICAN BANKERS ASSOCIATION PAC|",
    "C00004275|AMERICAN BANKERS ASSOCIATION PAC (BANKPAC)|X|A||C|DC|1|B|Q||M|T|AMERICAN BANKERS ASSOCIATION (ABA)|",
    "C30002851|AMERICAN BANKERS ASSOCIATION|X|A||C|DC|1|U|E||||NONE|",
    "C00048181|WISCONSIN BANKERS ASSOCIATION (WISBANKPAC)|X|A||C|WI|1|B|Q||M|T|WISCONSIN BANKERS ASSOCIATION|",
    "C00000001|COALPAC, A POLITICAL ACTION COMMITTEE OF THE NATIONAL MINING ASSOCIATION|X|A||C|DC|1|B|Q||M|T|MINEPAC, A POLITICAL ACTION COMMITTEE OF THE NATIONAL MINING ASSOCIATION|",
    "C00000002|MINEPAC, A POLITICAL ACTION COMMITTEE OF THE NATIONAL MINING ASSOCIATION|X|A||C|DC|1|B|Q||M|T|COALPAC, A POLITICAL ACTION COMMITTEE OF THE NATIONAL MINING ASSOCIATION|",
])


def test_a_sponsor_that_is_a_pac_is_followed_to_its_sponsor():
    master = parse_committee_master(CHAIN_ROWS)
    # Named with or without the alias, the ABA's PAC leads to the ABA; its
    # own type-E registration is the organization, not another PAC.
    assert master["C00074799"]["connectedOrg"] == "AMERICAN BANKERS ASSOCIATION (ABA)"
    assert master["C00035253"]["connectedOrg"] == "AMERICAN BANKERS ASSOCIATION (ABA)"
    assert master["C00004275"]["connectedOrg"] == "AMERICAN BANKERS ASSOCIATION (ABA)"


def test_a_sponsor_matching_only_the_committees_own_alias_is_the_organization():
    master = parse_committee_master(CHAIN_ROWS)
    assert master["C00048181"]["connectedOrg"] == "WISCONSIN BANKERS ASSOCIATION"


def test_a_pac_naming_itself_under_another_alias_names_no_sponsor():
    # cm26: C00343590 is "(MCA-PAC)" and names "(MCAA-PAC)" as its sponsor.
    master = parse_committee_master(
        "C00343590|MECHANICAL CONTRACTORS ASSOCIATION OF AMERICA POLITICAL ACTION COMMITTEE (MCA-PAC)"
        "|X|A||C|MD|1|B|Q||M|T|MECHANICAL CONTRACTORS ASSOCIATION OF AMERICA POLITICAL ACTION COMMITTEE (MCAA-PAC)|"
    )
    assert master["C00343590"]["connectedOrg"] is None


@pytest.mark.asyncio
async def test_a_sponsor_citing_a_pacs_earlier_name_resolves_across_cycles(db_session):
    # cm24 names C00007880 by its old name, cm26 by its new one; the
    # California league's cm26 registration still cites the old name.
    import io
    import zipfile
    from unittest.mock import MagicMock

    def cm_zip(rows: list[str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("cm.txt", "\n".join(rows))
        return buf.getvalue()

    old_name = "AMERICA'S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL ASSOCIATION, INC."
    cm24 = cm_zip([f"C00007880|{old_name}|X|A||C|DC|1|B|Q||M|T|AMERICA'S CREDIT UNIONS|"])
    cm26 = cm_zip([
        "C00007880|AMERICA'S CREDIT UNIONS PAC|X|A||C|DC|1|B|Q||M|T|AMERICA'S CREDIT UNIONS|",
        f"C00235929|CALIFORNIA'S CREDIT UNIONS POLITICAL ACTION COMMITTEE|X|A||C|CA|1|U|Q||M|T|{old_name}|",
    ])
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        MagicMock(content=cm24, raise_for_status=MagicMock()),
        MagicMock(content=cm26, raise_for_status=MagicMock()),
    ])
    master = await fetch_committee_master(client, db_session, [2024, 2026])
    assert master["C00235929"]["connectedOrg"] == "AMERICA'S CREDIT UNIONS"
    # Served from the cache the second time, resolved the same way.
    again = await fetch_committee_master(client, db_session, [2024, 2026])
    assert again["C00235929"]["connectedOrg"] == "AMERICA'S CREDIT UNIONS"
    assert client.get.await_count == 2


def test_a_sponsor_loop_names_no_sponsor():
    master = parse_committee_master(CHAIN_ROWS)
    assert master["C00000001"]["connectedOrg"] is None
    assert master["C00000002"]["connectedOrg"] is None


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
    # A fund naming itself as its sponsor names no sponsor.
    assert master["C00013342"]["connectedOrg"] is None
    assert master["C00013342"]["type"] == "Q"
