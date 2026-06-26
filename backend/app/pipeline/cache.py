"""
Pipeline caching layer — two independent stores with different invalidation strategies.

API Cache (ApiCache table)
--------------------------
TTL-based. Caches raw HTTP responses from external APIs (FEC, Congress.gov, etc.)
so repeated pipeline runs don't re-fetch unchanged data. Controlled by
PIPELINE_CACHE_TTL_HOURS (default 72h). Safe to clear at any time; the next
pipeline run will re-fetch. No manual intervention needed on code changes.

LLM / Analysis Cache (AnalysisCache table)
-------------------------------------------
Version-based, no TTL. Caches parsed LLM output keyed by (prompt_version, input_hash).
Old entries for a given prompt_version remain in the table but are never read once
the version changes — they are dead weight and can be pruned with:

    DELETE FROM analysis_cache WHERE prompt_version != '<current_version>';

When to bump prompt_version in prompts.py:
  - Any change to the system prompt or user prompt template text
  - Any change to the output schema (new/removed fields)
  - Any change to how input_data is constructed (affects what gets hashed)
  - Model change (model name is included in the hash, so this auto-invalidates)

When NOT to bump:
  - Whitespace-only changes inside prompt strings
  - Changes to surrounding Python code that don't affect the prompt text

Bumping the version (e.g. "explore-doc-summary-v3" → "v4") causes all cached
results for that prompt to be ignored on the next pipeline run. The LLM is called
fresh for every document and results are stored under the new version key.
Old version rows remain in the DB until pruned — they do not affect correctness.
"""
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
