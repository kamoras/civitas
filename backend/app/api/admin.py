"""Admin API — protected management endpoints for dashboard, pipeline control, and metrics."""

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.pipeline_runner import run_pipeline_in_thread
from app.config import settings
from app.database import get_db, get_visits_db
from app.http_client import make_async_client
from app.models import (
    ActionIssue,
    AnalysisCache,
    ApiCache,
    CampaignPromise,
    Donor,
    ExploreDocument,
    IndustryDonation,
    Justice,
    JusticeVote,
    KeyVote,
    LearnedClassification,
    LobbyingMatch,
    MonitorUpdate,
    NationalMonitor,
    PageView,
    PipelinePhaseTiming,
    PipelineRun,
    PipelineStatus,
    President,
    RepCampaignPromise,
    RepDonor,
    RepIndustryDonation,
    RepKeyVote,
    RepLobbyingMatch,
    RepSponsoredBill,
    Representative,
    ScoreSnapshot,
    Senator,
    SiteVisit,
    SponsoredBill,
    TimelineEntry,
)
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _get_admin_token() -> str:
    return settings.ADMIN_TOKEN or settings.PIPELINE_TRIGGER_TOKEN


def require_admin(authorization: str | None = Header(default=None)) -> None:
    token = _get_admin_token()
    if not token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    expected = f"Bearer {token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _live_elapsed(run) -> float | None:
    """Elapsed seconds for a pipeline run row, computed live if still running.

    A "running" row's stored elapsed_seconds is stale (only written on
    completion), so while it's in flight we compute it against now instead.
    """
    if run.status == PipelineStatus.RUNNING and run.started_at:
        return round((utcnow() - run.started_at).total_seconds(), 1)
    return run.elapsed_seconds


def _parse_progress_steps(run) -> list | None:
    """Decode a run's progress_detail JSON column, or None if absent/invalid."""
    if not getattr(run, "progress_detail", None):
        return None
    try:
        return json.loads(run.progress_detail)
    except (json.JSONDecodeError, TypeError):
        return None


def _history_entry(run, pipeline_type: str, extra: dict) -> dict:
    """Shared fields for a pipeline_history row; `extra` adds the type-specific ones."""
    return {
        "id": run.id,
        "pipelineType": pipeline_type,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        "status": run.status,
        "elapsedSeconds": run.elapsed_seconds,
        "errorMessage": run.error_message,
        **extra,
    }


def _clear_stuck_runs(db: Session, model, is_running: bool, pipeline_label: str) -> dict:
    """Mark any stuck (status=running) run of `model` as failed.

    Shared by the House and Stock Trades "clear stuck run" admin endpoints —
    use when the in-memory flag says idle but the DB record still shows
    running (e.g. after a container restart mid-run).
    """
    if is_running:
        raise HTTPException(
            status_code=409, detail=f"{pipeline_label} pipeline is actively running — stop it first"
        )

    stuck = db.query(model).filter(model.status == PipelineStatus.RUNNING).all()
    if not stuck:
        return {"cleared": 0, "message": "No stuck runs found"}

    now = utcnow()
    for run in stuck:
        run.status = PipelineStatus.FAILED
        run.error_message = "Cleared by admin (container restart)"
        run.completed_at = now
        if run.started_at:
            run.elapsed_seconds = round((now - run.started_at).total_seconds(), 1)
    db.commit()
    return {"cleared": len(stuck), "message": f"Marked {len(stuck)} run(s) as failed"}


@router.post("/auth")
async def admin_auth(authorization: str | None = Header(default=None)):
    """Validate an admin token. Returns 200 on success, 401 on failure."""
    require_admin(authorization)
    return {"status": "authenticated"}


def _read_system_stats() -> dict:
    """Read host-level system stats from /proc and /sys (works in Docker)."""
    stats: dict = {}

    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        stats["loadAvg"] = [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        stats["loadAvg"] = None

    try:
        stats["cpuCount"] = os.cpu_count() or 1
    except Exception:
        stats["cpuCount"] = 1

    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                k, _, v = line.partition(":")
                meminfo[k.strip()] = int(v.strip().split()[0]) * 1024
        stats["memTotalBytes"] = meminfo.get("MemTotal", 0)
        stats["memAvailableBytes"] = meminfo.get("MemAvailable", 0)
        stats["memUsedBytes"] = stats["memTotalBytes"] - stats["memAvailableBytes"]
        stats["memUsedPct"] = round(
            stats["memUsedBytes"] / stats["memTotalBytes"] * 100, 1
        ) if stats["memTotalBytes"] else 0
    except Exception:
        stats["memTotalBytes"] = 0
        stats["memAvailableBytes"] = 0
        stats["memUsedBytes"] = 0
        stats["memUsedPct"] = 0

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            stats["cpuTempC"] = round(int(f.read().strip()) / 1000, 1)
    except Exception:
        stats["cpuTempC"] = None

    try:
        st = os.statvfs("/data")
        stats["diskTotalBytes"] = st.f_frsize * st.f_blocks
        stats["diskUsedBytes"] = st.f_frsize * (st.f_blocks - st.f_bfree)
        stats["diskFreeBytes"] = st.f_frsize * st.f_bavail
        stats["diskUsedPct"] = round(
            stats["diskUsedBytes"] / stats["diskTotalBytes"] * 100, 1
        ) if stats["diskTotalBytes"] else 0
    except Exception:
        stats["diskTotalBytes"] = 0
        stats["diskUsedBytes"] = 0
        stats["diskFreeBytes"] = 0
        stats["diskUsedPct"] = 0

    try:
        with open("/proc/uptime") as f:
            stats["uptimeSeconds"] = int(float(f.read().split()[0]))
    except Exception:
        stats["uptimeSeconds"] = None

    try:
        rx_total = 0
        tx_total = 0
        for iface_dir in ("/host/net/eth0", "/host/net/docker-br"):
            if not os.path.isdir(iface_dir):
                continue
            try:
                with open(os.path.join(iface_dir, "rx_bytes")) as f:
                    rx_total += int(f.read().strip())
                with open(os.path.join(iface_dir, "tx_bytes")) as f:
                    tx_total += int(f.read().strip())
            except (OSError, ValueError):
                pass
        if rx_total == 0 and tx_total == 0:
            with open("/proc/net/dev") as f:
                for line in f:
                    line = line.strip()
                    if ":" not in line or line.startswith("Inter") or line.startswith("face"):
                        continue
                    iface, data = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue
                    cols = data.split()
                    rx_total += int(cols[0])
                    tx_total += int(cols[8])
        stats["netRxBytes"] = rx_total
        stats["netTxBytes"] = tx_total
    except Exception:
        stats["netRxBytes"] = 0
        stats["netTxBytes"] = 0

    return stats


def _collect_vector_db_stats(db: Session) -> dict:
    """Collect comprehensive vector DB and learning store metrics."""
    stats: dict = {}
    try:
        from app.pipeline.vector_store import (
            EMBEDDING_DIMENSIONS,
            EMBEDDING_MODEL_NAME,
            collection_stats,
            get_model_version,
        )
        vec_stats = collection_stats()
        stats["status"] = "ok"
        stats.update(vec_stats)
        stats["embeddingModel"] = EMBEDDING_MODEL_NAME
        stats["embeddingModelVersion"] = get_model_version()
        stats["embeddingDimensions"] = EMBEDDING_DIMENSIONS

    except Exception:
        logger.exception("Vector DB stats collection failed")
        stats = {"status": "unavailable", "error": "collection failed — see server logs"}

    # Learning store metrics (always attempt even if chroma is down)
    try:
        total_learned = db.query(func.count(LearnedClassification.entity_name)).scalar() or 0
        by_source = dict(
            db.query(LearnedClassification.source, func.count(LearnedClassification.entity_name))
            .group_by(LearnedClassification.source).all()
        )
        by_type = dict(
            db.query(LearnedClassification.entity_type, func.count(LearnedClassification.entity_name))
            .group_by(LearnedClassification.entity_type).all()
        )
        avg_confidence = db.query(func.avg(LearnedClassification.confidence)).scalar()
        confidence_buckets_raw = (
            db.query(
                func.round(LearnedClassification.confidence, 1).label("bucket"),
                func.count(LearnedClassification.entity_name),
            )
            .group_by("bucket")
            .order_by("bucket")
            .all()
        )
        confidence_dist = {str(round(float(b), 1)): c for b, c in confidence_buckets_raw}

        newest = db.query(func.max(LearnedClassification.learned_at)).scalar()
        oldest = db.query(func.min(LearnedClassification.learned_at)).scalar()

        stats["learningStore"] = {
            "totalEntries": total_learned,
            "bySource": by_source,
            "byType": by_type,
            "avgConfidence": round(float(avg_confidence), 3) if avg_confidence else None,
            "confidenceDistribution": confidence_dist,
            "newestEntry": newest.isoformat() if newest else None,
            "oldestEntry": oldest.isoformat() if oldest else None,
        }
    except Exception:
        logger.exception("Learning store metrics collection failed")
        stats["learningStore"] = {"error": "collection failed — see server logs"}

    return stats


@router.get("/system/stats", dependencies=[Depends(require_admin)])
async def admin_system_stats():
    """Lightweight endpoint for live system metrics polling."""
    return _read_system_stats()


@router.get("/visitor-stats", dependencies=[Depends(require_admin)])
def admin_visitor_stats(days: int = 30, db: Session = Depends(get_visits_db)) -> list[dict]:
    """Daily unique-visitor counts for the last N days, oldest first.

    Counts rows in SiteVisit (one per unique visitor per day, keyed by a
    salted daily-rotating hash — see models.py) — never raw IPs.
    """
    rows = (
        db.query(SiteVisit.date, func.count(SiteVisit.visitor_hash))
        .group_by(SiteVisit.date)
        .order_by(SiteVisit.date.desc())
        .limit(days)
        .all()
    )
    return [{"date": d, "uniqueVisitors": n} for d, n in reversed(rows)]


@router.get("/visitor-breakdown", dependencies=[Depends(require_admin)])
def admin_visitor_breakdown(date: str | None = None, db: Session = Depends(get_visits_db)) -> dict:
    """Browser/OS/device-type counts for one day (default today, UTC).

    Aggregate counts only — never joined back to individual visitor_hash
    rows in the response, so this can't be used to profile a single visit.
    """
    from datetime import UTC as _UTC

    day = date or datetime.now(_UTC).date().isoformat()

    def _counts(column):
        # Rows written before this column existed default to '' (the
        # ALTER TABLE ADD COLUMN default), not the "Other" the app itself
        # writes for an unrecognized UA — normalize both to the same
        # group so they don't show up as two separate "Other" rows.
        normalized = func.coalesce(func.nullif(column, ""), "Other")
        rows = (
            db.query(normalized, func.count(SiteVisit.visitor_hash))
            .filter(SiteVisit.date == day)
            .group_by(normalized)
            .order_by(func.count(SiteVisit.visitor_hash).desc())
            .all()
        )
        return [{"name": name, "count": n} for name, n in rows]

    return {
        "date": day,
        "browsers": _counts(SiteVisit.browser),
        "os": _counts(SiteVisit.os),
        "devices": _counts(SiteVisit.device_type),
    }


@router.get("/top-pages", dependencies=[Depends(require_admin)])
def admin_top_pages(days: int = 7, limit: int = 10, db: Session = Depends(get_visits_db)) -> list[dict]:
    """Most-visited page templates over the last N days, by raw view count.

    Counts PageView rows (every page view, not deduped by visitor — see
    models.py for why that's a separate table from SiteVisit) grouped by
    normalized route template (e.g. "/politicians/[id]").
    """
    from datetime import timedelta, UTC as _UTC

    cutoff = (datetime.now(_UTC).date() - timedelta(days=days - 1)).isoformat()
    rows = (
        db.query(PageView.path, func.sum(PageView.count).label("total"))
        .filter(PageView.date >= cutoff)
        .group_by(PageView.path)
        .order_by(func.sum(PageView.count).desc())
        .limit(limit)
        .all()
    )
    return [{"path": p, "views": int(n)} for p, n in rows]


_VACANCY_REASONS = {"deceased", "resigned", "expelled"}


@router.post("/politicians/{politician_id}/vacancy", dependencies=[Depends(require_admin)])
async def admin_set_vacancy(
    politician_id: str,
    is_current: bool = Query(...),
    reason: str | None = Query(default=None),
    left_office_date: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Mark a senator/representative's seat vacant, or restore it.

    An override on top of the automatic nightly roster reconciliation in
    pipeline/member_lifecycle.py — use it to record a departure before the
    roster catches up, or to correct one it got wrong. Note that the next
    reconciliation re-derives both fields from the roster, so restoring
    someone the roster still lists as gone will not stick. Historical
    scores/data are never touched here, only the vacancy fields.

    Marking someone vacant also starts their removal clock: once
    left_office_date is more than RETIREMENT_GRACE_DAYS old, the nightly
    purge deletes them and their child rows for good.
    """
    if reason is not None and reason not in _VACANCY_REASONS:
        raise HTTPException(status_code=400, detail=f"reason must be one of {sorted(_VACANCY_REASONS)}")

    # Validated, not merely stored: member_lifecycle's purge compares this
    # field as a string against a YYYY-MM-DD cutoff, so a plausible-looking
    # typo ("2026", "07/01/2026") sorts below every real cutoff and would
    # delete the member on the next nightly run. The purge re-stamps
    # anything malformed rather than acting on it, but the value should
    # never get that far.
    if left_office_date is not None:
        try:
            datetime.strptime(left_office_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="left_office_date must be YYYY-MM-DD",
            ) from None

    entity = db.query(Senator).filter(Senator.id == politician_id).first()
    if entity is None:
        entity = db.query(Representative).filter(Representative.id == politician_id).first()
    if entity is None:
        raise HTTPException(status_code=404, detail="Senator or representative not found")

    entity.is_current = is_current
    entity.vacancy_reason = reason if not is_current else None
    entity.left_office_date = left_office_date if not is_current else None
    db.commit()

    return {
        "id": entity.id,
        "name": entity.name,
        "isCurrent": entity.is_current,
        "vacancyReason": entity.vacancy_reason,
        "leftOfficeDate": entity.left_office_date,
    }


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def admin_dashboard(db: Session = Depends(get_db)):
    """Comprehensive admin dashboard with system health, data stats, and pipeline info."""

    # --- System health ---
    db_status = "ok"
    try:
        db.execute(func.count(Senator.id))
    except Exception:
        db_status = "unavailable"

    ollama_status = "unavailable"
    ollama_model = settings.OLLAMA_MODEL
    try:
        async with make_async_client(timeout=5.0) as client:
            if settings.LLM_BACKEND == "llama-server":
                resp = await client.get(f"{settings.LLAMA_SERVER_URL}/health")
            else:
                resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                ollama_status = "ok"
    except Exception:
        pass

    # --- Data counts ---
    data_counts = {
        "senators": db.query(func.count(Senator.id)).scalar() or 0,
        "senatorDonors": db.query(func.count(Donor.id)).scalar() or 0,
        "senatorIndustryDonations": db.query(func.count(IndustryDonation.id)).scalar() or 0,
        "senatorVotes": db.query(func.count(KeyVote.id)).scalar() or 0,
        "senatorLobbyingMatches": db.query(func.count(LobbyingMatch.id)).scalar() or 0,
        "senatorPromises": db.query(func.count(CampaignPromise.id)).scalar() or 0,
        "senatorBills": db.query(func.count(SponsoredBill.id)).scalar() or 0,
        "representatives": db.query(func.count(Representative.id)).scalar() or 0,
        "repDonors": db.query(func.count(RepDonor.id)).scalar() or 0,
        "repIndustryDonations": db.query(func.count(RepIndustryDonation.id)).scalar() or 0,
        "repVotes": db.query(func.count(RepKeyVote.id)).scalar() or 0,
        "repLobbyingMatches": db.query(func.count(RepLobbyingMatch.id)).scalar() or 0,
        "repPromises": db.query(func.count(RepCampaignPromise.id)).scalar() or 0,
        "repBills": db.query(func.count(RepSponsoredBill.id)).scalar() or 0,
        "presidents": db.query(func.count(President.id)).scalar() or 0,
        "justices": db.query(func.count(Justice.id)).scalar() or 0,
        "justiceVotes": db.query(func.count(JusticeVote.id)).scalar() or 0,
        "exploreDocuments": db.query(func.count(ExploreDocument.id)).scalar() or 0,
        "actionIssues": db.query(func.count(ActionIssue.id)).scalar() or 0,
        "nationalMonitors": db.query(func.count(NationalMonitor.id)).scalar() or 0,
        "monitorUpdates": db.query(func.count(MonitorUpdate.id)).scalar() or 0,
        "timelineEntries": db.query(func.count(TimelineEntry.id)).scalar() or 0,
        "scoreSnapshots": db.query(func.count(ScoreSnapshot.id)).scalar() or 0,
        "learnedClassifications": db.query(func.count(LearnedClassification.entity_name)).scalar() or 0,
        "pipelineRuns": db.query(func.count(PipelineRun.id)).scalar() or 0,
        "apiCacheEntries": db.query(func.count(ApiCache.cache_key)).scalar() or 0,
        "analysisCacheEntries": db.query(func.count(AnalysisCache.input_hash)).scalar() or 0,
    }

    # --- Database file size ---
    db_path = settings.DATABASE_URL.replace("sqlite:///", "").replace("sqlite:////", "/")
    try:
        db_size_bytes = os.path.getsize(db_path)
    except Exception:
        db_size_bytes = 0

    # --- Pipeline info ---
    last_run = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    total_runs = db.query(func.count(PipelineRun.id)).scalar() or 0
    successful_runs = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.status == PipelineStatus.COMPLETED)
        .scalar() or 0
    )
    failed_runs = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.status == PipelineStatus.FAILED)
        .scalar() or 0
    )

    from app.api.pipeline import _is_pipeline_running
    is_running = _is_pipeline_running(db)

    try:
        from app.scheduler import get_next_run_time
        next_scheduled = get_next_run_time()
    except Exception:
        next_scheduled = None

    pipeline_info = {
        "isRunning": is_running,
        "nextScheduled": next_scheduled,
        "cronSchedule": settings.PIPELINE_CRON_SCHEDULE,
        "totalRuns": total_runs,
        "successfulRuns": successful_runs,
        "failedRuns": failed_runs,
    }

    if last_run:
        dash_progress_steps = None
        if last_run.progress_detail:
            try:
                dash_progress_steps = json.loads(last_run.progress_detail)
            except (ValueError, TypeError):
                pass
        pipeline_info["lastRun"] = {
            "id": last_run.id,
            "startedAt": last_run.started_at.isoformat() if last_run.started_at else None,
            "completedAt": last_run.completed_at.isoformat() if last_run.completed_at else None,
            "status": last_run.status,
            "currentPhase": last_run.current_phase,
            "senatorsProcessed": last_run.senators_processed,
            "senatorsTotal": last_run.senators_total or 0,
            "senatorsFailed": last_run.senators_failed,
            "billsClassified": last_run.bills_classified,
            "llmCalls": last_run.llm_calls,
            "cacheHits": last_run.cache_hits,
            "cacheMisses": last_run.cache_misses,
            "elapsedSeconds": last_run.elapsed_seconds,
            "errorMessage": last_run.error_message,
            "progressSteps": dash_progress_steps,
            # Reference-senator score checks from the end of the run —
            # non-empty means scores shifted outside externally-verifiable
            # ranges and the run needs investigation before being trusted.
            "groundTruthFailures": json.loads(last_run.ground_truth_failures)
            if getattr(last_run, "ground_truth_failures", None)
            else [],
        }

    # --- Vector DB stats ---
    vector_db_stats = _collect_vector_db_stats(db)

    # --- LLM stats ---
    try:
        from app.pipeline.analyze.ollama_client import get_llm_stats
        llm_stats = get_llm_stats()
    except Exception:
        llm_stats = {}

    from app.main import PROCESS_STARTED_AT

    first_run = (
        db.query(PipelineRun.started_at)
        .order_by(PipelineRun.started_at.asc())
        .limit(1)
        .scalar()
    )

    uptime_info: dict = {
        "processStartedAt": PROCESS_STARTED_AT,
        "firstPipelineRun": first_run.isoformat() if first_run else None,
        "totalRestarts": total_runs,
    }

    from app.ops_alerts import recent_alerts

    return {
        "system": {
            "database": db_status,
            "ollama": ollama_status,
            "ollamaModel": ollama_model,
            "ollamaUrl": settings.OLLAMA_BASE_URL,
            "dbSizeBytes": db_size_bytes,
            "vectorDb": vector_db_stats,
        },
        "host": _read_system_stats(),
        "uptime": uptime_info,
        "data": data_counts,
        "pipeline": pipeline_info,
        "llm": llm_stats,
        "opsAlerts": recent_alerts(),
    }


@router.get("/pipeline/status", dependencies=[Depends(require_admin)])
async def admin_pipeline_status(db: Session = Depends(get_db)):
    """Live pipeline status for polling during a run."""
    db.expire_all()

    from app.api.pipeline import _is_pipeline_running
    from app.pipeline.house_pipeline import is_house_pipeline_running
    from app.pipeline.stock_pipeline import is_stock_pipeline_running
    from app.pipeline.supplementary_pipeline import is_supplementary_pipeline_running
    from app.pipeline.election_pipeline import is_election_pipeline_running
    from app.models import (
        ElectionPipelineRun, HousePipelineRun, StockTradesPipelineRun, SupplementaryPipelineRun,
    )
    is_running = _is_pipeline_running(db)

    last_run = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    last_house_run = (
        db.query(HousePipelineRun)
        .order_by(HousePipelineRun.started_at.desc())
        .first()
    )
    last_stock_run = (
        db.query(StockTradesPipelineRun)
        .order_by(StockTradesPipelineRun.started_at.desc())
        .first()
    )
    last_supplementary_run = (
        db.query(SupplementaryPipelineRun)
        .order_by(SupplementaryPipelineRun.started_at.desc())
        .first()
    )
    last_election_run = (
        db.query(ElectionPipelineRun)
        .order_by(ElectionPipelineRun.started_at.desc())
        .first()
    )

    result: dict = {
        "isRunning": is_running,
        "houseIsRunning": is_house_pipeline_running(),
        "stockTradesIsRunning": is_stock_pipeline_running(),
        "supplementaryIsRunning": is_supplementary_pipeline_running(),
        "electionIsRunning": is_election_pipeline_running(),
    }

    if last_supplementary_run:
        result["supplementaryLastRun"] = {
            "id": last_supplementary_run.id,
            "startedAt": last_supplementary_run.started_at.isoformat() if last_supplementary_run.started_at else None,
            "completedAt": last_supplementary_run.completed_at.isoformat() if last_supplementary_run.completed_at else None,
            "status": last_supplementary_run.status,
            "currentPhase": last_supplementary_run.current_phase,
            "exploreDocsIngested": last_supplementary_run.explore_docs_ingested,
            "justicesScored": last_supplementary_run.justices_scored,
            "justicesSkipped": last_supplementary_run.justices_skipped,
            "committeeLeadershipRefreshed": last_supplementary_run.committee_leadership_refreshed,
            "committeeLeadershipSkipped": last_supplementary_run.committee_leadership_skipped,
            "districtPviRefreshed": last_supplementary_run.district_pvi_refreshed,
            "districtPviSkipped": last_supplementary_run.district_pvi_skipped,
            "presidentsUpdated": last_supplementary_run.presidents_updated,
            "elapsedSeconds": _live_elapsed(last_supplementary_run),
            "errorMessage": last_supplementary_run.error_message,
            "progressSteps": _parse_progress_steps(last_supplementary_run),
        }

    if last_election_run:
        result["electionLastRun"] = {
            "id": last_election_run.id,
            "startedAt": last_election_run.started_at.isoformat() if last_election_run.started_at else None,
            "completedAt": last_election_run.completed_at.isoformat() if last_election_run.completed_at else None,
            "status": last_election_run.status,
            "currentPhase": last_election_run.current_phase,
            "candidatesSynced": last_election_run.candidates_synced,
            "financialsRefreshed": last_election_run.financials_refreshed,
            "coverageItemsIngested": last_election_run.coverage_items_ingested,
            "elapsedSeconds": _live_elapsed(last_election_run),
            "errorMessage": last_election_run.error_message,
            "progressSteps": _parse_progress_steps(last_election_run),
        }

    if last_stock_run:
        result["stockTradesLastRun"] = {
            "id": last_stock_run.id,
            "startedAt": last_stock_run.started_at.isoformat() if last_stock_run.started_at else None,
            "completedAt": last_stock_run.completed_at.isoformat() if last_stock_run.completed_at else None,
            "status": last_stock_run.status,
            "houseTradesIngested": last_stock_run.house_trades_ingested,
            "senateTradesIngested": last_stock_run.senate_trades_ingested,
            "presidentTradesIngested": last_stock_run.president_trades_ingested,
            "elapsedSeconds": _live_elapsed(last_stock_run),
            "errorMessage": last_stock_run.error_message,
            "progressSteps": _parse_progress_steps(last_stock_run),
        }

    if last_house_run:
        result["houseLastRun"] = {
            "id": last_house_run.id,
            "startedAt": last_house_run.started_at.isoformat() if last_house_run.started_at else None,
            "completedAt": last_house_run.completed_at.isoformat() if last_house_run.completed_at else None,
            "status": last_house_run.status,
            "repsProcessed": last_house_run.reps_processed,
            "repsTotal": last_house_run.reps_total,
            "repsFailed": last_house_run.reps_failed,
            "elapsedSeconds": _live_elapsed(last_house_run),
            "errorMessage": last_house_run.error_message,
            "groundTruthFailures": json.loads(last_house_run.ground_truth_failures)
            if getattr(last_house_run, "ground_truth_failures", None)
            else [],
            "progressSteps": _parse_progress_steps(last_house_run),
        }

    if last_run:
        result["lastRun"] = {
            "id": last_run.id,
            "startedAt": last_run.started_at.isoformat() if last_run.started_at else None,
            "completedAt": last_run.completed_at.isoformat() if last_run.completed_at else None,
            "status": last_run.status,
            "currentPhase": last_run.current_phase,
            "senatorsProcessed": last_run.senators_processed,
            "senatorsTotal": last_run.senators_total or 0,
            "senatorsFailed": last_run.senators_failed,
            "billsClassified": last_run.bills_classified,
            "llmCalls": last_run.llm_calls,
            "cacheHits": last_run.cache_hits,
            "cacheMisses": last_run.cache_misses,
            "elapsedSeconds": _live_elapsed(last_run),
            "errorMessage": last_run.error_message,
            "progressSteps": _parse_progress_steps(last_run),
        }

    from app.pipeline.analyze.action_center import get_action_refresh_state
    ac = get_action_refresh_state()
    result["actionRefresh"] = {
        "isRunning": ac["is_running"],
        "stage": ac["stage"],
        "stageDetail": ac["stage_detail"],
        "startedAt": ac["started_at"].isoformat() if ac["started_at"] else None,
        "lastCompletedAt": ac["last_completed_at"].isoformat() if ac["last_completed_at"] else None,
        "lastIssuesCreated": ac["last_issues_created"],
        "lastIssuesRetired": ac["last_issues_retired"],
        "lastStoriesGenerated": ac["last_stories_generated"],
        "lastBskyPosted": ac["last_bsky_posted"],
        "lastElapsed": ac["last_elapsed"],
    }

    return result


@router.get("/pipeline/history", dependencies=[Depends(require_admin)])
async def admin_pipeline_history(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return recent pipeline run history (Senate + supplementary + House + stock trades + election interleaved by date)."""
    from app.models import (
        ElectionPipelineRun, HousePipelineRun, StockTradesPipelineRun, SupplementaryPipelineRun,
    )
    senate_runs = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )
    house_runs = (
        db.query(HousePipelineRun)
        .order_by(HousePipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )
    stock_runs = (
        db.query(StockTradesPipelineRun)
        .order_by(StockTradesPipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )
    supplementary_runs = (
        db.query(SupplementaryPipelineRun)
        .order_by(SupplementaryPipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )
    election_runs = (
        db.query(ElectionPipelineRun)
        .order_by(ElectionPipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )

    senate_entries = [
        _history_entry(r, "senate", {
            "currentPhase": r.current_phase,
            "senatorsProcessed": r.senators_processed,
            "senatorsTotal": r.senators_total or 0,
            "senatorsFailed": r.senators_failed,
            "billsClassified": r.bills_classified,
            "llmCalls": r.llm_calls,
            "cacheHits": r.cache_hits,
            "cacheMisses": r.cache_misses,
            "progressSteps": _parse_progress_steps(r),
        })
        for r in senate_runs
    ]
    house_entries = [
        _history_entry(r, "house", {
            "repsProcessed": r.reps_processed,
            "repsTotal": r.reps_total,
            "repsFailed": r.reps_failed,
            "progressSteps": _parse_progress_steps(r),
        })
        for r in house_runs
    ]
    stock_entries = [
        _history_entry(r, "stock_trades", {
            "houseTradesIngested": r.house_trades_ingested,
            "senateTradesIngested": r.senate_trades_ingested,
            "presidentTradesIngested": r.president_trades_ingested,
            "progressSteps": _parse_progress_steps(r),
        })
        for r in stock_runs
    ]
    supplementary_entries = [
        _history_entry(r, "supplementary", {
            "currentPhase": r.current_phase,
            "exploreDocsIngested": r.explore_docs_ingested,
            "justicesScored": r.justices_scored,
            "justicesSkipped": r.justices_skipped,
            "committeeLeadershipRefreshed": r.committee_leadership_refreshed,
            "committeeLeadershipSkipped": r.committee_leadership_skipped,
            "districtPviRefreshed": r.district_pvi_refreshed,
            "districtPviSkipped": r.district_pvi_skipped,
            "presidentsUpdated": r.presidents_updated,
            "progressSteps": _parse_progress_steps(r),
        })
        for r in supplementary_runs
    ]
    election_entries = [
        _history_entry(r, "election", {
            "currentPhase": r.current_phase,
            "candidatesSynced": r.candidates_synced,
            "financialsRefreshed": r.financials_refreshed,
            "coverageItemsIngested": r.coverage_items_ingested,
            "progressSteps": _parse_progress_steps(r),
        })
        for r in election_runs
    ]

    # Each pipeline type's own query above is already capped at `limit` —
    # don't re-truncate the interleaved union down to that same `limit`.
    # Senate/House run far more often than Stock Trades/Supplementary (daily
    # vs. every few days), so a shared cap on the combined list silently
    # starves the infrequent ones out of the visible history entirely once
    # enough Senate/House runs accumulate (found 2026-07-23: Stock Trades'
    # only entry in the last 20 combined rows was one run-length away from
    # falling off, inflated further by the deploy-race bug's repeated
    # stale/failed Senate retries — see check-and-deploy.sh). Returning the
    # full union (bounded at 4x `limit` by the per-type queries) guarantees
    # every pipeline type keeps its own most recent `limit` runs visible.
    return sorted(
        senate_entries + house_entries + stock_entries + supplementary_entries + election_entries,
        key=lambda x: x["startedAt"] or "",
        reverse=True,
    )


# Run-model __tablename__ -> the pipelineType label already used by
# /pipeline/history, so both endpoints name the same pipeline the same way.
PHASE_TIMING_KINDS = {
    "pipeline_runs": "senate",
    "house_pipeline_runs": "house",
    "supplementary_pipeline_runs": "supplementary",
    "stock_trades_pipeline_runs": "stock_trades",
    "election_pipeline_runs": "election",
}


@router.get("/pipeline/timings", dependencies=[Depends(require_admin)])
async def admin_pipeline_timings(
    kind: str = Query(default="pipeline_runs"),
    runs: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Per-phase durations for the last `runs` runs of one pipeline.

    Answers the question a run's total elapsed_seconds cannot: when a
    pipeline's wall-clock grows, which phase absorbed it. The `phases`
    rollup groups by the coarse fetch/transform/analyze/finalize tag in
    each pipeline's STEPS definition — the split that distinguishes time
    blocked on rate-limited external APIs from local compute, and so the
    split that determines whether faster hardware could help at all.
    """
    if kind not in PHASE_TIMING_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown pipeline kind '{kind}'. Valid: {sorted(PHASE_TIMING_KINDS)}",
        )

    recent_run_ids = [
        r[0]
        for r in db.query(PipelinePhaseTiming.run_id)
        .filter(PipelinePhaseTiming.run_kind == kind)
        .distinct()
        .order_by(PipelinePhaseTiming.run_id.desc())
        .limit(runs)
        .all()
    ]
    if not recent_run_ids:
        return {"kind": kind, "pipelineType": PHASE_TIMING_KINDS[kind], "runs": [], "phaseTrend": {}}

    rows = (
        db.query(PipelinePhaseTiming)
        .filter(
            PipelinePhaseTiming.run_kind == kind,
            PipelinePhaseTiming.run_id.in_(recent_run_ids),
        )
        .order_by(PipelinePhaseTiming.run_id.desc(), PipelinePhaseTiming.id.asc())
        .all()
    )

    by_run: dict[int, list] = {}
    for row in rows:
        by_run.setdefault(row.run_id, []).append(row)

    run_entries = []
    phase_trend: dict[str, list] = {}
    for run_id in recent_run_ids:
        steps = by_run.get(run_id, [])
        # A step still in flight, or one whose start timestamp was lost to a
        # mid-run restart, has no duration — counting it as 0 would understate
        # the total, so it is reported separately rather than silently folded in.
        timed = [s for s in steps if s.duration_seconds is not None]
        total = round(sum(s.duration_seconds for s in timed), 1)

        phase_totals: dict[str, dict] = {}
        for step in timed:
            bucket = phase_totals.setdefault(step.phase or "", {"seconds": 0.0, "steps": 0})
            bucket["seconds"] += step.duration_seconds
            bucket["steps"] += 1

        phases = [
            {
                "phase": phase,
                "seconds": round(data["seconds"], 1),
                "pct": round(100.0 * data["seconds"] / total, 1) if total else 0.0,
                "steps": data["steps"],
            }
            for phase, data in sorted(
                phase_totals.items(), key=lambda kv: kv[1]["seconds"], reverse=True
            )
        ]
        for entry in phases:
            phase_trend.setdefault(entry["phase"], []).append(
                {"runId": run_id, "seconds": entry["seconds"]}
            )

        starts = [s.started_at for s in steps if s.started_at]
        ends = [s.completed_at for s in steps if s.completed_at]
        run_entries.append({
            "runId": run_id,
            "startedAt": min(starts).isoformat() if starts else None,
            "completedAt": max(ends).isoformat() if ends else None,
            "totalSeconds": total,
            "untimedSteps": len(steps) - len(timed),
            "phases": phases,
            "steps": [
                {
                    "stepKey": s.step_key,
                    "label": s.label,
                    "phase": s.phase,
                    "status": s.status,
                    "seconds": s.duration_seconds,
                }
                for s in sorted(
                    steps,
                    key=lambda s: (s.duration_seconds is None, -(s.duration_seconds or 0)),
                )
            ],
        })

    return {
        "kind": kind,
        "pipelineType": PHASE_TIMING_KINDS[kind],
        "runs": run_entries,
        "phaseTrend": phase_trend,
    }


@router.post("/pipeline/trigger", dependencies=[Depends(require_admin)])
async def admin_trigger_pipeline(
    senator: str | None = Query(default=None),
    fetch_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Trigger a pipeline run from the admin panel."""
    from app.api.pipeline import _is_pipeline_running
    from app.pipeline.senate_pipeline import run_senate_pipeline

    if _is_pipeline_running(db):
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    async def _run_pipelines():
        from app.pipeline.house_pipeline import run_house_pipeline
        from app.pipeline.supplementary_pipeline import run_supplementary_pipeline
        result = await run_senate_pipeline(senator_filter=senator, fetch_only=fetch_only)
        if senator is None and not fetch_only and result.get("status") not in ("skipped", "failed"):
            logger.info("Senate pipeline done — starting supplementary pipeline")
            await run_supplementary_pipeline()
            logger.info("Supplementary pipeline done — starting House pipeline")
            await run_house_pipeline()

    run_pipeline_in_thread(
        _run_pipelines, name="pipeline-run", error_label="Admin-triggered pipeline run failed",
    )
    return {
        "message": "Pipeline triggered",
        "senatorFilter": senator,
        "fetchOnly": fetch_only,
    }


@router.post("/pipeline/reembed-explore", dependencies=[Depends(require_admin)])
async def admin_reembed_explore(db: Session = Depends(get_db)):
    """Rebuild every search structure over the explore corpus.

    Use this after changing the embedding model, or any time search results
    look inconsistent with the documents actually in the table, to rebuild
    without running the full ingest pipeline. All three structures are
    derived from `explore_documents`, so all three are rebuilt together —
    rebuilding only the embeddings is how the vector index and the keyword
    index end up disagreeing about what exists.
    """
    from app.models import ExploreDocument
    from app.pipeline.analyze.document_authority import update_document_authority
    from app.pipeline.lexical_index import rebuild_index
    from app.pipeline.vector_store import (
        _write_model_version,
        clear_explore,
        embed_explore_documents,
    )

    try:
        clear_explore()
    except Exception:
        pass

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

    def _run():
        count = embed_explore_documents(doc_dicts)
        _write_model_version()
        return count

    count = await asyncio.to_thread(_run)
    indexed = await asyncio.to_thread(rebuild_index, db)
    authority = await asyncio.to_thread(update_document_authority, db)
    return {"embedded": count, "keywordIndexed": indexed, "authority": authority}


@router.post("/pipeline/trigger-house", dependencies=[Depends(require_admin)])
async def admin_trigger_house_pipeline(db: Session = Depends(get_db)):
    """Trigger a House representative pipeline run."""
    from app.pipeline.house_pipeline import run_house_pipeline

    run_pipeline_in_thread(
        run_house_pipeline, name="house-pipeline-run", error_label="House pipeline run failed",
    )
    return {"message": "House pipeline triggered"}


@router.post("/pipeline/clear-stuck-house", dependencies=[Depends(require_admin)])
async def admin_clear_stuck_house(db: Session = Depends(get_db)):
    """Mark any stuck (status=running) house pipeline run as failed.

    Use when the in-memory flag says idle but the DB record still shows running
    (e.g. after a container restart mid-run).
    """
    from app.models import HousePipelineRun
    from app.pipeline.house_pipeline import is_house_pipeline_running

    return _clear_stuck_runs(db, HousePipelineRun, is_house_pipeline_running(), "House")


@router.post("/pipeline/clear-stuck-stock-trades", dependencies=[Depends(require_admin)])
async def admin_clear_stuck_stock_trades(db: Session = Depends(get_db)):
    """Mark any stuck (status=running) stock-trades pipeline run as failed.

    Use when the in-memory flag says idle but the DB record still shows
    running (e.g. after a container restart mid-run).
    """
    from app.models import StockTradesPipelineRun
    from app.pipeline.stock_pipeline import is_stock_pipeline_running

    return _clear_stuck_runs(db, StockTradesPipelineRun, is_stock_pipeline_running(), "Stock trades")


@router.post("/pipeline/trigger-supplementary", dependencies=[Depends(require_admin)])
async def admin_trigger_supplementary_pipeline(db: Session = Depends(get_db)):
    """Trigger a supplementary (explore docs/SCOTUS/presidents) pipeline run."""
    from app.pipeline.supplementary_pipeline import run_supplementary_pipeline

    run_pipeline_in_thread(
        run_supplementary_pipeline,
        name="supplementary-pipeline-run",
        error_label="Supplementary pipeline run failed",
    )
    return {"message": "Supplementary pipeline triggered"}


@router.post("/pipeline/clear-stuck-supplementary", dependencies=[Depends(require_admin)])
async def admin_clear_stuck_supplementary(db: Session = Depends(get_db)):
    """Mark any stuck (status=running) supplementary pipeline run as failed.

    Use when the in-memory flag says idle but the DB record still shows
    running (e.g. after a container restart mid-run).
    """
    from app.models import SupplementaryPipelineRun
    from app.pipeline.supplementary_pipeline import is_supplementary_pipeline_running

    return _clear_stuck_runs(db, SupplementaryPipelineRun, is_supplementary_pipeline_running(), "Supplementary")


@router.post("/pipeline/trigger-election", dependencies=[Depends(require_admin)])
async def admin_trigger_election_pipeline(db: Session = Depends(get_db)):
    """Trigger a midterm-elections pipeline run (candidate roster,
    financials, coverage ingestion, Bluesky posting)."""
    from app.pipeline.election_pipeline import run_election_pipeline

    run_pipeline_in_thread(
        run_election_pipeline,
        name="election-pipeline-run",
        error_label="Election pipeline run failed",
    )
    return {"message": "Election pipeline triggered"}


@router.post("/pipeline/clear-stuck-election", dependencies=[Depends(require_admin)])
async def admin_clear_stuck_election(db: Session = Depends(get_db)):
    """Mark any stuck (status=running) election pipeline run as failed.

    Use when the in-memory flag says idle but the DB record still shows
    running (e.g. after a container restart mid-run).
    """
    from app.models import ElectionPipelineRun
    from app.pipeline.election_pipeline import is_election_pipeline_running

    return _clear_stuck_runs(db, ElectionPipelineRun, is_election_pipeline_running(), "Election")


@router.post("/data/reset", dependencies=[Depends(require_admin)])
async def admin_reset_data(db: Session = Depends(get_db)):
    """Wipe all pipeline-generated data for a clean start.

    Clears every table (senators, votes, donors, learning store, caches,
    ChromaDB), then re-seeds static reference data. The next pipeline run
    will rebuild everything from scratch with the latest code.
    """
    from app.api.pipeline import _is_pipeline_running

    if _is_pipeline_running(db):
        raise HTTPException(
            status_code=409,
            detail="Cannot reset while the pipeline is running",
        )

    from app.database import reset_all_data

    summary = reset_all_data()
    total_rows = sum(v for k, v in summary.items() if isinstance(v, int))
    return {
        "status": "reset_complete",
        "rowsDeleted": total_rows,
        "details": summary,
    }


# Counters that represent a story being dropped rather than published.
# Grouped so the endpoint below can answer the question these counters
# exist for — "did the pipeline see nothing, or see something and drop
# it?" — without the caller having to know which of the ten-odd gates in
# the action pipeline is which.
_SUPPRESSION_COUNTERS = (
    "issues_skipped_grounding",
    "issues_skipped_role_check",
    "issues_skipped_too_few_facts",
    "issues_skipped_duplicate_title",
    "issues_skipped_no_action_surface",
    "bsky_reposts_suppressed_no_new_information",
    "bsky_posts_suppressed_near_duplicate",
    "bsky_post_grounding_rejections",
)

_INTAKE_COUNTERS = ("articles_fetched", "articles_policy_relevant", "clusters_considered")

_OUTPUT_COUNTERS = ("issues_new_topic", "issues_matched_existing", "bsky_reposts_allowed")

_GROUPED_COUNTERS = frozenset(_INTAKE_COUNTERS + _OUTPUT_COUNTERS + _SUPPRESSION_COUNTERS)


def _coerce_counts(raw) -> dict[str, int] | None:
    """A run's counter payload as {name: int}, or None if unusable.

    Non-integer values are dropped rather than coerced: these are counts,
    and a payload where one isn't a number is corrupt in a way worth
    ignoring rather than guessing at. bool is excluded explicitly because
    it is an int subclass and would otherwise sum as 0/1.
    """
    if not isinstance(raw, dict):
        return None
    return {
        str(k): v for k, v in raw.items()
        if isinstance(v, int) and not isinstance(v, bool)
    }


@router.get("/action-metrics", dependencies=[Depends(require_admin)])
async def admin_action_metrics(
    limit: int = Query(48, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """Per-run Action Center validator counters, newest run first.

    The action pipeline records these on every refresh but nothing read
    them back, so the question they answer — is the platform quiet
    because the news is quiet, or because a validator is dropping
    everything? — could only be settled by opening a shell on the box and
    querying api_cache by hand. Every gate in the pipeline fails closed,
    which is the right default for a platform that publishes under its own
    name, but it means silence is the failure mode for roughly ten
    independent checks and none of them is louder than the others.

    ``runs`` is the raw per-run counter set. ``totals`` sums the window,
    split into intake (what arrived), output (what published), and
    suppressed (what was dropped, by which gate) — a window with healthy
    intake and near-zero output is a suppression problem; one where
    intake itself is near zero is a quiet news cycle or a broken feed.
    Anything the pipeline records that isn't in those three groups lands
    in ``other`` rather than being dropped: a counter this endpoint has
    never heard of is exactly the one worth seeing, and silently omitting
    it would reproduce the blind spot the endpoint exists to remove.

    Runs are hourly, so gaps in ``runs`` are themselves a signal: a
    refresh that crashed or was still holding the lock leaves no row.
    """
    rows = (
        db.query(ApiCache)
        .filter(ApiCache.tier == "action-metrics")
        .order_by(ApiCache.cached_at.desc())
        .limit(limit)
        .all()
    )

    runs = []
    for row in rows:
        try:
            payload = json.loads(row.data_json)
        except (ValueError, TypeError):
            continue  # a malformed row shouldn't blank the whole report
        # Shape is checked, not assumed: a diagnostic endpoint that 500s
        # on one bad row fails precisely when it's being reached for.
        counts = _coerce_counts(payload.get("counts")) if isinstance(payload, dict) else None
        if counts is None:
            continue
        runs.append({
            "run": row.cache_key,
            "recordedAt": row.cached_at.isoformat() if row.cached_at else None,
            "counts": counts,
        })

    def _sum(keys) -> dict:
        return {
            k: sum(r["counts"].get(k, 0) for r in runs)
            for k in keys
            if any(r["counts"].get(k) for r in runs)
        }

    suppressed = _sum(_SUPPRESSION_COUNTERS)
    ungrouped = sorted({
        k for r in runs for k in r["counts"] if k not in _GROUPED_COUNTERS
    })
    return {
        "runs": runs,
        "totals": {
            "intake": _sum(_INTAKE_COUNTERS),
            "output": _sum(_OUTPUT_COUNTERS),
            "suppressed": suppressed,
            "suppressedTotal": sum(suppressed.values()),
            "other": _sum(ungrouped),
        },
        "runsReturned": len(runs),
    }


@router.get("/classification/health", dependencies=[Depends(require_admin)])
async def admin_classification_health(db: Session = Depends(get_db)):
    """Classification system health metrics for monitoring adaptive learning."""
    from app.pipeline.analyze.bill_learning import get_health_metrics
    return get_health_metrics(db)


@router.get("/score-calibration", dependencies=[Depends(require_admin)])
async def get_score_calibration(entity_type: str = "senator"):
    """Score distribution monitoring across consecutive pipeline runs.

    Compares distributions between the two most recent snapshot dates.
    Drift events are logged automatically during each pipeline run; this
    endpoint surfaces the latest comparison for observability.

    Query params:
      entity_type: ``senator`` (default) or ``representative``
    """
    from app.pipeline.analyze.score_calibration import generate_calibration_report

    if entity_type not in ("senator", "representative"):
        raise HTTPException(
            status_code=400,
            detail="entity_type must be 'senator' or 'representative'",
        )

    report = generate_calibration_report(entity_type)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Fewer than 2 snapshot dates exist for entity_type={entity_type!r}",
        )
    return report


