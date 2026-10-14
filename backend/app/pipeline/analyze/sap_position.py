"""The position a Statement of Administration Policy takes on its measure.

Zero-shot embedding classification (AGENTS.md §1, Yin, Hay & Roth 2019):
each candidate sentence is compared by cosine similarity with three sets of
natural-language prototypes, and no keyword decides anything.

Candidate sentences come from the statement's opening and closing paragraphs
only. That is the SAP's document format, not a judgement: OMB states the
Administration's position on passage in the opening paragraph and closes
with the recommendation to the President ("his advisors would recommend
that he sign it into law" / "... veto it"); the paragraphs between argue
the case or take up individual provisions. A provision-by-provision SAP
(an appropriations bill, the NDAA) opens by thanking the committee and
closes by offering to keep working, and takes no position on passage.

Rule: the candidate sentence most similar to either position prototype is
the statement's position sentence. If that sentence is nearer the "other"
prototypes than to its position prototype, the statement takes no position
on passage ("none") and none of its votes are counted; otherwise the
position is whichever of support / oppose it is nearer. There is no
calibrated threshold.

Measured 2026-10-13 against a hand-checked reading of the SAPs OMB had
published for the 119th Congress: 194 on the index, 193 readable (one names
its nineteen resolutions only by count, so no vote can be matched to it),
of which 124 support, 60 oppose and 9 take no position on passage. 185 of
the 193 agree (95.9%). Of the eight that don't, seven are a position read
as no position: two appropriations SAPs that "support advancement" while
listing concerns, a budget resolution whose closing urges passage without
saying "support", one bill whose only position sentence is long and mostly
about the bill's content, and three Senate resolutions whose opening
sentence names the resolution's long title. The eighth is the reverse: a SAP
recommending signature "with amendments", read as support. No support was
read as oppose or the reverse, so the errors leave a vote out rather than
misread it. The prototypes were chosen among a handful of phrasings on that
same reading, so the figure carries that selection: it is a development
measurement, not a held-out one.
"""

import re

import numpy as np

from app.pipeline.vector_store import encode_normalized, get_embedding_model

# Bump with any change to the prototypes, the candidate sentences or the
# rule: stored positions read under an older version are read again.
CLASSIFIER_VERSION = 1

POSITION_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "support": (
        "The Administration supports passage of this bill.",
        "If this bill were presented to the President, his advisors would recommend that he sign it into law.",
    ),
    "oppose": (
        "The Administration opposes passage of this bill.",
        "If this bill were presented to the President, his advisors would recommend that he veto it.",
    ),
    "other": (
        "The Administration appreciates the work of the committee on this bill.",
        "The Administration looks forward to working with Congress to address its concerns with the bill.",
    ),
}

# Abbreviations a SAP's sentences contain mid-sentence ("H. Con. Res. 89",
# "U.S. Armed Forces", "Rep. Doe"): a period after one does not end the
# sentence. Text segmentation, not classification.
_ABBREVIATION = re.compile(
    r"(?:^|[\s(])(?:H|S|J|R|Res|Con|Rep|Sen|U\.S|Amdt|No|Sec|Mr|Ms|Dr|St|Jr|v|vs|Pub|L|Stat|U|Gen|Lt|Col|"
    r"Inc|Co|Corp|Dept|e\.g|i\.e|etc|approx|Fed|Reg|D|D\.C|Ph\.D)\.$"
)

_prototype_embeddings: dict[str, np.ndarray] | None = None


def sentences(text: str) -> list[str]:
    """`text` split into sentences, an abbreviation's period not ending one;
    fragments under four words (a stray citation) are dropped."""
    text = " ".join((text or "").split())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(“\"])", text)
    out: list[str] = []
    for part in parts:
        if out and _ABBREVIATION.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [s for s in out if len(s.split()) >= 4]


def _prototypes() -> dict[str, np.ndarray]:
    global _prototype_embeddings
    if _prototype_embeddings is None:
        model = get_embedding_model()
        _prototype_embeddings = {k: encode_normalized(model, list(v)) for k, v in POSITION_PROTOTYPES.items()}
    return _prototype_embeddings


def decide(similarity: dict[str, np.ndarray]) -> tuple[str, int]:
    """(position, index of the position sentence) from each candidate
    sentence's similarity to each prototype class (arrays aligned with the
    sentences). The rule in the module docstring."""
    support, oppose, other = similarity["support"], similarity["oppose"], similarity["other"]
    position_like = np.maximum(support, oppose)
    i = int(np.argmax(position_like))
    if position_like[i] <= other[i]:
        return "none", i
    return ("support" if support[i] > oppose[i] else "oppose"), i


def classify(opening: str, closing: str) -> tuple[str, str]:
    """(position, the verbatim sentence it was read from). Position is
    "support", "oppose" or "none"; the sentence is "" when there was none
    to read."""
    candidates = sentences(opening) + sentences(closing)
    if not candidates:
        return "none", ""
    embedded = encode_normalized(get_embedding_model(), candidates)
    similarity = {k: (embedded @ protos.T).max(axis=1) for k, protos in _prototypes().items()}
    position, i = decide(similarity)
    return position, candidates[i]
