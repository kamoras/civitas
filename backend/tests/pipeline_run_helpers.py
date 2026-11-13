"""What the whole-run tests of the member pipelines share
(test_senate_pipeline_run.py, test_house_pipeline_run.py): a stand-in for
the embedding model, the run's sessions pointed at the test's database, and
a dump of every row a run stores, pinned in a golden file."""

import hashlib
import json
import os
import pathlib
import re
from datetime import date, datetime

import numpy as np

_DIM = 384

# Bookkeeping about the run itself, not what it stored about the chamber
# (pipeline_phase_timings holds each step's duration, which no two runs share).
_NOT_STORED_RESULTS = {"pipeline_runs", "house_pipeline_runs", "api_cache", "pipeline_progress",
                       "source_read_stats", "pipeline_phase_timings"}


class HashedWords:
    """A stand-in sentence-transformer: each word hashed into a fixed
    vector, summed and normalised, so texts sharing words are near."""

    max_seq_length = 512

    def __init__(self, name=None, *args, **kwargs):
        self.name = name

        class _Tok:
            def num_special_tokens_to_add(self, *a, **k):
                return 0

            def __call__(self, text, *a, **k):
                return {"input_ids": list(range(len(str(text).split())))}

        self.tokenizer = _Tok()

    def get_sentence_embedding_dimension(self):
        return _DIM

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(_DIM)
        for word in re.findall(r"[a-z0-9]+", str(text).lower()):
            seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "little")
            v += np.random.default_rng(seed).standard_normal(_DIM)
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode(self, texts, *args, normalize_embeddings=False, convert_to_numpy=True, **kwargs):
        if isinstance(texts, str):
            return self._one(texts)
        return np.array([self._one(t) for t in texts]) if len(texts) else np.zeros((0, _DIM))


def run_on_test_database(monkeypatch, db_session, pipeline_module) -> None:
    """The pipeline's own session is the test's (left open at the run's
    end), the modules that open their own session (ops alerts, the score
    calibration check) reach the same in-memory database, and the
    embedding model is the stand-in."""
    from app import database
    from app.config import settings
    from app.pipeline import vector_store

    monkeypatch.setattr(pipeline_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setitem(database.SessionLocal.kw, "bind", db_session.get_bind())
    monkeypatch.setattr(settings, "DATA_GOV_API_KEY", "test-key")
    monkeypatch.setattr(vector_store, "SentenceTransformer", HashedWords)
    monkeypatch.setattr(vector_store, "_model", None)
    monkeypatch.setattr(vector_store, "_similarity_model", None)


def _plain(v):
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return _plain(json.loads(v))
        except ValueError:
            return v
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def stored(db) -> dict:
    """Every row of every table the run wrote, timestamps left out, in a
    stable order: what a reorganised run must reproduce exactly."""
    from app.database import Base

    out = {}
    for table in Base.metadata.sorted_tables:
        if table.name in _NOT_STORED_RESULTS:
            continue
        rows = []
        for row in db.execute(table.select()).mappings():
            rows.append({k: _plain(v) for k, v in row.items() if not isinstance(v, (datetime, date))})
        if rows:
            out[table.name] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def matches_golden(db, golden: pathlib.Path) -> bool:
    """Whether the run stored what `golden` pins. PIPELINE_RUN_GOLDEN=write
    rewrites it instead: only for a change meant to move the results."""
    got = stored(db)
    if os.environ.get("PIPELINE_RUN_GOLDEN") == "write":
        golden.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    return got == json.loads(golden.read_text())
