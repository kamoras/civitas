"""Explore document summaries: one LLM generation per text, shared by every
reader of it.

Served by the pipeline process (nginx routes `POST /api/explore/{id}/summary`
there; AGENTS.md "Anything that starts background work belongs to the
pipeline process"), which is always a single process. So the coordination
is plain state in this module — no claims in a store shared between workers,
and nothing that splits during a rolling update of the API:

- A text being summarised has one generation (`_runs`). A reader who asks
  while it runs joins it — every event so far, then each new one — rather
  than being turned away or starting a second.
- A generation finishes whether or not its readers stay, and is cached: an
  abandoned stream can't hold a document's summary off for anyone.
- At most MAX_GENERATIONS run at once (the device has one LLM), and a
  client starts at most one at a time (a generation outlives its reader, so
  one address must not hold every slot).
- A text whose output couldn't be used is answered with none for a while; one
  that ran out of time is held off briefly (the LLM may just have been busy),
  and as unusable if it runs out again soon after; an LLM that says it is
  busy holds every new generation off briefly.

A refusal is a wait (`Refusal`, X-Summary-Wait): the page asks again after
its Retry-After.
"""

import asyncio
import hashlib
import logging
import math
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

MAX_GENERATIONS = 2
GENERATION_LIMIT_S = 240.0
UNUSABLE_FOR_S = 30 * 60.0
SLOW_FOR_S = 2 * 60.0
BUSY_RETRY_AFTER_S = 30
# How often a stream waiting on the LLM sends an SSE comment: nginx drops a
# proxied response that sends nothing for its read timeout, which a busy
# LLM's prompt processing can exceed before the first token.
KEEPALIVE_S = 15.0
# Bump alongside explore_document_summary_prompt's promptVersion. 5: keyed on
# the prompt's hash too.
CACHE_KEY_VERSION = 5
# Marks a refusal that is only a wait: the page asks again after
# Retry-After. A refusal without it (nginx's own rate limit) is not waited
# out.
WAIT_OUT = {"X-Summary-Wait": "1"}

_NOTHING = {"summary": "", "keyPoints": [], "impact": ""}


class Refusal(Exception):
    """A request turned away for now: a wait, with the Retry-After the page
    waits before asking again."""

    def __init__(self, status: int, detail: str, retry_after: float):
        super().__init__(detail)
        self.status, self.detail = status, detail
        self.retry_after = max(1, math.ceil(retry_after))

    def error(self) -> HTTPException:
        return HTTPException(
            status_code=self.status,
            detail=self.detail,
            headers={"Retry-After": str(self.retry_after), **WAIT_OUT},
        )


def _busy(retry_after: float = BUSY_RETRY_AFTER_S) -> Refusal:
    return Refusal(503, "Summaries are busy right now; please try again shortly.", retry_after)


def sse(data: dict) -> str:
    import json

    return f"data: {json.dumps(data)}\n\n"


def cache_key(doc_id: int, prompt: dict) -> dict:
    """Keyed on what the LLM is given, not the document's id alone: a
    document whose text or metadata changes in place (a body backfilled, a
    re-ingest, a data reset reusing the id) is summarised afresh, and a
    generation that outlasts such a change files its summary under the text
    it read."""
    digest = hashlib.sha256(f"{prompt['systemPrompt']}\x00{prompt['userPrompt']}".encode()).hexdigest()[:32]
    return {"doc_id": doc_id, "v": CACHE_KEY_VERSION, "prompt": digest}


class _Run:
    """One generation, and every event it has sent — replayed to a reader
    who joins late."""

    def __init__(self, key: str, doc_id: int, prompt: dict, cache_key: dict, client: str | None):
        self.key, self.doc_id, self.prompt, self.cache_key = key, doc_id, prompt, cache_key
        self.client = client
        self.events: list[str] = []
        self.done = False
        self._changed = asyncio.Event()
        self.task: asyncio.Task | None = None

    def publish(self, event: str | None) -> None:
        """Send `event` to every reader (None: the generation is over)."""
        if event is None:
            self.done = True
        else:
            self.events.append(event)
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def follow(self) -> AsyncIterator[str]:
        """Every event so far, then each new one until the last."""
        sent = 0
        while True:
            while sent < len(self.events):
                yield self.events[sent]
                sent += 1
            if self.done:
                return
            changed = self._changed  # no await since the checks above
            try:
                await asyncio.wait_for(changed.wait(), KEEPALIVE_S)
            except TimeoutError:
                yield ": waiting\n\n"


# This process's state — the pipeline process is always one process.
_runs: dict[str, _Run] = {}
# The run each client started, by the client's key (an HMAC of its address,
# throttle.client_key — never the address).
_by_client: dict[str, str] = {}
# Text key -> (why, monotonic time it lifts): "unusable" or "slow".
_holds: dict[str, tuple[str, float]] = {}
# Text key -> monotonic time its first timeout is forgotten: a second one
# before then makes it unusable.
_strikes: dict[str, float] = {}
_llm_busy_until = 0.0


def _prune(now: float) -> None:
    for table in (_holds, _strikes):
        for key in [k for k, v in table.items() if (v[1] if isinstance(v, tuple) else v) <= now]:
            del table[key]


async def request(doc_id: int, prompt: dict, key_: dict, ip: str) -> AsyncIterator[str]:
    """The event stream for this text's summary: a generation joined, one
    started, or the answer that none can be made for now. Raises Refusal."""
    from app.api import throttle
    from app.pipeline.analyze.ollama_client import get_cached_llm_result

    # The awaits first; everything after them decides and registers without
    # yielding to another request, so two can't both pass the same check.
    client = await throttle.run(throttle.client_key, ip, "explore-summary-client")
    made = await asyncio.to_thread(get_cached_llm_result, prompt["promptVersion"], key_)
    if made is not None:  # finished by another reader a moment ago
        return _once({"done": True, **made})

    key = f"{doc_id}:{key_['prompt']}"
    run = _runs.get(key)
    if run is not None:
        return run.follow()

    now = time.monotonic()
    _prune(now)
    if _llm_busy_until > now:
        raise _busy(_llm_busy_until - now)
    held = _holds.get(key)
    if held is not None:
        why, until = held
        if why == "slow":
            raise Refusal(503, "This summary took too long a moment ago; please try again shortly.", until - now)
        return _once({"done": True, **_NOTHING})  # unusable: the answer, not a wait
    if client is None:
        raise Refusal(503, "Summaries are unavailable right now; please try again shortly.",
                      throttle.UNAVAILABLE_RETRY_AFTER_S)
    # Under yesterday's key too, so the rule doesn't reset at midnight.
    if any(k in _by_client for k in (str(client), client.previous) if k):
        raise _busy()
    if len(_runs) >= MAX_GENERATIONS:
        raise _busy()

    run = _Run(key, doc_id, prompt, key_, str(client))
    _runs[key] = run
    _by_client[run.client] = key
    run.task = asyncio.create_task(_generate(run))
    run.task.add_done_callback(lambda _t: _forget(run))
    return run.follow()


def _forget(run: _Run) -> None:
    if _runs.get(run.key) is run:
        del _runs[run.key]
    if _by_client.get(run.client) == run.key:
        del _by_client[run.client]
    if not run.done:  # cancelled before its last event: its readers' streams end
        run.publish(None)


async def _once(event: dict) -> AsyncIterator[str]:
    yield sse(event)


async def _generate(run: _Run) -> None:
    global _llm_busy_until
    from app.background import WritesHeld, writing
    from app.pipeline.analyze import ollama_client
    from app.pipeline.analyze.prompts import parse_explore_document_summary

    text = ""
    finished = at_limit = timed_out = llm_busy = False
    deadline = asyncio.timeout(GENERATION_LIMIT_S)
    try:
        async with deadline:
            async for delta in ollama_client.stream_llm(
                system_prompt=run.prompt["systemPrompt"],
                user_prompt=run.prompt["userPrompt"],
                max_tokens=512,
            ):
                text += delta
                run.publish(sse({"delta": delta}))
        finished = True
    except ollama_client.StreamCutOff:
        # At the token limit: as far as it will ever get (the same prompt
        # stops at the same place).
        at_limit = True
    except Exception as error:
        # Busy: the LLM said so (429/503, before any text — the stream checks
        # the status first): a fact about it, not this text. Out of time: the
        # deadline expired, or the LLM, once connected, stopped answering
        # within its read timeout. Anything else (unreachable, a bad
        # response) is a failure, which may be retried at once.
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (429, 503):
            logger.warning("Explore doc summary for doc_id=%s: the LLM is busy", run.doc_id)
            llm_busy = True
        elif deadline.expired() or isinstance(error, httpx.ReadTimeout):
            logger.warning("Explore doc summary for doc_id=%s ran out of time", run.doc_id)
            timed_out = True
        else:
            logger.exception("Explore doc summary streaming failed for doc_id=%s", run.doc_id)

    # Anything but a natural end stopped mid-sentence: the section it was
    # writing is dropped, for its readers as for the cache.
    parsed = parse_explore_document_summary(text, cut_off=not finished) if text else dict(_NOTHING)
    if at_limit:  # kept, less the section it stopped in: said so
        parsed["truncated"] = True
    ended = finished or at_limit
    now = time.monotonic()
    last = {"done": True, **parsed}
    if ended and parsed["summary"]:
        # Registered as a writer for the write, so a data reset in progress
        # holds it off (not cached then; the next reader makes it afresh).
        try:
            with writing("explore-summary"):
                await asyncio.to_thread(ollama_client.set_cached_llm_result, run.prompt["promptVersion"],
                                        run.cache_key, parsed)
        except WritesHeld:
            logger.info("Explore summary for doc_id=%s not cached: a data reset holds writes", run.doc_id)
    elif ended:
        # An output that couldn't be used: asked again soon, the same text
        # would most likely come out the same way.
        _holds[run.key] = ("unusable", now + UNUSABLE_FOR_S)
    elif timed_out:
        if _strikes.get(run.key, 0.0) > now:
            _holds[run.key] = ("unusable", now + UNUSABLE_FOR_S)
        else:
            _strikes[run.key] = now + UNUSABLE_FOR_S
            _holds[run.key] = ("slow", now + SLOW_FOR_S)
            if not parsed["summary"]:
                last["retryAfter"] = int(SLOW_FOR_S)
    elif llm_busy:
        _llm_busy_until = now + BUSY_RETRY_AFTER_S
        last["retryAfter"] = BUSY_RETRY_AFTER_S
    if not ended and parsed["summary"]:
        last["partial"] = True
    run.publish(sse(last))
    run.publish(None)


async def stop() -> None:
    """Cancel the generations under way (lifespan shutdown): their readers'
    streams end, and the page asks again."""
    tasks = [run.task for run in list(_runs.values()) if run.task is not None]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def reset() -> None:
    """Forget everything (tests)."""
    global _llm_busy_until
    _runs.clear()
    _by_client.clear()
    _holds.clear()
    _strikes.clear()
    _llm_busy_until = 0.0
