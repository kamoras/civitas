"""Compare candidate embedding models on this platform's OWN live failure
cases — the selection instrument for the 2026-07 embedding-model swap.

Why this exists: the current model (snowflake-arctic-embed-xs) places all
same-register text in a ~0.55-0.87 raw-cosine band, which is the root
cause under roughly a third of the open findings registry (see
docs/action_center_audit_2026-07.md and the permanent-solutions research):
reject/abstain thresholds that can never fire, explore-doc anchors from
unrelated floor speeches, politician-name disambiguation with fully
overlapping legit/bogus ranges, and a per-fact topicality check that had
to be rejected on measurement. Per the repo's calibration discipline, the
replacement is chosen by measured SEPARATION on real production failures,
not leaderboard rank.

Each task below is built from text that actually flowed through
production in July 2026 (verbatim or lightly trimmed), with known-correct
and known-incorrect pairs. The score per task is the separation gap:

    gap = min(similarity of should-match pairs)
        - max(similarity of should-NOT-match pairs)

gap > 0 means a clean threshold exists for that task; the bigger, the
more margin. The current model measures NEGATIVE gaps on several tasks —
that is the pathology being fixed, and the baseline row makes it visible.

Run (downloads models on first use; CPU is fine):
    cd backend && python3 scripts/evaluate_embedding_models.py
"""


CANDIDATES = [
    # (model_name, notes)
    ("Snowflake/snowflake-arctic-embed-xs", "CURRENT baseline — retrieval-asymmetric"),
    ("sentence-transformers/all-MiniLM-L6-v2", "22M symmetric-similarity classic"),
    ("BAAI/bge-small-en-v1.5", "33M retrieval + similarity"),
    ("thenlper/gte-small", "33M symmetric-friendly"),
    ("google/embeddinggemma-300m", "300M — check Pi CPU cost before adopting"),
]

# ---------------------------------------------------------------------------
# Task 1 — Politician-name disambiguation (the Ferran Torres failure).
# Prototype phrases vs mention contexts. Legit civic references must score
# HIGHER than a different person's (sports) usage of the same surname.
# ---------------------------------------------------------------------------
DISAMBIG_POSITIVE = [
    ("Senator Lindsey Graham from SC", "the floor speech in which Graham criticized the bill as"),
    ("Senator John Thune from SD", "leadership change. Thune announced the tribute details on"),
    ("Representative Ted Lieu from CA", "Committee hearing. Lieu criticized Ambassador Mike Waltz during"),
    ("Senator Gary Peters from MI", "planning gaps; Peters noted the Pentagon budget lacked"),
]
DISAMBIG_NEGATIVE = [
    ("Representative Ritchie Torres from NY", "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal"),
    ("Representative Norma J. Torres from CA", "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal"),
    ("Senator Tim Scott from SC", "the film's director Scott accepted the award at the festival"),
]

# ---------------------------------------------------------------------------
# Task 2 — Explore-doc anchoring (the PROMESA/World Cup failure). Issue
# titles vs civic-document titles. A genuinely related doc must outscore
# an unrelated floor speech.
# ---------------------------------------------------------------------------
DOC_POSITIVE = [
    ("House approves Pentagon funding framework", "DEPARTMENT OF DEFENSE APPROPRIATIONS ACT"),
    ("HIV prevention funding freeze announced", "FUNDING FOR HIV PREVENTION PROGRAMS"),
    ("DOJ seeks communications records from NYT reporters", "PROTECTING JOURNALISTS FROM GOVERNMENT SURVEILLANCE"),
]
DOC_NEGATIVE = [
    ("Spanish and Argentine reactions to World Cup final", "PROMESA IS A DEMOCRATIC TRAGEDY"),
    ("Spanish and Argentine reactions to World Cup final", "DEPARTMENT OF DEFENSE APPROPRIATIONS ACT"),
    ("Cyclosporiasis outbreak investigation updates", "PROMESA IS A DEMOCRATIC TRAGEDY"),
]

# ---------------------------------------------------------------------------
# Task 3 — Per-fact topicality (audit M6, rejected on measurement against
# the current model). Issue title vs facts; on-topic facts must outscore
# the cross-topic contaminants that actually published.
# ---------------------------------------------------------------------------
FACT_POSITIVE = [
    ("New York City may not arrest Netanyahu; federal action urged",
     "The federal government is considering issuing an arrest warrant for Benjamin Netanyahu."),
    ("Trump-backed candidates win Arizona primaries",
     "The Democratic primary for a Phoenix-area battleground race has not yet concluded."),
    ("FDA investigation continues over Taylor Farms lettuce",
     "Multiple states are reporting over 7,000 confirmed cases of cyclosporiasis nationwide."),
]
FACT_NEGATIVE = [
    ("New York City may not arrest Netanyahu; federal action urged",
     "President Zelenskyy removed his army chief amid protests and appointed a new leader."),
    ("Trump-backed candidates win Arizona primaries",
     "Over 27 senior officials have left their positions in the Trump administration since the start of his first term."),
    ("FDA investigation continues over Taylor Farms lettuce",
     "PhRMA has noted the situation as a key point of discussion in industry discussions."),
]

# ---------------------------------------------------------------------------
# Task 4 — Policy relevance (the old civic-gate failure: sports/celebrity
# passed at every threshold). Policy prototypes vs article headlines.
# ---------------------------------------------------------------------------
POLICY_PROTOTYPE = "US Congress bill vote legislation Senate House passed signed"
POLICY_POSITIVE = [
    "House approves Pentagon funding framework in narrow 216-212 vote",
    "Senate Budget Committee convenes after panel leadership change",
]
POLICY_NEGATIVE = [
    "Spain defeats Argentina 1-0 in World Cup final on Ferran Torres goal",
    "Pop star announces record-breaking stadium tour dates",
]


def _pair_sims(model, pairs):
    lefts = [a for a, _ in pairs]
    rights = [b for _, b in pairs]
    ea = model.encode(lefts, normalize_embeddings=True)
    eb = model.encode(rights, normalize_embeddings=True)
    return [float(x @ y) for x, y in zip(ea, eb)]


def _proto_sims(model, proto, texts):
    ep = model.encode([proto], normalize_embeddings=True)[0]
    et = model.encode(texts, normalize_embeddings=True)
    return [float(ep @ e) for e in et]


def evaluate(model_name: str) -> dict[str, float]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    gaps = {}

    pos = _pair_sims(model, DISAMBIG_POSITIVE)
    neg = _pair_sims(model, DISAMBIG_NEGATIVE)
    gaps["disambiguation"] = min(pos) - max(neg)

    pos = _pair_sims(model, DOC_POSITIVE)
    neg = _pair_sims(model, DOC_NEGATIVE)
    gaps["explore_docs"] = min(pos) - max(neg)

    pos = _pair_sims(model, FACT_POSITIVE)
    neg = _pair_sims(model, FACT_NEGATIVE)
    gaps["fact_topicality"] = min(pos) - max(neg)

    pos = _proto_sims(model, POLICY_PROTOTYPE, POLICY_POSITIVE)
    neg = _proto_sims(model, POLICY_PROTOTYPE, POLICY_NEGATIVE)
    gaps["policy_relevance"] = min(pos) - max(neg)

    return gaps


def main() -> None:
    print(f"{'model':<45} {'disambig':>9} {'docs':>9} {'facts':>9} {'policy':>9} {'tasks>0':>8}")
    for name, notes in CANDIDATES:
        try:
            gaps = evaluate(name)
        except Exception as exc:  # model unavailable — report, keep going
            print(f"{name:<45} FAILED: {exc}")
            continue
        clean = sum(1 for g in gaps.values() if g > 0)
        print(
            f"{name:<45} {gaps['disambiguation']:>+9.3f} {gaps['explore_docs']:>+9.3f} "
            f"{gaps['fact_topicality']:>+9.3f} {gaps['policy_relevance']:>+9.3f} {clean:>5}/4   # {notes}"
        )


if __name__ == "__main__":
    main()
