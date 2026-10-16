"""Which Bluesky trends a trend post may be about, and how they may be quoted.

Whether a trend is a U.S. policy, government or civic story at all is
Bluesky's own filing: getTrends lists each trend with a category, and only
POLITICS_CATEGORY is eligible (score_trends' `relevance`, 1.0 or 0.0). It
is a first cut, not the guarantee: a trend posts only with a pinned
official record behind it (trend_evidence), and that match is what keeps a
foreign election or a celebrity's name from reaching a post. Until
2026-10-16 this was a kNN vote over 350 hand-labelled topics. The 3,084
trends of the 2026-09-12..10-15 backtest predate the category, so they
measure the vote's removal: the full decision posts 4 trends without it
against 3 with it, the fourth an Insurrection Act trend the vote scored
0.28. On the 106 trends production has recorded with their category, the
vote passed 20 and Bluesky filed 19 of them under politics; the category
gate changes no post there (one decision moves from ineligible to skipped:
a foreign election whose issue record a later check refuses;
docs/research/trend-posts.md).

Four kNN votes (Cover & Hart 1967) over hand-labelled Bluesky trend topics
remain, each the similarity-weighted share of positive examples among a
trend's k nearest (policy_relevance.relevance_votes, all-MiniLM-L6-v2):

  - tragedy: people killed, injured, attacked or detained by force, or a
    death (app/data/political_violence_examples.json).
    A tragedy is not excluded: it is eligible only when an official record
    answers it (trend_poster). A sixth vote, whether a tragedy has a U.S.
    government or policy angle, was removed 2026-10-16: the official record
    is that angle, and the vote refused 44% of the political tragedies it
    was labelled on (leave-one-out recall 0.56).
  - loaded: the trend's display name, the only words a post quotes from
    Bluesky, judges or mocks rather than names
    (app/data/loaded_language_examples.json).

A fifth, whether a trend is about a nomination itself (the nominee
anchor's gate), was removed 2026-10-16: the anchor now needs the trend to
begin within a day of the vote (trend_evidence.NOMINEE_WINDOW).

k and each threshold come from app/data/trend_gates_calibration.json,
written by scripts/calibrate_trend_gates.py with its leave-one-out
measurement. test_trend_gates_calibration_matches_examples fails until the
script is rerun after an example changes.

Nothing here is read by a score or kept in the learning store: the module
is listed with the Action Center's paths outside the analysis fingerprint
(senate_pipeline._ACTION_CENTER_PATHS).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass

import numpy as np

from app import calibration_drift
from app.pipeline.analyze.action_center import _embed_texts_sim
from app.policy_relevance import relevance_votes
from app.pipeline.analyze.grounding import (
    editorializing_language,
    electioneering_language,
    proposal_stated_as_fact,
)
from app.pipeline.vector_store import get_similarity_model

_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data"
VIOLENCE_EXAMPLES_PATH = _DATA_DIR / "political_violence_examples.json"
LOADED_EXAMPLES_PATH = _DATA_DIR / "loaded_language_examples.json"
CALIBRATION_PATH = _DATA_DIR / "trend_gates_calibration.json"

# gate name -> (examples file, label field, subset field or None).
GATES: dict[str, tuple[pathlib.Path, str, str | None]] = {
    "tragedy": (VIOLENCE_EXAMPLES_PATH, "tragedy", None),
    "loaded": (LOADED_EXAMPLES_PATH, "loaded", None),
}

# Bluesky's getTrends category for a politics trend (fetch/trending). The
# platform's own filing of the topic, read as published, like an outlet's
# section in news_feeds.NEWS_FEEDS.
POLITICS_CATEGORY = "politics"

_examples_cache: dict[str, list[dict]] = {}
_calibration_cache: dict | None = None
_reference_cache: dict[tuple[str, int], np.ndarray] = {}


def gate_examples(gate: str) -> list[dict]:
    """The labelled examples a gate votes over (its subset applied)."""
    if gate not in _examples_cache:
        path, _, subset = GATES[gate]
        rows = json.loads(path.read_text())["examples"]
        _examples_cache[gate] = [r for r in rows if subset is None or r[subset]]
    return _examples_cache[gate]


def gate_examples_hash(gate: str) -> str:
    """Identifies the labelled set a gate's calibration was fitted to."""
    _, label, _ = GATES[gate]
    payload = json.dumps([[e["text"], bool(e[label])] for e in gate_examples(gate)])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def calibration() -> dict:
    global _calibration_cache
    if _calibration_cache is None:
        _calibration_cache = json.loads(CALIBRATION_PATH.read_text())
    return _calibration_cache


def _reference(gate: str) -> np.ndarray:
    key = (gate, id(get_similarity_model()))
    if key not in _reference_cache:
        _reference_cache[key] = _embed_texts_sim([e["text"] for e in gate_examples(gate)])
    return _reference_cache[key]


def gate_votes(gate: str, embeddings: np.ndarray) -> np.ndarray:
    """Each row's kNN vote for `gate`, from unit embeddings."""
    _, label, _ = GATES[gate]
    labels = np.array([bool(e[label]) for e in gate_examples(gate)], dtype=float)
    votes = relevance_votes(embeddings, _reference(gate), labels, calibration()[gate]["k"])
    calibration_drift.observe(f"trend_{gate}", votes, threshold(gate))
    return votes


def threshold(gate: str) -> float:
    return float(calibration()[gate]["threshold"])


@dataclass(frozen=True)
class TrendScores:
    # 1.0 when Bluesky filed the trend under POLITICS_CATEGORY, else 0.0.
    relevance: float
    tragedy: float
    loaded: float

    @property
    def relevant(self) -> bool:
        return self.relevance >= 0.5

    @property
    def is_tragedy(self) -> bool:
        return self.tragedy >= threshold("tragedy")

    @property
    def is_loaded(self) -> bool:
        return self.loaded >= threshold("loaded")


def trend_text(name: str, description: str | None) -> str:
    """What the topic gates embed: the shape of the labelled examples."""
    return name.strip() + (f". {description.strip()}" if description and description.strip() else "")


def score_trends(trends: list[tuple[str, str | None, str | None]]) -> list[TrendScores]:
    """Scores for each (display name, description, Bluesky category). The
    loaded vote reads the name alone, the words a post would quote."""
    if not trends:
        return []
    topic = _embed_texts_sim([trend_text(n, d) for n, d, _ in trends])
    names = _embed_texts_sim([n for n, _, _ in trends])
    rel = [1.0 if (c or "").strip().lower() == POLITICS_CATEGORY else 0.0 for _, _, c in trends]
    trag = gate_votes("tragedy", topic)
    loaded = gate_votes("loaded", names)
    return [TrendScores(float(a), float(b), float(c)) for a, b, c in zip(rel, trag, loaded)]


def neutrality_problems(quoted: str, context: str = "", loaded_vote: float | None = None) -> list[str]:
    """Why `quoted` can't appear in a Civitas post, empty when it can.

    The grounding module's source-independent checks, which hold whoever
    said the words first (grounding.electioneering_language's docstring:
    restating a voter directive in Civitas's voice is the endorsement), its
    advocacy-as-fact check against `context` (a name that announces as done
    what its own description only urges), and the loaded-language vote.
    Quoting with attribution doesn't neutralise a judgement: the name is
    still the words the post chose to repeat."""
    problems = [f"electioneering: {p}" for p in electioneering_language(quoted)]
    problems += [f"editorializing: {p}" for p in editorializing_language(quoted)]
    problems += [f"advocacy stated as fact: {p}" for p in proposal_stated_as_fact(quoted, context)]
    if loaded_vote is not None and loaded_vote >= threshold("loaded"):
        problems.append(f"loaded language (vote {loaded_vote:.2f})")
    return problems


def loaded_votes(texts: list[str]) -> list[float]:
    """The loaded-language vote for each text (a record title the post or
    image would show)."""
    if not texts:
        return []
    return [float(v) for v in gate_votes("loaded", _embed_texts_sim(texts))]
