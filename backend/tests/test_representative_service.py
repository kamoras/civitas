"""Tests for representative_service.build_rep_response's RepresentativeSchema
construction (see PR #122 — converted from a hand-typed dict, which had no
validation).

Production incident, 2026-07-16: the conversion briefly 500'd every
representative detail page because DonorSchema.type's Literal was missing
"SKIP" — a real, first-class donor-classification outcome (see
donor_classifier_ai.py / normalize_finance.py) that House data hits but no
senator happened to have at the time. These tests pin that gap shut and
guard against the same class of surprise for any other Literal-constrained
field build_rep_response feeds.
"""

from app.models import Representative, RepDonor
from app.services.representative_service import build_rep_response


def _make_rep(db_session, rep_id: str, donor_types: list[str]) -> Representative:
    rep = Representative(
        id=rep_id, name="Test Rep", state="CA", district=12, party="D",
        total_raised=500_000, total_from_pacs=100_000, small_donor_percentage=25,
        years_in_office=2,
    )
    db_session.add(rep)
    for i, donor_type in enumerate(donor_types):
        # "PAC" in the name keeps _fixup_donor_type's read-time safety net
        # from rewriting a type="PAC" row to "Org/Employees" — irrelevant to
        # the other types, which it always passes through unchanged.
        db_session.add(RepDonor(
            representative_id=rep_id, name=f"Donor {i} PAC", total=1_000, type=donor_type, rank=i,
        ))
    db_session.commit()
    db_session.refresh(rep)
    return rep


class TestBuildRepResponseDonorTypes:
    def test_skip_donor_type_does_not_raise(self, db_session):
        rep = _make_rep(db_session, "test-rep", ["SKIP"])
        result = build_rep_response(rep, db_session)
        assert result.funding.top_donors[0].type == "SKIP"

    def test_every_donor_type_seen_in_the_pipeline_is_accepted(self, db_session):
        # donor_classifier_ai.py / normalize_finance.py's full output vocabulary.
        donor_types = [
            "PAC", "Individual", "SuperPAC", "Org/Employees",
            "Party/Ideological", "CandidateAffiliated", "Self-Funded", "SKIP",
        ]
        rep = _make_rep(db_session, "test-rep-2", donor_types)
        result = build_rep_response(rep, db_session)
        assert [d.type for d in result.funding.top_donors] == donor_types
