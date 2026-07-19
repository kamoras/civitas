"""Tests for senate_pipeline helper functions."""

from app.pipeline.senate_pipeline import _build_donor_entries


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
