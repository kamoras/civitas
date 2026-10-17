"""What Explore holds, and what to say when a searched identifier isn't held.

Explore's corpus starts where each feed's ingest started, not at the
beginning of the record: executive orders from May 2025, rules and notices
from April 2026 (2026-10-16). A search for an executive order older than
that ("EO 14148") used to come back with thirty other orders and no word
that the one asked for predates the corpus. `coverage_windows` states each
type's window (the Explore page shows it under the search box), and
`not_held` answers an identifier no document is known by with where the
official copy is.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ExploreDocument
from app.pipeline.analyze.document_authority import _DOCKET_ID_RE, declared_identifiers, record_volume

# The document types as the Explore page names them, in the order shown.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Rules and notices", ("Final Rule", "Proposed Rule", "Notice")),
    ("Executive orders", ("Executive Order",)),
    ("Proclamations", ("Proclamation",)),
    ("Presidential memoranda", ("Presidential Memorandum",)),
    ("Floor speeches", ("Senate Floor Speech", "House Floor Speech")),
    ("Supreme Court opinions", ("Supreme Court Opinion",)),
)
_FR_GROUPS = ("Rules and notices", "Executive orders", "Proclamations", "Presidential memoranda")
# Federal Register volume 1 is 1936: volume = year - 1935.
_FR_VOLUME_YEAR = 1935


def coverage_windows(db: Session) -> list[dict]:
    """Each group's first and last document date and count, for the groups
    holding any: [{"label", "from", "to", "count"}]."""
    rows = (db.query(ExploreDocument.doc_type, func.min(ExploreDocument.date), func.max(ExploreDocument.date),
                     func.count()).group_by(ExploreDocument.doc_type).all())
    by_type = {t: (lo, hi, n) for t, lo, hi, n in rows}
    out = []
    for label, types in GROUPS:
        held = [by_type[t] for t in types if t in by_type]
        if held:
            out.append({"label": label, "from": min(h[0] for h in held), "to": max(h[1] for h in held),
                        "count": sum(h[2] for h in held)})
    return out


def _long(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%B} {d.day}, {d.year}"


def _oldest_eo(db: Session) -> int | None:
    numbers = [int(i.split(":", 1)[1])
               for ext, title, doc_type, ids in db.query(
                   ExploreDocument.external_id, ExploreDocument.title, ExploreDocument.doc_type,
                   ExploreDocument.identifiers).filter(ExploreDocument.doc_type == "Executive Order")
               for i in declared_identifiers(ext, title, doc_type, ids) if i.startswith("eo:")]
    return min(numbers) if numbers else None


def _describe(ident: str) -> tuple[str, tuple[str, ...], str, str] | None:
    """(how it is written, the groups that would hold it, where the
    official copy is, that source's name)."""
    namespace, _, value = ident.partition(":")
    if namespace == "eo":
        return (f"Executive Order {value}", ("Executive orders",),
                "https://www.federalregister.gov/presidential-documents/executive-orders", "the Federal Register")
    if namespace == "proc":
        return (f"Proclamation {value}", ("Proclamations",),
                "https://www.federalregister.gov/presidential-documents/proclamations", "the Federal Register")
    if namespace == "fr":
        volume, _, page = value.partition("-")
        return (f"{volume} FR {page}", _FR_GROUPS, f"https://www.govinfo.gov/link/fr/{volume}/{page}", "GovInfo")
    if namespace == "frdoc":
        return (f"FR Doc. {value}", _FR_GROUPS, f"https://www.federalregister.gov/d/{value}", "the Federal Register")
    if namespace == "rin":
        return (f"RIN {value}", ("Rules and notices",),
                f"https://www.reginfo.gov/public/do/eAgendaViewRule?RIN={value}", "Reginfo.gov")
    if namespace == "docket":
        docket, _, number = value.rpartition("-")
        is_document = len(number) == 4 and number.isdigit() and bool(_DOCKET_ID_RE.fullmatch(docket))
        return (f"{'Regulations.gov document' if is_document else 'docket'} {value}", ("Rules and notices",),
                f"https://www.regulations.gov/{'document' if is_document else 'docket'}/{value}", "Regulations.gov")
    if namespace == "crec":
        volume, _, page = value.partition("-")
        return (f"{volume} Cong. Rec. {page}", ("Floor speeches",),
                "https://www.govinfo.gov/app/collection/crec", "GovInfo")
    if namespace == "crecgranule":
        return (value, ("Floor speeches",), "https://www.govinfo.gov/app/collection/crec", "GovInfo")
    return None


def _older(db: Session, ident: str, start: str) -> bool:
    """Whether the identifier itself dates from before `start`, the
    earliest document of the groups that would hold it."""
    namespace, _, value = ident.partition(":")
    year = int(start[:4])
    if namespace == "eo":
        oldest = _oldest_eo(db)
        return oldest is not None and int(value) < oldest
    if namespace == "frdoc":
        return int(value[:4]) < year
    if namespace == "fr":
        return int(value.partition("-")[0]) < year - _FR_VOLUME_YEAR
    if namespace == "crec":
        return int(value.partition("-")[0]) < record_volume(year, int(start[5:7]), int(start[8:10]))
    return False


def not_held(db: Session, identifiers: set[str]) -> list[dict]:
    """For identifiers no document is known by: what the reader asked for,
    a sentence saying it isn't held (and, when it predates the corpus,
    that its type isn't held that far back), and the official source.
    [{"identifier", "sentence", "url", "source"}], in a stable order."""
    windows = {w["label"]: w for w in coverage_windows(db)}
    out = []
    for ident in sorted(identifiers):
        described = _describe(ident)
        if described is None:
            continue
        written, groups, url, source = described
        held = [windows[g] for g in groups if g in windows]
        if not held:
            sentence = f"{written} isn't held here: Civitas holds no {groups[0].lower()} yet."
        else:
            start = min(w["from"] for w in held)
            if _older(db, ident, start):
                kind = groups[0] if len(groups) == 1 else "Federal Register documents"
                sentence = f"{kind} before {_long(start)} aren't held here, so {written} isn't."
            else:
                spans = ", ".join(f"{w['label'].lower()} from {_long(w['from'])}" for w in held)
                sentence = f"No document here is {written}. Civitas holds {spans}, and may not have read it yet."
        out.append({"identifier": written, "sentence": sentence, "url": url, "source": source})
    return out
