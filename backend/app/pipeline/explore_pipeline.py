"""Explore pipeline — fetches government activity from multiple sources and
indexes it for semantic search.

Sources:
  1. Senate floor proceedings (Congressional Record via GovInfo)
  2. House floor proceedings (Congressional Record via GovInfo)
  3. Presidential actions (Federal Register: EOs, memoranda, proclamations)
  4. Supreme Court opinions (Oyez API)
  5. Federal Register rulemaking (rules, proposed rules, notices)

Each document is stored in the explore_documents table and embedded in ChromaDB
for free-text semantic search on the Explore page.
"""

import hashlib
import logging
import time

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ExploreDocument, Justice, Representative, Senator
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congressional_record import fetch_floor_remarks
from app.pipeline.fetch.house_record import fetch_house_floor_remarks
from app.pipeline.fetch.presidential_actions import (
    fetch_recent_presidential_actions,
    _fetch_body_text,
)
from app.pipeline.fetch.fr_rulemaking import fetch_fr_rulemaking
from app.pipeline.fetch.supreme_court import fetch_scotus_cases
from app.pipeline.vector_store import embed_explore_documents

logger = logging.getLogger(__name__)

EXPLORE_SEED_VERSION = "v12"


def _stable_hash(text: str) -> str:
    """Deterministic 8-hex-char digest, unlike Python's built-in hash().

    hash() on strings is randomized per-process (PYTHONHASHSEED, on since
    Python 3.3 for hash-flooding-DoS protection) — the same real speech
    text produces a DIFFERENT external_id after every container restart,
    which silently defeats the ExploreDocument.external_id dedup check
    below and re-inserts a duplicate row. Found live (2026-07 audit):
    1,758 exact-duplicate floor-speech rows (31% of all explore_documents)
    from repeated deploys re-ingesting the same recent-window speeches
    under a new hash seed each time.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _crec_url(date_str: str, chamber: str) -> str:
    """Build a Congressional Record URL for a given date and chamber."""
    section = "senate-section" if chamber == "Senate" else "house-section"
    return f"https://www.congress.gov/congressional-record/{date_str.replace('-', '/')}/{section}"


def _senator_lookup(db: Session) -> dict[str, str]:
    """Build a map of UPPERCASE last name -> senator ID for linking."""
    lookup: dict[str, str] = {}
    for s in db.query(Senator.id, Senator.name).all():
        parts = s.name.split()
        if parts:
            lookup[parts[-1].upper()] = s.id
    return lookup


def _rep_lookup(db: Session) -> dict[str, str]:
    """Build a map of UPPERCASE last name -> representative ID for linking."""
    lookup: dict[str, str] = {}
    for r in db.query(Representative.id, Representative.name).all():
        parts = r.name.split()
        if parts:
            lookup[parts[-1].upper()] = r.id
    return lookup


def _justice_lookup(db: Session) -> dict[str, str]:
    """Build a map of UPPERCASE last name -> justice ID for linking."""
    lookup: dict[str, str] = {}
    for j in db.query(Justice.id, Justice.name).filter(Justice.is_active == True).all():  # noqa: E712
        parts = j.name.split()
        if parts:
            lookup[parts[-1].upper()] = j.id
    return lookup


def _president_id_for_name(name: str) -> str | None:
    """Best-effort mapping from Federal Register president name to our ID."""
    name_lower = (name or "").lower()
    mapping = {
        "biden": "biden-46",
        "trump": "trump-47",
        "obama": "obama-44",
        "bush": "gwbush-43",
        "clinton": "clinton-42",
    }
    for key, pid in mapping.items():
        if key in name_lower:
            return pid
    return None


async def _backfill_presidential_bodies(
    db: Session, client: httpx.AsyncClient
) -> int:
    """Fetch body text for presidential documents that have empty body/summary."""
    import asyncio
    import re

    docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.doc_type.in_(["Executive Order", "Proclamation", "Presidential Memorandum"]),
            ExploreDocument.body == "",
        )
        .all()
    )
    if not docs:
        return 0

    logger.info("Backfilling body content for %d presidential documents...", len(docs))

    BATCH = 5
    filled = 0
    async with httpx.AsyncClient() as backfill_client:
        for i in range(0, len(docs), BATCH):
            batch = docs[i : i + BATCH]
            urls = []
            for d in batch:
                doc_num = (d.external_id or "").removeprefix("fr-")
                html_url = ""
                if doc_num and d.url:
                    m = re.search(r"/documents/(\d{4}/\d{2}/\d{2})/", d.url)
                    if m:
                        html_url = (
                            f"https://www.federalregister.gov/documents/full_text/html/"
                            f"{m.group(1)}/{doc_num}.html"
                        )
                urls.append(html_url)

            bodies = await asyncio.gather(
                *[_fetch_body_text(backfill_client, u) for u in urls]
            )

            for d, body_text in zip(batch, bodies):
                if body_text:
                    d.body = body_text
                    if not d.summary:
                        d.summary = body_text[:500]
                    filled += 1

    if filled:
        db.commit()
        logger.info("Backfilled body content for %d presidential documents", filled)
    return filled


async def _backfill_rulemaking_bodies(db: Session) -> int:
    """Fetch full body text for rulemaking docs that only have the abstract."""
    import asyncio
    from sqlalchemy import func as sa_func
    from app.pipeline.fetch.fr_rulemaking import _fetch_body_text

    from sqlalchemy import or_
    docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.chamber == "Regulatory",
            ExploreDocument.url.isnot(None),
            or_(
                sa_func.length(ExploreDocument.body) < 2000,
                ExploreDocument.body.like("Document Headings%"),
            ),
        )
        .all()
    )
    if not docs:
        return 0

    logger.info("Backfilling body content for %d rulemaking documents...", len(docs))

    BATCH = 8
    filled = 0
    async with httpx.AsyncClient() as backfill_client:
        for i in range(0, len(docs), BATCH):
            batch = docs[i : i + BATCH]
            body_html_urls = []
            for d in batch:
                doc_num = (d.external_id or "").removeprefix("fr-reg-")
                html_url = ""
                if doc_num and d.url:
                    import re
                    m = re.search(r"/documents/(\d{4}/\d{2}/\d{2})/", d.url)
                    if m:
                        html_url = (
                            f"https://www.federalregister.gov/documents/full_text/html/"
                            f"{m.group(1)}/{doc_num}.html"
                        )
                body_html_urls.append(html_url)

            bodies = await asyncio.gather(
                *[_fetch_body_text(backfill_client, u) for u in body_html_urls]
            )

            for d, body_text in zip(batch, bodies):
                if body_text:
                    d.body = body_text
                    filled += 1

            await asyncio.sleep(0.3)

    if filled:
        db.commit()
        logger.info("Backfilled body content for %d rulemaking documents", filled)
    return filled


async def run_explore_pipeline(days_back: int = 60) -> dict:
    """Run the full explore document ingestion pipeline.

    Returns dict with counts of documents ingested per source.
    """
    start = time.time()
    db: Session = SessionLocal()

    cached_version = api_cache_get(db, "explore", "seed_version")
    if cached_version == EXPLORE_SEED_VERSION:
        existing = db.query(ExploreDocument.id).limit(1).first()
        if existing:
            logger.info("Explore pipeline: data is current (version %s), skipping", EXPLORE_SEED_VERSION)
            db.close()
            return {"status": "skipped", "reason": "already_current"}

    try:
        senator_map = _senator_lookup(db)
        stats = {"senate_floor": 0, "house_floor": 0, "presidential": 0, "scotus": 0, "fr_rulemaking": 0}

        async with httpx.AsyncClient() as client:
            # --- 1. Senate floor proceedings ---
            logger.info("Explore pipeline: fetching Senate floor proceedings...")
            try:
                senate_remarks = await fetch_floor_remarks(
                    client, db, days_back=days_back, max_granules_per_day=8
                )
                for speaker, remarks in senate_remarks.items():
                    senator_id = senator_map.get(speaker)
                    for remark in remarks:
                        ext_id = f"senate-floor-{speaker}-{remark['date']}-{_stable_hash(remark['text'][:80])}"

                        exists = db.query(ExploreDocument.id).filter(
                            ExploreDocument.external_id == ext_id
                        ).first()
                        if exists:
                            continue

                        db.add(ExploreDocument(
                            doc_type="Senate Floor Speech",
                            source="Congressional Record (GovInfo)",
                            title=remark.get("title", f"Sen. {speaker.title()} — Floor Remarks"),
                            summary=remark["text"][:300],
                            body=remark["text"],
                            date=remark["date"],
                            url=_crec_url(remark["date"], "Senate"),
                            politician_name=speaker.title(),
                            politician_id=senator_id,
                            chamber="Senate",
                            external_id=ext_id,
                        ))
                        stats["senate_floor"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d Senate floor remarks", stats["senate_floor"])
            except Exception as e:
                logger.warning("Senate floor fetch failed: %s", e)
                db.rollback()

            # --- 2. House floor proceedings ---
            logger.info("Explore pipeline: fetching House floor proceedings...")
            rep_map = _rep_lookup(db)
            try:
                house_remarks = await fetch_house_floor_remarks(
                    client, db, days_back=days_back, max_granules_per_day=8
                )
                for remark in house_remarks:
                    speaker = remark["speaker"]
                    ext_id = f"house-floor-{speaker}-{remark['date']}-{_stable_hash(remark['text'][:80])}"

                    exists = db.query(ExploreDocument.id).filter(
                        ExploreDocument.external_id == ext_id
                    ).first()
                    if exists:
                        continue

                    rep_id = rep_map.get(speaker.upper())
                    db.add(ExploreDocument(
                        doc_type="House Floor Speech",
                        source="Congressional Record (GovInfo)",
                        title=remark.get("title", f"Rep. {speaker.title()} — Floor Remarks"),
                        summary=remark["text"][:300],
                        body=remark["text"],
                        date=remark["date"],
                        url=_crec_url(remark["date"], "House"),
                        politician_name=speaker.title(),
                        politician_id=rep_id,
                        chamber="House",
                        external_id=ext_id,
                    ))
                    stats["house_floor"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d House floor remarks", stats["house_floor"])
            except Exception as e:
                logger.warning("House floor fetch failed: %s", e)
                db.rollback()

            # --- 3. Presidential actions ---
            logger.info("Explore pipeline: fetching presidential actions...")
            try:
                actions = await fetch_recent_presidential_actions(client, pages=5)
                for action in actions:
                    ext_id = action["external_id"]

                    exists = db.query(ExploreDocument.id).filter(
                        ExploreDocument.external_id == ext_id
                    ).first()
                    if exists:
                        continue

                    president_id = _president_id_for_name(action.get("politician_name", ""))

                    db.add(ExploreDocument(
                        doc_type=action["doc_type"],
                        source="Federal Register",
                        title=action["title"],
                        summary=action["summary"],
                        body=action.get("body", ""),
                        date=action["date"],
                        url=action.get("url"),
                        politician_name=action.get("politician_name"),
                        politician_id=president_id,
                        chamber="Executive",
                        external_id=ext_id,
                    ))
                    stats["presidential"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d presidential actions", stats["presidential"])
            except Exception as e:
                logger.warning("Presidential actions fetch failed: %s", e)
                db.rollback()

            # --- 4. Supreme Court opinions ---
            logger.info("Explore pipeline: fetching Supreme Court opinions...")
            justice_map = _justice_lookup(db)
            try:
                scotus_cases = await fetch_scotus_cases(client)
                for case in scotus_cases:
                    ext_id = case["external_id"]

                    exists = db.query(ExploreDocument.id).filter(
                        ExploreDocument.external_id == ext_id
                    ).first()
                    if exists:
                        continue

                    # Link to the authoring justice when politician_name is set
                    author_name = case.get("politician_name") or ""
                    author_last = author_name.split()[-1].upper() if author_name.strip() else ""
                    justice_id = justice_map.get(author_last)

                    db.add(ExploreDocument(
                        doc_type=case["doc_type"],
                        source="Supreme Court (supremecourt.gov)",
                        title=case["title"],
                        summary=case["summary"],
                        body=case.get("body", ""),
                        date=case["date"],
                        url=case.get("url"),
                        politician_name=author_name or None,
                        politician_id=justice_id,
                        chamber="Judicial",
                        external_id=ext_id,
                    ))
                    stats["scotus"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d Supreme Court opinions", stats["scotus"])
            except Exception as e:
                logger.warning("Supreme Court fetch failed: %s", e)
                db.rollback()

            # --- 5. Federal Register rulemaking ---
            logger.info("Explore pipeline: fetching Federal Register rulemaking...")
            try:
                fr_docs = await fetch_fr_rulemaking(client, pages=5)
                for fr_doc in fr_docs:
                    ext_id = fr_doc["external_id"]

                    exists = db.query(ExploreDocument.id).filter(
                        ExploreDocument.external_id == ext_id
                    ).first()
                    if exists:
                        continue

                    db.add(ExploreDocument(
                        doc_type=fr_doc["doc_type"],
                        source="Federal Register",
                        title=fr_doc["title"],
                        summary=fr_doc["summary"],
                        body=fr_doc.get("body", ""),
                        date=fr_doc["date"],
                        url=fr_doc.get("url"),
                        politician_name=None,
                        politician_id=None,
                        chamber="Regulatory",
                        agency_name=fr_doc.get("agency_name"),
                        comment_url=fr_doc.get("comment_url"),
                        comments_close_on=fr_doc.get("comments_close_on"),
                        external_id=ext_id,
                    ))
                    stats["fr_rulemaking"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d Federal Register rulemaking docs", stats["fr_rulemaking"])
            except Exception as e:
                logger.warning("Federal Register rulemaking fetch failed: %s", e)
                db.rollback()

        # --- 6. Backfill docs missing body content ---
        await _backfill_presidential_bodies(db, client)
        await _backfill_rulemaking_bodies(db)

        # --- 7. Embed all documents into ChromaDB ---
        logger.info("Explore pipeline: embedding documents into vector store...")
        all_docs = db.query(ExploreDocument).all()
        doc_dicts = [
            {
                "id": d.id,
                "title": d.title,
                "summary": d.summary,
                "body": d.body,
                "doc_type": d.doc_type,
                "source": d.source,
                "date": d.date,
                "politician_name": d.politician_name,
                "politician_id": d.politician_id,
                "chamber": d.chamber,
            }
            for d in all_docs
        ]
        embedded = embed_explore_documents(doc_dicts)

        api_cache_set(db, "explore", "seed_version", EXPLORE_SEED_VERSION)
        db.commit()

        elapsed = time.time() - start
        total = sum(stats.values())
        logger.info(
            "Explore pipeline complete: %d new docs (%d embedded) in %.1fs",
            total, embedded, elapsed,
        )

        return {
            "status": "completed",
            "new_documents": stats,
            "total_embedded": embedded,
            "elapsed_seconds": round(elapsed, 1),
        }

    except Exception as e:
        logger.error("Explore pipeline failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()
