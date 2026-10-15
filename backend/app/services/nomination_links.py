"""Which stored nominations a roll call names: the join on the identifier
the Senate's own record states. Its vote question names its nominations by
citation ("On the Nomination PN999-1", or seventy at once for an en bloc
vote), read as a token, never matched loosely. Apart from
nomination_service so bill_record can use it without importing that
module's vote reading back."""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Nomination, RollCall

# A nomination citation as the Senate's vote question writes it.
PN_RE = re.compile(r"\bPN(\d+)(?:-(\d+))?\b")
# Names shown with a row; a military list names hundreds.
LIST_NAMES = 3


def citations_in(question: str) -> list[str]:
    """The nomination citations a vote question names, in its order."""
    return [f"PN{n}" + (f"-{int(p)}" if p else "") for n, p in PN_RE.findall(question or "")]


def _name(n: dict) -> str:
    full = " ".join(x for x in (n.get("firstName"), n.get("middleName"), n.get("lastName")) if x)
    return f"{full} ({n['state']})" if n.get("state") else full


def nominee_names(row: Nomination, limit: int = LIST_NAMES) -> list[str]:
    """The first `limit` nominees as published: "Jane Q. Doe (TX)"."""
    names = [n for p in json.loads(row.nominees or "[]") for n in p.get("names", [])]
    return [_name(n) for n in names[:limit]]


def nominations_for_roll_calls(db: Session, rows: list[RollCall]) -> dict[int, list[dict]]:
    """{roll call id: the nominations its question names that are stored},
    in the question's order: a nomination roll call's link to its record."""
    wanted = {rc.id: citations_in(rc.question) for rc in rows if rc.chamber == "senate"}
    keys = {(rc.congress, c) for rc in rows for c in wanted.get(rc.id, [])}
    if not keys:
        return {}
    found = {
        (n.congress, n.citation): n
        for n in db.execute(select(Nomination).where(
            Nomination.congress.in_({k[0] for k in keys}),
            Nomination.citation.in_({k[1] for k in keys}),
        )).scalars()
    }
    out: dict[int, list[dict]] = {}
    for rc in rows:
        linked = [found[(rc.congress, c)] for c in wanted.get(rc.id, []) if (rc.congress, c) in found]
        if linked:
            out[rc.id] = [{
                "citation": n.citation, "congress": n.congress, "positionTitle": n.position_title,
                "organization": n.organization, "status": n.status, "nominees": nominee_names(n),
                "nomineeCount": n.nominee_count,
            } for n in linked]
    return out
