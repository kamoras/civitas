"""Explore pipeline — fetches government activity from multiple sources and
indexes it for semantic search.

Sources:
  1. Senate floor proceedings (Congressional Record via GovInfo)
  2. House floor proceedings (Congressional Record via GovInfo)
  3. Presidential actions (Federal Register: EOs, memoranda, proclamations)
  4. Supreme Court opinions (Oyez API)
  5. Federal Register rulemaking (rules, proposed rules, notices)

Each document is stored in the explore_documents table, which then feeds
three search structures rebuilt from it at the end of every run: the
sentence-transformer embeddings in `vec_explore` (sqlite-vec), the BM25F
keyword index in `explore_fts` (SQLite FTS5), and the citation graph behind
each document's PageRank authority. See `services/explore_search.py` for how
the three are combined at query time.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import func as sa_func, or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.http_client import make_async_client
from app.models import ExploreDocument, Justice, Representative, Senator
from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.analyze.floor_speech import speech_flags, titled_speeches
from app.pipeline.fetch.congress import congress_of_date
from app.pipeline.fetch.congressional_record import (
    CHAMBERS,
    congress_start,
    fetch_crec_packages,
    fetch_day_speeches,
)
from app.pipeline.fetch.presidential_actions import (
    fetch_recent_presidential_actions,
    _fetch_body_text,
)
from app.pipeline.fetch.fr_rulemaking import (
    fetch_fr_rulemaking,
    _fetch_body_text as _fetch_rulemaking_body_text,
)
from app.pipeline.fetch.supreme_court import fetch_scotus_cases
from app.pipeline.transform.normalize_members import STATE_NAME_TO_CODE, strip_accents
from app.pipeline.analyze.document_authority import update_document_authority
from app.pipeline.explore_ranking import calibrate_and_store
from app.pipeline.lexical_index import rebuild_index
from app.pipeline.vector_store import (
    _META_FIELDS,
    alert_rebuild_failed,
    delete_explore_vectors,
    explore_embed_dict,
    explore_meta_hash,
    explore_text_hash,
    get_embedded_explore_ids,
    get_embedded_hashes,
    index_is_whole,
    is_busy_error,
    rebuild_explore_index,
    top_up_explore_index,
    wait_for_rebuild,
)

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


_NAME_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV"})


def _surname_keys(name: str) -> set[str]:
    """The ways the Record may print a member's surname: the last word, and
    the last two or three for a surname of several words ("VAN DER BERG",
    "VAN WEST", "DE LA PAZ"),
    accents dropped ("LUJAN") and suffixes ignored ("Robert P., Jr. Casey")."""
    words = [w for w in strip_accents(name).upper().replace(",", " ").replace(".", " ").split()
             if w not in _NAME_SUFFIXES]
    if not words:
        return set()
    return {words[-1], " ".join(words[-2:]), " ".join(words[-3:])}


class _SpeakerLookup:
    """A Record speaker ("SCOTT", "SCOTT of Florida") -> member id, or None.

    A surname is linked only when it names exactly one member: sitting
    members first, then anyone still on file. It used to be a dict of last
    name -> id built over every row, so of two members sharing a surname
    the later row won, a departed member could take a sitting one's
    speeches, and a "Jr." or a two-word surname never matched at all.
    """

    def __init__(self, rows):
        self._current: dict[tuple[str, str | None], set[str]] = {}
        self._anyone: dict[tuple[str, str | None], set[str]] = {}
        for member_id, name, state, is_current in rows:
            for key in _surname_keys(name or ""):
                for index in ((key, None), (key, state)):
                    self._anyone.setdefault(index, set()).add(member_id)
                    if is_current:
                        self._current.setdefault(index, set()).add(member_id)

    def get(self, speaker: str) -> str | None:
        surname, _, state_name = speaker.partition(" of ")
        state = STATE_NAME_TO_CODE.get(state_name.strip()) if state_name else None
        if state_name and state is None:
            return None
        key = (strip_accents(surname).upper().strip(), state)
        for index in (self._current, self._anyone):
            ids = index.get(key)
            if ids:
                return next(iter(ids)) if len(ids) == 1 else None
        return None


def _senator_lookup(db: Session) -> _SpeakerLookup:
    return _SpeakerLookup(db.query(Senator.id, Senator.name, Senator.state, Senator.is_current).all())


def _rep_lookup(db: Session) -> _SpeakerLookup:
    return _SpeakerLookup(
        db.query(Representative.id, Representative.name, Representative.state, Representative.is_current).all()
    )


def _speaker_surname(speaker: str) -> str:
    """"SCOTT of Florida" -> "Scott", for the document's politician name."""
    return speaker.partition(" of ")[0].title()


def _justice_lookup(db: Session) -> dict[str, str]:
    """Build a map of UPPERCASE last name -> justice ID for linking."""
    lookup: dict[str, str] = {}
    for j in db.query(Justice.id, Justice.last_name).filter(Justice.is_active == True).all():  # noqa: E712
        # The stored surname: a name's last word is "Jr." for two justices.
        if j.last_name:
            lookup[j.last_name.upper()] = j.id
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
) -> list[int]:
    """Fetch body text for presidential documents that have empty body/summary.

    Returns the ids of documents whose body changed, for the log. The embed
    step finds them by their text hash, not by this list."""
    docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.doc_type.in_(["Executive Order", "Proclamation", "Presidential Memorandum"]),
            ExploreDocument.body == "",
        )
        .all()
    )
    if not docs:
        return []

    logger.info("Backfilling body content for %d presidential documents...", len(docs))

    BATCH = 5
    filled: list[int] = []
    async with make_async_client() as backfill_client:
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
                # Same change-detection rule as the rulemaking backfill
                # below: a re-fetch that returns the text already stored
                # is not a refresh, and must not force a re-embed.
                if body_text and body_text != d.body:
                    d.body = body_text
                    if not d.summary:
                        d.summary = body_text[:500]
                    filled.append(d.id)

    if filled:
        db.commit()
        logger.info("Backfilled body content for %d presidential documents", len(filled))
    return filled


async def _backfill_rulemaking_bodies(db: Session) -> list[int]:
    """Fetch full body text for rulemaking docs that only have the abstract.

    Returns the ids of documents whose body changed, for the log, as
    _backfill_presidential_bodies."""
    docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.chamber == "Regulatory",
            ExploreDocument.url.isnot(None),
            # Never successfully fetched. Without this the selection below
            # is non-convergent: it matches on body shape, which a
            # genuinely short but complete document keeps matching after
            # every fetch, so the same 476 documents were re-downloaded
            # nightly forever. See ExploreDocument.body_fetched_at.
            ExploreDocument.body_fetched_at.is_(None),
            or_(
                sa_func.length(ExploreDocument.body) < 2000,
                ExploreDocument.body.like("Document Headings%"),
            ),
        )
        .all()
    )
    if not docs:
        return []

    logger.info("Backfilling body content for %d rulemaking documents...", len(docs))

    BATCH = 8
    filled: list[int] = []
    fetched = 0
    async with make_async_client() as backfill_client:
        for i in range(0, len(docs), BATCH):
            batch = docs[i : i + BATCH]
            body_html_urls = []
            for d in batch:
                doc_num = (d.external_id or "").removeprefix("fr-reg-")
                html_url = ""
                if doc_num and d.url:
                    m = re.search(r"/documents/(\d{4}/\d{2}/\d{2})/", d.url)
                    if m:
                        html_url = (
                            f"https://www.federalregister.gov/documents/full_text/html/"
                            f"{m.group(1)}/{doc_num}.html"
                        )
                body_html_urls.append(html_url)

            bodies = await asyncio.gather(
                *[_fetch_rulemaking_body_text(backfill_client, u)
                  for u in body_html_urls]
            )

            for d, body_text in zip(batch, bodies):
                # Only a body that actually CHANGED counts as refreshed.
                # The query above selects on `length(body) < 2000`, and a
                # short Federal Register notice whose real full text is
                # genuinely under that never stops matching it: 469 real
                # documents did on 2026-09-20, every one already complete
                # (longest body: 1,999 characters). Appending regardless
                # of change meant all 468 that fetched cleanly were
                # reported as refreshed every night, and step 7 re-chunked
                # and re-embedded each one into identical vectors -- a
                # permanent, growing nightly cost for zero new
                # information.
                if not body_text:
                    continue  # leave body_fetched_at NULL so this retries
                fetched += 1
                d.body_fetched_at = datetime.now(UTC)
                if body_text != d.body:
                    d.body = body_text
                    filled.append(d.id)

            await asyncio.sleep(0.3)

    # Commits on `fetched`, not on `filled`: the whole point of the marks
    # is the run where nothing changed, and committing only on a change
    # would throw them away and re-fetch the same documents tomorrow.
    if fetched:
        db.commit()
        logger.info(
            "Backfilled %d rulemaking documents (%d bodies changed)",
            fetched, len(filled),
        )
    return filled


# Floor speeches, stored per day of the Record, for the whole sitting
# Congress (AGENTS.md §6). Bump SPEECH_FORMAT when what a day becomes
# changes (the parse, the floor-business test, titles, the external_id):
# every day is then read again, SPEECH_DAYS_PER_RUN a run, and each day's
# documents are replaced as it is read — the old ones stay searchable
# until then.
SPEECH_FORMAT = "v1"
_SPEECH_TYPES = {"Senate": "Senate Floor Speech", "House": "House Floor Speech"}
# How long a day stays recorded as read: past the Congress, since the
# Record of a day does not change once published.
_DAY_READ_TTL_HOURS = 24 * 365 * 3
_SPEECH_ID_PREFIX = f"crec-{SPEECH_FORMAT}-"

# Days of the Record read per run, newest unread first: new issues, then
# the back-fill toward the start of the Congress. Each costs, for a full
# session day (measured over 14 days of the Record, 2026-03 to 2026-09):
# about 73 GovInfo requests at GOVINFO_RPS = 1, so ~75 s waiting on the
# rate limiter (the event loop free throughout); ~1,270 paragraphs through
# the floor-business test (436/s on a desktop CPU, so ~32 s on the Pi,
# which embedded at 8 windows/s against the desktop's 86 — run 2026-09-20)
# in a worker thread; and ~286,000 characters of speech for the embed
# step, ~320 windows or ~40 s on the Pi, also in a worker thread. About
# 2.5 minutes a day, so 15 days add at most ~36 minutes to the nightly
# Supplementary run (8-hour overrun budget); pro forma days cost a few
# requests. The 119th Congress had 372 issues on 2026-10-08 (GovInfo's
# CREC sitemaps), so a full back-fill, or a SPEECH_FORMAT re-read, takes
# about 25 nights. The work on the event loop itself is parsing a
# granule's text (~2 ms) and one commit a day.
SPEECH_DAYS_PER_RUN = 15


def _day_read_key(package_id: str) -> str:
    return f"crec-speeches-{SPEECH_FORMAT}-{package_id}"


def _read_frontier(packages: list[str], read: set[str]) -> str | None:
    """The cursor: the oldest date down to which every issue, from the
    newest, has been read in this format — the Congress's first day once
    all have; None when the newest has not been. A day that failed holds
    it until a later run reads that day."""
    frontier = None
    for package_id in packages:  # newest first
        if package_id not in read:
            return frontier
        frontier = package_id.removeprefix("CREC-")
    return congress_start(settings.CURRENT_CONGRESS).isoformat()


def _purge_out_of_scope_speeches(db: Session, frontier: str | None) -> int:
    """Delete floor speeches outside the sitting Congress, and those in an
    older format dated on or after the read frontier.

    Explore's speeches are the sitting Congress's, as every scored window
    is (AGENTS.md §6). GovInfo's collection index lists packages by when
    they were last modified, and speeches from a reprocessed 1996 issue
    (six) and 2017 ones (two) reached the index that way (fixed at the
    source in fetch_crec_packages).

    An older format's speech — before SPEECH_FORMAT, "senate-floor-"/
    "house-floor-" ids, each turn cut to 400/500 characters and titled
    after its whole Record section — goes when its day is read again (the
    day's documents are replaced). What this sweeps is what is left past
    the frontier, where every issue has been read: rows on a date with no
    issue. Nothing older than the frontier is touched, so search has no
    gap while the back-fill runs.
    """
    rows = (
        db.query(ExploreDocument.id, ExploreDocument.date, ExploreDocument.external_id)
        .filter(ExploreDocument.doc_type.in_(_SPEECH_TYPES.values()))
        .all()
    )
    doomed = [
        r.id for r in rows
        if congress_of_date(r.date or "") != settings.CURRENT_CONGRESS
        or (frontier is not None and (r.date or "") >= frontier
            and not (r.external_id or "").startswith(_SPEECH_ID_PREFIX))
    ]
    for i in range(0, len(doomed), 500):
        (db.query(ExploreDocument)
           .filter(ExploreDocument.id.in_(doomed[i:i + 500]))
           .delete(synchronize_session=False))
    db.commit()
    if doomed:
        logger.info("Purged %d floor speeches outside the sitting Congress or in an old format", len(doomed))
    return len(doomed)


def _speech_documents(chamber: str, speeches: list[dict], lookup: "_SpeakerLookup") -> list[ExploreDocument]:
    return [
        ExploreDocument(
            doc_type=_SPEECH_TYPES[chamber],
            source="Congressional Record (GovInfo)",
            title=s["title"],
            summary=s["text"][:300],
            body=s["text"],
            date=s["date"],
            url=s["url"],
            politician_name=_speaker_surname(s["speaker"]),
            politician_id=lookup.get(s["speaker"]),
            chamber=chamber,
            external_id=f"{_SPEECH_ID_PREFIX}{s['granule_id']}-{_stable_hash(s['speaker'] + chr(10) + s['text'])}",
        )
        for s in speeches
    ]


async def _ingest_floor_speeches(db: Session, client: httpx.AsyncClient) -> dict[str, int]:
    """Store the sitting Congress's floor speeches, up to
    SPEECH_DAYS_PER_RUN days of the Record a run, newest unread first;
    returns how many were added per chamber.

    A day is read whole (every granule in which the Record lists a member
    speaking), its turns sorted into speeches and floor business
    (analyze/floor_speech.py), and its stored speeches — any format —
    replaced by what it holds now; then it is recorded as read and not
    fetched again. A day that could not be fetched whole is left
    unrecorded: it holds the read frontier (_read_frontier) and is tried
    next run, never taken as a day with no speeches.
    """
    added = {chamber: 0 for chamber in CHAMBERS}
    packages = await fetch_crec_packages(client)
    if packages is None:
        logger.warning("Congressional Record index unavailable — floor speeches not updated this run")
        _purge_out_of_scope_speeches(db, None)
        return added
    read = {p for p in packages
            if api_cache_get(db, "govinfo", _day_read_key(p), max_age_hours=_DAY_READ_TTL_HOURS)}
    lookups = {"Senate": _senator_lookup(db), "House": _rep_lookup(db)}
    failed: list[str] = []
    for package_id in [p for p in packages if p not in read][:SPEECH_DAYS_PER_RUN]:
        day = await fetch_day_speeches(client, package_id)
        if day is None:
            failed.append(package_id)
            continue
        stored = {
            r.external_id: r.id for r in db.query(ExploreDocument.id, ExploreDocument.external_id).filter(
                ExploreDocument.doc_type.in_(_SPEECH_TYPES.values()),
                ExploreDocument.date == package_id.removeprefix("CREC-"))
        }
        new: list[ExploreDocument] = []
        seen: set[str] = set()
        for chamber, turns in day.items():
            # CPU: an embedding per paragraph. Off the event loop.
            flags = await asyncio.to_thread(speech_flags, [t["text"] for t in turns])
            for doc in _speech_documents(chamber, titled_speeches(turns, flags), lookups[chamber]):
                if doc.external_id in seen:
                    continue
                seen.add(doc.external_id)
                if doc.external_id not in stored:
                    new.append(doc)
                    added[chamber] += 1
        stale = [i for ext, i in stored.items() if ext not in seen]  # held before, gone now
        if stale:
            db.query(ExploreDocument).filter(ExploreDocument.id.in_(stale)).delete(synchronize_session=False)
        db.add_all(new)
        api_cache_set(db, "govinfo", _day_read_key(package_id), True,
                      normal_ttl_hours=_DAY_READ_TTL_HOURS, commit=False)
        db.commit()
        read.add(package_id)
    frontier = _read_frontier(packages, read)
    _purge_out_of_scope_speeches(db, frontier)
    logger.info("Congressional Record: %d of %d days read in format %s, frontier %s",
                len(read), len(packages), SPEECH_FORMAT, frontier or "none yet")
    if failed:
        logger.warning("Congressional Record: %d days could not be read whole (%s) — retried next run",
                       len(failed), ", ".join(failed))
    return added


async def _embed_step(db: Session) -> int:
    """Step 7: bring the vector index up to the corpus; returns how many
    documents were embedded.

    An index that isn't a complete build by this model (a rebuild that
    failed or was cut off, a model or layout change) is rebuilt whole, here
    and under this run's lease, rather than topped up: an incremental pass
    can't make it whole, and calibration measures it. Otherwise only
    documents missing from it, or whose text has changed since their vectors
    were written (a body backfilled, a re-ingest — explore_text_hash, which
    every embed records with the vectors), are encoded: re-encoding the
    whole corpus every night is what made the old 72h skip gate look
    necessary.

    A look or a rebuild that only meets a lock skips the step: the index may
    well be whole (no reason to drop it) and may not be (a top-up beside a
    rebuild would insert chunks twice). Nothing is lost by skipping: what
    is missing or changed still reads so to the next run.
    """
    from app.ops_alerts import resolve_ops_alert

    whole = await _index_is_whole_or_none()
    outcome = "skipped"  # or "failed", "rebuilt", "topped up", "whole, top-up skipped"
    embedded = 0
    if whole is False:
        logger.info("Explore pipeline: vector index incomplete — rebuilding it whole...")
        try:
            # Waiting out one already running (a start's): embedding beside
            # it would insert every missing document's chunks twice.
            rebuilt = await asyncio.to_thread(rebuild_explore_index, SessionLocal, wait=True, if_incomplete=True)
        except Exception as exc:
            # A lock before the swap touched nothing: skipped. After it
            # (RebuildFailed, whatever the cause) the index is gone: failed.
            if is_busy_error(exc):
                logger.warning("Explore pipeline: vector index busy — rebuild left to the next run (%s)", exc)
            else:
                logger.exception("Explore pipeline: vector index rebuild failed")
                outcome = "failed"
                await asyncio.to_thread(alert_rebuild_failed, "Explore run", exc)
        else:
            if rebuilt is not None:
                outcome, embedded = "rebuilt", rebuilt
            else:
                # A start's rebuild finished it while this waited: topped up
                # below. That rebuild reads documents by id without this
                # run's lease, so one this run deleted meanwhile may have
                # been embedded after the purge above.
                await asyncio.to_thread(_purge_orphaned_vectors, db)
                whole = True
    if whole is True:
        try:
            embedded = await _top_up(db)
            outcome = "topped up"
        except Exception as exc:
            # Each document is left as it was or as it now is (a batch of
            # whole documents per transaction): what wasn't reached still
            # reads as missing or changed. A lock skips the step; anything
            # else fails the run as it always has.
            if not is_busy_error(exc):
                raise
            logger.warning("Explore pipeline: vector index busy — top-up left to the next run (%s)", exc)
            outcome = "whole, top-up skipped"  # whole all the same: its alert ends

    if outcome == "skipped":
        logger.warning("Explore pipeline: vector index busy — embed step skipped this run")
    elif outcome != "failed":
        # Whole now, by this run or a start's: a failed rebuild's alert ends.
        await asyncio.to_thread(resolve_ops_alert, "explore-index-rebuild")
    return embedded


def _top_up_plan(db: Session) -> tuple[list[tuple[dict, str, str | None]], list[tuple[dict, str, str]]]:
    """(the documents to embed, the documents to relabel), each with the
    hash it is written with and the one the index held when this read it:
    to embed, those the index has no record of and those whose text changed
    since their vectors were written; to relabel, those whose metadata
    alone changed (search filters on it: written in place, not
    re-encoded). Read outside the rebuild lock, on a session of its own
    (the run's is left as it was), a batch of rows at a time: bodies are
    long, and most documents are current. An unreadable index raises (a
    lock is a skip): read as empty, it would re-encode the whole corpus."""
    from app.database import own_session

    held = get_embedded_hashes()
    embed: list[tuple[dict, str, str | None]] = []
    relabel: list[tuple[dict, str, str]] = []
    with own_session(db) as scan:
        after = 0
        while True:
            docs = (
                scan.query(ExploreDocument).filter(ExploreDocument.id > after)
                .order_by(ExploreDocument.id).limit(500).all()
            )
            if not docs:
                return embed, relabel
            for d in docs:
                doc = explore_embed_dict(d)
                text = explore_text_hash(doc)
                was_text, was_meta = held.get(d.id, (None, None))
                # Every embed records its document's hashes with its vectors
                # (a document with no text too), so none recorded means
                # never embedded, and a different one means changed since.
                if was_text != text:
                    embed.append((doc, text, was_text))
                elif was_meta != (meta := explore_meta_hash(doc)):
                    # Its id and metadata only: the body isn't written, and
                    # is let go of with the batch.
                    relabel.append(({f: doc[f] for f in ("id", *_META_FIELDS)}, meta, was_meta))
            after = docs[-1].id
            scan.expunge_all()  # the bodies read, let go of


def _still_wanted(plan: list[tuple[dict, str, str | None]], held: dict[int, tuple[str, str]]) -> list[dict]:
    """The embed plan re-checked under the rebuild lock, against the index
    alone (no bodies read or hashed again): only documents the index holds
    as this plan found them. One written since — by a rebuild this waited
    out — was written from the database at least as late as this plan
    read it: not embedded twice, nor put back to the plan's older text.
    `held`: get_embedded_hashes() as read under the lock."""
    return [
        {**doc, "_text_hash": current}
        for doc, current, was in plan
        if held.get(doc["id"], (None, None))[0] == was
    ]


def _still_to_relabel(plan: list[tuple[dict, str, str]], held: dict[int, tuple[str, str]]) -> list[dict]:
    """The relabel plan re-checked the same way: documents whose recorded
    metadata is still what this plan found. The same read serves both: the
    embed between them writes only its own documents, none of these."""
    return [doc for doc, _meta, was in plan if held.get(doc["id"], (None, None))[1] == was]


async def _top_up(db: Session) -> int:
    logger.info("Explore pipeline: embedding documents into vector store...")
    plan, relabel = await asyncio.to_thread(_top_up_plan, db)
    # Off the event loop: encoding is pure CPU inside sentence-transformers
    # and ran for 23 MINUTES in one call against the real corpus (1,557
    # documents / 11,022 chunks, measured on the Pi 2026-09-20). Awaiting it
    # inline froze the whole FastAPI process, so /api/health stopped
    # answering, Swarm's healthcheck (every 30s, 5s timeout, 3 retries -- so
    # ~90s of unresponsiveness is fatal) failed, and the container was
    # SIGKILLed mid-run (exit 137, "unhealthy container") -- which is what
    # actually broke every nightly run from 2026-09-02 onward. The killed
    # process left its run row stuck "active", so the 12h "hang" in the
    # admin view was the NEXT night's staleness sweep, not real running
    # time; House/Stock/Election never ran again because they are chained
    # behind this phase. Same asyncio.to_thread treatment
    # donor_classifier_ai.py and api/explore.py already give their own
    # CPU-bound calls. Under the rebuild lock (top_up_explore_index): a
    # start's rebuild waits for it rather than embed beside it.
    held: dict[int, tuple[str, str]] = {}

    def to_embed() -> list[dict]:
        held.update(get_embedded_hashes())  # under the lock, read once
        return _still_wanted(plan, held)

    return await asyncio.to_thread(top_up_explore_index, to_embed, lambda: _still_to_relabel(relabel, held))


async def _index_is_whole_or_none() -> bool | None:
    """Whether the vector index is a complete build: False when it can't be
    read (a rebuild recreates it), None when it is only locked — after
    waiting out any rebuild in this process and looking once more."""
    for attempt in (1, 2):
        try:
            return await asyncio.to_thread(index_is_whole)
        except Exception as error:
            if not is_busy_error(error):
                logger.exception("Explore pipeline: could not read the vector index — rebuilding it")
                return False
            if attempt == 1:
                await asyncio.to_thread(wait_for_rebuild)
    return None


def _purge_orphaned_vectors(db: Session) -> int:
    """Drop vectors whose ExploreDocument no longer exists.

    Semantic search answers out of vec_explore's own columns without
    joining back to explore_documents, so an orphaned vector is not
    inert — it keeps being returned as a search hit. Nothing swept these
    before: embed_explore_documents only clears vectors for documents it
    is about to re-embed, which by definition still exist.
    """
    try:
        # Chunks and text-hash rows both (get_embedded_explore_ids): a
        # document with no text has only its hash row.
        embedded = get_embedded_explore_ids()
    except Exception:
        logger.warning("Could not read the vector index — skipping orphan sweep")
        return 0
    from app.database import own_session

    # On a session of its own: this runs on a worker thread, and the run's
    # session is closed under it if the run is cancelled meanwhile.
    with own_session(db) as own:
        live = {row[0] for row in own.query(ExploreDocument.id).all()}
    orphans = embedded - live
    if not orphans:
        return 0
    try:
        removed = delete_explore_vectors(orphans)
    except Exception as exc:
        # A lock is a skip, as it is for the rest of the embed step: the
        # delete rolled back, and the orphans read the same next run.
        if not is_busy_error(exc):
            raise
        logger.warning("Vector index busy — orphan sweep left to the next run (%s)", exc)
        return 0
    logger.info(
        "Purged %d orphaned vector chunks for %d deleted documents",
        removed, len(orphans),
    )
    return removed


async def run_explore_pipeline() -> dict:
    """Run the full explore document ingestion pipeline.

    Returns dict with counts of documents ingested per source.
    """
    start = time.time()
    db: Session = SessionLocal()

    # No same-version skip here anymore: the old seed_version gate rode on
    # ApiCache's 72h TTL, so the "nightly" supplementary run actually
    # ingested at most once every 3 days — no new executive orders, rules,
    # or floor speeches on the other two nights — while reporting the skip
    # as success. Bootstrap-on-restart is already guarded by main.py's own
    # emptiness check, ingestion is incremental (external_id dedupe + each
    # fetcher's own ApiCache), and the embed step below only encodes new or
    # refreshed documents, so running every night is cheap.
    try:
        stats = {"senate_floor": 0, "house_floor": 0, "presidential": 0, "scotus": 0, "fr_rulemaking": 0}

        async with make_async_client() as client:
            # --- 1-2. Senate and House floor speeches ---
            logger.info("Explore pipeline: fetching floor speeches...")
            try:
                floor = await _ingest_floor_speeches(db, client)
                stats["senate_floor"], stats["house_floor"] = floor["Senate"], floor["House"]
                logger.info("Explore pipeline: ingested %d Senate and %d House floor speeches",
                            floor["Senate"], floor["House"])
            except Exception as e:
                logger.warning("Floor speech ingest failed: %s", e)
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
                        identifiers=json.dumps(action.get("identifiers") or []),
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
            sitting = [(j.name, j.last_name) for j in db.query(Justice).filter(Justice.is_active == True)]  # noqa: E712
            try:
                scotus_cases = await fetch_scotus_cases(client, justices=sitting)
                for case in scotus_cases:
                    ext_id = case["external_id"]

                    # Link to the authoring justice when politician_name is set
                    author_name = case.get("politician_name") or ""
                    author_last = next((last for name, last in sitting if name == author_name), "")
                    justice_id = justice_map.get((author_last or "").upper())

                    stored = db.query(ExploreDocument).filter(
                        ExploreDocument.external_id == ext_id
                    ).first()
                    if stored is not None:
                        # The Court posts an opinion after the case is first
                        # stored: the link, author and holding follow it.
                        if case.get("url") and case["url"] != stored.url:
                            stored.url, stored.summary, stored.body = case["url"], case["summary"], case.get("body", "")
                            stored.politician_name, stored.politician_id = author_name or None, justice_id
                        continue

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
                        identifiers=json.dumps(fr_doc.get("identifiers") or []),
                    ))
                    stats["fr_rulemaking"] += 1

                db.commit()
                logger.info("Explore pipeline: ingested %d Federal Register rulemaking docs", stats["fr_rulemaking"])
            except Exception as e:
                logger.warning("Federal Register rulemaking fetch failed: %s", e)
                db.rollback()

        # --- 6. Backfill docs missing body content ---
        # Their vectors are re-embedded in step 7, whose text hashes show
        # the change.
        backfilled = await _backfill_presidential_bodies(db, client)
        backfilled += await _backfill_rulemaking_bodies(db)
        if backfilled:
            logger.info("Explore pipeline: backfilled %d document bodies", len(backfilled))

        # --- 7. Embed new/refreshed documents into the vector index ---
        # The orphan sweep goes first: it drops the vectors of documents
        # deleted above (floor speeches out of scope or superseded).
        # Off the loop, which serves summary streams and the admin status
        # check-and-deploy polls: it scans every chunk's document id.
        await asyncio.to_thread(_purge_orphaned_vectors, db)

        embedded = await _embed_step(db)

        # --- 8. Rebuild the keyword index ---
        # Triggers keep explore_fts live between runs, but the backfill
        # steps above rewrite bodies in place and an external-content FTS5
        # index goes quietly wrong if a trigger's 'delete' ever sees values
        # that differ from what was indexed. A full re-tokenise here makes
        # any drift self-healing within a day.
        logger.info("Explore pipeline: rebuilding keyword index...")
        indexed = await asyncio.to_thread(rebuild_index, db)

        # --- 9. Recompute citation-graph authority ---
        # After ingestion, so today's documents both earn citations and
        # count as citing documents in the same pass.
        logger.info("Explore pipeline: recomputing citation authority...")
        try:
            authority_stats = await asyncio.to_thread(update_document_authority, db)
        except Exception:
            logger.exception("Citation authority pass failed — ranking falls back "
                             "to relevance + freshness until the next run")
            db.rollback()
            authority_stats = {"documents": 0, "cited": 0}

        # --- 10. Recalibrate ranking against the corpus just built ---
        # Last, because every derivation reads the finished indexes: field
        # weights are fitted on keyword retrieval, the prior weights on how
        # far the two channels disagree, the pool on how many candidates
        # survive filtering. Ranking parameters therefore always describe
        # the corpus actually being searched, and nobody ever types one.
        logger.info("Explore pipeline: recalibrating ranking...")
        calibration = await asyncio.to_thread(calibrate_and_store, db)

        api_cache_set(db, "explore", "seed_version", EXPLORE_SEED_VERSION)
        db.commit()

        elapsed = time.time() - start
        total = sum(stats.values())
        logger.info(
            "Explore pipeline complete: %d new docs (%d embedded, %d keyword-indexed, "
            "%d cited) in %.1fs",
            total, embedded, indexed, authority_stats["cited"], elapsed,
        )

        return {
            "status": "completed",
            "new_documents": stats,
            "total_embedded": embedded,
            "keyword_indexed": indexed,
            "authority": authority_stats,
            "calibration": calibration,
            "elapsed_seconds": round(elapsed, 1),
        }

    except Exception as e:
        logger.error("Explore pipeline failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()
