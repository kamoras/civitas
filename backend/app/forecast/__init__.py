"""The 2026 general-election map's poll-informed vote-margin ranges.

Kept apart from everything that scores members: nothing here reads a
Representation Score, a scoring reference or SCORE_WEIGHTS, and nothing
in scoring reads this package. It lives outside app/pipeline/, so it is
not part of the analysis-code fingerprint and editing it never forces a
cold re-score.
"""
