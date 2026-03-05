"""Admin API — protected management endpoints for dashboard, pipeline control, and metrics."""

import asyncio
import logging
import os
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    AnalysisCache,
    ApiCache,
    CampaignPromise,
    Donor,
    ExploreDocument,
    IndustryDonation,
    KeyVote,
    LearnedClassification,
    LobbyingMatch,
    PipelineRun,
    President,
    Senator,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _get_admin_token() -> str:
    return settings.ADMIN_TOKEN or settings.PIPELINE_TRIGGER_TOKEN


def require_admin(authorization: str | None = Header(default=None)) -> None:
    token = _get_admin_token()
    if not token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


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
            get_chroma_client,
            get_model_version,
            EMBEDDING_MODEL_NAME,
            EMBEDDING_DIMENSIONS,
        )
        chroma = get_chroma_client()
        collections = chroma.list_collections()
        total_vectors = 0
        collection_details = []
        for col in collections:
            count = col.count()
            total_vectors += count
            meta = col.metadata or {}
            detail: dict = {
                "name": col.name,
                "count": count,
                "metadata": {k: str(v) for k, v in meta.items()} if meta else {},
            }
            if count > 0:
                peek = col.peek(1)
                if peek and peek.get("metadatas") and peek["metadatas"][0]:
                    detail["sampleMetadataKeys"] = sorted(peek["metadatas"][0].keys())
            collection_details.append(detail)

        chroma_path = "/data/chroma"
        chroma_size = 0
        for dirpath, _, filenames in os.walk(chroma_path):
            for f in filenames:
                try:
                    chroma_size += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass

        stats["status"] = "ok"
        stats["totalVectors"] = total_vectors
        stats["sizeBytes"] = chroma_size
        stats["collections"] = collection_details
        stats["embeddingModel"] = EMBEDDING_MODEL_NAME
        stats["embeddingModelVersion"] = get_model_version()
        stats["embeddingDimensions"] = EMBEDDING_DIMENSIONS

    except Exception as e:
        stats = {"status": "unavailable", "error": str(e)}

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
    except Exception as e:
        stats["learningStore"] = {"error": str(e)}

    return stats


@router.get("/system/stats", dependencies=[Depends(require_admin)])
async def admin_system_stats():
    """Lightweight endpoint for live system metrics polling."""
    return _read_system_stats()


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def admin_dashboard(db: Session = Depends(get_db)):
    """Comprehensive admin dashboard with system health, data stats, and pipeline info."""
    import httpx

    # --- System health ---
    db_status = "ok"
    try:
        db.execute(func.count(Senator.id))
    except Exception:
        db_status = "unavailable"

    ollama_status = "unavailable"
    ollama_model = settings.OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
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
        "presidents": db.query(func.count(President.id)).scalar() or 0,
        "donors": db.query(func.count(Donor.id)).scalar() or 0,
        "industryDonations": db.query(func.count(IndustryDonation.id)).scalar() or 0,
        "keyVotes": db.query(func.count(KeyVote.id)).scalar() or 0,
        "lobbyingMatches": db.query(func.count(LobbyingMatch.id)).scalar() or 0,
        "campaignPromises": db.query(func.count(CampaignPromise.id)).scalar() or 0,
        "exploreDocuments": db.query(func.count(ExploreDocument.id)).scalar() or 0,
        "learnedClassifications": db.query(func.count(LearnedClassification.entity_name)).scalar() or 0,
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
        .filter(PipelineRun.status == "completed")
        .scalar() or 0
    )
    failed_runs = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.status == "failed")
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
                import json as _json
                dash_progress_steps = _json.loads(last_run.progress_detail)
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
        }

    # --- Vector DB stats ---
    vector_db_stats = _collect_vector_db_stats(db)

    # --- LLM stats ---
    try:
        from app.pipeline.analyze.ollama_client import get_llm_stats
        llm_stats = get_llm_stats()
    except Exception:
        llm_stats = {}

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
        "data": data_counts,
        "pipeline": pipeline_info,
        "llm": llm_stats,
    }


@router.get("/pipeline/status", dependencies=[Depends(require_admin)])
async def admin_pipeline_status(db: Session = Depends(get_db)):
    """Live pipeline status for polling during a run."""
    db.expire_all()

    from app.api.pipeline import _is_pipeline_running
    is_running = _is_pipeline_running(db)

    last_run = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )

    result: dict = {"isRunning": is_running}
    if last_run:
        elapsed = last_run.elapsed_seconds
        if last_run.status == "running" and last_run.started_at:
            from datetime import datetime
            now = datetime.utcnow()
            elapsed = round((now - last_run.started_at).total_seconds(), 1)

        progress_steps = None
        if last_run.progress_detail:
            try:
                import json
                progress_steps = json.loads(last_run.progress_detail)
            except (json.JSONDecodeError, TypeError):
                pass

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
            "elapsedSeconds": elapsed,
            "errorMessage": last_run.error_message,
            "progressSteps": progress_steps,
        }
    return result


@router.get("/pipeline/history", dependencies=[Depends(require_admin)])
async def admin_pipeline_history(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return recent pipeline run history."""
    runs = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "startedAt": r.started_at.isoformat() if r.started_at else None,
            "completedAt": r.completed_at.isoformat() if r.completed_at else None,
            "status": r.status,
            "currentPhase": r.current_phase,
            "senatorsProcessed": r.senators_processed,
            "senatorsTotal": r.senators_total or 0,
            "senatorsFailed": r.senators_failed,
            "billsClassified": r.bills_classified,
            "llmCalls": r.llm_calls,
            "cacheHits": r.cache_hits,
            "cacheMisses": r.cache_misses,
            "elapsedSeconds": r.elapsed_seconds,
            "errorMessage": r.error_message,
        }
        for r in runs
    ]


@router.post("/pipeline/trigger", dependencies=[Depends(require_admin)])
async def admin_trigger_pipeline(
    senator: str | None = Query(default=None),
    fetch_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Trigger a pipeline run from the admin panel."""
    from app.api.pipeline import _is_pipeline_running
    from app.pipeline.orchestrator import run_full_pipeline

    if _is_pipeline_running(db):
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                run_full_pipeline(senator_filter=senator, fetch_only=fetch_only)
            )
        except BaseException:
            logger.exception("Admin-triggered pipeline run failed")
        finally:
            loop.close()

    threading.Thread(target=_run_in_thread, daemon=True, name="pipeline-run").start()
    return {
        "message": "Pipeline triggered",
        "senatorFilter": senator,
        "fetchOnly": fetch_only,
    }


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


@router.get("/classification/health", dependencies=[Depends(require_admin)])
async def admin_classification_health(db: Session = Depends(get_db)):
    """Classification system health metrics for monitoring adaptive learning."""
    from app.pipeline.analyze.bill_learning import get_health_metrics
    return get_health_metrics(db)


