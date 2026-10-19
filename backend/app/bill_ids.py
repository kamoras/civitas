"""A bill's id, in the one spelling every response uses ("HR.997",
"SJRES.138"), however it arrived: a Senate roll call names a bill "H.R.
997", a Daily Digest "S.J.Res. 138", a reader types "hr 997". The display
label ("H.R. 997", congress_service.bill_label) is a separate field and is
never an id.

Here, with no imports of its own, so the response helpers every route goes
through can use it without pulling in the services."""

from typing import Any

# Congress.gov's bill types, by the prefix the site's id uses, with the
# path segment Congress.gov's API names them by.
BILL_TYPES = {
    "HR": "hr", "S": "s", "HRES": "hres", "SRES": "sres", "HJRES": "hjres",
    "SJRES": "sjres", "HCONRES": "hconres", "SCONRES": "sconres",
}

# Response keys whose value is a bill id, or a list of them.
_ID_KEYS = {"billId"}
_ID_LIST_KEYS = {"billIds"}


def canonical_bill_id(raw: str | None) -> str | None:
    """"H.R. 997", "hr997", "S 2398", "S.2398" -> "HR.997" / "S.2398": the
    site's own spelling of a bill id, from any of the spellings people and
    the site itself print (the hub shows "S. 2398"); None for anything that
    is not a bill id. Spaces and dots carry nothing in a bill number, which
    is how frontend/src/lib/congress.ts billPageHref reads one too."""
    if not raw or len(raw) > 24:
        return None
    compact = raw.replace(".", "").replace(" ", "").upper()
    prefix = compact.rstrip("0123456789")
    number = compact[len(prefix):]
    if prefix not in BILL_TYPES or not number:
        return None
    return f"{prefix}.{number}"


def bill_id_out(raw: str | None) -> str | None:
    """A bill id as a response gives it: the canonical spelling, or the value
    unchanged when it is not a bill (a nomination's "PN123" in a vote's
    billId, an empty string)."""
    return canonical_bill_id(raw) or raw


def canonical_ids(data: Any) -> Any:
    """`data` with every billId (and every entry of a billIds list), at any
    depth, in the canonical spelling. The JSON response helpers apply it, so
    no route can publish a bill under another spelling."""
    if isinstance(data, dict):
        return {
            k: (bill_id_out(v) if k in _ID_KEYS and isinstance(v, str)
                else [bill_id_out(x) if isinstance(x, str) else x for x in v]
                if k in _ID_LIST_KEYS and isinstance(v, list)
                else canonical_ids(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [canonical_ids(x) for x in data]
    return data
