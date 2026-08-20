"""Which currently-live ActionIssues clear a real traction bar.

A relative-only comparison ("today's most-viewed issue") would flag
something "trending" even on a quiet day where the top issue has a single
view and every other issue has none — see compute_trending_issue_ids.
"""

# Absolute floor: below this, "traction" doesn't mean anything regardless
# of how it compares to other issues that day.
_TRENDING_MIN_VIEWS = 10

# An issue has to clear this multiple of the day's median to stand out
# from the pack, not just be a merely-average issue that happened to be
# highest among a low pool.
_TRENDING_MEDIAN_MULTIPLE = 2.0


def _median(values: list[int]) -> float:
    n = len(values)
    values = sorted(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def compute_trending_issue_ids(view_counts: dict[str, int]) -> set[str]:
    """issue_public_ids whose view count clears BOTH the absolute floor
    and a multiple of its PEERS' median — everyone else's counts, not
    including its own. Comparing an issue's count against a median that
    includes itself makes the multiple-of-median bar nearly unreachable
    once there are only a few issues in play (with one issue, the median
    IS its own count, so 2x-the-median can never hold); excluding self
    is also just the more honest question — "does this stand out from the
    rest", not "does this exceed a number partly made of itself". An
    issue with no peers at all (the only one with any views today) is
    judged on the floor alone, since there's nothing to compare it to.
    """
    if not view_counts:
        return set()
    trending: set[str] = set()
    for issue_id, count in view_counts.items():
        if count < _TRENDING_MIN_VIEWS:
            continue
        others = [c for oid, c in view_counts.items() if oid != issue_id]
        bar = max(_TRENDING_MIN_VIEWS, _median(others) * _TRENDING_MEDIAN_MULTIPLE) if others else _TRENDING_MIN_VIEWS
        if count >= bar:
            trending.add(issue_id)
    return trending
