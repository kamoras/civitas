"""fetch_congress_bill_titles: the whole congress's bill titles, the rival
pool for LDA bill matching (analyze/lobbying_records.TitlePool)."""

import asyncio

from app.pipeline.fetch import congress


def test_pages_every_type_and_keys_by_site_bill_id(db_session, monkeypatch):
    calls = []

    async def _fake(client, url):
        calls.append(url)
        if "/hr?" in url and "offset=0" in url:
            return {"bills": [{"number": str(n), "title": f"Bill {n}"} for n in range(1, 251)]}
        if "/hr?" in url and "offset=250" in url:
            return {"bills": [{"number": "251", "title": "Last House bill"}]}
        if "/s?" in url:
            return {"bills": [{"number": "1040", "title": "Affordable Prescriptions for Patients Act"}]}
        return {"bills": []}

    monkeypatch.setattr(congress, "_fetch_with_retry", _fake)
    titles = asyncio.run(congress.fetch_congress_bill_titles(None, db_session, 119))

    assert titles["HR.251"] == "Last House bill"
    assert titles["S.1040"] == "Affordable Prescriptions for Patients Act"
    assert len(titles) == 252
    # Cached: a second call makes no requests.
    n = len(calls)
    asyncio.run(congress.fetch_congress_bill_titles(None, db_session, 119))
    assert len(calls) == n


def test_a_failed_listing_is_not_cached(db_session, monkeypatch):
    async def _fails_for_senate(client, url):
        return None if "/s?" in url else {"bills": []}

    monkeypatch.setattr(congress, "_fetch_with_retry", _fails_for_senate)
    # A partial pool is never returned: it could lack the sibling that wins.
    assert asyncio.run(congress.fetch_congress_bill_titles(None, db_session, 119)) is None

    async def _works(client, url):
        return {"bills": [{"number": "1", "title": "x"}]} if "/s?" in url else {"bills": []}

    monkeypatch.setattr(congress, "_fetch_with_retry", _works)
    assert asyncio.run(congress.fetch_congress_bill_titles(None, db_session, 119)) == {"S.1": "x"}


def test_a_listing_short_of_its_own_count_is_retried_then_refused(db_session, monkeypatch):
    calls = []

    async def _short(client, url):
        calls.append(url)
        if "/hr?" in url:
            # A bill moved pages mid-crawl: 2 listed, the listing says 3.
            return {"bills": [{"number": "1", "title": "a"}, {"number": "2", "title": "b"}], "pagination": {"count": 3}}
        return {"bills": [], "pagination": {"count": 0}}

    monkeypatch.setattr(congress, "_fetch_with_retry", _short)
    assert asyncio.run(congress.fetch_congress_bill_titles(None, db_session, 119)) is None
    assert sum("/hr?" in u for u in calls) == 2
