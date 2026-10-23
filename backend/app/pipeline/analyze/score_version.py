"""The member scoring version (re-exported by score_calculator)."""

from app import scoring_revisions

# Bump when scoring formulas or their data inputs change in a way that
# shifts scores. Keep frontend/src/lib/scoreVersions.ts (the public
# changelog) in sync, and add a decision record for the new version under
# docs/methodology/member-score/ — that is where the reasons go, not here.
# What every ScoreSnapshot records is scoring_version(): this, followed by
# the count of refits the data refresh has installed under the acceptance
# rule since (app/scoring_revisions.py), so trend charts annotate those too.
ALGORITHM_VERSION = "v6.38"


def scoring_version() -> str:
    """The version stored scores carry: ALGORITHM_VERSION and the accepted
    refits installed under it ("v6.38.1")."""
    return scoring_revisions.scoring_version(ALGORITHM_VERSION)
