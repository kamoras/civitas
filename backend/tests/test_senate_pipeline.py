"""Tests for senate_pipeline helper functions."""

from app.config import settings
from app.pipeline.senate_pipeline import (
    _build_current_term_sponsored_for_cosponsor,
    _build_donor_entries,
)


class TestBuildDonorEntries:
    """_build_donor_entries flattens FEC receipts for classify_donors_hybrid.

    Every entry needs `fec_receipt` attached so Tier 1 (FEC entity_type) can
    resolve it — otherwise a bare company name like "Airbnb" falls through
    to the semantic embedding tier, which has no reliable signal for it
    (see test_donor_classifier.py's IND -> Org/Employees mapping).
    """

    def test_employer_receipts_carry_fec_receipt(self):
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "receipts": [
                    {
                        "contributor_employer": "Airbnb",
                        "contribution_receipt_amount": 10000,
                        "entity_type": "IND",
                    }
                ],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert entries[0]["name"] == "Airbnb"
        assert entries[0]["fec_receipt"]["entity_type"] == "IND"

    def test_pac_receipts_carry_fec_receipt(self):
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "pacReceipts": [
                    {
                        "contributor_name": "Test PAC",
                        "contribution_receipt_amount": 5000,
                        "entity_type": "PAC",
                    }
                ],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert entries[0]["fec_receipt"]["entity_type"] == "PAC"

    def test_skips_senators_with_no_fec_data(self):
        senators = [{"id": "sen-1"}, {"id": "sen-2"}]
        fec_data = {"sen-1": {"receipts": [{"contributor_employer": "Acme"}]}}
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1

    def test_skips_receipts_with_no_employer(self):
        senators = [{"id": "sen-1"}]
        fec_data = {"sen-1": {"receipts": [{"contributor_employer": ""}]}}
        assert _build_donor_entries(senators, fec_data) == []

    def test_aggregated_entries_have_no_fec_receipt(self):
        """Aggregated (by_contributor) rows don't carry a raw receipt — this
        is unchanged pre-existing behavior, not part of the employer-receipt
        fix."""
        senators = [{"id": "sen-1"}]
        fec_data = {
            "sen-1": {
                "aggregated": [{"contributor_name": "Some Donor", "total": 2500}],
            }
        }
        entries = _build_donor_entries(senators, fec_data)
        assert len(entries) == 1
        assert "fec_receipt" not in entries[0]


class TestBuildCurrentTermSponsoredForCosponsor:
    """No per-senator cap — a prior 10-bill cap meant a prolific sponsor's
    ideology/leadership score was computed from under 15% of their actual
    sponsored bills (2026-07 audit, prompted by a real senator with 49
    sponsored bills in the current congress landing near the ideological
    center despite a real-world reputation, corroborated by GovTrack, as
    one of the most ideologically extreme senators)."""

    def _prep(self, bio_id: str, bills: list[dict], party: str = "D") -> dict:
        return {
            "senator": {"bioguideId": bio_id, "party": party},
            "sponsoredBills": bills,
        }

    def test_no_cap_on_bill_count(self):
        many_bills = [
            {"congress": settings.CURRENT_CONGRESS, "billId": f"S.{i}"}
            for i in range(49)
        ]
        prepared = [self._prep("S001", many_bills)]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert len(entries) == 49

    def test_excludes_bills_from_other_congresses(self):
        bills = [
            {"congress": settings.CURRENT_CONGRESS - 1, "billId": "S.1"},
            {"congress": settings.CURRENT_CONGRESS, "billId": "S.2"},
            {"congress": settings.CURRENT_CONGRESS + 1, "billId": "S.3"},
        ]
        prepared = [self._prep("S001", bills)]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert len(entries) == 1
        assert entries[0]["billId"] == "S.2"

    def test_skips_senators_with_no_bioguide_id(self):
        prepared = [{
            "senator": {"bioguideId": "", "party": "D"},
            "sponsoredBills": [{"congress": settings.CURRENT_CONGRESS, "billId": "S.1"}],
        }]
        assert _build_current_term_sponsored_for_cosponsor(prepared) == []

    def test_skips_bills_with_no_bill_id(self):
        bills = [{"congress": settings.CURRENT_CONGRESS}]
        prepared = [self._prep("S001", bills)]
        assert _build_current_term_sponsored_for_cosponsor(prepared) == []

    def test_entry_shape(self):
        bills = [{
            "congress": settings.CURRENT_CONGRESS,
            "billId": "S.42",
            "isLaw": True,
            "latestAction": "Signed by President",
        }]
        prepared = [self._prep("S001", bills, party="R")]
        entries = _build_current_term_sponsored_for_cosponsor(prepared)
        assert entries == [{
            "billId": "S.42",
            "congress": settings.CURRENT_CONGRESS,
            "sponsorBioguide": "S001",
            "sponsorParty": "R",
            "isLaw": True,
            "latestAction": "Signed by President",
        }]
