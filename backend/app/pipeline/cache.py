import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AnalysisCache, ApiCache


def api_cache_get(
    db: Session, tier: str, key: str, max_age_hours: int | None = None,
) -> dict | None:
    """Get cached API response, return None if expired or missing."""
    entry = (
        db.query(ApiCache)
        .filter(ApiCache.tier == tier, ApiCache.cache_key == key)
        .first()
    )
    if not entry:
        return None
    age = datetime.utcnow() - entry.cached_at
    ttl = max_age_hours if max_age_hours is not None else settings.PIPELINE_CACHE_TTL_HOURS
    if age > timedelta(hours=ttl):
        return None
    return json.loads(entry.data_json)


def api_cache_set(db: Session, tier: str, key: str, data) -> None:
    """Store API response in cache (upsert)."""
    entry = (
        db.query(ApiCache)
        .filter(ApiCache.tier == tier, ApiCache.cache_key == key)
        .first()
    )
    data_json = json.dumps(data, default=str)
    if entry:
        entry.data_json = data_json
        entry.cached_at = datetime.utcnow()
    else:
        entry = ApiCache(
            tier=tier,
            cache_key=key,
            data_json=data_json,
            cached_at=datetime.utcnow(),
        )
        db.add(entry)
    db.commit()


def analysis_cache_get(
    db: Session, version: str, input_hash: str
) -> dict | None:
    """Get cached analysis result (no TTL - invalidated by version change)."""
    try:
        entry = (
            db.query(AnalysisCache)
            .filter(
                AnalysisCache.prompt_version == version,
                AnalysisCache.input_hash == input_hash,
            )
            .first()
        )
        if not entry:
            return None
        return json.loads(entry.result_json)
    except Exception:
        return None


def analysis_cache_set(
    db: Session, version: str, input_hash: str, data: dict
) -> None:
    """Store analysis result in cache (upsert)."""
    entry = (
        db.query(AnalysisCache)
        .filter(
            AnalysisCache.prompt_version == version,
            AnalysisCache.input_hash == input_hash,
        )
        .first()
    )
    result_json = json.dumps(data, default=str)
    if entry:
        entry.result_json = result_json
        entry.created_at = datetime.utcnow()
    else:
        entry = AnalysisCache(
            prompt_version=version,
            input_hash=input_hash,
            result_json=result_json,
            created_at=datetime.utcnow(),
        )
        db.add(entry)
    db.commit()
