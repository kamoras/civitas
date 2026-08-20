"""Public-facing identifiers for ActionIssue rows.

The raw autoincrement `id` doubles as "the Nth issue we've ever run", which
reads as a popularity/count signal it was never meant to carry once it's
printed on a Bluesky post or a share link. `to_public_id`/`from_public_id`
are a bijection on a fixed-width modulus, not a lookup table: every id maps
to exactly one public id and back, so uniqueness is guaranteed by
construction — no stored column, no migration, no collision to ever handle.

_MULTIPLIER is odd, hence coprime with _MODULUS (a power of two), hence
invertible mod _MODULUS — the standard trick behind "obfuscated auto-
increment id" schemes (Knuth's multiplicative hash constant).
"""

_MODULUS = 2**32
_MULTIPLIER = 2654435761
_MULTIPLIER_INV = pow(_MULTIPLIER, -1, _MODULUS)
_PREFIX = "i"


def to_public_id(issue_id: int) -> str:
    return f"{_PREFIX}{(issue_id * _MULTIPLIER) % _MODULUS:08x}"


def from_public_id(public_id: str) -> int | None:
    """Inverse of `to_public_id`. None if the string isn't a well-formed one.

    Case-insensitive: `to_public_id` only ever emits lowercase, but the UI
    displays it upper-cased (matching "ISSUE-"), and a reader who copies
    that label straight into a URL should land on the issue, not a 404
    over letter case alone.
    """
    public_id = public_id.lower()
    if not public_id.startswith(_PREFIX) or len(public_id) != len(_PREFIX) + 8:
        return None
    try:
        obfuscated = int(public_id[len(_PREFIX):], 16)
    except ValueError:
        return None
    return (obfuscated * _MULTIPLIER_INV) % _MODULUS


def demo() -> None:
    for issue_id in (1, 2, 3, 999_999, 2**31):
        public_id = to_public_id(issue_id)
        assert from_public_id(public_id) == issue_id, (issue_id, public_id)
    seen = {to_public_id(i) for i in range(2000)}
    assert len(seen) == 2000, "collision in the first 2000 ids"
    assert from_public_id("42") is None  # not one of ours — no "i" prefix
    assert from_public_id("izzzzzzzz") is None  # not hex
    print("ok")


if __name__ == "__main__":
    demo()
