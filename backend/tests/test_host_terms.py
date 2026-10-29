"""Hosts whose terms of use forbid automated access (app/data/host_terms.json):
never asked by any path, said so on the source map and /about/crawler, and
their terms re-read every week from the Internet Archive's copy."""

import urllib.request

import httpx
import pytest
import requests

from app import crawl_policy, crawler, host_terms, host_terms_check, script_gate, source_health, source_reads
from app.pipeline.fetch import ballot_lookup, state_source_crawler
from app.pipeline.fetch.web_archive import Capture

QUOTE = host_terms.all_hosts()["michigan.gov"]["quote"]


def test_a_host_and_every_name_under_it_match_and_nothing_else_does():
    assert host_terms.forbid("www.michigan.gov")["publisher"] == "State of Michigan"
    assert host_terms.forbid("michigan.gov") and host_terms.forbid("results.michigan.gov")
    assert host_terms.forbid("mvic.sos.state.mi.us") and host_terms.forbid("mi-boe.entellitrak.com")
    # The Legislature's site allows automated reads at one a second.
    assert host_terms.forbid("www.legislature.mi.gov") is None
    assert host_terms.forbid("notmichigan.gov") is None and host_terms.forbid("entellitrak.com") is None


def test_every_entry_quotes_its_terms_and_says_when_they_were_read():
    for host, entry in host_terms.all_hosts().items():
        assert entry["quote"] and entry["terms_url"].startswith("https://") and entry["read_on"], host
        assert entry["text_sha256"] and entry["reviewed_capture"].startswith("https://web.archive.org/web/"), host


def test_the_gate_refuses_before_anything_is_sent():
    with pytest.raises(crawl_policy.TermsForbid) as refused:
        crawl_policy._check("www.michigan.gov")
    assert isinstance(refused.value, crawl_policy.HostRefusing) and "asked Civitas not to" in str(refused.value)


@pytest.fixture()
def gated(monkeypatch):
    """script_gate installed for the test only, with the crawl policy on."""
    monkeypatch.setattr(crawl_policy, "ENABLED", True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", httpx.HTTPTransport.handle_request)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", requests.adapters.HTTPAdapter.send)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", urllib.request.OpenerDirector.open)
    script_gate.install()


def test_a_refresh_script_s_requests_keep_the_terms_whatever_library_sends_them(gated):
    # Nothing reaches the network: the fast suite resolves no host but
    # loopback, so a request that got past the gate would fail differently.
    with pytest.raises(crawl_policy.TermsForbid):
        httpx.get("https://mvic.sos.state.mi.us/RegisterVoter")
    with pytest.raises(crawl_policy.TermsForbid):
        requests.get("https://www.michigan.gov/sos")
    with pytest.raises(crawl_policy.TermsForbid):
        urllib.request.urlopen("https://mi-boe.entellitrak.com/report")


def test_the_data_refresh_runs_its_scripts_through_the_gate(monkeypatch):
    from app import data_refresh
    ran = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(data_refresh.subprocess, "run", lambda argv, **kw: ran.setdefault("argv", argv) and Done())
    data_refresh.run_command([data_refresh.PY, "/x/scripts/build_election_calendar.py", "--out", "o"], 60)
    assert ran["argv"][3:7] == [data_refresh.PY, "-m", "app.script_gate", "/x/scripts/build_election_calendar.py"]


@pytest.mark.asyncio
async def test_a_link_on_a_host_that_refuses_checks_is_shown_unconfirmed_never_hidden():
    class Client:
        async def get(self, url, **kw):
            crawl_policy._check(httpx.URL(url).host)

    assert await ballot_lookup._check_once(Client(), "https://mvic.sos.state.mi.us/") == ballot_lookup.BLOCKED


def test_the_weekly_crawl_leaves_out_a_refused_state_s_hosts(monkeypatch):
    assert state_source_crawler.election_domains("MI") == []
    assert state_source_crawler._hosts_for("MI") == []
    assert state_source_crawler.election_domains("MN") == ["sos.mn.gov"]
    # An entry naming exact hosts leaves the rest of the domain to be probed.
    monkeypatch.setattr(host_terms, "all_hosts", lambda: {"sos.mn.gov": {"subdomains": False}})
    probed = set(state_source_crawler._hosts_for("MN"))
    assert probed.isdisjoint({"sos.mn.gov"}) and probed >= {"results.sos.mn.gov"}


def test_the_crawler_page_lists_them():
    listed = {t["host"]: t for t in crawler.summary()["termsHosts"]}
    assert set(listed) == set(host_terms.all_hosts())
    assert listed["michigan.gov"]["quote"] == QUOTE


def test_the_source_map_says_terms_not_failure(db_session, monkeypatch):
    monkeypatch.setattr(crawler, "hosts", lambda: [{"host": "mi-boe.entellitrak.com", "readers": [
        {"module": "app.pipeline.fetch.senate_ptr", "rps": None, "registry": None}]}])
    monkeypatch.setattr(crawl_policy, "refusals", lambda: {})
    source_reads.answered("mi-boe.entellitrak.com", 200, False)  # an old good read
    body = source_health.build(db_session)
    entry = body["sources"][0]
    assert entry["state"] == "terms" and entry["stateLabel"] == "Not read: asked us not to"
    assert entry["line"].startswith("Not read automatically since Oct 28, 2026: its owner asked Civitas not to")
    assert "not read automatically because its owner asked us not to" in body["summary"]


# ---- the weekly check of the terms themselves ----------------------------

PAGE = ("<html><body><main><h1>Terms of Use Policy</h1><p>By visiting you agree. " + QUOTE
        + " and shall comply.</p><p>You agree not to resell.</p></main><footer>menu</footer></body></html>")


def test_the_check_compares_the_terms_around_the_quote():
    entry = {"quote": QUOTE}
    text = host_terms_check.page_text(PAGE.encode())
    entry["text_sha256"] = host_terms_check.text_hash(host_terms_check.excerpt(entry, text))
    assert host_terms_check.verdict(entry, text) == "same"
    # A banner or menu outside <main> changes nothing.
    assert host_terms_check.verdict(entry, host_terms_check.page_text(PAGE.replace("menu", "new menu").encode())) == "same"
    # A clause beside the quote does.
    assert host_terms_check.verdict(entry, text.replace("resell", "resell or copy")) == "changed"
    assert host_terms_check.verdict(entry, text.replace("web crawlers", "robots")) == "sentence gone"


@pytest.mark.asyncio
async def test_a_changed_page_alerts_and_the_host_stays_unasked(db_session, monkeypatch):
    sent, resolved = [], []
    monkeypatch.setattr(host_terms_check, "send_ops_alert", lambda subject, body, **kw: sent.append((subject, kw)))
    monkeypatch.setattr(host_terms_check, "resolve_ops_alert", lambda condition: resolved.append(condition))
    capture = Capture("20261021111035", "https://www.michigan.gov/som/footer/policies", "X")

    async def newest(client, url):
        return capture

    page = {"body": PAGE.encode()}

    async def read_page(client, cap):
        return page["body"]

    monkeypatch.setattr(host_terms_check.web_archive, "newest_capture", newest)
    monkeypatch.setattr(host_terms_check.web_archive, "read_page", read_page)
    entry = {"quote": QUOTE, "terms_url": capture.original}
    text = host_terms_check.page_text(PAGE.encode())
    entry["text_sha256"] = host_terms_check.text_hash(host_terms_check.excerpt(entry, text))

    record = await host_terms_check._check(None, db_session, "michigan.gov", entry)
    assert record["outcome"] == "same" and record["confirmedOn"] == "2026-10-21"
    assert resolved == ["host-terms-michigan.gov"] and not sent

    page["body"] = PAGE.replace("web crawlers", "robots").encode()
    record = await host_terms_check._check(None, db_session, "michigan.gov", entry)
    assert record["outcome"] == "sentence gone" and record["confirmedOn"] == "2026-10-21"
    assert sent[0][0] == "Terms of use for michigan.gov changed"
    assert sent[0][1]["condition"] == "host-terms-michigan.gov"
    assert host_terms.forbid("michigan.gov")  # unasked until the entry is reviewed



def test_only_a_site_that_asked_us_to_stop_is_listed():
    """Notice and stop (owner policy, 2026-10-29): boilerplate terms alone
    don't list a site; Michigan, which refused Civitas directly, is."""
    assert set(host_terms.all_hosts()) == {"michigan.gov", "mvic.sos.state.mi.us", "mi-boe.entellitrak.com"}
    assert all(e["asked"] for e in host_terms.all_hosts().values())
    for host in ("elections.hawaii.gov", "www.coloradosos.gov", "www.sos.mn.gov", "sos.nebraska.gov", "sc.gov"):
        assert host_terms.forbid(host) is None, host


def test_an_exact_entry_covers_its_own_name_only(monkeypatch):
    monkeypatch.setattr(host_terms, "all_hosts", lambda: {"sos.example.gov": {"subdomains": False}})
    assert host_terms.forbid("sos.example.gov") and host_terms.forbid("results.sos.example.gov") is None


def _refuse(monkeypatch, state, host):
    """A measures registry entry naming a refused host (`terms`)."""
    from app.pipeline.fetch import ballot_measure_pdf_sources as registry
    entry = {**registry.source_for_state(state), "terms": {"host": host}}
    monkeypatch.setattr(registry, "source_for_state", lambda s, _real=registry.source_for_state: entry if s == state else _real(s))
    monkeypatch.setattr(host_terms, "all_hosts", lambda: {host: {"publisher": "Example", "terms_url": "https://example.gov/t"}})


@pytest.mark.asyncio
async def test_a_terms_blocked_state_s_reader_is_never_called_and_its_measures_stand(db_session, monkeypatch):
    _refuse(monkeypatch, "NE", "sos.nebraska.gov")
    from app.models import MeasureCoverage
    from app.pipeline import election_pipeline as ep

    async def never(*a, **kw):
        raise AssertionError("the reader was called")

    monkeypatch.setattr(ep, "_fetch_with_reasons", never)
    db_session.add(MeasureCoverage(state="NE", election_date="2026-11-03", status=MeasureCoverage.COVERED,
                                   measure_count=2, source_name="Nebraska Secretary of State"))
    db_session.commit()
    await ep._sync_pdf_measures(db_session, None, "2026-11-03", states=["NE"])
    row = db_session.query(MeasureCoverage).filter_by(state="NE").one()
    assert row.status == MeasureCoverage.COVERED and row.measure_count == 2
    assert "asked Civitas not to read it automatically" in row.error_detail
