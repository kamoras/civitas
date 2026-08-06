"""Tests for retrieval-first question answering.

The load-bearing property under test throughout: **no figure in an answer
is ever generated**. Every number comes from a row and is returned as a
citation. A language model asked to state a donation total will sometimes
state a plausible wrong one, and for a project whose value is that its
numbers are checkable, that is the failure that would discredit it.

Tests that need the sentence-transformer model are marked `slow`, matching
the repo convention — the intent classifier is the only part that needs
it, and every handler below is exercised without it.
"""

import pytest

from app.models import Donor, IndustryDonation, Representative, Senator
from app.services import qa


@pytest.fixture()
def seeded(db_session):
    db_session.add(Senator(
        id="S1", name="Jane Doe", state="CA", party="D",
        total_raised=2_400_000.0,
        score_funding_independence=82.0, score_promise_persistence=71.0,
        score_independent_voting=64.0, score_funding_diversity=55.0,
        score_legislative_effectiveness=48.0,
    ))
    db_session.add(Senator(
        id="S2", name="John Roe", state="NY", party="R",
        total_raised=900_000.0,
        score_funding_independence=31.0, score_promise_persistence=22.0,
        score_independent_voting=18.0, score_funding_diversity=25.0,
        score_legislative_effectiveness=20.0,
    ))
    db_session.add(Representative(id="R1", name="Sam Poe", state="TX", party="R"))
    db_session.add(Donor(
        senator_id="S1", name="Acme PAC", total=250_000.0, type="PAC",
        rank=1, industry="PHARMACEUTICALS",
    ))
    db_session.add(Donor(
        senator_id="S1", name="Beta Corp", total=100_000.0, type="ORG",
        rank=2, industry="TECH",
    ))
    db_session.add(IndustryDonation(
        senator_id="S1", industry="PHARMACEUTICALS", name="Pharma",
        total=250_000.0, percentage=32.5,
    ))
    db_session.add(IndustryDonation(
        senator_id="S2", industry="PHARMACEUTICALS", name="Pharma",
        total=90_000.0, percentage=10.0,
    ))
    db_session.commit()
    return db_session


# --- Member resolution -------------------------------------------------

def test_resolves_a_member_by_full_name(seeded):
    member, how = qa.resolve_member(seeded, "how does Jane Doe score")
    assert member.id == "S1"
    assert how == "full"


def test_resolves_a_member_by_unambiguous_surname(seeded):
    member, how = qa.resolve_member(seeded, "who funds Roe")
    assert member.id == "S2"
    assert how == "surname"


def test_ambiguous_surname_resolves_to_nothing(db_session):
    """"How did Johnson vote" must not silently pick one of several
    Johnsons and answer confidently about the wrong person."""
    db_session.add(Senator(id="S1", name="Alice Johnson", state="CA", party="D"))
    db_session.add(Senator(id="S2", name="Bob Johnson", state="NY", party="R"))
    db_session.commit()

    member, how = qa.resolve_member(db_session, "who funds Johnson")
    assert member is None
    assert how == "ambiguous"


def test_surname_match_respects_word_boundaries(db_session):
    db_session.add(Senator(id="S1", name="Ted Cruz", state="TX", party="R"))
    db_session.commit()

    member, how = qa.resolve_member(db_session, "tell me about Cruzeiro football")
    assert member is None
    assert how == "none"


# --- Handlers ----------------------------------------------------------

def test_scorecard_answer_cites_every_figure_it_prints(seeded):
    member, _ = qa.resolve_member(seeded, "Jane Doe")
    result = qa._answer_member_scorecard(seeded, member)

    assert "82/100" in result["answer"]
    cited = {c["field"]: c["value"] for c in result["citations"]}
    assert cited["score_funding_independence"] == 82.0
    # Every score line has a matching citation — the property that makes
    # an answer checkable rather than merely plausible.
    assert len(result["citations"]) == len(qa.SCORE_FIELDS)


def test_donor_answer_is_ordered_and_cited(seeded):
    member, _ = qa.resolve_member(seeded, "Jane Doe")
    result = qa._answer_member_donors(seeded, member)

    assert "Acme PAC" in result["answer"]
    assert [c["name"] for c in result["citations"]] == ["Acme PAC", "Beta Corp"]
    assert result["citations"][0]["total"] == 250_000.0


def test_donor_answer_says_so_rather_than_answering_about_the_wrong_chamber(seeded):
    rep = seeded.query(Representative).one()
    result = qa._answer_member_donors(seeded, rep)
    assert "representative" in result["answer"]
    assert result["citations"] == []


def test_donor_answer_with_no_records_does_not_invent_any(seeded):
    member = seeded.query(Senator).filter_by(id="S2").one()
    result = qa._answer_member_donors(seeded, member)
    assert "No donor records" in result["answer"]
    assert result["citations"] == []


def test_industry_answer_is_cited(seeded):
    member, _ = qa.resolve_member(seeded, "Jane Doe")
    result = qa._answer_member_industries(seeded, member)
    assert "PHARMACEUTICALS" in result["answer"]
    assert result["citations"][0]["percentage"] == 32.5


def test_top_by_score_ranks_descending_by_default(seeded):
    result = qa._answer_top_by_score(seeded, "which senators score highest")
    assert result["answer"].startswith("Highest-scoring")
    assert [c["entityId"] for c in result["citations"]] == ["S1", "S2"]


def test_top_by_score_inverts_for_worst(seeded):
    result = qa._answer_top_by_score(seeded, "which senators are the worst")
    assert result["answer"].startswith("Lowest-scoring")
    assert [c["entityId"] for c in result["citations"]] == ["S2", "S1"]


def test_industry_leaders_reads_labels_from_the_data(seeded):
    """The industry vocabulary is whatever the classifier actually emitted,
    not a hardcoded list that can drift out of step with it."""
    result = qa._answer_industry_leaders(seeded, "who takes the most pharmaceuticals money")
    assert [c["entityId"] for c in result["citations"]] == ["S1", "S2"]
    assert result["citations"][0]["total"] == 250_000.0


def test_industry_leaders_says_it_could_not_tell_rather_than_guessing(seeded):
    result = qa._answer_industry_leaders(seeded, "who takes the most money from wombats")
    assert "could not tell which industry" in result["answer"]
    assert result["citations"] == []


# --- The LLM guard -----------------------------------------------------

def test_rephrasing_is_off_by_default(seeded):
    text, used = qa._maybe_rephrase("Jane Doe raised $2.4M")
    assert text == "Jane Doe raised $2.4M"
    assert used is False


def test_number_guard_accepts_a_faithful_rewrite():
    assert qa._numbers_are_preserved(
        "Acme PAC gave $250,000 to Jane Doe",
        "Jane Doe's largest contributor was Acme PAC at $250,000.",
    )


def test_number_guard_accepts_dropping_a_figure():
    """A terser sentence is fine. Only invention is disqualifying."""
    assert qa._numbers_are_preserved(
        "Acme PAC gave $250,000 and Beta Corp gave $100,000",
        "Acme PAC was the largest contributor.",
    )


def test_number_guard_rejects_an_invented_figure():
    """The whole reason the optional LLM path is safe to have at all."""
    assert not qa._numbers_are_preserved(
        "Acme PAC gave $250,000 to Jane Doe",
        "Acme PAC gave $450,000 to Jane Doe.",
    )


def test_altered_rewrite_is_discarded_and_the_deterministic_answer_returned(monkeypatch):
    """End-to-end on the guard: an LLM that changes a figure must not be
    able to put that figure in front of a reader."""
    monkeypatch.setattr(qa.settings, "QA_LLM_PHRASING", True)
    monkeypatch.setattr(
        "app.pipeline.analyze.ollama_client.call_llm",
        lambda **kwargs: {"text": "Acme PAC gave $999,999 to Jane Doe."},
    )

    original = "Acme PAC — $250,000"
    text, used = qa._maybe_rephrase(original)
    assert text == original
    assert used is False


def test_faithful_rewrite_is_used(monkeypatch):
    monkeypatch.setattr(qa.settings, "QA_LLM_PHRASING", True)
    monkeypatch.setattr(
        "app.pipeline.analyze.ollama_client.call_llm",
        lambda **kwargs: {"text": "Acme PAC contributed $250,000."},
    )

    text, used = qa._maybe_rephrase("Acme PAC — $250,000")
    assert text == "Acme PAC contributed $250,000."
    assert used is True


def test_llm_failure_falls_back_to_the_deterministic_answer(monkeypatch):
    monkeypatch.setattr(qa.settings, "QA_LLM_PHRASING", True)

    def _boom(**kwargs):
        raise RuntimeError("llama-server down")

    monkeypatch.setattr("app.pipeline.analyze.ollama_client.call_llm", _boom)

    text, used = qa._maybe_rephrase("Acme PAC — $250,000")
    assert text == "Acme PAC — $250,000"
    assert used is False


# --- Orchestration -----------------------------------------------------

def test_answer_question_reports_latency_and_intent(seeded, monkeypatch):
    monkeypatch.setattr(qa, "classify_intent", lambda q: ("member_donors", 0.8, 0.2))

    result = qa.answer_question(seeded, "who funds Jane Doe")
    assert result["intent"] == "member_donors"
    assert result["memberResolution"] == "full"
    assert result["latencyMs"] >= 0
    assert result["usedLlm"] is False
    # The deterministic answer is always returned alongside, so a reviewer
    # can see exactly what retrieval produced.
    assert result["deterministicAnswer"] == result["answer"]
    assert result["citations"]


def test_member_question_naming_no_member_falls_back_to_documents(seeded, monkeypatch):
    monkeypatch.setattr(qa, "classify_intent", lambda q: ("member_donors", 0.8, 0.2))
    monkeypatch.setattr(
        qa, "_answer_documents",
        lambda db, q, limit=5: {"answer": "Related documents:", "citations": []},
    )

    result = qa.answer_question(seeded, "who funds nobody in particular")
    assert result["intent"] == "documents"


def test_document_fallback_survives_search_being_unavailable(seeded, monkeypatch):
    monkeypatch.setattr(qa, "classify_intent", lambda q: ("documents", 0.1, 0.0))

    def _boom(*args, **kwargs):
        raise RuntimeError("index rebuilding")

    monkeypatch.setattr("app.services.explore_search.hybrid_search", _boom)

    result = qa.answer_question(seeded, "anything at all")
    assert "temporarily unavailable" in result["answer"]
    assert result["citations"] == []


# --- Intent classification (needs the embedding model) -----------------

def _require_embedding_model():
    """Skip rather than pass when the model cannot load.

    classify_intent deliberately degrades to "documents" when embeddings
    are unavailable, which means every assertion below would pass
    vacuously in an environment with no model — reporting green for a
    classifier that never ran. An explicit skip keeps the distinction
    between "verified" and "not exercised" honest.
    """
    try:
        from app.pipeline.vector_store import get_embedding_model

        get_embedding_model()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"embedding model unavailable: {exc}")


@pytest.mark.slow
@pytest.mark.parametrize(
    "question,expected",
    [
        ("who are the biggest donors to this senator", "member_donors"),
        ("which senators score the highest", "top_by_score"),
        ("which industries fund this senator", "member_industries"),
    ],
)
def test_intent_classification_on_representative_questions(question, expected):
    _require_embedding_model()
    qa.reset_prototype_cache()
    intent, score, margin = qa.classify_intent(question)
    assert intent == expected, f"got {intent} (score={score:.3f}, margin={margin:.3f})"


@pytest.mark.slow
def test_ambiguous_question_falls_back_to_documents_rather_than_guessing():
    """bill_analyzer's 2026-07 audit found prototype anchors cluster within
    a few hundredths for almost any input. An unguarded argmax would answer
    the wrong structured question with confident-looking real numbers."""
    _require_embedding_model()
    qa.reset_prototype_cache()
    intent, score, margin = qa.classify_intent("hello there")
    # Must fall back because the gate rejected it, not because the model
    # was missing — assert a real score was computed.
    assert score > 0.0
    assert intent == "documents"


def test_classification_without_embeddings_degrades_to_documents(monkeypatch):
    """The fallback is always a working answer path, never an error."""
    qa.reset_prototype_cache()
    monkeypatch.setattr(
        "app.pipeline.vector_store.get_embedding_model",
        lambda: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    intent, score, margin = qa.classify_intent("who funds Jane Doe")
    assert intent == "documents"
    assert score == 0.0
