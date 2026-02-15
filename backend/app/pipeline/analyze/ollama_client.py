"""
Ollama LLM client -- replaces Gemini SDK with local Ollama HTTP API.

Uses httpx.AsyncClient to POST to Ollama's /api/generate endpoint.
Serializes requests with asyncio.Semaphore(1) since the Pi 5 can only
handle one inference at a time.
"""

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import analysis_cache_get, analysis_cache_set

logger = logging.getLogger(__name__)

# --- Module-level state ---
_semaphore = asyncio.Semaphore(1)
_total_calls = 0
_cache_hits = 0
_cache_misses = 0
_max_retries = 3


def _make_input_hash(prompt_version: str, input_data: Any) -> str:
    """Create a short hash from prompt version + input data for cache key."""
    return hashlib.sha256(
        (prompt_version + json.dumps(input_data, sort_keys=True, default=str)).encode()
    ).hexdigest()[:16]


def extract_json(text: str) -> Any | None:
    """
    Extract JSON from LLM response text.
    Tries direct parse, then markdown code block, then regex extraction.
    """
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from markdown code block
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding JSON array or object
    json_match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


async def call_llm(
    *,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    cache_key: Any,
    db_session: Session | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
) -> Any | None:
    """
    Call Ollama with structured JSON output.
    Results are cached by prompt version + input hash in the database.

    Args:
        prompt_version: Version string for caching.
        system_prompt: System instruction.
        user_prompt: User message.
        cache_key: Data used to generate the cache key.
        db_session: SQLAlchemy session for cache access.
        model: Override model (defaults to settings.OLLAMA_MODEL).
        max_tokens: Max output tokens (default 2048).

    Returns:
        Parsed JSON response, or None on failure.
    """
    global _total_calls, _cache_hits, _cache_misses

    # Check analysis cache first
    input_hash = _make_input_hash(prompt_version, cache_key)
    if db_session is not None:
        cached = analysis_cache_get(db_session, prompt_version, input_hash)
        if cached is not None:
            logger.debug("LLM cache hit: %s", prompt_version)
            _cache_hits += 1
            return cached
        _cache_misses += 1

    use_model = model or settings.OLLAMA_MODEL
    max_attempts = _max_retries + 3  # Extra retries for transient failures

    for attempt in range(1, max_attempts + 1):
        # Serialize requests -- Pi 5 can only do one inference at a time
        async with _semaphore:
            try:
                logger.debug(
                    "Ollama call: %s (attempt %d, model: %s)",
                    prompt_version,
                    attempt,
                    use_model,
                )

                payload = {
                    "model": use_model,
                    "prompt": user_prompt,
                    "system": system_prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                }

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(300.0, connect=30.0)
                ) as client:
                    response = await client.post(
                        f"{settings.OLLAMA_BASE_URL}/api/generate",
                        json=payload,
                    )
                    response.raise_for_status()

                result_data = response.json()
                text = result_data.get("response", "")

                _total_calls += 1

                # Parse JSON from response
                parsed = extract_json(text)
                if parsed is None:
                    logger.warning(
                        "Ollama returned non-JSON for %s, attempt %d",
                        prompt_version,
                        attempt,
                    )
                    if attempt < _max_retries:
                        await asyncio.sleep(2.0 * attempt)
                        continue
                    logger.error(
                        "Ollama JSON parse failed for %s. Raw: %s",
                        prompt_version,
                        text[:500],
                    )
                    return None

                # Cache the result
                if db_session is not None:
                    analysis_cache_set(
                        db_session, prompt_version, input_hash, parsed
                    )

                return parsed

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 30.0 * attempt
                    logger.warning(
                        "Ollama rate limited, waiting %.0fs...", wait
                    )
                    await asyncio.sleep(wait)
                    continue

                if attempt == max_attempts:
                    logger.error(
                        "Ollama call failed for %s: %s",
                        prompt_version,
                        str(e),
                    )
                    return None
                await asyncio.sleep(2.0 * attempt)

            except (httpx.RequestError, httpx.TimeoutException) as e:
                if attempt == max_attempts:
                    logger.error(
                        "Ollama call failed for %s: %s",
                        prompt_version,
                        str(e),
                    )
                    return None
                wait = 5.0 * attempt
                logger.warning(
                    "Ollama request error (attempt %d): %s -- retrying in %.0fs",
                    attempt,
                    str(e),
                    wait,
                )
                await asyncio.sleep(wait)

    return None


def get_llm_stats() -> dict:
    """Get LLM usage statistics."""
    return {
        "total_calls": _total_calls,
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "estimated_cost": "free (local Ollama)",
    }


def reset_stats() -> None:
    """Reset LLM call counter (useful between pipeline runs)."""
    global _total_calls, _cache_hits, _cache_misses
    _total_calls = 0
    _cache_hits = 0
    _cache_misses = 0
