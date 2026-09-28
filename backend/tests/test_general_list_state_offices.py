"""State offices read from the certified November lists that already
supplied the federal races -- Maine, Iowa, Colorado, Maryland, Alaska,
Hawaii, Delaware, Nebraska, North Dakota (certified_table), Illinois
(grouped_list_pdf) and Florida (dos_canlist) -- and the two defects found
on the way: Illinois's primary results naming the loser of its Republican
Secretary of State primary, and a "Write-in" bucket stored as a nominee.

Every row below is real, copied from the state's own list as fetched on
2026-09-28 and trimmed to a handful of contests (only columns the adapter
never reads are dropped). Each state's `format` comes from the real
sources file, so a change there is tested here too:

  ME  maine.gov "2026 General Candidate List.xlsx"
  CO  sos.state.co.us 2026GeneralCandidateListOfficial.xlsx
  MD  elections.maryland.gov 2026_GG_statewide_candidatelist.csv
  NE  sos.nebraska.gov final statewide general candidate list (PDF rows)
  ND  vip.sos.nd.gov/candidatelist.aspx?eid=348, posted per contest
  AK  elections.alaska.gov/candidates/?election=26genr (3 pages)
  IL  elections.il.gov Website Candidate List, STATEWIDE EXECUTIVE OFFICES
      and STATE REPRESENTATIVE/STATE SENATOR groups (word positions)
  FL  dos.elections.myflorida.com/candidates/canlist.asp, CAB and LEG
      groups of 20261103-GEN and the same-day special 20261103-S01
"""

import json
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import state_candidates as sc
from app.pipeline.fetch import state_candidates_tabular as tabular
from app.pipeline.fetch.state_candidates_certified_table import (
    fetch_confirmed_candidates as fetch_certified,
    parse_certified_rows,
)
from app.pipeline.fetch.state_candidates_dos_canlist import (
    fetch_confirmed_candidates as fetch_canlist,
    parse_canlist,
)
from app.pipeline.fetch.state_candidates_grouped_list_pdf import (
    fetch_confirmed_candidates as fetch_grouped,
    parse_grouped_list,
)
from app.models import StatewideNominee

_SOURCES = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "state_candidate_sources.json").read_text()
)["states"]


def _general(state):
    return _SOURCES[state]["general_list"]


def _state(records):
    """(office, district, party, label, name) for every state-office record."""
    return {
        (r["office"], r["district"], r["party"], r.get("party_label"), r["last_name"])
        for r in records if r["office"] not in ("S", "H")
    }


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_every_list_named_here_is_opted_in():
    for state in ("ME", "IA", "CO", "MD", "AK", "HI", "DE", "NE", "ND", "IL", "FL"):
        assert _general(state).get("statewide_offices") is True, state


# ── Maine: the SOS's own office codes ────────────────────────────────

def _me(office, dist, party, last, first, middle="", suffix=""):
    return {"Office": office, "Dist": dist, "County": "", "Party": party, "Last Name": last,
            "First Name": first, "Middle Name": middle, "Suffix": suffix}


ME_ROWS = [
    _me("US", "", "R", "Collins", "Susan", "M."),
    _me("CG", "1", "D", "Pingree", "Chellie"),
    _me("GOV", "", "Independent", "Bennett", "Rick"),
    _me("GOV", "", "R", "Charles", "Robert", "B."),
    _me("GOV", "", "D", "Pingree", "Hannah", "M."),
    _me("SS", "9", "Unenrolled", "Carey", "Stephen", "Richard"),
    _me("SS", "13", "Unenrolled", "Mitchell", "Edwin", "M.", "Jr."),
    _me("SR", "12", "Unenrolled", "Gott", "Roy", "D."),
    _me("CC", "1", "D", "Doe", "Jane"),  # a county commissioner: no code, never read
    _me("", "", "", "", ""),
]


def test_maine_reads_its_governor_and_legislature_through_its_own_codes():
    records = parse_certified_rows(ME_ROWS, _general("ME")["format"], state_offices=True)
    assert _state(records) == {
        # Rick Bennett, the independent no primary could show.
        ("governor", None, "I", None, "Rick Bennett"),
        ("governor", None, "R", None, "Robert B. Charles"),
        ("governor", None, "D", None, "Hannah M. Pingree"),
        # "Unenrolled" is Maine's word for no party.
        ("upper", "9", "I", None, "Stephen Richard Carey"),
        ("upper", "13", "I", None, "Edwin M. Mitchell Jr."),
        ("lower", "12", "I", None, "Roy D. Gott"),
    }
    assert {(r["office"], r["district"], r["last_name"]) for r in records if r["office"] in ("S", "H")} == {
        ("S", None, "Collins"), ("H", 1, "Pingree"),
    }


def test_state_office_codes_are_read_only_under_the_opt_in():
    records = parse_certified_rows(ME_ROWS, _general("ME")["format"], state_offices=False)
    assert _state(records) == set()


# ── Colorado: offices spelled out, Congress by a bare district ───────

def _co(name, office, district, party, write_in="N"):
    return {"Candidate Name": name, "Office": office, "District": district, "Party": party, "Write In?": write_in}


CO_ROWS = [
    _co("Gabe Evans", "US House of Representatives", "8", "Republican Party"),
    _co("Phil Weiser", "Governor", "State", "Democratic Party"),
    _co("Lesley Dahlkemper", "Lt. Governor", "State", "Democratic Party"),
    _co("Jeff Peckman", "Governor", "State", "Unity Party"),
    _co("Greg Lopez", "Governor", "State", "Unaffiliated"),
    _co("Edie Hooton", "University of Colorado Board of Regents", "2", "Democratic Party"),
    _co("Jamie Jeffery", "State Senate", "1", "Democratic Party"),
    _co("Javier Mabrey", "State House of Representatives", "1", "Democratic Party"),
    _co("Andrew C. Poland", "District Court", "1", "", ""),  # a judgeship: no code
    _co("Donald Willoughby", "US Senate", "State", "Unaffiliated", "Y"),  # declared write-in
]


def test_colorado_reads_every_party_its_ballot_prints():
    records = parse_certified_rows(CO_ROWS, _general("CO")["format"], state_offices=True)
    assert _state(records) == {
        ("governor", None, "D", None, "Phil Weiser"),
        ("lt_governor", None, "D", None, "Lesley Dahlkemper"),
        # Unity Party has no FEC code: kept as printed, not dropped.
        ("governor", None, "O", "Unity Party", "Jeff Peckman"),
        ("governor", None, "I", None, "Greg Lopez"),
        ("university_regent", "2", "D", None, "Edie Hooton"),
        ("upper", "1", "D", None, "Jamie Jeffery"),
        ("lower", "1", "D", None, "Javier Mabrey"),
    }
    # Congress still resolves through its exact code with its district.
    assert [(r["office"], r["district"]) for r in records if r["office"] == "H"] == [("H", 8)]


# ── Maryland: the running mate is a related candidate ─────────────────

def _md(office, district, last, first, party, mate_first="", mate_last="", status="Active"):
    return {
        "Office Name": office, "Contest Run By District Name and Number": district,
        "Candidate Ballot Last Name and Suffix": last, "Candidate First Name and Middle Name": first,
        "Office Political Party": party, "Candidate Status": status,
        "Related Candidate First Name and Middle Name": mate_first,
        "Related Candidate Last Name and Suffix": mate_last,
    }


def test_maryland_shows_the_ticket_and_a_party_it_cannot_code():
    rows = [
        _md("Governor / Lt. Governor", "State Of Maryland", "Moore", "Wes", "Democratic", "Aruna", "Miller"),
        _md("Governor / Lt. Governor", "State Of Maryland", "White", "Cathy", "Working Class Party",
            "Cathy", "Permut"),
        _md("Comptroller", "State Of Maryland", "Dunn", "Sonya", "Republican"),
        _md("House of Delegates", "Legislative District 1A", "Jobe", "Jason M.", "Democratic"),
    ]
    assert _state(parse_certified_rows(rows, _general("MD")["format"], state_offices=True)) == {
        ("governor", None, "D", None, "Wes Moore and Aruna Miller"),
        ("governor", None, "O", "Working Class Party", "Cathy White and Cathy Permut"),
        ("comptroller", None, "R", None, "Sonya Dunn"),
        ("lower", "1A", "D", None, "Jason M. Jobe"),
    }


# ── Nebraska: a party name wrapped onto a line of its own ─────────────

NE_ROWS = [
    {"Office": "For Governor and Lt. Governor", "Party (if applicable)": "Republican",
     "Candidate Name": "Jim Pillen and Joe Kelly"},
    {},
    {"Office": "For Governor and Lt. Governor", "Party (if applicable)": "Legal Marijuana",
     "Candidate Name": "Rick Beard and Keenya Barnes"},
    {"Party (if applicable)": "NOW"},
    {"Office": "For Secretary of State", "Party (if applicable)": "Nebraska Working",
     "Candidate Name": "Paul Rumbaugh"},
    {"Party (if applicable)": "People"},
    {"Office": "For United States Senator", "Party (if applicable)": "Legal Marijuana",
     "Candidate Name": "Mike Marvin"},
    {"Party (if applicable)": "NOW"},
    # Non-partisan, and not a label the shared legislative gate reads.
    {"Office": "For Member of the Legislature", "District Name": "District 02", "Candidate Name": "Dean Helmick"},
]


def test_nebraska_joins_a_wrapped_party_name_back_together():
    records = parse_certified_rows(NE_ROWS, _general("NE")["format"], state_offices=True)
    assert _state(records) == {
        ("governor", None, "R", None, "Jim Pillen and Joe Kelly"),
        ("governor", None, "O", "Legal Marijuana NOW", "Rick Beard and Keenya Barnes"),
        ("secretary_of_state", None, "O", "Nebraska Working People", "Paul Rumbaugh"),
    }
    # The federal row gets its whole printed party too.
    marvin = next(r for r in records if r["office"] == "S")
    assert marvin["party_label"] == "Legal Marijuana NOW"


# ── North Dakota: the district is its own column ──────────────────────

def _nd(contest, district, first, last, party, middle=""):
    return {"Contest": contest, "District": district, "First Name": first, "Middle Name": middle,
            "Last Name": last, "Party": party}


def test_north_dakota_reads_both_public_service_seats_and_its_districts():
    rows = [
        _nd("Representative in Congress", "", "Helene", "Neville", "independent nomination"),
        _nd("Public Service Commissioner", "", "Sheri", "Haugen-Hoffart", "Republican"),
        _nd("Public Service Commissioner", "", "Jill", "Kringstad", "Republican"),
        _nd("Public Service Commissioner", "", "Scot", "Kelsh", "Democratic-NPL"),
        _nd("Public Service Commissioner", "", "John", "Pederson", "Democratic-NPL", "M"),
        _nd("Superintendent of Public Instruction", "", "Levi", "Bachmeier", "Nonpartisan"),
        _nd("Superintendent of Public Instruction", "", "Tracy", "Foss", "Nonpartisan", "Layne"),
        _nd("State Senator", "District 03", "Bob", "Paulson", "Republican"),
        _nd("State Representative", "District 03", "Tara", "Hiatt", "Democratic-NPL"),
    ]
    records = parse_certified_rows(rows, _general("ND")["format"], state_offices=True)
    assert _state(records) == {
        # Two seats this year: both of each party's names are on the ballot.
        ("public_service_commission", None, "R", None, "Sheri Haugen-Hoffart"),
        ("public_service_commission", None, "R", None, "Jill Kringstad"),
        ("public_service_commission", None, "D", None, "Scot Kelsh"),
        ("public_service_commission", None, "D", None, "John M Pederson"),
        ("upper", "3", "R", None, "Bob Paulson"),
        ("lower", "3", "D", None, "Tara Hiatt"),
        # Elected on the no-party ballot: non-partisan, never "IND".
        ("school_superintendent", None, "N", "Nonpartisan", "Levi Bachmeier"),
        ("school_superintendent", None, "N", "Nonpartisan", "Tracy Layne Foss"),
    }
    # The at-large House seat still reads with an empty district column,
    # and a federal row keeps its old reading (the matcher is unchanged).
    assert [(r["office"], r["district"], r["party"]) for r in records if r["office"] == "H"] == [("H", None, "I")]


# ── Alaska: a paged list and joint tickets ────────────────────────────

def _ak_page(heading, names, next_page=None):
    rows = "".join(f"<tr><td>{n}</td><td></td></tr>" for n in names)
    more = f'<a href="{next_page}" class="next">Next Page&#xBB;</a>' if next_page else ""
    return (
        "<html><body><h1>Candidates</h1><h2>2026 General Election</h2><h3>General Information</h3>"
        f"<h4>{heading}</h4><table><thead><tr><th>Candidate Name on Ballot</th><th>Campaign Address</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>{more}</body></html>"
    )


AK_INDEX = '<a href="https://www.elections.alaska.gov/candidates/?election=26genr">2026 General</a>'
AK_PAGE_1 = _ak_page("GOVERNOR / LIEUTENANT GOVERNOR", [
    "Bronson, Dave / Church, Josh (Registered Republican) (Certified)",
    "Kreiss-Tomkins, Jonathan S. “JKT” / Johnson, Zac (Registered Democrat / Nonpartisan) (Certified)",
    "Certified Write-In Parkin, James W. “JP4” / Greer, Ramadhani “Ram” "
    "(Registered Republican / Registered Democrat) (Certified)",
]) + _ak_page("UNITED STATES REPRESENTATIVE", [
    "Begich, Nick (Registered Republican) (Certified) Incumbent",
], next_page="/candidates/?election=26genr&amp;frm-page-407=2")
AK_PAGE_2 = _ak_page("HOUSE DISTRICT 08", [
    "Elam, William H. “Bill” (Registered Republican) (Certified)",
    "Quinn, Frank (Nonpartisan) (Certified)",
])


@pytest.mark.asyncio
async def test_alaska_reads_every_page_and_each_ticket_in_reading_order():
    asked = []

    def handler(request):
        asked.append(str(request.url))
        if "frm-page-407=2" in str(request.url):
            return httpx.Response(200, text=AK_PAGE_2)
        if "election=26genr" in str(request.url):
            return httpx.Response(200, text=AK_PAGE_1)
        return httpx.Response(200, text=AK_INDEX)

    async with _client(handler) as client:
        records = await fetch_certified(client, 2026, "AK", _general("AK"))
    assert _state(records) == {
        ("governor", None, "R", None, "Dave Bronson / Josh Church"),
        # The ticket's party is the governor's registration, the first one.
        ("governor", None, "D", None, "Jonathan S. “JKT” Kreiss-Tomkins / Zac Johnson"),
        # Page 2: the House districts past the first page.
        ("lower", "8", "R", None, "William H. “Bill” Elam"),
        # Alaska prints "(Nonpartisan)" beside him: shown as printed.
        ("lower", "8", "N", "Nonpartisan", "Frank Quinn"),
    }
    assert any("frm-page-407=2" in url for url in asked)


@pytest.mark.asyncio
async def test_alaska_a_page_that_fails_fails_the_whole_list():
    def handler(request):
        if "frm-page-407=2" in str(request.url):
            return httpx.Response(404)
        if "election=26genr" in str(request.url):
            return httpx.Response(200, text=AK_PAGE_1)
        return httpx.Response(200, text=AK_INDEX)

    async with _client(handler) as client:
        # Half a list would publish the unread House districts as absent.
        assert await fetch_certified(client, 2026, "AK", _general("AK")) is None


# ── Illinois: two more office groups of the same PDF ──────────────────

def _w(x, text, top):
    return {"x0": x, "x1": x + 8 * len(text), "top": top, "bottom": top + 8, "text": text}


def _line(top, *cells):
    return [_w(x, t, top) for x, t in cells]


IL_EXEC = [
    *_line(40, (230, "GOVERNOR"), (300, "AND"), (330, "LIEUTENANT"), (420, "GOVERNOR")),
    *_line(55, (18, "DEMOCRATIC"), (155, "JB"), (175, "Pritzker"), (448, "10/27/2025")),
    # The running mate's line: nothing to the left, no office named.
    *_line(62, (155, "Christian"), (230, "Mitchell"), (300, "(Pritzker)"), (448, "10/27/2025")),
    *_line(70, (18, "GREEN"), (155, "Griselda"), (225, "Romero"), (448, "5/26/2026")),
    *_line(78, (155, "5550"), (190, "Abbey"), (240, "Dr"), (300, "REMOVED"), (360, "7/14/2026")),
    *_line(90, (18, "INDEPENDENT"), (155, "Collin"), (210, "Corbett"), (448, "5/26/2026")),
    # Not in the real list: an address the statewide gate would read as
    # an office, at the name column where every address line starts.
    *_line(100, (161, "1"), (175, "Attorney"), (240, "General"), (300, "Way")),
    *_line(115, (18, "LIBERTARIAN"), (155, "Pat"), (185, "Doe"), (448, "5/26/2026")),
    *_line(130, (240, "TREASURER")),
    *_line(145, (18, "REPUBLICAN"), (155, "Max"), (185, "Solomon"), (448, "4/23/2026")),
]
IL_LEG = [
    *_line(40, (240, "42ND"), (280, "SENATE")),
    *_line(55, (18, "DEMOCRATIC"), (155, "Linda"), (200, "Holmes"), (448, "10/27/2025")),
    *_line(62, (155, "WITHDRAWN"), (240, "6/22/2026"), (320, "8:43AM")),
    *_line(70, (18, "DEMOCRATIC"), (155, "Jared"), (200, "Ploger"), (448, "7/6/2026")),
    *_line(90, (240, "118TH"), (285, "REPRESENTATIVE")),
    *_line(105, (18, "WORKING"), (80, "CLASS"), (120, "PARTY"), (155, "Jordan"), (205, "Villas"),
           (448, "5/26/2026")),
]


def test_illinois_reads_its_state_offices_and_drops_the_struck():
    fmt = _general("IL")["format"]
    assert _state(parse_grouped_list([IL_EXEC], fmt, state_offices=True)) == {
        ("governor", None, "D", None, "JB Pritzker"),
        ("governor", None, "I", None, "Collin Corbett"),
        # Still the governor's race: an address never moves the office.
        ("governor", None, "L", None, "Pat Doe"),
        # Slated after nobody ran in the Republican primary.
        ("treasurer", None, "R", None, "Max Solomon"),
    }
    assert _state(parse_grouped_list([IL_LEG], fmt, state_offices=True)) == {
        # The withdrawn incumbent's replacement, not the incumbent.
        ("upper", "42", "D", None, "Jared Ploger"),
        ("lower", "118", "O", "WORKING CLASS PARTY", "Jordan Villas"),
    }
    # Without the opt-in a state heading is not an office at all.
    assert parse_grouped_list([IL_EXEC], fmt) == []


@pytest.mark.asyncio
async def test_illinois_opt_in_without_its_state_groups_says_nothing():
    source = {**_general("IL"), "discovery": {
        k: v for k, v in _general("IL")["discovery"].items() if k != "state_url_templates"
    }}
    async with _client(lambda r: httpx.Response(200, text="")) as client:
        assert await fetch_grouped(client, 2026, "IL", source) is None


# ── Florida: the Governor and Cabinet and Senate and House groups ─────

FL_CAB = """<html><body>
<b>Governor</b>
<table class="results"><tr><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>Donalds, Byron (REP) / Avila, Bryan</td><td>Qualified</td><td>Won</td><td></td></tr>
<tr><td>Jewett, Scott Eckhard (LPF) / Skelly, Nicole</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
<tr><td>Russo, Frank J. (NPA) / Rodriguez, Rachel</td><td>Qualified</td><td></td><td></td></tr>
<tr><td>Anderson, Kathy (WRI) / Nokovich, Daniel</td><td>Qualified</td><td></td><td></td></tr>
<tr><td>Collins, Jay (REP)</td><td>Defeated</td><td>Eliminated</td><td></td></tr>
</table>
<b>Attorney General</b>
<table class="results"><tr><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>Rodriguez, Jose Javier (DEM)</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
<tr><td>Uthmeier, James (REP) *Incumbent</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
</table></body></html>"""
FL_LEG = """<html><body>
<b>State Senator</b>
<table class="results"><tr><th>District</th><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>2</td><td>Donahoo, Lauren (DEM)</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
<tr><td></td><td>Trumbull, Jay (REP) *Incumbent</td><td>Qualified</td><td>Unopposed</td><td></td></tr>
<tr><td>4</td><td>Smith, Pat (REP)</td><td>Unopposed</td><td>Unopposed</td><td>Unopposed</td></tr>
</table></body></html>"""
FL_S01 = """<html><body>
<b>State Senator</b>
<table class="results"><tr><th>District</th><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>21</td><td>Hensley, Jordan (DEM)</td><td>Qualified</td><td></td><td></td></tr>
<tr><td></td><td>Nocco, Chris (REP)</td><td>Qualified</td><td></td><td></td></tr>
</table></body></html>"""
FL_FED = """<html><body><b>United States Senator</b>
<table class="results"><tr><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>Moody, Ashley (REP) *Incumbent</td><td>Qualified</td><td>Won</td><td></td></tr>
</table></body></html>"""
FL_INDEX = '<select name="elecid"><option value="20261103-GEN">2026 Election</option></select>' \
    '<select name="elecid"><option value="20261103-S01">2026 Special: Senate 21</option>' \
    '<option value="20260324-S03">2026 Special: House 51</option></select>'


def test_florida_reads_only_qualified_state_candidates():
    assert _state(parse_canlist(FL_CAB, state_offices=True, federal=False)) == {
        ("governor", None, "R", None, "Byron Donalds"),
        # LPF is the Libertarian Party of Florida's own code.
        ("governor", None, "L", None, "Scott Eckhard Jewett"),
        ("governor", None, "I", None, "Frank J. Russo"),
        # Unopposed for their nominations, so absent from primary results.
        ("attorney_general", None, "D", None, "Jose Javier Rodriguez"),
        ("attorney_general", None, "R", None, "James Uthmeier"),
    }
    # "Unopposed" as a STATUS was elected at qualifying: not on the ballot.
    assert _state(parse_canlist(FL_LEG, state_offices=True, federal=False)) == {
        ("upper", "2", "D", None, "Lauren Donahoo"),
        ("upper", "2", "R", None, "Jay Trumbull"),
    }


@pytest.mark.asyncio
async def test_florida_reads_the_same_day_special_too():
    posted = []

    from app.pipeline.fetch import state_candidates_dos_canlist as canlist
    canlist._legend_cache.clear()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=FL_PARTIES if "dos.fl.gov" in str(request.url) else FL_INDEX)
        form = dict(httpx.QueryParams(request.content.decode()))
        posted.append((form["elecid"], form["OfficeGroup"]))
        page = {("20261103-GEN", "FED"): FL_FED, ("20261103-GEN", "CAB"): FL_CAB,
                ("20261103-GEN", "LEG"): FL_LEG, ("20261103-S01", "ALL"): FL_S01}[posted[-1]]
        return httpx.Response(200, text=page)

    async with _client(handler) as client:
        records = await fetch_canlist(client, 2026, "FL", _SOURCES["FL"]["general_list"])
    assert posted == [("20261103-GEN", "FED"), ("20261103-GEN", "CAB"), ("20261103-GEN", "LEG"),
                      ("20261103-S01", "ALL")]
    upper = {(r["district"], r["last_name"]) for r in records if r["office"] == "upper"}
    assert ("21", "Chris Nocco") in upper and ("2", "Jay Trumbull") in upper
    # A March special is another ballot, never read.
    assert ("20260324-S03", "ALL") not in posted


# ── Illinois's primary results: one party, one contest ────────────────

def _il_row(candidate, party, votes, juris="MASON"):
    return {"JurisName": juris, "CandidateName": candidate, "ContestName": "SECRETARY OF STATE",
            "PartyName": party, "VoteCount": str(votes)}


def test_one_party_spelled_four_ways_is_one_primary():
    """Illinois's 2026 Secretary of State file spells the Republican party
    four ways across its jurisdictions. Tallied as four contests, the last
    one written ("Republican Party": Adamczyk 20,266 to 17,876) named the
    candidate who lost the primary; statewide, Diane M. Harris won 279,727
    to 248,198. These are those four real sub-totals."""
    rows = [
        _il_row("DIANE M. HARRIS", "REPUBLICAN", 157916), _il_row("WALTER ADAMCZYK", "REPUBLICAN", 116943),
        _il_row("WALTER ADAMCZYK", "Republican", 96997), _il_row("DIANE M. HARRIS", "Republican", 79826),
        _il_row("DIANE M. HARRIS", "REPUBLICAN PARTY", 24109), _il_row("WALTER ADAMCZYK", "REPUBLICAN PARTY", 13992),
        _il_row("DIANE M. HARRIS", "Republican Party", 17876), _il_row("WALTER ADAMCZYK", "Republican Party", 20266),
        _il_row("ALEXI GIANNOULIAS", "DEMOCRATIC", 227282), _il_row("ALEXI GIANNOULIAS", "Democratic Party", 58648),
    ]
    fmt = _SOURCES["IL"]["format"]
    tally = tabular._tally(rows, fmt)
    assert {k: dict(v["votes"]) for k, v in tally.items()} == {
        "SECRETARY OF STATE REPUBLICAN": {"DIANE M. HARRIS": 279727, "WALTER ADAMCZYK": 248198},
        "SECRETARY OF STATE DEMOCRATIC": {"ALEXI GIANNOULIAS": 285930},
    }
    by_seat: dict = {}
    tabular._collect(rows, fmt, by_seat, None, 1, state_offices=True)
    assert {r["party"]: r["last_name"] for recs in by_seat.values() for r in recs} == {
        "R": "DIANE M. HARRIS", "D": "ALEXI GIANNOULIAS",
    }


def test_a_write_in_bucket_is_never_stored_as_a_nominee(db_session):
    """Nobody ran in Illinois's 2026 Republican primary for Treasurer, so
    the results' "Write-in" row won it."""
    sc._sync_statewide_nominees(db_session, 2026, "IL", {"statewide_offices": True}, [
        {"office": "treasurer", "district": None, "party": "R", "last_name": "Write-in"},
        {"office": "treasurer", "district": None, "party": "D", "last_name": "MICHAEL W. FRERICHS"},
    ])
    assert [r.display_name for r in db_session.query(StatewideNominee)] == ["MICHAEL W. FRERICHS"]


# ── Delaware: a party printed only as an abbreviation ────────────────

def test_delaware_spells_out_the_independent_party_of_delaware():
    """The real 2026 Auditor rows. "Ind Pty of DE" is the Independent
    Party of Delaware (FEC IDE): a party, shown by its name -- neither a
    bare abbreviation nor an independent."""
    def row(office, last, ballot, party):
        return {"Office": office, "Party": party, "Last Name": last, "BallotName": ballot,
                "DisplayedStatus": "Qualified"}
    rows = [
        row("Auditor of Accounts", "York", "Lydia York", "Democratic"),
        row("Auditor of Accounts", "Cassidy", "Austin Cassidy", "Ind Pty of DE"),
    ]
    assert _state(parse_certified_rows(rows, _general("DE")["format"], state_offices=True)) == {
        ("auditor", None, "D", None, "Lydia York"),
        ("auditor", None, "O", "Independent Party of Delaware", "Austin Cassidy"),
    }


# ── Florida: codes read through the Division's own party legend ──────

# Trimmed from dos.fl.gov/elections/candidates-committees/political-parties/
# as served 2026-09-28 (one entry keeps its code inside a <span>).
FL_PARTIES = """<h2>Major Political Parties</h2><ul>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=1539">Florida Democratic Party</a> (DEM)</li>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=4700">Republican Party of Florida</a> (REP)</li>
</ul><h2>Minor Political Parties</h2><ul>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=88571" data-anchor="?account=88571">American Solidarity Party of Florida</a> (ASP)</li>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=69767">Independent Party of Florida</a> (IND)</li>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=3402">Libertarian Party of Florida</a> (LPF)</li>
<li><a href="https://dos.elections.myflorida.com/committees/ComDetail.asp?account=89139" data-anchor="?account=89139">MGTOW Party</a><span> (MGT)</span></li>
</ul>"""
# The real ASP and MGT rows of the 2026 LEG report; the IND row is the
# report's own shape with a stand-in name (no IND candidate qualified for
# a state office in 2026).
FL_MINOR = """<html><body>
<b>State Senator</b>
<table class="results"><tr><th>District</th><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>6</td><td>Thornton, Joseph (ASP)</td><td>Qualified</td><td></td><td></td></tr>
<tr><td></td><td>Doe, Pat (IND)</td><td>Qualified</td><td></td><td></td></tr>
</table>
<b>State Representative</b>
<table class="results"><tr><th>District</th><th>Candidate</th><th>Status</th><th>Primary</th><th>General</th></tr>
<tr><td>85</td><td>Metwally, Amr (MGT)</td><td>Qualified</td><td></td><td></td></tr>
<tr><td>94</td><td>Barrow, Doug (NPA)</td><td>Qualified</td><td></td><td></td></tr>
</table></body></html>"""


def test_florida_party_codes_render_as_the_divisions_own_names():
    from app.pipeline.fetch.state_candidates_dos_canlist import parse_party_legend
    legend = parse_party_legend(FL_PARTIES)
    assert legend["MGT"] == "MGTOW Party"
    assert _state(parse_canlist(FL_MINOR, state_offices=True, federal=False, legend=legend)) == {
        ("upper", "6", "O", "American Solidarity Party of Florida", "Joseph Thornton"),
        # A party named "Independent" is still a party.
        ("upper", "6", "O", "Independent Party of Florida", "Pat Doe"),
        ("lower", "85", "O", "MGTOW Party", "Amr Metwally"),
        # NPA is not on the legend: "No Party Affiliation", an independent.
        ("lower", "94", "I", None, "Doug Barrow"),
    }


@pytest.mark.asyncio
async def test_florida_without_its_legend_reads_no_state_office():
    from app.pipeline.fetch import state_candidates_dos_canlist as canlist
    canlist._legend_cache.clear()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404) if "dos.fl.gov" in str(request.url) else httpx.Response(200, text=FL_INDEX)
        return httpx.Response(200, text=FL_FED)

    async with _client(handler) as client:
        records = await fetch_canlist(client, 2026, "FL", _SOURCES["FL"]["general_list"])
    # The federal list stands; the state offices wait for the names (the
    # sync records nothing about them from a federal-only answer).
    assert {r["office"] for r in records} == {"S"}
