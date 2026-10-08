"""Which Congressional Record turns are speeches, and what each is titled.

A member's turn on the floor is often floor business, not a speech: "I ask
unanimous consent that the order for the quorum call be rescinded", "I
yield 2 minutes to the gentleman from Ohio", "I move to suspend the rules
and pass the bill". Indexed as speeches, those made the leader who rescinds
every quorum call the most prolific speaker in Explore (69 of one member's
75 speech documents, 2026-10-07 audit).

Zero-shot embedding classification (AGENTS.md principle 1), per paragraph:
the Record prints each of a turn's paragraphs on its own line, and floor
business comes as whole paragraphs of its own ("Mr. Speaker, I yield myself
such time as I may consume." opens most House debate speeches). A
paragraph is floor business when its best cosine similarity to the
procedural prototypes beats its best similarity to the speech prototypes
by more than PROCEDURAL_MARGIN; a turn is a speech when what remains
reaches MIN_SPEECH_CHARS.

Both prototype sets are in the Record's own register: the procedural ones
are its formulas, the speech ones sentences a member might open with.
Prototypes describing a speech instead ("A member's argument for or
against legislation...") lost to the procedural formulas on any paragraph
about a bill.

Calibrated 2026-10-08 on all-MiniLM-L6-v2 (the similarity model, below)
over 2,612 member turns parsed out of 14 days of the Record
(2026-03 to 2026-09, both chambers), in three strata by turn length, each
sampled at random and labelled by hand — a turn stating any view, however
short, counted as a speech:

  - under 300 characters (1,019 turns): 145 labelled, 129 floor business;
  - 300 to 3,000 (1,217 turns): 150 labelled, 12 floor business;
  - 3,000 and over (376 turns): every one taken as a speech, every drop
    counted.

Grid over the margin (-0.04 up, in steps of 0.02) and the floor (1 to 200
characters).
(0.00, 70) has the fewest estimated errors over the 2,612 turns, 79 (65
speeches dropped, 14 business turns kept); (0.04, 70), used here, has 85
(57 and 28): a dropped speech hides a member's words, a kept business
turn is clutter. At (0.04, 70), of the labelled turns: 7 short speeches
dropped and 4 business turns kept; 1 middle speech dropped, no business
kept; no long speech dropped. The speeches dropped are mostly one-line
closers ("I urge all Members to oppose the amendment, and I yield back")
and short colloquy replies; of roughly 1,000 business turns, the old
ingest indexed every one over 40 characters that it read.

Measured and rejected: speech prototypes that describe speeches rather
than sound like them, best total 103; procedural similarity alone (no
speech prototypes), best 155; on an earlier sample of 300 turns under
2,000 characters, turn length alone (best cut, 270 characters) erred on
38 and the whole turn's margin on 43 — a long House speech opening with
"I yield myself such time as I may consume" reads as floor business whole.

Change a prototype or either constant and re-measure; then bump
SPEECH_FORMAT in explore_pipeline.py so the Congress is re-read.
"""

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

PROCEDURAL_PROTOTYPES: tuple[str, ...] = (
    "I ask unanimous consent that the order for the quorum call be rescinded.",
    "I yield 2 minutes to the gentleman from Ohio. I yield myself such time as I may consume. "
    "I reserve the balance of my time. I yield back the balance of my time.",
    "I move to proceed to Calendar No. 300. I move to suspend the rules and pass the bill, as amended. "
    "On that I demand the yeas and nays. I send a cloture motion to the desk. I have an amendment at the desk.",
    "I ask unanimous consent that the bill be considered read a third time and passed and that the motion "
    "to reconsider be considered made and laid upon the table.",
    "I ask unanimous consent that when the Senate completes its business today, it adjourn until 10 a.m. tomorrow.",
    "The following Senators are necessarily absent.",
    "I ask unanimous consent that all Members may have 5 legislative days to revise and extend their remarks.",
)
SPEECH_PROTOTYPES: tuple[str, ...] = (
    "I rise in support of this bill because it will help families in my district who are struggling with rising costs.",
    "Under current law the agency has no authority to do this, and this legislation would change that and protect taxpayers.",
    "I rise today to honor the life of a dedicated public servant who gave decades of service to our community.",
    "The administration's policy is hurting working Americans, and Congress must act to stop it.",
    "I rise today to speak about an issue that matters to the people I represent.",
    "The facts show that this policy has failed, and here is why.",
    "I oppose this nomination because of the nominee's record.",
    "This is a matter of justice, and the government must be held accountable.",
)

PROCEDURAL_MARGIN = 0.04
MIN_SPEECH_CHARS = 70


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def speech_flags(texts: list[str], model=None) -> list[bool]:
    """True for each turn that is a speech rather than floor business."""
    if not texts:
        return []
    if model is None:
        from app.pipeline.vector_store import get_similarity_model
        model = get_similarity_model()
    paras: list[str] = []
    owner: list[int] = []
    for i, text in enumerate(texts):
        for p in _paragraphs(text):
            paras.append(p)
            owner.append(i)
    if not paras:
        return [False] * len(texts)
    emb, procedural, speech = (
        model.encode(list(xs), normalize_embeddings=True, show_progress_bar=False)
        for xs in (paras, PROCEDURAL_PROTOTYPES, SPEECH_PROTOTYPES)
    )
    margin = (emb @ procedural.T).max(axis=1) - (emb @ speech.T).max(axis=1)
    content = np.zeros(len(texts))
    np.add.at(content, np.array(owner), np.where(margin > PROCEDURAL_MARGIN, 0, [len(p) for p in paras]))
    return [bool(c >= MIN_SPEECH_CHARS) for c in content]


def _plain(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip()


def titled_speeches(turns: list[dict], flags: list[bool]) -> list[dict]:
    """The speeches among a day's parsed turns (in Record order, as
    congressional_record.parse_granule_speeches gives them, each with its
    ``granule_id``), each with a ``title``.

    A speech that opens a heading of the Record's is titled with it: the
    Record printed that heading over that member's words. Any other speech
    — a reply in a colloquy, a member speaking in a debate someone else
    opened — is "Remarks on <topic>", the topic being the nearest heading
    before it in the same granule that a speech opened, else the granule's
    own title. A heading opened only by floor business ("General Leave",
    "Vote on Amendment No. 6776") never becomes a topic: in the House it
    sits over the whole debate that follows it.
    """
    out: list[dict] = []
    topic: dict[str, str] = {}  # granule -> latest heading a speech opened
    for turn, is_speech in zip(turns, flags):
        granule = turn["granule_id"]
        heading = _plain(turn.get("heading") or "")
        if not is_speech:
            continue
        if turn.get("opens") and heading:
            topic[granule] = heading
            title = heading
        else:
            subject = topic.get(granule) or _plain(turn.get("granule_title") or "") or heading
            title = f"Remarks on {subject}" if subject else "Floor remarks"
        out.append({**turn, "title": title})
    return out
