"""Site search's entity index: one row for every thing with a page.

Built at write time, in the pipeline process (scheduler `_site_search_index`),
and read by the API with plain SQLite (app/site_search.py): no model, no
LLM, on the read path. A separate file, `/data/site_search.db`, written
whole to a temporary file and renamed into place, so a reader never sees a
half-built index and nothing in the main database is locked or migrated for
it. It is derived data: delete it and the next tick rebuilds it.

What is indexed is what the site links to (the sitemap's own set, plus the
things that live on a page as a section of it: a race, a ballot measure, a
district's member, a state's members): members (with their former ids),
presidents, justices, bills, roll calls, agencies, committees, states,
districts, races, ballot measures, Action Center issues and monitors,
nominations, the identifiers Explore documents are cited by (their titles
are searched in Explore's own index, `explore_fts`, at query time), and the
site's own pages (`app/data/site_pages.json`).

Rebuilt only when a cheap fingerprint of its sources moves (row counts,
newest rows, last updates), checked every ten minutes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.issue_ids import to_public_id
from app.ordinals import ordinal
from app.pipeline.analyze.document_authority import declared_identifiers
from app.services.committee_record import committees
from app.services.congress_service import bill_label
from app.state_names import STATE_NAMES

logger = logging.getLogger(__name__)

SITE_SEARCH_DB = "/data/site_search.db"
_DATA = Path(__file__).parent / "data"
SITE_PAGES_PATH = _DATA / "site_pages.json"

# Bump when the tables below change shape: a file at another version is
# rebuilt on the next tick rather than queried.
SCHEMA_VERSION = "1"

# Kinds, and the group heading each one is listed under.
KIND_GROUPS = {
    "page": "Pages",
    "member": "Members of Congress",
    "district": "Districts",
    "president": "Presidents",
    "justice": "Justices",
    "bill": "Bills",
    "rollcall": "Roll calls",
    "agency": "Agencies",
    "committee": "Committees",
    "state": "States",
    "race": "Races",
    "measure": "Ballot measures",
    "issue": "Action Center issues",
    "monitor": "Ongoing stories",
    "nomination": "Nominations",
    "document": "Documents",
}

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(s: str | None) -> str:
    """Lower case, accents folded, everything but letters and digits a
    single space: what both the index and a query are compared in."""
    if not s:
        return ""
    folded = unicodedata.normalize("NFKD", s)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return _NON_ALNUM.sub(" ", folded.replace("&", " and ")).strip()


def district_code(state: str, district: int | None) -> str:
    """"TX", 23 -> "TX-23"; a delegate or at-large seat (0) -> "AK-AL"."""
    return f"{state}-{district}" if district else f"{state}-AL"


def district_aliases(state: str, district: int | None) -> list[str]:
    name = STATE_NAMES.get(state, state)
    if not district:
        return [f"{state} AL", f"{state} at large", f"{name} at large", f"{name} at-large district"]
    return [f"{state} {district}", f"{state}{district}", f"{name} {district}",
            f"{name} {ordinal(district)}", f"{name} district {district}", f"{name} {ordinal(district)} district"]


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def surname(name: str) -> str | None:
    """A person's last name as written ("Joseph R. Biden, Jr." -> "Biden",
    "Alexandria Ocasio-Cortez" -> "Ocasio-Cortez"): the name a person is
    referred to by alone, so it is one of their names in the index. Two
    people of one surname are then two exact answers, and neither is jumped
    to (site_search.is_direct)."""
    words = [w for w in name.replace(",", " ").split() if normalize(w) not in _SUFFIXES]
    return words[-1] if len(words) > 1 else None


@dataclass
class Entity:
    kind: str
    label: str
    url: str
    context: str = ""
    importance: float = 0.5
    aliases: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    # False for rows found by key only (Explore documents: their words are
    # searched in Explore's own index).
    words: bool = True


# The longest context line: a bill's official title runs to hundreds of
# words, and one such line made a single result taller than a phone's
# screen at a doubled text size (WCAG 1.4.10, 2.4.11). The whole title is
# on the bill's page and still searchable (it is one of the bill's names).
CONTEXT_MAX = 160


def _clip(text: str, limit: int = CONTEXT_MAX) -> str:
    """`text` cut at the last word boundary before `limit`, with an
    ellipsis; unchanged when it fits."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return f"{cut}…"


def _q(db: Session, sql: str, **params):
    return db.execute(text(sql), params).fetchall()


def _has_table(db: Session, name: str) -> bool:
    return bool(_q(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n", n=name))


def _recency(day: str | None, newest: str | None, span_days: int) -> float:
    """1.0 for the newest, falling linearly to 0 at `span_days` older."""
    try:
        age = (date.fromisoformat(newest[:10]) - date.fromisoformat(day[:10])).days
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, 1.0 - age / span_days)


# ── Sources ──────────────────────────────────────────────────────

def _members(db: Session) -> list[Entity]:
    aliases: dict[str, list[str]] = {}
    if _has_table(db, "member_id_aliases"):
        for old, new in _q(db, "SELECT old_id, new_id FROM member_id_aliases"):
            aliases.setdefault(new, []).append(old.replace("-", " "))
    out: list[Entity] = []
    for chamber, table in (("senate", "senators"), ("house", "representatives")):
        cols = "id, name, state, party, is_current, bioguide_id" + (", district" if chamber == "house" else ", 0")
        for mid, name, state, party, current, bioguide, district in _q(db, f"SELECT {cols} FROM {table}"):
            office = "Senator" if chamber == "senate" else ("Representative" if district else "Delegate")
            seat = f"{party}-{state}" if chamber == "senate" else f"{party}-{district_code(state, district)}"
            context = f"{office} · {seat}" if current else f"Former {office.lower()} · {seat}"
            out.append(Entity(
                "member", name, f"/politicians/{mid}", context,
                importance=1.0 if current else 0.4,
                aliases=[a for a in (surname(name), *aliases.get(mid, [])) if a],
                keys=[f"bioguide:{bioguide.upper()}"] if bioguide else [],
            ))
            if chamber == "house" and current:
                out.append(Entity(
                    "district", district_code(state, district), f"/politicians/{mid}",
                    f"{STATE_NAMES.get(state, state)} · represented by {name} ({party})",
                    importance=0.9,
                    aliases=district_aliases(state, district),
                    keys=[f"district:{district_code(state, district)}"],
                ))
    return out


def _presidents_and_justices(db: Session) -> list[Entity]:
    out = []
    for pid, name, number, start, end, current in _q(
        db, "SELECT id, name, number, term_start, term_end, is_current FROM presidents"
    ):
        years = f"{(start or '')[:4]}–{(end or '')[:4]}" if end else f"since {(start or '')[:4]}"
        out.append(Entity("president", name, f"/politicians/{pid}", f"{ordinal(number)} President · {years}",
                          importance=1.0 if current else 0.4 + 0.4 * number / 50,
                          aliases=[a for a in (surname(name),) if a]))
    for jid, name, role, active in _q(db, "SELECT id, name, role_title, is_active FROM justices"):
        role = "Chief Justice" if "Chief" in (role or "") else "Associate Justice"
        out.append(Entity("justice", name, f"/politicians/{jid}",
                          f"{role} · Supreme Court" if active else f"Former {role.lower()}",
                          importance=0.9 if active else 0.4, aliases=[a for a in (surname(name),) if a]))
    return out


def _bills(db: Session) -> list[Entity]:
    current = settings.CURRENT_CONGRESS
    bills: dict[tuple[str, int], dict] = {}
    for table in ("sponsored_bills", "rep_sponsored_bills"):
        for bill_id, congress, title, day, law in _q(
            db, f"SELECT bill_id, congress, title, latest_action_date, is_law FROM {table}"
        ):
            row = bills.setdefault((bill_id, congress or current), {"title": title, "day": day or "", "law": False})
            row["law"] = row["law"] or bool(law)
            row["day"] = max(row["day"], day or "")
    # Bills a Congress report links to that no member's list carries: a
    # roll call's own title stands in.
    for bill_id, congress, title, day in _q(
        db, "SELECT bill_id, congress, max(title), max(date) FROM roll_calls WHERE bill_id IS NOT NULL GROUP BY bill_id, congress"
    ):
        bills.setdefault((bill_id, congress), {"title": title or "", "day": day or "", "law": False})
    newest = max((b["day"] for b in bills.values()), default="")
    out = []
    for (bill_id, congress), b in bills.items():
        label = bill_label(bill_id)
        if not label:
            continue
        is_current = congress == current
        url = f"/congress/bills/{quote(bill_id)}" + ("" if is_current else f"?congress={congress}")
        # A bill number names this Congress's bill (as the bill page reads
        # it); an earlier Congress's is labelled and keyed with its Congress.
        out.append(Entity(
            "bill", label if is_current else f"{label} ({ordinal(congress)} Congress)", url,
            _clip(b["title"]),
            importance=(0.55 + 0.25 * _recency(b["day"], newest, 365) + (0.2 if b["law"] else 0.0))
            if is_current else 0.2,
            aliases=[b["title"]] if b["title"] else [],
            keys=[f"bill:{bill_id}" if is_current else f"bill:{bill_id}:{congress}"],
        ))
    return out


def _roll_calls(db: Session) -> list[Entity]:
    rows = _q(db, "SELECT chamber, congress, session, number, date, question, title FROM roll_calls")
    newest = max((r[4] or "" for r in rows), default="")
    out = []
    for chamber, congress, session, number, day, question, title in rows:
        detail = ": ".join(p for p in (question, title) if p)
        out.append(Entity(
            "rollcall", f"{chamber.title()} roll call {number}",
            f"/congress/votes/{chamber}/{congress}/{session}/{number}",
            _clip(f"{day} · {ordinal(congress)} Congress, session {session}" + (f" · {detail}" if detail else "")),
            importance=0.3 + 0.4 * _recency(day, newest, 730),
            keys=[f"rc:{chamber}:{number}", f"rc:{number}"],
            words=False,
        ))
    return out


def _agencies(db: Session) -> list[Entity]:
    if not _has_table(db, "agencies"):
        return []
    with_docs = {s for (s,) in _q(db, "SELECT DISTINCT agency_slug FROM explore_documents WHERE agency_slug IS NOT NULL")}
    abbrevs: dict[str, list[str]] = {}
    if _has_table(db, "agency_budgets"):
        for slug, abbr in _q(db, "SELECT agency_slug, abbreviation FROM agency_budgets WHERE agency_slug IS NOT NULL"):
            if abbr:
                abbrevs.setdefault(slug, []).append(abbr)
    rows = _q(db, "SELECT slug, name, short_name, parent_slug FROM agencies")
    names = {slug: name for slug, name, _s, _p in rows}
    out = []
    for slug, name, short, parent in rows:
        aliases = [a for a in {short, *abbrevs.get(slug, [])} if a and normalize(a) != normalize(name)]
        busy = slug in with_docs or slug in abbrevs
        out.append(Entity(
            "agency", name, f"/agencies/{quote(slug)}",
            f"Part of {names[parent]}" if parent in names else "Federal agency",
            importance=(0.6 if busy else 0.3) + (0.2 if not parent else 0.0),
            aliases=aliases,
            keys=[f"abbr:{normalize(a)}" for a in aliases if len(a) <= 12],
        ))
    return out


def _committees(db: Session) -> list[Entity]:
    out = []
    for c in committees(db):
        chamber = c["chamber"].title()
        short = re.sub(r"^(Committee|Commission) on (the )?", "", c["name"])
        param = quote(f"{c['chamber']}|{c['name']}", safe="")
        out.append(Entity(
            "committee", f"{chamber} {c['name']}",
            f"/congress/committees?committee={param}#committee-results",
            f"{chamber} committee · {c['meetings']} meeting{'' if c['meetings'] == 1 else 's'} on record, latest {c['latest']}",
            importance=0.6, aliases=[short, f"{chamber} {short}"],
        ))
    return out


def _elections(db: Session) -> list[Entity]:
    out = []
    current_states = {s for (s,) in _q(db, "SELECT DISTINCT state FROM senators WHERE is_current = 1")}
    for code, name in STATE_NAMES.items():
        out.append(Entity("state", name, f"/elections/states/{code}", f"{name}'s ballot: federal races and statewide measures",
                          importance=0.9, aliases=[code, f"{name} ballot"], keys=[f"state:{code}"]))
        if code in current_states:
            out.append(Entity("state", f"{name} members of Congress", f"/congress/members?state={code}",
                              f"{name}'s senators and representatives",
                              importance=0.7, aliases=[f"{code} members", f"{name} delegation",
                                                       f"{name} senators", f"{name} representatives"]))
    cycle = _q(db, "SELECT max(cycle_year) FROM races")[0][0]
    if cycle:
        for rid, office, state, district, special in _q(
            db, "SELECT id, office, state, district, is_special FROM races WHERE cycle_year = :c", c=cycle
        ):
            name = STATE_NAMES.get(state, state)
            kind = " special election" if special else " race"
            url = f"/elections/states/{state}#race-{quote(rid)}"
            if office == "H":
                code = district_code(state, district)
                aliases = [f"{code} race", f"{code} election"] + [
                    f"{a} {cycle}" for a in [code, *district_aliases(state, district)]
                ]
                out.append(Entity("race", f"{code} House{kind}, {cycle}", url,
                                  f"{name} · on the {cycle} ballot", importance=0.75,
                                  aliases=aliases, keys=[f"race:{code}"]))
            elif office == "S":
                aliases = [f"{w1} senate {w2}" for w1 in (name, state) for w2 in (cycle, "race", "election")]
                out.append(Entity("race", f"{name} Senate{kind}, {cycle}", url,
                                  f"On the {cycle} ballot", importance=0.75 if not special else 0.7,
                                  aliases=aliases, keys=[f"race:{state}-SEN"]))
        for mid, state, number, title, mtype, status in _q(
            db, "SELECT id, state, number, title, measure_type, status FROM ballot_measures "
                "WHERE substr(election_date, 1, 4) = :c", c=str(cycle)
        ):
            name = STATE_NAMES.get(state, state)
            noun = mtype or "measure"
            label = f"{name} {noun} {number}".strip() if number else (title or "")[:80]
            out.append(Entity(
                "measure", label, f"/elections/states/{state}#measure-{quote(mid)}",
                _clip(title or "") + (" · removed from the ballot" if status == "removed" else ""),
                importance=0.3 if status == "removed" else 0.6,
                aliases=[a for a in (f"{state} {noun} {number}" if number else "", title) if a],
            ))
    return out


def _action_center(db: Session) -> list[Entity]:
    rows = _q(db, "SELECT id, title, date FROM action_issues WHERE status != 'developing'")
    newest = max((r[2] or "" for r in rows), default="")
    out = [
        Entity("issue", title, f"/issue/{to_public_id(iid)}", f"Action Center · {day}",
               importance=0.3 + 0.5 * _recency(day, newest, 180),
               keys=[f"issue:{to_public_id(iid)}"])
        for iid, title, day in rows
    ]
    status_weight = {"active": 0.8, "watching": 0.65, "closed": 0.4}
    for slug, title, status in _q(db, "SELECT slug, title, status FROM national_monitors"):
        out.append(Entity("monitor", title, f"/action?tab=monitors&monitor={quote(slug, safe='')}",
                          f"Ongoing story · {status}", importance=status_weight.get(status, 0.5)))
    return out


def _nominations(db: Session) -> list[Entity]:
    if not _has_table(db, "nominations"):
        return []
    out = []
    for congress, citation, number, position, org, nominees, status, is_list in _q(
        db, "SELECT congress, citation, number, position_title, organization, nominees, status, is_list FROM nominations"
    ):
        names = []
        if not is_list:
            try:
                for p in json.loads(nominees or "[]"):
                    for n in p.get("names", []):
                        names.append(" ".join(x for x in (n.get("firstName"), n.get("lastName")) if x))
            except (ValueError, TypeError, AttributeError):
                names = []
        who = names[0] if len(names) == 1 else ""
        label = f"{citation}: {who}" if who else f"{citation}: {position or org}"[:120]
        out.append(Entity(
            "nomination", label, f"/congress/nominations/{congress}/{quote(citation)}",
            _clip(" · ".join(p for p in (position, org, status) if p)),
            importance=(0.55 if status == "pending" else 0.4) if congress == settings.CURRENT_CONGRESS else 0.2,
            aliases=[n for n in names if n] + ([position] if position else []),
            keys=[f"pn:{citation.lower()}", f"pn:{citation.lower().split('-')[0]}"],
        ))
    return out


def _documents(db: Session) -> list[Entity]:
    """Explore documents by the identifiers they are cited by: their words
    are Explore's own index's job."""
    out = []
    for did, title, dtype, day, ext, ids in _q(
        db, "SELECT id, title, doc_type, date, external_id, identifiers FROM explore_documents"
    ):
        keys = sorted(declared_identifiers(ext, title, dtype, ids))
        if keys:
            out.append(Entity("document", title, f"/explore/{did}", f"{dtype} · {day}",
                              importance=0.5, keys=keys, words=False))
    return out


def _pages() -> list[Entity]:
    pages = json.loads(SITE_PAGES_PATH.read_text())["pages"]
    return [Entity("page", p["label"], p["href"], p["section"], importance=0.8, aliases=p["aliases"]) for p in pages]


def collect_entities(db: Session) -> list[Entity]:
    out: list[Entity] = _pages()
    for source in (_members, _presidents_and_justices, _bills, _roll_calls, _agencies, _committees,
                   _elections, _action_center, _nominations, _documents):
        try:
            out.extend(source(db))
        except Exception:
            # One source failing (a table a fresh database lacks) leaves the
            # others searchable; the next tick tries it again.
            logger.exception("Site search: %s could not be read", source.__name__)
            db.rollback()
    return out


# ── Fingerprint ──────────────────────────────────────────────────

_FINGERPRINT_SQL = {
    "senators": "count(*), max(updated_at)",
    "representatives": "count(*), max(updated_at)",
    "presidents": "count(*), max(is_current || id)",
    "justices": "count(*), sum(is_active)",
    "sponsored_bills": "count(*), max(id)",
    "rep_sponsored_bills": "count(*), max(id)",
    "roll_calls": "count(*), max(id)",
    "agencies": "count(*), max(synced_at)",
    "agency_budgets": "count(*)",
    "congress_events": "count(*), max(id)",
    "races": "count(*), max(updated_at)",
    "ballot_measures": "count(*), max(updated_at)",
    "action_issues": "count(*), max(id), sum(status != 'developing')",
    "national_monitors": "count(*), max(updated_at)",
    "nominations": "count(*), max(fetched_at)",
    "explore_documents": "count(*), max(id)",
    "member_id_aliases": "count(*)",
}


def source_fingerprint(db: Session) -> str:
    """Cheap aggregates over every source table, the page list and the
    schema version: equal fingerprints, nothing to rebuild."""
    h = hashlib.sha256(SCHEMA_VERSION.encode())
    h.update(SITE_PAGES_PATH.read_bytes())
    h.update(str(settings.CURRENT_CONGRESS).encode())
    for table, expr in _FINGERPRINT_SQL.items():
        try:
            row = db.execute(text(f"SELECT {expr} FROM {table}")).fetchone()
        except Exception:
            db.rollback()
            row = None
        h.update(f"{table}:{tuple(row) if row else None};".encode())
    return h.hexdigest()


def stored_fingerprint(path: str | None = None) -> str | None:
    # Resolved here, not as a default argument: a default is bound at
    # import, before the tests' data-volume redirect moves SITE_SEARCH_DB.
    path = path or SITE_SEARCH_DB
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return rows.get("fingerprint") if rows.get("schema_version") == SCHEMA_VERSION else None


# ── Writing ──────────────────────────────────────────────────────

def write_index(entities: list[Entity], path: str, fingerprint: str = "") -> int:
    """Write `entities` to a new file and rename it over `path`."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".site_search.", suffix=".db", dir=directory)
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp)
        conn.executescript("""
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE entity (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
                context TEXT NOT NULL, url TEXT NOT NULL, importance REAL NOT NULL,
                names TEXT NOT NULL
            );
            CREATE TABLE entity_key (key TEXT NOT NULL, entity_id INTEGER NOT NULL);
            CREATE VIRTUAL TABLE entity_fts USING fts5(
                names, content='',
                tokenize="unicode61 remove_diacritics 2", prefix='1 2 3'
            );
            CREATE VIRTUAL TABLE entity_tri USING fts5(names, content='', tokenize='trigram');
        """)
        for i, e in enumerate(entities, start=1):
            names = [e.label, *e.aliases]
            normalized = " | ".join(dict.fromkeys(n for n in (normalize(x) for x in names) if n))
            conn.execute("INSERT INTO entity VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (i, e.kind, e.label, e.context, e.url, round(e.importance, 4), normalized))
            conn.executemany("INSERT INTO entity_key VALUES (?, ?)", [(k.lower(), i) for k in dict.fromkeys(e.keys)])
            if e.words and normalized:
                conn.execute("INSERT INTO entity_fts(rowid, names) VALUES (?, ?)", (i, normalized))
                conn.execute("INSERT INTO entity_tri(rowid, names) VALUES (?, ?)", (i, normalized))
        conn.execute("CREATE INDEX entity_key_key ON entity_key(key)")
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("schema_version", SCHEMA_VERSION), ("fingerprint", fingerprint), ("entities", str(len(entities))),
        ])
        conn.commit()
        conn.execute("INSERT INTO entity_fts(entity_fts) VALUES ('optimize')")
        conn.execute("INSERT INTO entity_tri(entity_tri) VALUES ('optimize')")
        conn.commit()
        conn.close()
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(entities)


def refresh_index(db: Session, path: str | None = None, force: bool = False) -> int | None:
    """Rebuild the index when its sources moved (or it is missing, or at an
    old schema). Returns the entity count written, None when unchanged."""
    path = path or SITE_SEARCH_DB
    fingerprint = source_fingerprint(db)
    if not force and stored_fingerprint(path) == fingerprint:
        return None
    count = write_index(collect_entities(db), path, fingerprint)
    logger.info("Site search index rebuilt: %d entities", count)
    return count
