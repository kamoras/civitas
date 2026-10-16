"""Site search: from what a reader types to the page (or the section of a
page) that holds it.

Reads the entity index `app/site_search_index.py` writes, and Explore's own
keyword index for document titles. Lexical only: an identifier parser, FTS5
prefix matching, trigram matching for typos, and a deterministic score. No
model and no LLM on this path (AGENTS.md, "Read path must stay
lightweight").

Score = match + importance_weight * importance, where match is 1.0 for an
exact identifier (a bill number, a district code, a nomination, an FR
document number), 0.95 for a query equal to a name, and below that the
share of the query's words found in a name (a whole word counting 1, a
prefix `prefix_credit`, a near spelling `fuzzy_credit` times its
similarity), discounted by how much of the name went unmatched
(`label_floor`). Importance is a property of the entity (a sitting member
over a former one, a recent issue over an old one; site_search_index).

A query goes straight to its page ("direct") when the best match is at
least `direct_floor` and leads the next page by at least `direct_margin`:
a wrong jump is worse than a list. Every parameter is fitted on known-item
queries built from the index itself by scripts/calibrate_site_search.py and
read from app/data/site_search_calibration.json.

Nothing here logs or keeps a query (AGENTS.md §8).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import text

from app.pipeline.analyze.document_authority import (
    _EO_ABBR_RE, _EO_RE, _EO_TITLE_RE, _FR_CITE_RE, _FR_DOC_RE, _PROC_RE, _RIN_RE, _normalize_rin,
)
from app.pipeline.lexical_index import build_match_expression
from app.services.bill_record import canonical_bill_id
from app.services.congress_service import bill_label
from app.site_search_index import KIND_GROUPS, SITE_SEARCH_DB, district_code, normalize
from app.state_names import STATE_NAMES
from app.time_utils import comment_period_today

_DATA = Path(__file__).parent / "data"
CALIBRATION_PATH = _DATA / "site_search_calibration.json"
SECTIONS_PATH = _DATA / "site_search_sections.json"

# A name matching less than this much of the query is not listed at all.
# Below it the "matches" are a shared short word ("of", a state's code).
LIST_FLOOR = 0.3
# Candidates read from each index before scoring: enough that the best name
# is always among them (an FTS5 bm25 order is close to ours), few enough to
# score in a few milliseconds.
_POOL = 120
# Long enough for a pasted Federal Register title.
QUERY_MAX = 300

_STATE_BY_NAME = {normalize(n): c for c, n in STATE_NAMES.items()}


# ── Calibration and section data ─────────────────────────────────

@dataclass(frozen=True)
class Params:
    prefix_credit: float = 0.8
    fuzzy_credit: float = 0.7
    label_floor: float = 0.6
    importance_weight: float = 0.15
    direct_margin: float = 0.2
    direct_floor: float = 0.85


@lru_cache(maxsize=1)
def params() -> Params:
    """The fitted parameters (scripts/calibrate_site_search.py), or the
    defaults above before it has run."""
    try:
        fitted = json.loads(CALIBRATION_PATH.read_text())["params"]
    except (OSError, ValueError, KeyError):
        return Params()
    return Params(**{k: float(v) for k, v in fitted.items() if k in Params.__dataclass_fields__})


_STOP = {"the", "from", "where", "comes", "by", "of", "and", "to", "a", "in", "on", "for", "with", "top"}


@lru_cache(maxsize=1)
def sections() -> list[tuple[str, str, str, frozenset[str]]]:
    """(page kind, anchor, its first heading, the headings' words)."""
    data = json.loads(SECTIONS_PATH.read_text())["kinds"]
    return [
        (kind, s["anchor"], s["headings"][0],
         frozenset(w for h in s["headings"] for w in normalize(h).split() if w not in _STOP and len(w) >= 3))
        for kind, rows in data.items() for s in rows
    ]


# The entity kinds whose page each section kind is: a district's entry
# leads to its member's page.
_SECTION_KINDS = {"member": {"member", "district"}, "bill": {"bill"}}


# ── Identifiers ──────────────────────────────────────────────────

_DISTRICT_CODE = re.compile(r"^([a-z]{2})\s*-?\s*(\d{1,2}|al)$")
_DISTRICT_NAMED = re.compile(r"^(.+?)\s+(?:district\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+district)?$")
_PN = re.compile(r"^pn\s*(\d{1,5})(?:\s*-\s*(\d{1,3}))?$")
_ROLL_CALL = re.compile(r"^(senate|house)?\s*(?:roll\s*call|vote|rc)\s*(?:no\s*)?(\d{1,4})$")
_FR_DOC_BARE = re.compile(r"^\d{4}-\d{4,6}$")
_ISSUE = re.compile(r"^issue[\s-]*([0-9a-z]{6,})$")


def identifier_keys(query: str) -> list[str]:
    """The index keys a query names exactly, if it is an identifier: a bill
    number in any spelling, a district code ("TX-23", "tx23", "Texas 23rd",
    "AK-AL"), a nomination (PN123, PN123-4), a roll call ("senate roll call
    252"), an FR document number, FR citation, executive order,
    proclamation or RIN, a state, an issue's public id. [] otherwise."""
    raw = (query or "").strip()
    q = normalize(raw)
    if not q:
        return []
    keys: list[str] = []
    bill = canonical_bill_id(raw)
    if bill and any(ch.isdigit() for ch in bill):
        keys.append(f"bill:{bill}")
    if m := _DISTRICT_CODE.match(q):
        code, number = m.group(1).upper(), m.group(2)
        if code in STATE_NAMES:
            keys.append(f"district:{district_code(code, 0 if number == 'al' else int(number))}")
    elif m := _DISTRICT_NAMED.match(q):
        code = _STATE_BY_NAME.get(m.group(1)) or (m.group(1).upper() if m.group(1).upper() in STATE_NAMES else None)
        if code:
            keys.append(f"district:{district_code(code, int(m.group(2)))}")
    if q in _STATE_BY_NAME or (len(q) == 2 and q.upper() in STATE_NAMES):
        keys.append(f"state:{_STATE_BY_NAME.get(q) or q.upper()}")
    if m := _PN.match(raw.lower()):
        keys.append(f"pn:pn{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else ""))
    if m := _ROLL_CALL.match(q):
        keys.append(f"rc:{m.group(1)}:{int(m.group(2))}" if m.group(1) else f"rc:{int(m.group(2))}")
    if m := _ISSUE.match(q.replace(" ", "")):
        keys.append(f"issue:{m.group(1)}")
    if _FR_DOC_BARE.match(raw):
        keys.append(f"frdoc:{raw}")
    for pattern, fmt in ((_EO_RE, "eo:{}"), (_EO_ABBR_RE, "eo:{}"), (_PROC_RE, "proc:{}")):
        if m := re.compile(pattern.pattern, re.IGNORECASE).fullmatch(raw):
            keys.append(fmt.format(int(m.group(1))))
    if m := re.compile(_EO_TITLE_RE.pattern, re.IGNORECASE).fullmatch(raw):
        keys.append(f"eo:{int(m.group(1))}")
    if m := re.compile(_FR_CITE_RE.pattern, re.IGNORECASE).fullmatch(raw):
        keys.append(f"fr:{int(m.group(1))}-{int(m.group(2))}")
    if m := re.compile(_FR_DOC_RE.pattern, re.IGNORECASE).fullmatch(raw):
        keys.append(f"frdoc:{m.group(1)}")
    if m := re.compile(_RIN_RE.pattern, re.IGNORECASE).fullmatch(raw):
        keys.append(f"rin:{_normalize_rin(m.group(1))}")
    return list(dict.fromkeys(k.lower() for k in keys))


# ── Matching ─────────────────────────────────────────────────────

@dataclass
class Hit:
    kind: str
    label: str
    context: str
    url: str
    importance: float
    names: tuple[str, ...]
    match: float = 0.0
    score: float = 0.0
    section: str | None = None
    exact: bool = False

    def public(self) -> dict:
        return {
            "kind": self.kind, "group": KIND_GROUPS.get(self.kind, self.kind),
            "label": self.label + (f" · {self.section}" if self.section else ""),
            "context": self.context, "url": self.url,
        }


@lru_cache(maxsize=65536)
def _ratio(a: str, b: str) -> float:
    """Ratcliff-Obershelp similarity (difflib), 0 below 0.75 without the
    full computation when its cheap upper bound already says so."""
    m = SequenceMatcher(None, a, b)
    if m.real_quick_ratio() < 0.75 or m.quick_ratio() < 0.75:
        return 0.0
    return m.ratio()


def _token_credit(t: str, nt: str, p: Params) -> float:
    if nt == t:
        return 1.0
    if nt.startswith(t):
        return p.prefix_credit
    if len(t) >= 4 and abs(len(t) - len(nt)) <= 2:
        r = _ratio(t, nt)
        if r >= 0.75:
            return p.fuzzy_credit * r
    return 0.0


def name_match(q: str, name: str, p: Params) -> float:
    """How well a normalized query matches one normalized name, 0-0.95."""
    if q == name:
        return EXACT
    qt, nt = q.split(), name.split()
    if not qt or not nt:
        return 0.0
    total, used = 0.0, set()
    for t in qt:
        best, where = 0.0, None
        for j, w in enumerate(nt):
            c = _token_credit(t, w, p)
            if c > best or (c == best and c > 0 and j not in used and where in used):
                best, where = c, j
        total += best
        if where is not None:
            used.add(where)
    m = 0.9 * (total / len(qt)) * (p.label_floor + (1 - p.label_floor) * len(used) / len(nt))
    if len(q) >= 5 and m < 0.9 * p.fuzzy_credit and abs(len(q) - len(name)) <= len(q) // 4:
        r = _ratio(q, name)
        if r >= 0.8:
            m = max(m, 0.9 * p.fuzzy_credit * r)
    return min(m, 0.94)


def _fts_phrase(token: str, prefix: bool) -> str:
    return '"' + token.replace('"', "") + '"' + ("*" if prefix else "")


def _trigrams(q: str) -> list[str]:
    grams = []
    for t in q.split():
        grams += [t[i:i + 3] for i in range(len(t) - 2)]
    return list(dict.fromkeys(grams))


class SearchUnavailable(Exception):
    """The index file is missing or unreadable (before the first build)."""


def _connect(path: str) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM entity LIMIT 1")
    except sqlite3.Error as exc:
        raise SearchUnavailable(str(exc)) from None
    return conn


def _candidates(conn: sqlite3.Connection, raw: str, q: str) -> tuple[dict[int, Hit], set[int]]:
    rows: dict[int, tuple] = {}
    key_ids: set[int] = set()
    keys = identifier_keys(raw)
    if keys:
        marks = ",".join("?" * len(keys))
        for (eid,) in conn.execute(f"SELECT entity_id FROM entity_key WHERE key IN ({marks})", keys):
            key_ids.add(eid)
    tokens = q.split()
    ids: list[int] = list(key_ids)
    if tokens:
        exprs = [" ".join(_fts_phrase(t, True) for t in tokens)]
        if len(tokens) > 1:
            exprs.append(" OR ".join(_fts_phrase(t, True) for t in tokens))
        for expr in exprs:
            ids += [r[0] for r in conn.execute(
                "SELECT rowid FROM entity_fts WHERE entity_fts MATCH ? ORDER BY bm25(entity_fts) LIMIT ?",
                (expr, _POOL))]
            if len(ids) >= 20:
                break
        grams = _trigrams(q)
        if grams and len(ids) < 20:
            ids += [r[0] for r in conn.execute(
                "SELECT rowid FROM entity_tri WHERE entity_tri MATCH ? ORDER BY bm25(entity_tri) LIMIT ?",
                (" OR ".join(_fts_phrase(g, False) for g in grams), _POOL))]
    ids = list(dict.fromkeys(ids))
    if ids:
        marks = ",".join("?" * len(ids))
        for row in conn.execute(
            f"SELECT id, kind, label, context, url, importance, names FROM entity WHERE id IN ({marks})", ids
        ):
            rows[row[0]] = row
    return {
        eid: Hit(kind, label, context, url, importance, tuple(names.split(" | ")))
        for eid, (_, kind, label, context, url, importance, names) in rows.items()
    }, key_ids


def _documents(db, q: str) -> list[Hit]:
    """Explore documents whose titles hold every word of the query (the
    last as a prefix), from Explore's own index."""
    tokens = q.split()
    if db is None or not tokens or len(q.replace(" ", "")) < 3:
        return []
    expr = "title : (" + " AND ".join(_fts_phrase(t, i == len(tokens) - 1) for i, t in enumerate(tokens)) + ")"
    try:
        rows = db.execute(text(
            "SELECT d.id, d.title, d.doc_type, d.date, d.comments_close_on, d.comment_url "
            "FROM explore_fts JOIN explore_documents d ON d.id = explore_fts.rowid "
            "WHERE explore_fts MATCH :m ORDER BY bm25(explore_fts, 1.0, 0.0, 0.0) LIMIT 20"
        ), {"m": expr}).fetchall()
    except Exception:
        db.rollback()
        return []
    today = comment_period_today()
    out = []
    for did, title, dtype, day, closes, comment_url in rows:
        open_comment = bool(comment_url) and (closes or "") >= today
        context = f"{dtype} · {day}" + (f" · comments close {closes}" if open_comment else "")
        out.append(Hit("document", title, context, f"/explore/{did}", 0.6 if open_comment else 0.4,
                       (normalize(title),)))
    return out


def gather(raw: str, path: str | None = None, db=None) -> tuple[str, list[Hit]]:
    """The normalized query and every candidate for it, unscored: an exact
    identifier's entity marked `exact`."""
    q = normalize((raw or "")[:QUERY_MAX])
    if not q:
        return q, []
    conn = _connect(path or SITE_SEARCH_DB)
    try:
        hits, key_ids = _candidates(conn, raw[:QUERY_MAX], q)
    finally:
        conn.close()
    for eid in key_ids & hits.keys():
        hits[eid].exact = True
    extra = []
    if not key_ids:
        # A bill number of this Congress that no member's list carries
        # still has a page (the bill page reads any bill from Congress.gov).
        for key in identifier_keys(raw[:QUERY_MAX]):
            if key.startswith("bill:"):
                bill_id = key[5:].upper()
                label = bill_label(bill_id)
                if label:
                    extra.append(Hit("bill", label, "Bill, this Congress", f"/congress/bills/{quote(bill_id)}",
                                     0.5, (normalize(label),), exact=True))
    return q, [*hits.values(), *extra, *_documents(db, q)]


def score(q: str, hits: list[Hit], p: Params) -> list[Hit]:
    """`hits` scored under `p`, best first, one per page, the unlisted
    dropped."""
    try:
        for h in hits:
            h.match = 1.0 if h.exact else max(name_match(q, n, p) for n in h.names)
            h.score = h.match + p.importance_weight * h.importance
    finally:
        # Token similarities are memoized within one query only: nothing
        # a reader typed outlives the request (AGENTS.md §8).
        _ratio.cache_clear()
    best: dict[str, Hit] = {}
    for h in sorted(hits, key=lambda h: (-h.score, h.label)):
        if h.match >= LIST_FLOOR and h.url not in best:
            best[h.url] = h
    return list(best.values())


def rank(raw: str, path: str | None = None, db=None, p: Params | None = None) -> list[Hit]:
    """Every listed match for `raw`, best first, one per page."""
    return score(*gather(raw, path, db), p or params())


# A match this good is the query itself (an identifier, or a name equal to
# it). Two such pages are two answers, whatever their importance says.
EXACT = 0.95


def is_direct(ranked: list[Hit], p: Params | None = None) -> bool:
    """Whether the best match is clearly the page meant: not one of two
    pages the query names exactly ("NE": Nebraska and an agency), and
    either an identifier's page or a match good enough (`direct_floor`) and
    clear of the next by `direct_margin`."""
    p = p or params()
    if not ranked or ranked[0].match < p.direct_floor:
        return False
    if len(ranked) == 1:
        return True
    if ranked[1].match >= EXACT:
        return False
    # An identifier names one page by definition (a bill number, a
    # district code): no margin to clear, only no second exact answer.
    return ranked[0].exact or ranked[0].score - ranked[1].score >= p.direct_margin


def _section_intent(q: str) -> list[tuple[str, str, str, list[str]]]:
    """(section kind, anchor, heading, the rest of the query) for each way
    the query's first or last one or two words name a section heading."""
    tokens = q.split()
    out = []
    for n in (2, 1):
        if len(tokens) <= n:
            continue
        for words, rest in ((tokens[-n:], tokens[:-n]), (tokens[:n], tokens[n:])):
            for kind, anchor, heading, vocab in sections():
                if all(any(w == v or (len(w) >= 4 and (v.startswith(w) or w.startswith(v))) for v in vocab)
                       for w in words):
                    out.append((kind, anchor, heading, rest))
    return out


@dataclass
class Answer:
    ranked: list[Hit]
    direct: Hit | None


def answer(raw: str, path: str | None = None, db=None, p: Params | None = None) -> Answer:
    """The ranked matches and, when one page is clearly meant, that page:
    with a section anchor when the query's other words name a section of it
    ("H.R. 1 votes", "<member> donors")."""
    p = p or params()
    q = normalize((raw or "")[:QUERY_MAX])
    for kind, anchor, heading, rest in _section_intent(q):
        sub = rank(" ".join(rest), path, db, p)
        if is_direct(sub, p) and sub[0].kind in _SECTION_KINDS[kind]:
            top = replace(sub[0], url=f"{sub[0].url.split('#')[0]}#{anchor}", section=heading)
            return Answer([top] + sub[1:], top)
    ranked = rank(raw or "", path, db, p)
    return Answer(ranked, ranked[0] if is_direct(ranked, p) else None)


# ── API shapes ───────────────────────────────────────────────────

def suggest(raw: str, db=None, limit: int = 8, path: str | None = None) -> dict:
    a = answer(raw, path, db)
    return {
        "query": (raw or "")[:QUERY_MAX],
        "direct": a.direct.url if a.direct else None,
        "items": [h.public() for h in a.ranked[:limit]],
    }


def _explore_count(db, raw: str) -> int | None:
    expr = build_match_expression(raw)
    if db is None or not expr:
        return None
    try:
        return db.execute(text(
            "SELECT count(*) FROM (SELECT rowid FROM explore_fts WHERE explore_fts MATCH :m LIMIT 1001)"
        ), {"m": expr}).scalar()
    except Exception:
        db.rollback()
        return None


def results(raw: str, db=None, path: str | None = None, per_group: int = 10) -> dict:
    """The /search page: every match grouped by kind (each group's count
    and its first `per_group`), Explore's own count for the words, the page
    a confident query goes to, the two to four pages a near miss could
    have meant, and the nearest names when nothing matched."""
    raw = (raw or "")[:QUERY_MAX]
    a = answer(raw, path, db)
    groups: dict[str, list[Hit]] = {}
    for h in a.ranked:
        groups.setdefault(h.kind, []).append(h)
    p = params()
    # A close call: a match good enough to jump to, but with one to three
    # others too near it. More than that is a list, not a close call.
    near_top = [h for h in a.ranked if a.ranked[0].score - h.score < p.direct_margin] if a.ranked else []
    close = ([h.public() for h in near_top]
             if a.direct is None and a.ranked and a.ranked[0].match >= p.direct_floor and 2 <= len(near_top) <= 4
             else [])
    near = _nearest(raw, path) if not a.ranked and normalize(raw) else []
    return {
        "query": raw,
        "direct": a.direct.url if a.direct else None,
        "groups": [
            {"kind": kind, "label": KIND_GROUPS.get(kind, kind), "count": len(hits),
             "items": [h.public() for h in hits[:per_group]]}
            for kind, hits in groups.items()
        ],
        "explore": {"count": _explore_count(db, raw), "url": f"/explore?q={quote(raw)}"},
        "alternatives": close,
        "near": near,
    }


def _nearest(raw: str, path: str | None) -> list[dict]:
    """The closest names by spelling alone, for an empty result: trigram
    candidates scored by whole-string similarity, however low."""
    q = normalize(raw)
    grams = _trigrams(q)
    if not grams:
        return []
    conn = _connect(path or SITE_SEARCH_DB)
    try:
        rows = conn.execute(
            "SELECT e.kind, e.label, e.context, e.url, e.importance, e.names FROM entity_tri t "
            "JOIN entity e ON e.id = t.rowid WHERE entity_tri MATCH ? ORDER BY bm25(entity_tri) LIMIT ?",
            (" OR ".join(_fts_phrase(g, False) for g in grams), _POOL),
        ).fetchall()
    finally:
        conn.close()
    hits = [Hit(k, label, ctx, url, imp, tuple(names.split(" | "))) for k, label, ctx, url, imp, names in rows]
    for h in hits:
        h.score = max(SequenceMatcher(None, q, n).ratio() for n in h.names)
    hits.sort(key=lambda h: -h.score)
    seen, out = set(), []
    for h in hits:
        if h.url not in seen:
            seen.add(h.url)
            out.append(h.public())
        if len(out) == 3:
            break
    return out
