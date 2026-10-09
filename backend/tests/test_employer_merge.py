"""One employer typed two ways is one donor (normalize_finance.merge_misspelled_employers)."""

from app.pipeline.transform.normalize_finance import merge_misspelled_employers


def _emp(total):
    return {"name": "", "total": total, "type": "Org/Employees", "industry": "OTHER"}


def test_spellings_from_the_cached_fec_totals():
    # Pairs as the FEC's employer totals carry them (2026-10).
    donors = {
        "FRY EYE ASSOCIATES": _emp(17400), "FRY EYE ASSCOIATES": _emp(7900),
        "TRIDENT SEAFOODS, INC.": _emp(5000), "TRIDENT SEAFOODS INC.": _emp(1000),
        "HARTFORD HEALTHCARE": _emp(3000), "HARTFORD HEALTCARE": _emp(500),
        # Different employers one word apart.
        "CENTERVIEW PARTNERS": _emp(4000), "CRESTVIEW PARTNERS": _emp(2000),
        "HEALTH2047 INC": _emp(900), "HEALTH247 INC": _emp(800),
        "UNIVERSITY OF MAINE": _emp(700), "UNIVERSITY OF MIAMI": _emp(600),
        "USDA": _emp(400), "USAF": _emp(300),
        # A committee is never merged.
        "FRY EYE ASSOCIATE": {"name": "", "total": 100, "type": "PAC", "industry": "OTHER", "isCommittee": True},
    }
    merge_misspelled_employers(donors)
    assert donors["FRY EYE ASSOCIATES"]["total"] == 25300 and "FRY EYE ASSCOIATES" not in donors
    assert donors["TRIDENT SEAFOODS, INC."]["total"] == 6000
    assert donors["HARTFORD HEALTHCARE"]["total"] == 3500
    for kept in ("CRESTVIEW PARTNERS", "HEALTH247 INC", "UNIVERSITY OF MIAMI", "USAF", "FRY EYE ASSOCIATE"):
        assert kept in donors
