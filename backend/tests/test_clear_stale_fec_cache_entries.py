"""Tests for scripts/clear_stale_fec_cache_entries.py — the one-time
cleanup for cached FEC candidate-search results that already point at
one of the three known-stale ids the 2026-08-26 audit confirmed."""

import json
from unittest.mock import patch

from app.models import ApiCache


class TestClearStaleFecCacheEntries:
    async def _fake_exists(self, client, db, candidate_id):
        return candidate_id not in ("H4NY04158", "H2TX03290", "H2MD04315")

    def test_a_known_stale_entry_is_removed(self, db_session):
        from scripts import clear_stale_fec_cache_entries as script

        db_session.add(ApiCache(
            tier="fec", cache_key="candidate-search-Laura_Gillen-NY-H",
            data_json=json.dumps({"candidate_id": "H4NY04158"}),
        ))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        with patch.object(script, "_candidate_exists", side_effect=self._fake_exists):
            import asyncio
            asyncio.run(script.main())

        assert db_session.query(ApiCache).filter(
            ApiCache.cache_key == "candidate-search-Laura_Gillen-NY-H",
        ).first() is None

    def test_an_unrelated_entry_is_left_alone(self, db_session):
        from scripts import clear_stale_fec_cache_entries as script

        db_session.add(ApiCache(
            tier="fec", cache_key="candidate-search-Real_Person-CA-H",
            data_json=json.dumps({"candidate_id": "H2CA12001"}),
        ))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        with patch.object(script, "_candidate_exists", side_effect=self._fake_exists):
            import asyncio
            asyncio.run(script.main())

        assert db_session.query(ApiCache).filter(
            ApiCache.cache_key == "candidate-search-Real_Person-CA-H",
        ).first() is not None

    def test_a_known_stale_id_that_now_resolves_is_left_alone(self, db_session):
        # Re-confirmed live rather than trusted blindly — if FEC's own
        # data changed since the audit, don't delete a now-valid entry.
        from scripts import clear_stale_fec_cache_entries as script

        db_session.add(ApiCache(
            tier="fec", cache_key="candidate-search-Laura_Gillen-NY-H",
            data_json=json.dumps({"candidate_id": "H4NY04158"}),
        ))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None

        async def always_exists(client, db, candidate_id):
            return True

        with patch.object(script, "_candidate_exists", side_effect=always_exists):
            import asyncio
            asyncio.run(script.main())

        assert db_session.query(ApiCache).filter(
            ApiCache.cache_key == "candidate-search-Laura_Gillen-NY-H",
        ).first() is not None
