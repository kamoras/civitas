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

_max_retries = 3

# HTTP timeout scales with requested output length so a long generation
# (e.g. a full_story call at max_tokens=2048) doesn't get killed mid-response
# by a timeout sized for a short one, while a short call doesn't wait the
# full ceiling on a hung connection. ~6 tokens/sec is a conservative
# generation-rate estimate for the Pi's CPU inference; 60s covers fixed
# overhead (model load/prompt processing) independent of output length.
_HTTP_TIMEOUT_TOKENS_PER_SECOND = 6
_HTTP_TIMEOUT_BASE_OVERHEAD_S = 60
_HTTP_TIMEOUT_MIN_S = 120
_HTTP_TIMEOUT_MAX_S = 600


class LLMCallStats:
    """Tracks call_llm()'s cache hit/miss counters, overall and per prompt
    version — was 4 separate module-level globals mutated via `global`
    statements, the same shape as the already-fixed PipelineRunTracker.
    """

    def __init__(self) -> None:
        self.total_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.per_prompt: dict[str, dict[str, int]] = {}

    def _bucket(self, prompt_version: str) -> dict[str, int]:
        return self.per_prompt.setdefault(prompt_version, {"hits": 0, "misses": 0})

    def record_call(self) -> None:
        self.total_calls += 1

    def record_hit(self, prompt_version: str) -> None:
        self.cache_hits += 1
        self._bucket(prompt_version)["hits"] += 1

    def record_miss(self, prompt_version: str) -> None:
        self.cache_misses += 1
        self._bucket(prompt_version)["misses"] += 1

    def snapshot(self) -> dict:
        total = self.cache_hits + self.cache_misses
        hit_rate = round(self.cache_hits / total, 3) if total > 0 else None
        per_prompt = {
            v: {
                **s,
                "hit_rate": round(s["hits"] / (s["hits"] + s["misses"]), 3)
                if (s["hits"] + s["misses"]) > 0 else None,
            }
            for v, s in self.per_prompt.items()
        }
        return {
            "total_calls": self.total_calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": hit_rate,
            "per_prompt": per_prompt,
            "estimated_cost": "free (local LLM)",
        }

    def reset(self) -> None:
        self.total_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.per_prompt = {}


_stats = LLMCallStats()


def _make_input_hash(prompt_version: str, input_data: Any, model: str = "") -> str:
    raw = prompt_version + "|" + model + "|" + json.dumps(input_data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _strip_thinking_tokens(text: str) -> str:
    """Strip <think>...</think> chain-of-thought blocks emitted by reasoning models.

    Reasoning models (e.g. DeepSeek-R1, used previously — see config.py's
    OLLAMA_MODEL history) emit a reasoning trace before the final answer.
    If not removed, the greedy JSON-extraction regex matches from the
    first { inside the think block to the last } in the output, producing
    an unparseable span. A no-op safeguard for non-reasoning models (the
    current default, LFM2.5-1.2B-Instruct, doesn't emit these — unlike its
    sibling LFM2.5-1.2B-Thinking, deliberately not used here; see the
    2026-07 model evaluation for why).
    """
    # Strip closed <think>...</think> blocks, then also drop an UNTERMINATED
    # trailing <think> (a length-truncated reasoning trace never emits its
    # closing tag, and would otherwise poison the JSON extraction below).
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def extract_json(text: str) -> Any | None:
    text = _strip_thinking_tokens(text)

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

    # Try the object span and the array span INDEPENDENTLY, each first-to-
    # last of its own bracket type. A single greedy alternation matched
    # leftmost-first, so a stray "[Note]" before a real JSON object made
    # the match run from that "[" to the last "]" (inside the object) and
    # fail with no second chance. Prefer whichever span parses; try the
    # object first since these prompts return objects.
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                continue

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
            "temperature": 0.0,
        },
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        raw = resp.read()
    data = json.loads(raw)
    if data.get("done_reason") == "length":
        logger.warning("Ollama output truncated (done_reason=length) for model %s", model)
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

    ``cache_key=None`` disables caching entirely for this call (both the
    lookup and the write-back) — several callers pass it explicitly for
    time-sensitive or always-fresh generations (role-reversal re-checks,
    Bluesky post text, spotlight highlights). Caching used to be keyed off
    ``_make_input_hash(prompt_version, None, model)``, which JSON-serializes
    ``None`` to the constant string ``"null"`` — every call sharing a
    prompt_version and model then collided on the *same* cache row
    regardless of actual content. Confirmed live 2026-07-15: a single
    stale/wrong role-check verdict about an unrelated story got cached
    under that shared slot and was then returned for every subsequent
    role-check regardless of topic, silently rejecting every generated
    action-center summary for 26+ hours (zero new issues, zero Bluesky
    posts) since the check never reflected what was actually being
    verified.
    """
    use_model = model or settings.OLLAMA_MODEL
    use_backend = settings.LLM_BACKEND

    use_cache = cache_key is not None and db_session is not None
    input_hash = _make_input_hash(prompt_version, cache_key, use_model) if use_cache else None
    if use_cache:
        try:
            cached = _cache_get_with_own_session(prompt_version, input_hash)
            if cached is not None:
                _stats.record_hit(prompt_version)
                return cached
        except Exception:
            logger.debug("LLM cache lookup failed", exc_info=True)
        _stats.record_miss(prompt_version)

    estimated_prompt_tokens = (len(system_prompt) + len(user_prompt)) // 3
    required = estimated_prompt_tokens + max_tokens
    if required > num_ctx * 0.95:
        old_ctx = num_ctx
        num_ctx = min(required + 512, 8192)
        logger.warning(
            "Prompt for %s estimated at %d tokens (need %d), bumping num_ctx %d -> %d",
            prompt_version, estimated_prompt_tokens, required, old_ctx, num_ctx,
        )

    http_timeout = min(
        max(
            max_tokens // _HTTP_TIMEOUT_TOKENS_PER_SECOND + _HTTP_TIMEOUT_BASE_OVERHEAD_S,
            _HTTP_TIMEOUT_MIN_S,
        ),
        _HTTP_TIMEOUT_MAX_S,
    )

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

            _stats.record_call()

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

            if use_cache:
                try:
                    _cache_set_with_own_session(prompt_version, input_hash, parsed)
                except Exception:
                    logger.debug("LLM cache write failed", exc_info=True)

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
    return _stats.snapshot()


def reset_client() -> None:
    pass


def reset_stats() -> None:
    _stats.reset()
