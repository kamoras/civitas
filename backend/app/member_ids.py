"""Member ids: the `first-last` slug in /politicians/<id>, its uniqueness
across both chambers, and renaming a member in place when it changes.

The id is the primary key of `senators` / `representatives` and the public
URL. Both chambers share the one URL namespace, so:

- the same person (same bioguide id) has one id in both tables — a
  representative who went on to the Senate keeps it, which
  member_lifecycle._purge_member_traces relies on;
- two different people with the same slug are told apart by the state
  code (`jane-doe-oh`), and should that also be taken, by the bioguide id
  (`jane-doe-oh-d000001`). Whoever already holds an id keeps it: a
  newcomer takes the suffix, so nobody's URL moves because someone else
  arrived. Within one roster the order is by bioguide id, so the outcome
  doesn't depend on the order Congress.gov lists members in.

Ids are derived again on every pipeline run. When a member's derived id
differs from the stored one (the derivation changed, or a legal name
change), `assign_member_ids` renames them in place — every row that
references the old id, in the caller's transaction — and records the old id
in `member_id_aliases`, which the API and /politicians/<id> resolve, so
posted and indexed URLs keep working. Matching is by bioguide id, never by
id: re-deriving an id must never read as one member leaving and another
arriving (that would retire the old row and purge its history after
member_lifecycle.RETIREMENT_GRACE_DAYS).
"""

import json
import logging
import re
import unicodedata

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    ActionIssue,
    BroadcastPost,
    BskySenatorSpotlight,
    ExploreDocument,
    MemberIdAlias,
    Representative,
    ScoreSnapshot,
    Senator,
)
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

CHAMBER_SENATE = "senate"
CHAMBER_HOUSE = "house"
MODELS = {CHAMBER_SENATE: Senator, CHAMBER_HOUSE: Representative}
# ScoreSnapshot.entity_type as written by senate_pipeline/house_pipeline.
SNAPSHOT_ENTITY = {CHAMBER_SENATE: "senator", CHAMBER_HOUSE: "representative"}
# ActionIssue JSON columns whose entries name members as {"id", "chamber"}.
ISSUE_MEMBER_FIELDS = ("related_senators", "related_officials")

# Generational suffixes as Congress.gov prints them — a data-format
# convention of the name string ("Doe, John Jr."), not a classification.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
# A nickname printed inside the formal name: 'Doe, John Q. "Jack"'. Quoted
# is Congress.gov's marker for the name a member goes by; a parenthesised
# aside is only dropped.
_QUOTED = re.compile(r'"([^"]*)"|\u201c([^\u201d]*)\u201d')
_NICKNAME = re.compile(r'"[^"]*"|“[^”]*”|\([^)]*\)')


def _slug(text: str) -> str:
    """ASCII-folded (NFKD, combining marks dropped), lowercase, every run of
    anything else turned into one hyphen."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def member_slug(raw_name: str, structured_last: str | None = None) -> str:
    """`first-last` from Congress.gov's "Last, First Middle" name: the name
    the member goes by, where the source says what it is.

    first: the quoted nickname when the name has one — Congress.gov's own
    marker for the name a member goes by ('Doe, Jonathan Q. "Jack"' ->
    jack-doe). Otherwise the first given name, passing over initials and
    suffixes ("Doe, J. Quincy Jr." -> quincy-doe); an initial only when the
    name has nothing else. The member detail's structured firstName is not
    used: it holds a familiar form for some members without the name
    string marking it, which would rename members whose ids are already
    right.

    last: the whole surname before the comma ("Doe Roe, Jane" -> doe-roe),
    or `structured_last` (the member detail's lastName) when that is a
    longer multi-word surname ending with it ("Roe" vs "Doe Roe") — the
    longer form is the surname the member goes by.

    A name with no comma is read as "First ... Last".
    """
    if "," in raw_name:
        last, given = raw_name.split(",", 1)
    else:
        parts = raw_name.split()
        last, given = (parts[-1], " ".join(parts[:-1])) if parts else ("", "")
    last_words = last.split()
    while len(last_words) > 1 and _slug(last_words[-1]) in _SUFFIXES:
        last_words.pop()
    surname = _slug(" ".join(last_words))
    longer = _slug(structured_last or "")
    if surname and longer.endswith(f"-{surname}"):
        surname = longer
    nickname = next((_slug(a or b) for a, b in _QUOTED.findall(given) if _slug(a or b)), "")
    tokens = [t for t in _NICKNAME.sub(" ", given).replace(",", " ").split() if _slug(t)]
    tokens = [t for t in tokens if _slug(t) not in _SUFFIXES] or tokens
    names = [t for t in tokens if len(_slug(t)) > 1]  # "J." is an initial
    first = nickname or _slug((names or tokens or [""])[0])
    return "-".join(p for p in (first, surname) if p)


def _chamber_of(row) -> str:
    return CHAMBER_SENATE if isinstance(row, Senator) else CHAMBER_HOUSE


def assign_member_ids(db: Session, chamber: str, members: list[dict]) -> dict[str, str]:
    """Settle the id of every member on tonight's roster (normalize_members'
    records: `id` is the derived slug, `bioguideId`, `state`), rewriting
    each record's `id` and renaming any stored member whose id changes.

    Runs before anything writes a member by id. Returns {old_id: new_id}
    for the renames. Doesn't commit: the caller's transaction holds the
    renames together with the run's other writes.
    """
    roster = sorted((m for m in members if m.get("bioguideId") and m.get("id")), key=lambda m: m["bioguideId"])
    on_roster = {m["bioguideId"] for m in roster}
    holders: dict[str, set[str | None]] = {}  # id -> who holds it this run
    rows_of: dict[str, list] = {}  # bioguide id -> its stored rows, both chambers
    for model in MODELS.values():
        for row in db.query(model).all():
            if row.bioguide_id in on_roster:
                rows_of.setdefault(row.bioguide_id, []).append(row)
            else:
                # Not on this roster (departed, the other chamber, no
                # bioguide id): their ids stay theirs.
                holders.setdefault(row.id, set()).add(row.bioguide_id)

    def candidates(m: dict) -> list[str]:
        slug = m["id"]
        st = (m.get("state") or "").lower()
        return [slug, f"{slug}-{st}", f"{slug}-{st}-{m['bioguideId'].lower()}"]

    def free(candidate: str, bio: str) -> bool:
        return holders.get(candidate, set()) <= {bio}

    # Whoever already holds one of their own candidate ids keeps it, before
    # anyone is placed, so a newcomer never takes an id from its holder.
    chosen: dict[str, str] = {}
    for m in roster:
        bio = m["bioguideId"]
        held = {row.id for row in rows_of.get(bio, [])}
        keep = next((c for c in candidates(m) if c in held and free(c, bio)), None)
        if keep:
            chosen[bio] = keep
            holders.setdefault(keep, set()).add(bio)
    for m in roster:
        bio = m["bioguideId"]
        if bio not in chosen:
            chosen[bio] = next((c for c in candidates(m) if free(c, bio)), candidates(m)[-1])
            holders.setdefault(chosen[bio], set()).add(bio)
        m["id"] = chosen[bio]

    moves = [
        (_chamber_of(row), row.id, chosen[bio], bio)
        for bio, rows in rows_of.items()
        for row in rows
        if row.id != chosen[bio]
    ]
    _rename_all(db, moves)
    renames = {old: new for _, old, new, _ in moves}
    if renames:
        logger.warning(
            "Renamed %d member id(s) on the %s roster: %s", len(renames), chamber,
            ", ".join(f"{o} -> {n}" for o, n in sorted(renames.items())),
        )
    return renames


def rename_member(db: Session, chamber: str, old_id: str, new_id: str, bioguide_id: str | None) -> None:
    """Move one member from `old_id` to `new_id` everywhere the id is
    stored, and record the alias. Doesn't commit."""
    _rename_all(db, [(chamber, old_id, new_id, bioguide_id)])


def _rename_all(db: Session, moves: list[tuple[str, str, str, str | None]]) -> None:
    """Carry out (chamber, old_id, new_id, bioguide_id) renames together.

    Each goes through a placeholder id first, so one member can take an id
    another gives up in the same run, whatever the order. The alias goes
    from the old id straight to the new one."""
    if not moves:
        return
    if db.get_bind().dialect.name == "sqlite":
        # Parent and children are updated one after another; checked at
        # commit, if foreign keys are enforced at all.
        db.execute(text("PRAGMA defer_foreign_keys = ON"))
    db.flush()
    # A loaded member object keeps its old primary key in the session's
    # identity map: drop it. Everything else rewritten underneath the
    # session is expired at the end.
    for chamber, old_id, _, _ in moves:
        if (stale := db.get(MODELS[chamber], old_id)) is not None:
            db.expunge(stale)
    placeholder = {(chamber, old_id): f"~renaming~{chamber}~{old_id}" for chamber, old_id, _, _ in moves}
    for chamber, old_id, _, _ in moves:
        _move(db, chamber, old_id, placeholder[(chamber, old_id)])
    for chamber, old_id, new_id, bioguide_id in moves:
        _move(db, chamber, placeholder[(chamber, old_id)], new_id)
        _record_alias(db, old_id, new_id, bioguide_id)
    db.flush()
    db.expire_all()


def _move(db: Session, chamber: str, old_id: str, new_id: str) -> None:
    """Rewrite every stored reference to one member's id.

    - The member row and every child table with a foreign key to it,
      found from the models' own metadata so a child table added later is
      covered: SQLite's foreign keys have no ON UPDATE CASCADE.
    - The references no foreign key covers (the four
      member_lifecycle._purge_member_traces clears, plus the broadcast
      posts' subject): score snapshots, the Bluesky spotlight rotation,
      Explore documents' politician link, action issues' member entries.
      Each is scoped to this chamber, as the purge is.
    - Not rewritten: a published post's URL and text (the record of what
      was published; the alias answers the old URL), and the Explore vector
      index's copy of politician_id, which the next Explore run writes onto
      the index in place (vector_store.update_explore_metadata), as it does
      after a purge.
    """
    table = MODELS[chamber].__table__
    db.execute(update(table).where(table.c.id == old_id).values(id=new_id))
    for child in Base.metadata.sorted_tables:
        for fk in child.foreign_keys:
            if fk.column.table is table and fk.column.name == "id":
                db.execute(update(child).where(fk.parent == old_id).values({fk.parent.name: new_id}))
    db.execute(
        update(ScoreSnapshot)
        .where(ScoreSnapshot.entity_type == SNAPSHOT_ENTITY[chamber], ScoreSnapshot.entity_id == old_id)
        .values(entity_id=new_id)
    )
    db.execute(
        update(BskySenatorSpotlight)
        .where(BskySenatorSpotlight.senator_id == old_id, BskySenatorSpotlight.chamber == chamber)
        .values(senator_id=new_id)
    )
    db.execute(
        update(ExploreDocument)
        .where(ExploreDocument.politician_id == old_id, ExploreDocument.chamber == chamber.title())
        .values(politician_id=new_id)
    )
    db.execute(
        update(BroadcastPost)
        .where(BroadcastPost.subject == f"member:{chamber}:{old_id}")
        .values(subject=f"member:{chamber}:{new_id}")
    )
    edit_issue_members(db, old_id, chamber, new_id)
    db.flush()


def _record_alias(db: Session, old_id: str, new_id: str, bioguide_id: str | None) -> None:
    """old_id now answers as new_id. Earlier aliases of the person are
    repointed at the newest id, and one that would shadow the id they hold
    again is dropped."""
    db.execute(update(MemberIdAlias).where(MemberIdAlias.new_id == old_id).values(new_id=new_id))
    db.query(MemberIdAlias).filter(MemberIdAlias.old_id == new_id).delete(synchronize_session=False)
    alias = db.get(MemberIdAlias, old_id)
    if alias is None:
        db.add(MemberIdAlias(old_id=old_id, new_id=new_id, bioguide_id=bioguide_id, renamed_at=utcnow()))
    else:
        alias.new_id, alias.bioguide_id, alias.renamed_at = new_id, bioguide_id, utcnow()
    db.flush()


def edit_issue_members(db: Session, member_id: str, chamber: str, new_id: str | None) -> None:
    """Point every action issue's entries for this member at `new_id`, or
    remove them when it is None. Covers both member lists an issue keeps
    (ISSUE_MEMBER_FIELDS). An entry without a chamber is a senator's (the
    lists held only senators before representatives were added)."""
    # LIKE prefilter so this touches only the handful of issues that
    # actually name the member, rather than rewriting the whole table.
    pattern = f'%"{member_id}"%'
    issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.related_senators.like(pattern) | ActionIssue.related_officials.like(pattern))
        .all()
    )
    for issue in issues:
        for field in ISSUE_MEMBER_FIELDS:
            try:
                entries = json.loads(getattr(issue, field) or "[]")
            except (ValueError, TypeError):
                continue
            if not isinstance(entries, list):
                continue
            edited, changed = [], False
            for e in entries:
                if isinstance(e, dict) and e.get("id") == member_id and (e.get("chamber") or CHAMBER_SENATE) == chamber:
                    changed = True
                    if new_id is not None:
                        edited.append({**e, "id": new_id})
                else:
                    edited.append(e)
            if changed:
                setattr(issue, field, json.dumps(edited))


def resolve_member_id(db: Session, member_id: str) -> str:
    """The id a member is served under: `member_id` itself when a senator
    or representative holds it, else the id it was renamed to, else
    unchanged (the caller's lookup then 404s as before)."""
    if db.get(Senator, member_id) is not None or db.get(Representative, member_id) is not None:
        return member_id
    alias = db.get(MemberIdAlias, member_id)
    return alias.new_id if alias is not None else member_id
