"""The model sizes /environmental shows, read from the models themselves.

Runs in the pipeline process (scheduler `_environmental_sample`, daily and
once at start), never on a read: the language model's parameters and
weight bytes come from the running llama.cpp server's own metadata
(`GET /v1/models`, `meta.n_params` and `meta.size`), and each embedding
model's parameters from its safetensors header in the Hugging Face cache
(every tensor's shape, multiplied out and summed: the same count the
loaded model gives, without loading it). Written to
`/data/environmental_runtime.json` with when each was read. A model that
can't be read this time (the server down, an embedding model not in this
container's cache yet) keeps its last reading, with its own date, and is
never written as zero.
"""

import json
import logging
import math
import struct

import httpx
from huggingface_hub import try_to_load_from_cache

from app.atomic_write import runtime_data_path, update_json_file
from app.config import settings
from app.pipeline.vector_store import _SIMILARITY_MODEL_NAME, EMBEDDING_MODEL_NAME
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

RUNTIME_FILE = "environmental_runtime.json"

# What each embedding model is used for (AGENTS.md: classification and
# similarity), keyed by the names vector_store loads.
EMBEDDING_MODELS = {
    EMBEDDING_MODEL_NAME: "classification",
    _SIMILARITY_MODEL_NAME: "search and similarity",
}


def llm_meta(base_url: str | None = None) -> dict | None:
    """The served model's name, parameters and weight bytes; None when the
    server can't be read or names neither."""
    try:
        resp = httpx.get(f"{base_url or settings.LLAMA_SERVER_URL}/v1/models", timeout=10)
        resp.raise_for_status()
        model = resp.json()["data"][0]
        meta = model.get("meta") or {}
    except Exception:
        logger.info("Environmental sample: llama-server unreadable", exc_info=True)
        return None
    if not meta.get("n_params") or not meta.get("size"):
        return None
    return {
        "name": model["id"].rsplit("/", 1)[-1],
        "parameters": int(meta["n_params"]),
        "bytes": int(meta["size"]),
        "type": meta.get("ftype"),
    }


def safetensors_parameters(path: str) -> int:
    """Parameters in a safetensors file, from its header alone."""
    with open(path, "rb") as fh:
        (length,) = struct.unpack("<Q", fh.read(8))
        header = json.loads(fh.read(length))
    return sum(math.prod(t["shape"]) for k, t in header.items() if k != "__metadata__")


def embedding_parameters(repo: str) -> int | None:
    path = try_to_load_from_cache(repo, "model.safetensors")
    if not isinstance(path, str):
        return None
    try:
        return safetensors_parameters(path)
    except (OSError, ValueError, KeyError, struct.error):
        logger.info("Environmental sample: %s unreadable", path, exc_info=True)
        return None


def sample() -> dict:
    """Read what can be read now and merge it over the last readings."""
    now = utcnow().replace(microsecond=0).isoformat() + "Z"
    fresh: dict = {}
    llm = llm_meta()
    if llm:
        fresh["llm"] = {**llm, "readAt": now}
    for repo, use in EMBEDDING_MODELS.items():
        n = embedding_parameters(repo)
        if n:
            fresh.setdefault("embedding", {})[repo] = {"parameters": n, "use": use, "readAt": now}

    def merge(doc: dict) -> dict:
        if "llm" in fresh:
            doc["llm"] = fresh["llm"]
        doc["embedding"] = {**(doc.get("embedding") or {}), **fresh.get("embedding", {})}
        return doc

    written = update_json_file(runtime_data_path(RUNTIME_FILE), merge, end="\n", indent=1)
    logger.info("Environmental sample: read %s", sorted(fresh) or "nothing")
    return written
