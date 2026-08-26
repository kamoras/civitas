"""Clear cached FEC candidate-search results pointing at an id that
doesn't actually resolve on FEC — the specific fix in
fix/verify-crosswalk-fec-ids only helps FUTURE lookups; a candidate
whose wrong id is already cached under their name/state/office key
keeps returning that cached (wrong) result forever, since find_candidate
checks the cache before ever reaching the crosswalk/verification logic.

2026-08-26 audit found three sitting members with exactly this problem
(Gillen/NY, Self/TX, Ivey/MD) — their crosswalk carried a stale/invalid
id alongside the real one, and the unverified first match happened to
be the invalid one. This targets those three specific known-bad ids
rather than sweeping every cached FEC match on the box: the full
election-candidate universe is thousands of entries against a rate-
limited API (1 req/4s), so a general self-healing sweep needs its own
batched/watermarked design (like election_pipeline.py's financial
refresh), not a one-shot interactive script.

Run from the repo:
    python3 backend/scripts/clear_stale_fec_cache_entries.py
"""

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ApiCache  # noqa: E402
from app.pipeline.fetch.fec import _candidate_exists  # noqa: E402

# The three ids the audit confirmed don't resolve on FEC.
KNOWN_STALE_IDS = ["H4NY04158", "H2TX03290", "H2MD04315"]


async def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(ApiCache)
            .filter(ApiCache.tier == "fec", ApiCache.cache_key.like("candidate-search-%"))
            .all()
        )
        stale_rows = []
        for row in rows:
            try:
                data = json.loads(row.data_json)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("candidate_id") in KNOWN_STALE_IDS:
                stale_rows.append(row)

        if not stale_rows:
            print(f"Checked {len(rows)} cached FEC candidate-search entries — none matched the known-stale ids.")
            return

        async with httpx.AsyncClient(timeout=30) as client:
            for row in stale_rows:
                data = json.loads(row.data_json)
                candidate_id = data["candidate_id"]
                # Re-confirm live rather than trusting the hardcoded list blindly —
                # a candidate id's resolvability is a fact about FEC, not this script.
                if await _candidate_exists(client, db, candidate_id):
                    print(f"Skipping {row.cache_key} -> {candidate_id}: resolves fine now, leaving it")
                    continue
                print(f"Removing {row.cache_key} -> {candidate_id} (confirmed does not resolve)")
                db.delete(row)

        db.commit()
        print(f"Checked {len(rows)} cached entries, cleared {len(stale_rows)} stale one(s).")
        print("Next financial-refresh pass will re-resolve these via the verified crosswalk lookup.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
