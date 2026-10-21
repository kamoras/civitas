"""Which currently-live ActionIssues draw more readers than chance.

An issue is "trending" when today's views on it are more than an even
share of today's views across every live issue would give it, by a
one-sided exact binomial test (each view landing on any live issue alike
under the null) at the conventional 5% level, Bonferroni-corrected for the
live issues tested at once. No floor or multiple is typed: on a quiet day
the few views there are can't clear the test, and on a busy one an issue
merely matching the pack doesn't.

Views are IssueView's counts, which count a client once per issue an hour
(api/visits.py), so a reader refreshing a page can't push an issue over.
"""

import math

# The conventional significance level (Fisher), shared by the live issues
# tested together (Bonferroni).
ALPHA = 0.05


def _upper_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed in log space so a busy day's
    thousands of views don't overflow."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    head = math.lgamma(n + 1)
    return min(1.0, sum(math.exp(head - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q)
                        for i in range(k, n + 1)))


def compute_trending_issue_ids(view_counts: dict[str, int], live: int) -> set[str]:
    """issue_public_ids whose view count today is significantly above an
    even share of the day's views over the `live` issues (those with no
    views count too; `view_counts` holds only the viewed ones). With one
    live issue there is nothing to stand out from, and nothing trends."""
    live = max(live, len(view_counts))
    total = sum(view_counts.values())
    if live < 2 or total == 0:
        return set()
    share, level = 1 / live, ALPHA / live
    return {issue_id for issue_id, count in view_counts.items()
            if count > total * share and _upper_tail(count, total, share) < level}
