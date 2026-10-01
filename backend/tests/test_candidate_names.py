"""Tests for deterministic self-donor detection."""

import pytest

from app.pipeline.transform.candidate_names import is_candidate_self_donor


class TestSelfDonorDetection:
    @pytest.mark.parametrize("donor,candidate", [
        ("Scott, Rex", "Rex Scott"),
        ("Scott, Rex Senator", "Rex Scott"),
        ("Scott, Rex Gov", "Rex Scott"),
        ("Mccracken, Dave", "David McCracken"),
        ("Tolliver, Thomas H.", "Tommy Tolliver"),
        ("Rasch, James E Mr", "James E. Rasch"),
        ("King, Fergus Stanley Jr", "Fergus S., Jr. King"),
        ("Lomax, Celia Mrs.", "Celia M. Lomax"),
        ("Whitlock, Sherwood", "Sherwood Whitlock"),
        ("Carver, Ron H Mr", "Ron Carver"),
        ("Rickard, Pete", "Pete Rickard"),
        ("Moretti, Benny", "Benny Moretti"),
    ])
    def test_matches_self(self, donor, candidate):
        assert is_candidate_self_donor(donor, candidate)

    @pytest.mark.parametrize("donor,candidate", [
        ("Pinnacle Bank", "Bill Halloran"),
        ("Charles Schwab & CO INC", "Ray Holloway"),
        ("Janney Montgomery Scott, LLC", "Rex Scott"),
        ("Danny Kim For Congress", "Danny Kim"),          # committee, not the person
        ("Scott, Ann", "Rex Scott"),                    # different first name
        ("Scott", "Rex Scott"),                         # last name alone: not enough
        ("Cornyn Victory Committee", "Lisa Marchowski"),
        ("", "Rex Scott"),
        ("Scott, Rex", ""),
    ])
    def test_rejects_non_self(self, donor, candidate):
        assert not is_candidate_self_donor(donor, candidate)

    def test_uppercase_fec_form(self):
        assert is_candidate_self_donor("SCOTT, REX", "Rex Scott")
