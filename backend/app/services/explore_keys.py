"""Permanent addresses for Explore documents (/explore/{key}).

A document's row id is not permanent: the Federal Register refresh removes a
document listed under a provisional number, a re-read Record day replaces
the speeches whose text changed, and a copy of a speech from an overlapping
granule is dropped. Every cited or shared /explore/{id} link went dead with
its row. The key is built from the document's official identifier instead,
so it names the same document whichever row holds it:

    fr-2026-17123                         Federal Register document number
                                          (rules, notices, presidential documents)
    crec-CREC-2026-09-16-pt1-PgH5836-3-213d7a94
                                          GovInfo's Congressional Record granule,
                                          and which speaker's turn in it (a
                                          hash of speaker and text)
    scotus-2025-24-440                    the Court's term and docket

It is `external_id` without what is internal to Civitas: whether the FR
document came from the rulemaking or the presidential feed ("fr-reg-"), and
the speech parser's format version ("crec-v1-"). The old /explore/{id}
answers a permanent redirect to it (frontend explore/[id]/page.tsx).
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import ExploreDocument

_SPEECH_FORMAT_RE = re.compile(r"^crec-v\d+-")
_GRANULE_RE = re.compile(r"^crec-(CREC-(\d{4}-\d{2}-\d{2})-pt\d+-Pg[HSED]\d+(?:-\d+)?)-([0-9a-f]+)$")
# A key is printable in a URL as is: the official identifiers are letters,
# digits and hyphens.
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def public_key(external_id: str | None, row_id: int) -> str:
    """The document's permanent key; its row id only for a row with no
    external id (none on 2026-10-16, but the column allows it)."""
    ext = external_id or ""
    if not ext:
        return str(row_id)
    if ext.startswith("fr-reg-"):
        return "fr-" + ext.removeprefix("fr-reg-")
    return _SPEECH_FORMAT_RE.sub("crec-", ext)


def find_by_key(db: Session, key: str) -> ExploreDocument | None:
    """The document a key names. A speech whose copy from an overlapping
    granule was dropped (explore_pipeline._drop_overlapping_granule_copies)
    is found under the copy kept: same day, same speaker and text."""
    if not KEY_RE.match(key) or key.isdigit():
        return None
    if key.startswith("fr-"):
        number = key.removeprefix("fr-")
        candidates = [key, f"fr-reg-{number}"]
        return (db.query(ExploreDocument).filter(ExploreDocument.external_id.in_(candidates))
                .order_by(ExploreDocument.id.desc()).first())
    if key.startswith("crec-"):
        rest = key.removeprefix("crec-")
        doc = (db.query(ExploreDocument)
               .filter(ExploreDocument.external_id.like("crec-v%-" + rest.replace("%", "").replace("_", r"\_"),
                                                        escape="\\"))
               .order_by(ExploreDocument.id.desc()).first())
        if doc is not None:
            return doc
        m = _GRANULE_RE.match(key)
        if m:
            return (db.query(ExploreDocument)
                    .filter(ExploreDocument.date == m.group(2),
                            ExploreDocument.external_id.like(f"crec-v%-%-{m.group(3)}"))
                    .order_by(ExploreDocument.id.desc()).first())
        return None
    return db.query(ExploreDocument).filter(ExploreDocument.external_id == key).first()

