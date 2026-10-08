"""A member's top industry is an industry: never small donors, unattributed
individuals or other non-industry money, which often hold the most dollars."""

from app.models import IndustryDonation, Senator
from app.services.senator_service import get_leaderboard


def test_the_top_industry_skips_money_that_is_no_industry(db_session):
    db_session.add(Senator(id="jane-doe", name="Jane Doe", state="CA", party="D", is_current=True))
    db_session.add_all([
        IndustryDonation(senator_id="jane-doe", industry="LARGE_INDIVIDUAL", name="LARGE INDIVIDUAL", total=900_000),
        IndustryDonation(senator_id="jane-doe", industry="SMALL_DONORS", name="SMALL DONORS", total=800_000),
        IndustryDonation(senator_id="jane-doe", industry="LAWYERS", name="LAWYERS", total=50_000),
    ])
    db_session.commit()
    [entry] = get_leaderboard(db_session)
    assert entry.top_industry == "LAWYERS"
