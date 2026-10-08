"""sec_tickers: a security's industry from the SIC code the SEC assigned
its issuer, found by ticker or by exact company name."""

import httpx
import pytest

from app.pipeline.fetch import sec_tickers
from app.pipeline.fetch.sec_tickers import SecUnavailable, industry_for_sic, issuer_industries, issuer_key

# The issuers production mislabelled from their names (2026-09-29), with the
# SIC code the SEC assigns each.
SUBMISSIONS = {
    1730168: {"sic": "3674"},   # Broadcom: semiconductors
    1163165: {"sic": "1311"},   # ConocoPhillips: crude petroleum & natural gas
    1653482: {"sic": "7372"},   # GitLab: prepackaged software
    1057352: {"sic": "7389"},   # CoStar: business services, nec
    899051: {"sic": "6331"},    # Allstate: fire, marine & casualty insurance
}
TICKERS = {
    "0": {"cik_str": 1730168, "ticker": "AVGO", "title": "Broadcom Inc."},
    "1": {"cik_str": 1163165, "ticker": "COP", "title": "CONOCOPHILLIPS"},
    "2": {"cik_str": 1653482, "ticker": "GTLB", "title": "GitLab Inc."},
    "3": {"cik_str": 1057352, "ticker": "CSGP", "title": "COSTAR GROUP, INC."},
    "4": {"cik_str": 899051, "ticker": "ALL", "title": "ALLSTATE CORP"},
    "5": {"cik_str": 111, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC /DE/"},
    # Two issuers, one title: matched to neither.
    "6": {"cik_str": 222, "ticker": "AAA", "title": "Twin Co"},
    "7": {"cik_str": 333, "ticker": "BBB", "title": "TWIN CO"},
}


@pytest.mark.parametrize("sic, industry", [
    (3674, "TECH"),          # narrower than manufacturing's 2000-3999
    (2834, "PHARMA"),
    (1311, "OIL_GAS"),
    (6021, "FINANCE"),       # the embedding read "National Commercial Banks" as REAL_ESTATE
    (6798, "REAL_ESTATE"),   # a REIT, carved out of 6700-6799
    (3711, "MANUFACTURING"),
    (4953, None),            # refuse systems: no category of ours
    (8880, None),            # American depositary receipts: outside every range
    (None, None),
])
def test_industry_for_sic(sic, industry):
    assert industry_for_sic(sic) == industry


def test_issuer_key_drops_punctuation_the_state_tag_and_legal_forms():
    assert issuer_key("AMETEK INC/") == issuer_key("Ametek, Inc.") == "AMETEK"
    assert issuer_key("EXXON MOBIL CORP /NJ/") == "EXXON MOBIL"
    assert issuer_key("The Home Depot Inc.") == issuer_key("HOME DEPOT, INC.") == "HOME DEPOT"
    assert issuer_key("Deere & Company") == issuer_key("DEERE & CO") == "DEERE"
    assert issuer_key("AT&T Inc.") == "AT&T"


@pytest.fixture
def sec(monkeypatch):
    monkeypatch.setattr(sec_tickers, "_issuers_cache", None)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        cik = int(request.url.path.rsplit("CIK", 1)[1].removesuffix(".json"))
        return httpx.Response(200, json=SUBMISSIONS[cik]) if cik in SUBMISSIONS else httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


async def test_tickers_and_names_resolve_to_the_sec_industry(db_session, sec):
    client, _ = sec
    by_ticker, by_name = await issuer_industries(
        client, db_session, ["AVGO", "COP", "GTLB", "CSGP", "ZZZZ"],
        ["Allstate Corp", "Allstate Corporation", "Allstate Insurance"],
    )
    # CoStar's code has no category of ours; ZZZZ has no SEC record at all.
    assert by_ticker == {"AVGO": "TECH", "COP": "OIL_GAS", "GTLB": "TECH", "CSGP": None}
    # The same name however its legal form is spelled, never a near one.
    assert by_name == {"Allstate Corp": "INSURANCE", "Allstate Corporation": "INSURANCE"}


async def test_a_share_class_written_with_a_dot_is_found(db_session, sec):
    client, requests = sec
    by_ticker, _ = await issuer_industries(client, db_session, ["BRK.B"], [])
    assert "BRK.B" in by_ticker
    assert any("CIK0000000111" in r for r in requests)


async def test_a_title_two_issuers_share_matches_neither(db_session, sec):
    client, _ = sec
    _, by_name = await issuer_industries(client, db_session, [], ["Twin Co"])
    assert by_name == {}


async def test_sic_codes_are_cached_including_no_record(db_session, sec):
    client, requests = sec
    await issuer_industries(client, db_session, ["AVGO", "BRK.B"], [])
    by_ticker, _ = await issuer_industries(client, db_session, ["AVGO", "BRK.B"], [])
    assert by_ticker == {"AVGO": "TECH", "BRK.B": None}  # Berkshire: 404, no record
    assert sum("CIK0001730168" in r for r in requests) == 1
    assert sum("CIK0000000111" in r for r in requests) == 1


async def test_an_unreadable_sec_raises_rather_than_answering_nothing(db_session, monkeypatch):
    """An empty answer would read as "no SEC record" for every trade, and
    the nightly pass would clear every stored label on a night the SEC was
    down."""
    monkeypatch.setattr(sec_tickers, "_issuers_cache", None)
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(SecUnavailable):
        await issuer_industries(down, db_session, ["AVGO"], [])

    def tickers_only(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        return httpx.Response(503)

    monkeypatch.setattr(sec_tickers, "fetch_with_retry", _no_retry(sec_tickers.fetch_with_retry))
    with pytest.raises(SecUnavailable):
        await issuer_industries(httpx.AsyncClient(transport=httpx.MockTransport(tickers_only)), db_session, ["AVGO"], [])


def _no_retry(fetch):
    async def once(*args, **kwargs):
        return await fetch(*args, **{**kwargs, "retries": 1, "backoff_s": 0})
    return once
