"""Which Bluesky trends a trend post may be about, and how they may be quoted.

Four kNN votes (Cover & Hart 1967) over hand-labelled Bluesky trend topics,
each the similarity-weighted share of positive examples among a trend's k
nearest (action_center.relevance_votes, all-MiniLM-L6-v2):

  - relevance: a U.S. policy, government or civic story
    (app/data/trend_relevance_examples.json). The Action Center's article
    gate is fitted to news articles; trend topics are a short name and a
    one-line description, a different distribution, so they get their own
    labelled set.
  - tragedy: people killed, injured, attacked or detained by force, or a
    death (app/data/political_violence_examples.json).
  - political violence: among the tragedy examples only, the share with a
    U.S. government or policy angle (the same file). A tragedy is not
    excluded outright; it is eligible only when this vote is high and an
    official record answers it (trend_poster).
  - loaded: the trend's display name, the only words a post quotes from
    Bluesky, judges or mocks rather than names
    (app/data/loaded_language_examples.json).
  - appointment: the trend is about a federal nomination or appointment
    itself (app/data/appointment_examples.json). A nominee named in a roll
    call anchors a trend only then (trend_evidence): a trend that merely
    names an official is about what they did, not the vote that put them
    in office.

k and each threshold come from app/data/trend_gates_calibration.json,
written by scripts/calibrate_trend_gates.py with its leave-one-out
measurement; the relevance threshold of 0.5 is a decision recorded there,
not a fit. test_trend_gates_calibration_matches_examples fails until the
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

from app.pipeline.analyze.action_center import _embed_texts_sim, relevance_votes
from app.pipeline.analyze.grounding import (
    editorializing_language,
    electioneering_language,
    proposal_stated_as_fact,
)
from app.pipeline.vector_store import get_similarity_model

_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data"
RELEVANCE_EXAMPLES_PATH = _DATA_DIR / "trend_relevance_examples.json"
VIOLENCE_EXAMPLES_PATH = _DATA_DIR / "political_violence_examples.json"
LOADED_EXAMPLES_PATH = _DATA_DIR / "loaded_language_examples.json"
APPOINTMENT_EXAMPLES_PATH = _DATA_DIR / "appointment_examples.json"
CALIBRATION_PATH = _DATA_DIR / "trend_gates_calibration.json"

# gate name -> (examples file, label field, subset field or None). The
# political-violence vote is taken among tragedy examples only: it answers
# "given a tragedy, is it a political one", not "is it political".
GATES: dict[str, tuple[pathlib.Path, str, str | None]] = {
    "relevance": (RELEVANCE_EXAMPLES_PATH, "relevant", None),
    "tragedy": (VIOLENCE_EXAMPLES_PATH, "tragedy", None),
    "political_violence": (VIOLENCE_EXAMPLES_PATH, "political", "tragedy"),
    "loaded": (LOADED_EXAMPLES_PATH, "loaded", None),
    "appointment": (APPOINTMENT_EXAMPLES_PATH, "appointment", None),
}

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
    return relevance_votes(embeddings, _reference(gate), labels, calibration()[gate]["k"])


def threshold(gate: str) -> float:
    return float(calibration()[gate]["threshold"])


@dataclass(frozen=True)
class TrendScores:
    relevance: float
    tragedy: float
    political_violence: float
    loaded: float
    appointment: float = 0.0

    @property
    def relevant(self) -> bool:
        return self.relevance >= threshold("relevance")

    @property
    def is_tragedy(self) -> bool:
        return self.tragedy >= threshold("tragedy")

    @property
    def political_tragedy(self) -> bool:
        return self.political_violence >= threshold("political_violence")

    @property
    def is_loaded(self) -> bool:
        return self.loaded >= threshold("loaded")

    @property
    def about_appointment(self) -> bool:
        return self.appointment >= threshold("appointment")


def trend_text(name: str, description: str | None) -> str:
    """What the topic gates embed: the shape of the labelled examples."""
    return name.strip() + (f". {description.strip()}" if description and description.strip() else "")


def score_trends(trends: list[tuple[str, str | None]]) -> list[TrendScores]:
    """Scores for each (display name, description). The loaded vote reads the
    name alone, the words a post would quote."""
    if not trends:
        return []
    topic = _embed_texts_sim([trend_text(n, d) for n, d in trends])
    names = _embed_texts_sim([n for n, _ in trends])
    rel, trag, pol, appt = (gate_votes(g, topic) for g in ("relevance", "tragedy", "political_violence", "appointment"))
    loaded = gate_votes("loaded", names)
    return [TrendScores(float(a), float(b), float(c), float(d), float(e))
            for a, b, c, d, e in zip(rel, trag, pol, loaded, appt)]


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
