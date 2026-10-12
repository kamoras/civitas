"""Who an Action Center story is principally about.

Each issue's principal actor is stored when it publishes
(ActionIssue.principal_actor), and the admin dashboard charts the weekly
concentration of those actors (``GET /api/admin/actor-concentration``), so a
feed drifting toward one person shows up as a number, not an impression.

The actor comes from the pipeline's own extraction first: of the officials
_find_related_officials matched (members by full name or an unshared
surname, the sitting president, justices), the one named earliest in the
HEADLINE. The headline, not the lede, because the headline is what a reader
sees and what the measured complaint was about (2026-10-10: 37% of the last
90 headlines named the president).

A headline that names no tracked official gets a class instead: a
similarity-weighted vote among the STORY_CLASS_K nearest of the relevance
gate's hand-labelled articles (app/data/policy_relevance_examples.json),
whose categories say who acts — U.S. government, courts, an election,
foreign politics, business, crime, a disaster (kNN, Cover & Hart 1967; the
labelled set is the same human knowledge that seeds the relevance gate).
Prototype sentences were tried first and measured useless here: on
hand-labelled stories the government/non-government prototype margin caught
0 of 11 held-out non-government stories at a margin that was precise on the
training dates (both models, 2026-10-12).

Rules measured and not shipped (2026-10-12). Every hourly run since the
claim layer (2026-09-24) was replayed from its recorded candidates
(llm_generation_samples): the replay reproduces all 33 news issues
production published in that span. Rule parameters were chosen on
2026-09-24..10-03 and tested unchanged on 10-04..10-12.

- Concentration as measured: 37 of the last 90 news issues (41%) are led by
  the president. The weekly Herfindahl index of principal actors ran
  0.08-0.30 from July to mid-September, then 0.24, 0.34 and 0.46 in the
  three weeks to 2026-10-11 (11 of 17 issues that last week led by the
  president).
- A cap of one issue per person per day: held out, the president's share fell
  from 10 of 16 issues to 5 of 11. But nothing replaced the 5 refused stories,
  not even with any eligible story composed at any hour of the same day,
  because every story that passed the gates that day had already published.
  All five were real civic stories (a methane-rule proposal, a DOJ inquiry
  into networks over press-pool coverage, a Hatch Act rule change...), so the
  cap would have bought balance by publishing 31% fewer real issues. A cap of
  two changed nothing.
- Demoting stories with no government actor (the class vote above): it
  changed nothing in either window, because a demoted story still published
  when no other candidate passed. On the stories that do publish, it flagged
  3 held out, and 2 were civic (a lawsuit against the government, a state
  execution), so it would have demoted more real stories than corporate ones.

The concentration comes from supply, not ordering: of the stories that pass
every gate, most are led by the president. What to watch is the weekly
figure on the admin dashboard.
"""

import re
from collections.abc import Sequence

import numpy as np

# The label every story about the sitting president shares.
PRESIDENT = "president"

# The president's mentions, as _find_related_officials attributes a story to
# him (it matches these besides his surname and full name).
PRESIDENT_PHRASES = ("the president", "the white house", "executive order")

# Relevance-example categories in which no government acts.
NO_GOVERNMENT_CATEGORIES = frozenset({
    "business", "crime", "human_interest", "science_health", "sports_ent", "disaster", "other",
})

# Neighbours in the class vote; the class is the vote's largest category.
# Measured on 200 hand-labelled candidate stories whose headline names no
# official (2026-10-12): as a no-government-actor test, on the stories
# before 2026-09-24 it found 10 of 21 at 4 false alarms, and on the 55 held
# out after, 8 of 12 at 3 false alarms. Good enough to WATCH a weekly share,
# not to act on one story (see "Rules measured and not shipped" above).
STORY_CLASS_K = 5


def _first_position(text_lower: str, forms: Sequence[str]) -> int | None:
    hits = []
    for form in forms:
        form = form.strip().lower()
        if form:
            m = re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)", text_lower)
            if m:
                hits.append(m.start())
    return min(hits) if hits else None


def _label(official: dict) -> str:
    if official.get("branch") == "president":
        return PRESIDENT
    return f"{official.get('branch')}:{official.get('id')}"


def _forms(official: dict) -> list[str]:
    name = official.get("name") or ""
    forms = [name, name.split()[-1]] if name else []
    if official.get("branch") == "president":
        forms += PRESIDENT_PHRASES
    return forms


def headline_official(title: str, officials: Sequence[dict]) -> str | None:
    """The label of the official named earliest in the headline, or None."""
    lower = (title or "").lower()
    best: tuple[int, str] | None = None
    for official in officials:
        pos = _first_position(lower, _forms(official))
        if pos is not None and (best is None or pos < best[0]):
            best = (pos, _label(official))
    return best[1] if best else None


def story_class(
    embedding: np.ndarray, reference: np.ndarray, categories: Sequence[str], k: int = STORY_CLASS_K,
) -> str:
    """``class:<category>`` by a similarity-weighted vote of the k nearest
    labelled examples (unit vectors; negative similarities weigh nothing)."""
    sims = reference @ embedding
    k = min(k, len(sims))
    nearest = np.argpartition(-sims, k - 1)[:k]
    votes: dict[str, float] = {}
    for i in nearest:
        votes[categories[i]] = votes.get(categories[i], 0.0) + max(float(sims[i]), 0.0)
    return "class:" + max(votes, key=votes.get)


def has_government_actor(label: str) -> bool:
    return not (label.startswith("class:") and label[len("class:"):] in NO_GOVERNMENT_CATEGORIES)


def herfindahl(labels: Sequence[str]) -> float | None:
    """Herfindahl-Hirschman index of a set of principal actors: the sum of
    squared shares. 1.0 when one actor leads every story, 1/n when each of
    n stories has its own. None for an empty set."""
    if not labels:
        return None
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sum((c / len(labels)) ** 2 for c in counts.values())
