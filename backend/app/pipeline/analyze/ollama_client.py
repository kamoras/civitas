"""
LLM client -- supports native llama-server (fast, ARM-optimized) and Ollama fallback.

Backend selection via settings.LLM_BACKEND: "llama-server" or "ollama".
"""

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import analysis_cache_get, analysis_cache_set
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_total_calls = 0
_cache_hits = 0
_cache_misses = 0
_max_retries = 3


def _make_input_hash(prompt_version: str, input_data: Any, model: str = "") -> str:
    raw = prompt_version + "|" + model + "|" + json.dumps(input_data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def extract_json(text: str) -> Any | None:
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    json_match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def unwrap_list(data: Any) -> list | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return None


def _cache_get_with_own_session(version: str, input_hash: str) -> Any | None:
    db = SessionLocal()
    try:
        return analysis_cache_get(db, version, input_hash)
    finally:
        db.close()


def _cache_set_with_own_session(version: str, input_hash: str, data: dict) -> None:
    db = SessionLocal()
    try:
        analysis_cache_set(db, version, input_hash, data)
    finally:
        db.close()


def _call_llama_server(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    num_ctx: int,
    http_timeout: int,
) -> str | None:
    """Call native llama-server via OpenAI-compatible /v1/chat/completions."""
    url = f"{settings.LLAMA_SERVER_URL}/v1/chat/completions"
    body = json.dumps({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        raw = resp.read()
    data = json.loads(raw)
    choice = data["choices"][0]
    finish = choice.get("finish_reason", "")
    if finish == "length":
        logger.warning("llama-server output truncated (finish_reason=length)")
    return choice["message"]["content"]


def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int,
    num_ctx: int,
    http_timeout: int,
) -> str | None:
    """Call Ollama via /api/generate."""
    url = f"{settings.OLLAMA_BASE_URL}/api/generate"
    body = json.dumps({
        "model": model,
        "prompt": user_prompt,
        "system": system_prompt,
        "format": "json",
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "num_ctx": num_ctx,
        },
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        raw = resp.read()
    data = json.loads(raw)
    return data.get("response", "")


def call_llm(
    *,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    cache_key: Any,
    db_session: Session | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
    num_ctx: int = 4096,
) -> Any | None:
    """
    Call the configured LLM backend with structured JSON output.
    Results are cached by prompt version + input hash in the database.
    """
    global _total_calls, _cache_hits, _cache_misses

    use_model = model or settings.OLLAMA_MODEL
    use_backend = settings.LLM_BACKEND

    input_hash = _make_input_hash(prompt_version, cache_key, use_model)
    if db_session is not None:
        try:
            cached = _cache_get_with_own_session(prompt_version, input_hash)
            if cached is not None:
                _cache_hits += 1
                return cached
        except Exception:
            pass
        _cache_misses += 1

    estimated_prompt_tokens = (len(system_prompt) + len(user_prompt)) // 3
    required = estimated_prompt_tokens + max_tokens
    if required > num_ctx * 0.95:
        old_ctx = num_ctx
        num_ctx = min(required + 512, 8192)
        logger.warning(
            "Prompt for %s estimated at %d tokens (need %d), bumping num_ctx %d -> %d",
            prompt_version, estimated_prompt_tokens, required, old_ctx, num_ctx,
        )

    http_timeout = min(max(max_tokens // 6 + 60, 120), 600)

    for attempt in range(1, _max_retries + 1):
        try:
            if use_backend == "llama-server":
                text = _call_llama_server(
                    system_prompt, user_prompt, max_tokens, num_ctx, http_timeout,
                )
            else:
                text = _call_ollama(
                    system_prompt, user_prompt, use_model, max_tokens, num_ctx, http_timeout,
                )

            _total_calls += 1

            parsed = extract_json(text)
            if parsed is None:
                logger.warning(
                    "LLM returned non-JSON for %s, attempt %d (len=%d). Raw (500 chars): %.500s",
                    prompt_version, attempt, len(text) if text else 0, text,
                )
                if attempt < _max_retries:
                    time.sleep(1.0)
                    continue
                logger.error(
                    "LLM JSON parse failed for %s after %d attempts",
                    prompt_version, _max_retries,
                )
                return None

            if db_session is not None:
                try:
                    _cache_set_with_own_session(prompt_version, input_hash, parsed)
                except Exception:
                    pass

            return parsed

        except urllib.error.HTTPError as e:
            logger.warning("LLM HTTP error for %s (attempt %d): %s", prompt_version, attempt, e)
            if attempt == _max_retries:
                return None
            time.sleep(1.0)

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning("LLM request error for %s (attempt %d): %s", prompt_version, attempt, e)
            if attempt == _max_retries:
                return None
            time.sleep(attempt * 2.0)

        except Exception:
            logger.exception("Unexpected error in call_llm for %s (attempt %d)", prompt_version, attempt)
            if attempt == _max_retries:
                return None
            time.sleep(attempt * 2.0)

    return None


def get_llm_stats() -> dict:
    return {
        "total_calls": _total_calls,
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "estimated_cost": "free (local LLM)",
    }


def reset_client() -> None:
    pass


def reset_stats() -> None:
    global _total_calls, _cache_hits, _cache_misses
    _total_calls = 0
    _cache_hits = 0
    _cache_misses = 0
