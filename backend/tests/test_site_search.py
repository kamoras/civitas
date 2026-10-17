"""Site search (app/site_search.py, app/site_search_index.py, app/api/search.py):
identifier parsing, ranking and direct navigation on known items, the index
built from the database, the read path's lightness and its privacy."""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from app import site_search
from app.api import search as search_api
from app.main import DropQueryString
from app.models import ActionIssue, Agency, Race, Representative, Senator, SponsoredBill
from app.site_search import answer, identifier_keys, rank
from app.site_search_index import (
    Entity,
    collect_entities,
    district_code,
    normalize,
    refresh_index,
    stored_fingerprint,
    write_index,
)
from app.time_utils import utcnow

BACKEND = Path(__file__).resolve().parent.parent


# ── Identifiers ──────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["HR 1", "H.R.1", "hr1", "h.r. 1", "H.R. 1"])
def test_bill_numbers_in_every_spelling(q):
    assert "bill:hr.1" in identifier_keys(q)


@pytest.mark.parametrize("q,key", [
    ("S. 2398", "bill:s.2398"),
    ("hjres 12", "bill:hjres.12"),
    ("TX-23", "district:tx-23"),
    ("tx23", "district:tx-23"),
    ("tx 23", "district:tx-23"),
    ("Texas 23rd", "district:tx-23"),
    ("Texas 23", "district:tx-23"),
    ("texas district 23", "district:tx-23"),
    ("AK-AL", "district:ak-al"),
    ("PN123", "pn:pn123"),
    ("pn 123-4", "pn:pn123-4"),
    ("senate roll call 252", "rc:senate:252"),
    ("roll call 99", "rc:99"),
    ("2026-08172", "frdoc:2026-08172"),
    ("FR Doc. 2026-08172", "frdoc:2026-08172"),
    ("89 FR 52508", "fr:89-52508"),
    ("EO 14110", "eo:14110"),
    ("executive order 14110", "eo:14110"),
    ("Proclamation 10914", "proc:10914"),
    ("RIN 1615-AD22", "rin:1615-ad22"),
    ("texas", "state:tx"),
    ("TX", "state:tx"),
])
def test_identifier_keys(q, key):
    assert key in identifier_keys(q)


@pytest.mark.parametrize("q", ["warren", "election map", "", "   ", "hr", "XX-12", "zz 3"])
def test_words_are_not_identifiers(q):
    keys = identifier_keys(q)
    assert not any(k.startswith(("bill:", "district:", "pn:", "rc:")) for k in keys)


def test_district_codes():
    assert district_code("TX", 23) == "TX-23"
    assert district_code("TX", 5) == "TX-5"
    assert district_code("AK", 0) == "AK-AL"


# ── Ranking and direct navigation on known items ─────────────────

def _fixture_entities() -> list[Entity]:
    return [
        Entity("page", "Seat map", "/elections/map?chamber=senate&view=usual", "Elections", 0.8,
               ["election map", "map"]),
        Entity("page", "Calendar", "/elections/calendar", "Elections", 0.8, ["election calendar"]),
        Entity("page", "Archive", "/action?tab=timeline", "Action Center", 0.8, []),
        Entity("page", "Archive", "/elections/archive", "Elections", 0.8, []),
        Entity("member", "Elizabeth Warren", "/politicians/elizabeth-warren", "Senator · D-MA", 1.0),
        Entity("member", "Warren Davidson", "/politicians/warren-davidson", "Representative · R-OH-8", 1.0),
        Entity("member", "Alexandria Ocasio-Cortez", "/politicians/alexandria-ocasio-cortez",
               "Representative · D-NY-14", 1.0),
        Entity("member", "John Smith", "/politicians/john-smith", "Representative · R-TX-1", 1.0),
        Entity("member", "John Smith", "/politicians/john-smith-ny", "Former representative · D-NY-2", 0.4),
        Entity("district", "TX-23", "/politicians/tony-rep", "Texas · represented by Tony Rep (R)", 0.9,
               ["TX 23", "Texas 23rd"], ["district:TX-23"]),
        Entity("race", "TX-23 House race, 2026", "/elections/states/TX#race-2026-HOUSE-TX-23",
               "Texas · on the 2026 ballot", 0.75, ["TX-23 2026", "TX-23 race", "Texas 23rd 2026"],
               ["race:TX-23"]),
        Entity("bill", "H.R. 1", "/congress/bills/HR.1", "One Big Beautiful Bill Act", 0.9,
               ["One Big Beautiful Bill Act"], ["bill:HR.1"]),
        Entity("bill", "H.R. 1 (118th Congress)", "/congress/bills/HR.1?congress=118", "Lower Energy Costs Act",
               0.2, ["Lower Energy Costs Act"], ["bill:HR.1:118"]),
        Entity("agency", "Environmental Protection Agency", "/agencies/environmental-protection-agency",
               "Federal agency", 0.8, ["EPA"], ["abbr:epa"]),
        Entity("agency", "Energy Department", "/agencies/energy-department", "Federal agency", 0.8, ["DOE"]),
        Entity("state", "Texas", "/elections/states/TX", "Texas's ballot", 0.9, ["TX"], ["state:TX"]),
        Entity("document", "EO 14110: Safe AI", "/explore/7", "Executive Order · 2023-10-30", 0.5,
               keys=["eo:14110", "frdoc:2023-24283"], words=False),
    ]


@pytest.fixture
def index(tmp_path):
    path = str(tmp_path / "site_search.db")
    write_index(_fixture_entities(), path, "fp")
    return path


@pytest.mark.parametrize("q,url", [
    ("elizabeth warren", "/politicians/elizabeth-warren"),            # a member's full name
    ("EPA", "/agencies/environmental-protection-agency"),             # an agency by acronym
    ("HR 1", "/congress/bills/HR.1"),                                 # a bill by number: this Congress's
    ("h.r.1", "/congress/bills/HR.1"),
    ("TX-23", "/politicians/tony-rep"),                               # a district code: its member
    ("texas 23rd", "/politicians/tony-rep"),
    ("election map", "/elections/map?chamber=senate&view=usual"),     # a page by another name
    ("calendar", "/elections/calendar"),
    ("2023-24283", "/explore/7"),                                     # an FR document number
    ("EO 14110", "/explore/7"),
    ("texas", "/elections/states/TX"),
    ("TX-23 2026", "/elections/states/TX#race-2026-HOUSE-TX-23"),     # a district and a year: its race
    ("ocasio cortez", "/politicians/alexandria-ocasio-cortez"),
])
def test_known_items_go_straight_to_their_page(index, q, url):
    a = answer(q, index)
    assert a.direct is not None and a.direct.url == url


@pytest.mark.parametrize("q,url", [
    ("hr 1 votes", "/congress/bills/HR.1#votes"),
    ("H.R. 1 vote", "/congress/bills/HR.1#votes"),
    ("hr1 history", "/congress/bills/HR.1#history"),
    ("elizabeth warren donors", "/politicians/elizabeth-warren#funding-independence"),
    ("donors elizabeth warren", "/politicians/elizabeth-warren#funding-independence"),
    ("elizabeth warren holdings", "/politicians/elizabeth-warren#holdings"),
    ("TX-23 donors", "/politicians/tony-rep#funding-independence"),
])
def test_section_words_land_on_the_section(index, q, url):
    a = answer(q, index)
    assert a.direct is not None and a.direct.url == url


@pytest.mark.parametrize("q", [
    "warren",          # two members
    "john smith",      # two members of one name
    "archive",         # two pages of one name
    "warren donors",   # the section is clear, the member isn't
])
def test_ambiguous_queries_list_rather_than_jump(index, q):
    a = answer(q, index)
    assert a.direct is None
    assert len(a.ranked) >= 2


def test_a_bill_number_not_in_the_index_still_opens_its_page(index):
    # Every bill has a page (it reads Congress.gov); the number names it.
    a = answer("H.R. 7008", index)
    assert a.direct is not None and a.direct.url == "/congress/bills/HR.7008"


def test_surname_ranks_the_member_first(index):
    assert rank("davidson", index)[0].url == "/politicians/warren-davidson"


def test_a_typo_still_finds_the_name(index):
    assert rank("elizbeth warren", index)[0].url == "/politicians/elizabeth-warren"
    assert rank("enviromental protection", index)[0].url == "/agencies/environmental-protection-agency"


def test_prefix_finds_names_as_they_are_typed(index):
    assert "/politicians/alexandria-ocasio-cortez" in [h.url for h in rank("alexan", index)[:8]]
    assert rank("environ", index)[0].url == "/agencies/environmental-protection-agency"


def test_nothing_on_file_offers_the_nearest_names(index):
    out = site_search.results("calnedar", path=index)
    if not out["groups"]:
        assert out["near"], "an empty result names the closest spellings"
    out = site_search.results("qqqqzzzz", path=index)
    assert out["groups"] == [] and out["direct"] is None


def test_results_group_by_kind_with_counts(index):
    out = site_search.results("warren", path=index)
    groups = {g["kind"]: g for g in out["groups"]}
    assert groups["member"]["count"] == 2
    assert {i["url"] for i in groups["member"]["items"]} == {
        "/politicians/elizabeth-warren", "/politicians/warren-davidson"}
    assert out["explore"]["url"] == "/explore?q=warren"


def test_close_calls_are_offered(index):
    out = site_search.results("john smith", path=index)
    assert out["direct"] is None
    assert {a["url"] for a in out["alternatives"]} == {"/politicians/john-smith", "/politicians/john-smith-ny"}
    # A broad query is a list, not a close call.
    assert site_search.results("agency", path=index)["alternatives"] == []


def test_a_missing_index_says_so(tmp_path):
    with pytest.raises(site_search.SearchUnavailable):
        rank("warren", str(tmp_path / "absent.db"))


# ── The index, built from the database ───────────────────────────

def _seed(db):
    db.add_all([
        Senator(id="elizabeth-warren", name="Elizabeth Warren", state="MA", party="D", is_current=True,
                bioguide_id="W000817"),
        Representative(id="tony-rep", name="Tony Rep", state="TX", district=23, party="R", is_current=True),
        Representative(id="gone-rep", name="Gone Rep", state="OH", district=2, party="R", is_current=False),
        Agency(slug="environmental-protection-agency", fr_id=145, name="Environmental Protection Agency",
               short_name="EPA", synced_at=utcnow()),
        Race(id="2026-HOUSE-TX-23", cycle_year=2026, office="H", state="TX", district=23),
        ActionIssue(date="2026-10-01", rank=1, title="Senate passes stopgap spending bill", is_current=True),
        SponsoredBill(senator_id="elizabeth-warren", bill_id="S.1234", title="Stop Price Gouging Act",
                      congress=119),
    ])
    db.commit()


def test_index_built_from_the_database(db_session, tmp_path):
    _seed(db_session)
    path = str(tmp_path / "site_search.db")
    assert refresh_index(db_session, path) > 0
    assert answer("elizabeth warren", path).direct.url == "/politicians/elizabeth-warren"
    assert answer("EPA", path).direct.url == "/agencies/environmental-protection-agency"
    assert answer("TX-23", path).direct.url == "/politicians/tony-rep"
    assert answer("TX-23 2026", path).direct.url == "/elections/states/TX#race-2026-HOUSE-TX-23"
    assert answer("S 1234", path).direct.url == "/congress/bills/S.1234"
    assert answer("stopgap spending", path).ranked[0].kind == "issue"
    assert answer("election map", path).direct.url.startswith("/elections/map")
    former = [h for h in rank("gone rep", path) if h.url == "/politicians/gone-rep"][0]
    assert former.context.startswith("Former")


def test_index_rebuilds_only_when_its_sources_move(db_session, tmp_path):
    _seed(db_session)
    path = str(tmp_path / "site_search.db")
    assert refresh_index(db_session, path) is not None
    assert refresh_index(db_session, path) is None  # nothing moved
    db_session.add(Senator(id="new-sen", name="New Senator", state="VT", party="I", is_current=True))
    db_session.commit()
    assert refresh_index(db_session, path) is not None
    assert stored_fingerprint(path) is not None


def test_every_site_page_is_indexed(db_session):
    pages = json.loads((BACKEND / "app/data/site_pages.json").read_text())["pages"]
    indexed = {(e.label, e.url) for e in collect_entities(db_session) if e.kind == "page"}
    assert {(p["label"], p["href"]) for p in pages} <= indexed


def test_normalize_folds_accents_and_punctuation():
    assert normalize("Núñez, Jr.") == "nunez jr"
    assert normalize("H.R. 1") == "h r 1"
    assert normalize("AT&T") == "at and t"


# ── Read path: no model, no LLM ──────────────────────────────────

def test_read_path_loads_no_model():
    """Importing and running the search API never imports an embedding
    model or a model runtime (AGENTS.md, read path)."""
    code = (
        "import sys\n"
        "import app.api.search, app.site_search\n"
        "bad = [m for m in ('sentence_transformers', 'torch', 'transformers') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
                          env={"PYTHONPATH": str(BACKEND), "PATH": "/usr/bin:/bin"}, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


# ── API ──────────────────────────────────────────────────────────

def test_api_answers_privately_cached(monkeypatch, index, db_session):
    monkeypatch.setattr(site_search, "SITE_SEARCH_DB", index)
    res = search_api.suggest(q="EPA", db=db_session)
    body = json.loads(res.body)
    assert res.headers["cache-control"] == "private, max-age=300"
    assert body["direct"] == "/agencies/environmental-protection-agency"
    assert body["items"][0]["group"] == "Agencies"
    res = search_api.search(q="warren", db=db_session)
    assert json.loads(res.body)["groups"][0]["kind"] == "member"
    assert "private" in res.headers["cache-control"]


def test_api_before_the_index_exists(monkeypatch, tmp_path, db_session):
    monkeypatch.setattr(site_search, "SITE_SEARCH_DB", str(tmp_path / "absent.db"))
    res = search_api.suggest(q="warren", db=db_session)
    assert json.loads(res.body)["unavailable"] is True
    assert res.headers["cache-control"] == "no-store"


# ── Privacy: queries are never logged ────────────────────────────

def test_access_log_drops_query_strings():

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/search/suggest?q=elizabeth+warren", "1.1", 200), None)
    assert DropQueryString().filter(record)
    assert "warren" not in record.getMessage()
    assert "/api/search/suggest" in record.getMessage()


def test_calibration_file_is_in_range():
    data = json.loads(site_search.CALIBRATION_PATH.read_text())
    p = site_search.params()
    assert p.direct_floor == data["params"]["direct_floor"]
    assert data["held_out"]["all"]["wrong_direct_rate"] <= 2 * data["wrong_direct_target"]
