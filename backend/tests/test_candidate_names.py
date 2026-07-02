"""Tests for deterministic self-donor detection."""

import pytest

from app.pipeline.transform.candidate_names import is_candidate_self_donor


class TestSelfDonorDetection:
    @pytest.mark.parametrize("donor,candidate", [
        ("Scott, Rick", "Rick Scott"),
        ("Scott, Rick Senator", "Rick Scott"),
        ("Scott, Rick Gov", "Rick Scott"),
        ("Mccormick, Dave", "David McCormick"),
        ("Tuberville, Thomas H.", "Tommy Tuberville"),
        ("Risch, James E Mr", "James E. Risch"),
        ("King, Angus Stanley Jr", "Angus S., Jr. King"),
        ("Lummis, Cynthia Mrs.", "Cynthia M. Lummis"),
        ("Whitehouse, Sheldon", "Sheldon Whitehouse"),
        ("Johnson, Ron H Mr", "Ron Johnson"),
        ("Ricketts, Pete", "Pete Ricketts"),
        ("Moreno, Bernie", "Bernie Moreno"),
    ])
    def test_matches_self(self, donor, candidate):
        assert is_candidate_self_donor(donor, candidate)

    @pytest.mark.parametrize("donor,candidate", [
        ("Pinnacle Bank", "Bill Hagerty"),
        ("Charles Schwab & CO INC", "John Thune"),
        ("Janney Montgomery Scott, LLC", "Rick Scott"),
        ("Andy Kim For Congress", "Andy Kim"),          # committee, not the person
        ("Scott, Ann", "Rick Scott"),                    # different first name
        ("Scott", "Rick Scott"),                         # last name alone: not enough
        ("Cornyn Victory Committee", "Lisa Murkowski"),
        ("", "Rick Scott"),
        ("Scott, Rick", ""),
    ])
    def test_rejects_non_self(self, donor, candidate):
        assert not is_candidate_self_donor(donor, candidate)

    def test_uppercase_fec_form(self):
        assert is_candidate_self_donor("SCOTT, RICK", "Rick Scott")
