"""Visitor hashes can't be turned back into IPs once their day is over.

The hash used to be HMAC(ip, date) under a key derived from ADMIN_TOKEN
(the literal "civitas" when unset). That key never changed and the IPv4
space is small enough to enumerate, so anyone holding the key could recover
every stored visitor's IP. Now each UTC day has a random salt, shared by
both API workers through the visits database and deleted when the day ends.
"""

import asyncio
import hashlib
import hmac
from unittest.mock import patch

from sqlalchemy import create_engine, text

from app import database
from app.api import visits
from app.api.visits import _daily_salt, _load_or_create_salt, _visitor_hash, track_visit
from app.models import SiteVisit, VisitSalt, VisitsMigration

from tests.test_visits import _drain_queue_and_write, _make_request


def _use(db):
    return patch.object(visits, "VisitsSessionLocal", lambda: _NoClose(db))


class _NoClose:
    """Hands the shared test session to code that closes its own session."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


class TestDailySalt:
    def test_same_day_same_salt_so_both_workers_agree(self, db_session):
        with _use(db_session):
            a = _load_or_create_salt("2026-09-24")
            b = _load_or_create_salt("2026-09-24")
        assert a == b and len(a) == 32

    def test_a_new_day_deletes_every_other_days_salt(self, db_session):
        with _use(db_session):
            first = _load_or_create_salt("2026-09-24")
            second = _load_or_create_salt("2026-09-25")
        assert first != second
        assert [r.date for r in db_session.query(VisitSalt).all()] == ["2026-09-25"]

    def test_unavailable_store_degrades_without_caching(self, monkeypatch):
        # One fallback per process per day, never cached as the shared
        # salt (retried next call). A fresh salt per call made every visit
        # during the outage a new unique visitor.
        monkeypatch.setattr(visits, "_salt_cache", None)
        monkeypatch.setattr(visits, "_fallback_salt", None)

        def boom(date):
            raise RuntimeError("db down")

        monkeypatch.setattr(visits, "_load_or_create_salt", boom)
        a = asyncio.run(_daily_salt("2026-09-24"))
        b = asyncio.run(_daily_salt("2026-09-24"))
        assert a == b and visits._salt_cache is None
        assert asyncio.run(_daily_salt("2026-09-25")) != a


class TestHash:
    def test_same_ip_same_day_dedupes(self, db_session, monkeypatch):
        monkeypatch.setattr(visits, "_salt_cache", None)
        with _use(db_session):
            asyncio.run(track_visit(_make_request(peer_ip="198.51.100.7"), path="/"))
            asyncio.run(track_visit(_make_request(peer_ip="198.51.100.7"), path="/about"))
            asyncio.run(track_visit(_make_request(peer_ip="198.51.100.8"), path="/"))
        _drain_queue_and_write(db_session)
        assert db_session.query(SiteVisit).count() == 2

    def test_old_admin_token_key_no_longer_reproduces_the_hash(self, db_session, monkeypatch):
        monkeypatch.setattr(visits, "_salt_cache", None)
        with _use(db_session):
            salt = _load_or_create_salt("2026-09-24")
        ip = "198.51.100.7"
        legacy_key = hashlib.sha256(b"civitas:visitor-salt").digest()
        legacy = hmac.new(legacy_key, f"{ip}:2026-09-24".encode(), hashlib.sha256).hexdigest()[:32]
        assert _visitor_hash(ip, salt) != legacy

    def test_hash_depends_on_the_salt(self):
        assert _visitor_hash("198.51.100.7", b"a" * 32) != _visitor_hash("198.51.100.7", b"b" * 32)


class TestLegacyRekey:
    def _setup(self, db_session, monkeypatch):
        engine = db_session.get_bind()
        monkeypatch.setattr(database, "visits_engine", engine)
        # A separate, empty main database: no pre-split legacy table.
        monkeypatch.setattr(database, "engine", create_engine("sqlite://"))
        db_session.add_all([
            SiteVisit(date="2026-09-01", visitor_hash="a" * 32),
            SiteVisit(date="2026-09-01", visitor_hash="b" * 32),
            SiteVisit(date="2026-09-02", visitor_hash="a" * 32),
        ])
        db_session.commit()

    def test_rows_are_rekeyed_once_and_stay_distinct(self, db_session, monkeypatch):
        self._setup(db_session, monkeypatch)
        database._rekey_legacy_visitor_hashes()
        db_session.expire_all()
        after = {(r.date, r.visitor_hash) for r in db_session.query(SiteVisit).all()}
        assert len(after) == 3
        assert not any(h in ("a" * 32, "b" * 32) for _, h in after)
        assert db_session.query(VisitsMigration).count() == 1

        database._rekey_legacy_visitor_hashes()  # second start: no-op
        db_session.expire_all()
        assert {(r.date, r.visitor_hash) for r in db_session.query(SiteVisit).all()} == after

    def test_per_day_unique_counts_are_unchanged(self, db_session, monkeypatch):
        self._setup(db_session, monkeypatch)
        before = db_session.execute(text("SELECT date, COUNT(*) FROM site_visits GROUP BY date")).all()
        database._rekey_legacy_visitor_hashes()
        after = db_session.execute(text("SELECT date, COUNT(*) FROM site_visits GROUP BY date")).all()
        assert before == after



def test_a_salt_from_a_day_that_ended_is_dropped(monkeypatch):
    # An idle worker must not hold yesterday's salt: it could recompute
    # every one of yesterday's hashes from an IP.
    monkeypatch.setattr(visits, "_salt_cache", ("2000-01-01", b"x" * 32))
    monkeypatch.setattr(visits, "_fallback_salt", ("2000-01-01", b"y" * 32, True))
    visits._forget_stale_salts()
    assert visits._salt_cache is None and visits._fallback_salt is None


def test_todays_salt_is_kept(monkeypatch):
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    monkeypatch.setattr(visits, "_salt_cache", (today, b"x" * 32))
    visits._forget_stale_salts()
    assert visits._salt_cache == (today, b"x" * 32)


def test_a_salt_outage_falls_back_to_one_salt_for_every_worker(monkeypatch, throttle_store):
    # Two workers are two processes; what they share is the RAM store.
    # Simulated by clearing this process's cached fallback between calls.
    def boom(date):
        raise RuntimeError("db down")

    monkeypatch.setattr(visits, "_salt_cache", None)
    monkeypatch.setattr(visits, "_load_or_create_salt", boom)
    today = __import__("datetime").datetime.now(__import__("datetime").UTC).date().isoformat()
    monkeypatch.setattr(visits, "_fallback_salt", None)
    worker_a = asyncio.run(_daily_salt(today))
    monkeypatch.setattr(visits, "_fallback_salt", None)
    worker_b = asyncio.run(_daily_salt(today))
    assert worker_a == worker_b


def test_a_worker_behind_midnight_keeps_the_new_days_visit_salt(db_session):
    with _use(db_session):
        tomorrow = visits._load_or_create_salt("2099-01-02")
        visits._load_or_create_salt("2099-01-01")  # a worker that read the clock just before midnight
        assert visits._load_or_create_salt("2099-01-02") == tomorrow


def test_a_private_fallback_gives_way_to_the_shared_one(monkeypatch):
    # Both stores down: this process's own salt. The RAM store back: the
    # shared one, not the private one for the rest of the day.
    from app.api import throttle

    def boom(date):
        raise RuntimeError("db down")

    monkeypatch.setattr(visits, "_salt_cache", None)
    monkeypatch.setattr(visits, "_fallback_salt", None)
    monkeypatch.setattr(visits, "_load_or_create_salt", boom)
    monkeypatch.setattr(throttle, "derived_salt", lambda purpose: None)
    private = asyncio.run(_daily_salt("2026-09-24"))
    assert asyncio.run(_daily_salt("2026-09-24")) == private  # stable meanwhile
    monkeypatch.setattr(throttle, "derived_salt", lambda purpose: b"s" * 32)
    assert asyncio.run(_daily_salt("2026-09-24")) == b"s" * 32
