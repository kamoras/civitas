"""Which roll calls a news story is related to: "Related votes in Congress".

Until 2026-10 an Action Center issue reached the record only through a bill
its articles named by number or short title, and news never prints
"S.J.Res. 185": 0 of 543 confirmed issues ever linked a bill, so the members'
votes on the issue never showed, and the Iran monitor (303 updates) showed
none of Congress's Iran war-powers votes, including a 212-212 House tie.

A story and a roll call are linked when the vote was taken in the days
around the story (WINDOW) and the story's headline is close to the vote's
measure title in two embedding spaces at once: all-MiniLM-L6-v2 (the
similarity model) and Snowflake Arctic-XS (the classification model), both
already loaded by the hourly refresh. The score is the mean of the two
cosines, and a pair counts at the calibrated threshold.

What is compared was measured on the replay (scripts/calibrate_roll_call_links.py,
the curve's best exact bound): the headline alone against the vote's title
alone did best (0.91). Adding the verified lede, an issue's facts or the
vote's question ("On the Motion to Discharge S.J.Res. 185", shared by every
vote of a kind) each lowered it (to 0.85, and below 0.80 with all of them,
where centring each side on its mean had been needed; with the titles alone
the plain cosine held out better, at twice the recall).

No model writes anything here: the link is a pair of stored records, shown
as "Related votes in Congress", never as a cause.

The threshold is fitted by scripts/calibrate_roll_call_links.py on
hand-labelled pairs (app/data/roll_call_link_examples.json): every stored
issue, monitor update and timeline entry with a roll call in its window,
against every such roll call. It is where the precision's exact
(Clopper-Pearson) lower 95% bound peaks, which must be at least 0.80 (the
Action Center's bar for anything shown as fact); the same choice made on
three blocks of dates and scored on the fourth is the held-out figure.
Measured 2026-10-17 on the masked examples: 0.64; held out, 53 of 57
links right (precision 0.93, exact bound 0.83), recall 0.22: it prefers
saying nothing to guessing.

Why hand labels: no data-supplied label existed. The articles named a bill
one of the candidate votes was on in 0 of the replay's stories, and a vote's
record was an issue's primary source in 2 (the committee-routing precedent).
Every structural link found from now on is kept as one
(record_structural_labels, RollCallLinkLabel) for the next recalibration.

Person names are masked as NAME_MASK in both texts (NameMask: every name the
site holds a record of) and left out of what is compared, as they are in the
checked-in examples. The gate's pass rate is watched by the calibration-drift
check (DRIFT_GATE, app/calibration_drift.py).
When the peak misses the bar nothing is linked (`threshold` is null). Rerun the script when the examples change:
test_calibration_matches_the_examples fails until you do.

Lives outside app/pipeline/ (like committee_routing), so it is outside the
analysis-code fingerprint: nothing classified or scored reads it, and its
only writer is the Action Center's hourly refresh.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import date, timedelta
from typing import Callable

import numpy as np

from app import calibration_drift

# Days from the story's date: a roll call from three days before to two
# after is a candidate (a story the day or two after a vote, or a preview of
# one). A convention, not a fit: the labelled pairs were drawn inside it, so
# the replay can't say what a wider one would add.
WINDOW = (-3, 2)

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
EXAMPLES_PATH = _DATA_DIR / "roll_call_link_examples.json"
CALIBRATION_PATH = _DATA_DIR / "roll_call_link_calibration.json"

# The name this gate's decisions are counted under by the calibration-drift
# check (app/calibration_drift.GATES).
DRIFT_GATE = "roll_call_links"

# How each chamber's record words the question on an amendment vote (the
# same forms presidential_support reads).
_AMENDMENT_QUESTIONS = ("On the Amendment", "On Agreeing to the Amendment")

# The two spaces, by name.
MODELS = ("minilm", "arctic")

_cache: dict = {}


def _load(path: pathlib.Path) -> dict:
    if path not in _cache:
        _cache[path] = json.loads(path.read_text())
    return _cache[path]


def examples() -> dict:
    return _load(EXAMPLES_PATH)


def calibration() -> dict:
    return _load(CALIBRATION_PATH)


def data_hash() -> str:
    """Identifies the labelled pairs a calibration was fitted to."""
    ex = examples()
    payload = json.dumps([ex["roll_calls"], [
        [i["date"], story_text(i["title"]), i["candidates"], i["related"]]
        for i in ex["items"]
    ]], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def story_text(title: str) -> str:
    """What a story is embedded as: its headline (an issue's title, a
    monitor update's article title). Measured on the replay: adding the
    verified lede lowered the best exact bound (0.88 to 0.85 with the vote's
    title alone), and adding an issue's facts lowered it further."""
    return _unmasked(title)


def _unmasked(text: str) -> str:
    """TEXT with each NAME_MASK left out. Masked names are what neither side
    is compared on: kept as a token, the mask made every story naming
    someone close to every nomination vote ("[name], of Florida, to be
    ..."), and the best exact bound fell from 0.92 to 0.79."""
    return re.sub(r"\s+", " ", text.replace(NAME_MASK, " ")).strip()


def is_amendment_vote(question: str | None) -> bool:
    return (question or "").startswith(_AMENDMENT_QUESTIONS)


def roll_call_text(question: str | None, title: str | None, purpose: str | None = None) -> str:
    """What a vote is embedded as: its measure title (the nominee and post,
    the bill's or resolution's title), or the chamber's question when the
    record has no title. The question ("On the Motion to Discharge S.J.Res.
    185") was measured and dropped: every vote of a kind shares it, and with
    it the best exact bound fell from 0.88 to 0.85. An amendment vote's
    title names only the amendment ("Paul Amdt. No. 6715"), so it is
    embedded as its stated purpose (RollCall.amendment_purpose, the record's
    own words), and "" when none is stored."""
    if is_amendment_vote(question):
        return _unmasked(purpose or "")
    return _unmasked(title or question or "")


def linkable(question: str | None, purpose: str | None = None) -> bool:
    """Whether a vote says what was voted on: every vote but an amendment
    vote with no stated purpose stored. Without one, an amendment's words
    are its sponsor's name and state ("Mace of South Carolina Part A
    Amendment No. 20"), which in the replay matched stories about the state.
    Housekeeping questions are left out by the caller, as everywhere votes
    are compared (normalize_votes.is_housekeeping)."""
    return bool(roll_call_text(question, "x", purpose))


def roll_call_ref(chamber: str, congress: int, session: int, number: int) -> str:
    """How a link is stored: the vote's identity in its chamber's record
    ("senate-119-2-584"), the key the members endpoint already uses."""
    return f"{chamber}-{congress}-{session}-{number}"


def in_window(story_day: str, vote_day: str) -> bool:
    gap = (date.fromisoformat(vote_day) - date.fromisoformat(story_day)).days
    return WINDOW[0] <= gap <= WINDOW[1]


def window_dates(story_day: str) -> tuple[str, str]:
    """The first and last vote dates a story's window takes, as ISO days."""
    d = date.fromisoformat(story_day)
    return str(d + timedelta(days=WINDOW[0])), str(d + timedelta(days=WINDOW[1]))


def encoders() -> dict[str, Callable[[list[str]], np.ndarray]]:
    from app.pipeline.vector_store import get_embedding_model, get_similarity_model

    def enc(get):
        return lambda texts: get().encode(texts, show_progress_bar=False, normalize_embeddings=True)

    return {"minilm": enc(get_similarity_model), "arctic": enc(get_embedding_model)}


def scores(story_vecs: dict[str, np.ndarray], vote_vecs: dict[str, np.ndarray]) -> np.ndarray:
    """(stories, votes): the mean over MODELS of the cosine between the two
    (the encoders return unit vectors)."""
    return sum(story_vecs[m] @ vote_vecs[m].T for m in MODELS) / len(MODELS)


def link_scored(stories: list[tuple[str, str]], votes: list[tuple[str, str, str]],
                encode: dict[str, Callable[[list[str]], np.ndarray]] | None = None,
                cal: dict | None = None) -> list[tuple[list[str], float | None]]:
    """For each story (its ISO day, its story_text): the refs of the votes
    (ref, ISO day, roll_call_text) in its window that clear the calibrated
    threshold, best first, and its best score (None when no vote is in its
    window). No links for any story when the calibration met no threshold.
    The caller passes linkable votes only (linkable())."""
    cal = cal or calibration()
    threshold = cal.get("threshold")
    out: list[tuple[list[str], float | None]] = [([], None) for _ in stories]
    if threshold is None or not stories or not votes:
        return out
    pairs = [[j for j, (_, day, _) in enumerate(votes) if in_window(sd, day)] for sd, _ in stories]
    wanted_votes = sorted({j for js in pairs for j in js})
    wanted_stories = [i for i, js in enumerate(pairs) if js]
    if not wanted_votes:
        return out
    encode = encode or encoders()
    sv = {m: encode[m]([stories[i][1] for i in wanted_stories]) for m in MODELS}
    vv = {m: encode[m]([votes[j][2] for j in wanted_votes]) for m in MODELS}
    s = scores(sv, vv)
    col = {j: c for c, j in enumerate(wanted_votes)}
    for row, i in enumerate(wanted_stories):
        scored = [(float(s[row, col[j]]), votes[j][0]) for j in pairs[i]]
        hits = sorted(((sc, ref) for sc, ref in scored if sc >= threshold), reverse=True)
        out[i] = ([ref for _, ref in hits], max(sc for sc, _ in scored))
    return out


def link(stories: list[tuple[str, str]], votes: list[tuple[str, str, str]],
         encode: dict[str, Callable[[list[str]], np.ndarray]] | None = None,
         cal: dict | None = None) -> list[list[str]]:
    """link_scored's refs alone."""
    return [refs for refs, _ in link_scored(stories, votes, encode, cal)]


# Rows re-matched every run: a story's window ends WINDOW[1] days after it,
# and roll calls are fetched nightly, so a vote can be stored a few days after
# the story it belongs to. A week covers the window and a missed night or two.
RECHECK_DAYS = 7
# Rows never matched (stored before this existed, or reset by a new
# calibration), newest first, per run: bounded so one hourly refresh embeds
# at most a few hundred stories.
BACKFILL_PER_RUN = 200
# Which calibration the stored links were made with; a different one resets
# them all to unmatched, and the backfill redoes them.
_MARKER_TIER, _MARKER_KEY = "roll-call-links", "calibration"
_MARKER_HOURS = 24 * 365


# What a person's name becomes in both texts before they are compared, and in
# the checked-in examples: a story is linked to a vote by what it is about,
# not who is in it, and no checked-in file names a politician (AGENTS.md,
# evergreen files). The same mask as the relevance gate's seed.
NAME_MASK = "[name]"
# The Senate's nomination vote title: "<Name>, of <State>, to be ..."
# (trend_evidence reads it the same way).
_NOMINEE_TITLE = re.compile(r"^([^,]+),\s+of\s+")


def name_forms(name: str) -> set[str]:
    """How a recorded name is printed in a headline: the whole name, and the
    first given name or a quoted nickname with the surname ("Donald J.
    Trump" -> "donald j trump", "donald trump"; FEC's "SMITH, JOHN Q" ->
    "john q smith", "john smith"), folded to lower case and no accents."""
    from app.pipeline.analyze.action_center import _record_name

    rec = _record_name(name)
    if not rec.surname:
        return set()
    forms = {" ".join([*rec.given, rec.surname])}
    forms |= {f"{g} {rec.surname}" for g in [*rec.given[:1], *rec.nicknames] if len(g) > 1}
    return {f for f in forms if " " in f}


class NameMask:
    """Replaces every name of a person the site holds a record of with
    NAME_MASK: members of Congress (past and sitting), justices,
    presidents, candidates, and the nominees the Senate's own roll-call
    titles name; and the sitting president's surname alone, as the
    relevance gate masks it. A bare surname is not masked otherwise: it is
    too often a word, a place or someone else (policy_relevance.OfficialNames)."""

    def __init__(self, names, president: str | None = None):
        from app.pipeline.analyze.action_center import _record_name

        forms: set[str] = set()
        for n in names:
            forms |= name_forms(n)
        if president and (surname := _record_name(president).surname):
            forms.add(surname)
        ordered = sorted(forms, key=len, reverse=True)
        # Words of a form may be split by spaces or an initial's period.
        alternatives = "|".join(r"[\s.]+".join(map(re.escape, f.split())) for f in ordered)
        self._pattern = re.compile(rf"(?<![\w'])({alternatives})(?!\w)", re.IGNORECASE) if ordered else None

    def __call__(self, text: str) -> str:
        return self._pattern.sub(NAME_MASK, text) if self._pattern and text else (text or "")

    @classmethod
    def from_db(cls, db) -> "NameMask":
        from app.models import Candidate, Justice, President, Representative, RollCall, Senator

        names = [n for model in (Senator, Representative, Justice, President, Candidate)
                 for (n,) in db.query(model.name)]
        names += [m.group(1) for (t,) in db.query(RollCall.title).filter(RollCall.chamber == "senate")
                  if t and (m := _NOMINEE_TITLE.match(t))]
        president = db.query(President.name).filter(President.is_current.is_(True)).scalar()
        return cls(names, president)


def _version(cal: dict) -> str:
    return f"{cal.get('data_hash')}:{cal.get('threshold')}"


def refresh_links(db, today: str, encode=None, cal: dict | None = None) -> int:
    """Match roll calls to the issues and monitor updates that need it (the
    last RECHECK_DAYS days, and up to BACKFILL_PER_RUN unmatched rows) and
    store them. Returns how many rows were written. Runs in the hourly
    Action Center refresh, where both models are loaded; the API only reads
    the stored refs."""

    from app.models import ActionIssue, MonitorUpdate, RollCall
    from app.pipeline.cache import api_cache_get, api_cache_set
    from app.pipeline.transform.normalize_votes import is_housekeeping

    cal = cal or calibration()
    version = _version(cal)
    if (api_cache_get(db, _MARKER_TIER, _MARKER_KEY, max_age_hours=_MARKER_HOURS) or {}).get("version") != version:
        db.query(ActionIssue).update({ActionIssue.related_roll_call_refs: None}, synchronize_session=False)
        db.query(MonitorUpdate).update({MonitorUpdate.roll_call_refs: None}, synchronize_session=False)
    # Rewritten every run, so the 60-day api_cache prune never takes it.
    api_cache_set(db, _MARKER_TIER, _MARKER_KEY, {"version": version}, commit=False)

    since = str(date.fromisoformat(today) - timedelta(days=RECHECK_DAYS))
    rows: list = []
    for model, col in ((ActionIssue, ActionIssue.related_roll_call_refs), (MonitorUpdate, MonitorUpdate.roll_call_refs)):
        rows += db.query(model).filter(model.date >= since).all()
        rows += (
            db.query(model).filter(col.is_(None), model.date < since)
            .order_by(model.date.desc(), model.id.desc()).limit(BACKFILL_PER_RUN).all()
        )
    if not rows:
        return 0
    days = [r.date for r in rows]
    lo, hi = window_dates(min(days))[0], window_dates(max(days))[1]
    votes = [
        (roll_call_ref(rc.chamber, rc.congress, rc.session, rc.number), rc.date,
         roll_call_text(rc.question, rc.title, rc.amendment_purpose))
        for rc in db.query(RollCall).filter(RollCall.date >= lo, RollCall.date <= hi)
        if linkable(rc.question, rc.amendment_purpose) and not is_housekeeping(rc.question, rc.chamber)
    ]
    titles = [r.title if isinstance(r, ActionIssue) else r.article_title for r in rows]
    mask = NameMask.from_db(db)
    votes = [(ref, day, mask(text)) for ref, day, text in votes]
    stories = [(r.date, mask(story_text(t or ""))) for r, t in zip(rows, titles)]
    first_time = [_stored(r) is None for r in rows]
    scored = link_scored(stories, votes, encode=encode, cal=cal)
    # The drift check (app/calibration_drift.py) counts each story once,
    # when it is first matched and has a vote in its window: the hourly
    # recheck of the last week would count a story about 168 times, and a
    # day with no votes (a recess) decides nothing.
    if cal.get("threshold") is not None:
        calibration_drift.observe(
            DRIFT_GATE, [best for (_, best), new in zip(scored, first_time) if new and best is not None],
            cal["threshold"],
        )
    record_structural_labels(db, [r for r in rows if isinstance(r, ActionIssue)], votes, mask)
    links = [refs for refs, _ in scored]
    for r, refs in zip(rows, links):
        value = json.dumps(refs)
        if isinstance(r, ActionIssue):
            r.related_roll_call_refs = value
        else:
            r.roll_call_refs = value
    db.flush()
    return len(rows)


def _stored(row) -> str | None:
    return row.related_roll_call_refs if hasattr(row, "related_roll_call_refs") else row.roll_call_refs


def record_structural_labels(db, issues: list, votes: list[tuple[str, str, str]], mask: NameMask) -> int:
    """Keep, as data-supplied labels for the next recalibration, every issue
    that reached a vote structurally: its primary source is the vote's own
    record (a roll-call draft), or its articles named a bill by number or
    short title (related_bill_ids) and a vote in its window was on that bill
    in that Congress. Each label is the issue's masked headline, every
    linkable vote in its window with its masked text (the candidates), and
    the structurally linked ones (`related`), in RollCallLinkLabel, one row
    per issue, rewritten as the window fills. There was no such label when
    the calibration was first fitted (0 article-named bills, 2 primary
    sources among 550 issues), which is why its labels are by hand."""
    from app.models import RollCall, RollCallLinkLabel

    in_window_votes = {ref: (day, text) for ref, day, text in votes}
    written = 0
    for issue in issues:
        bills = {(b["id"].upper(), b["congress"]) for b in _bill_entries(issue.related_bill_ids)}
        if not bills and not issue.primary_source_url:
            continue
        lo, hi = window_dates(issue.date)
        related, sources = [], set()
        for rc in db.query(RollCall).filter(RollCall.date >= lo, RollCall.date <= hi):
            ref = roll_call_ref(rc.chamber, rc.congress, rc.session, rc.number)
            if ref not in in_window_votes:
                continue
            if issue.primary_source_url and rc.source_url == issue.primary_source_url:
                related.append(ref)
                sources.add("primary_source")
            elif ((rc.bill_id or "").upper(), rc.congress) in bills:
                related.append(ref)
                sources.add("bill_named")
        if not related:
            continue
        candidates = {ref: text for ref, (day, text) in in_window_votes.items() if in_window(issue.date, day)}
        row = db.query(RollCallLinkLabel).filter_by(issue_id=issue.id).one_or_none()
        if row is None:
            row = RollCallLinkLabel(issue_id=issue.id)
            db.add(row)
        row.date = issue.date
        row.story = mask(story_text(issue.title or ""))
        row.source = ",".join(sorted(sources))
        row.related = json.dumps(sorted(related))
        row.candidates = json.dumps(candidates, sort_keys=True)
        written += 1
    return written


def _bill_entries(raw: str | None) -> list[dict]:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [b for b in value if isinstance(b, dict) and b.get("id") and isinstance(b.get("congress"), int)] \
        if isinstance(value, list) else []


def parse_ref(ref: str) -> tuple[str, int, int, int] | None:
    """("senate", 119, 2, 244) from "senate-119-2-244"; None for anything else."""
    parts = ref.split("-") if isinstance(ref, str) else []
    if len(parts) != 4 or parts[0] not in ("house", "senate") or not all(p.isdigit() for p in parts[1:]):
        return None
    return parts[0], int(parts[1]), int(parts[2]), int(parts[3])


def stored_refs(raw: str | None) -> list[str]:
    """The refs a row stores, [] when unmatched or unreadable."""
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [r for r in value if parse_ref(r)] if isinstance(value, list) else []
