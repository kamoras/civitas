"""The caucus the House Clerk records for each member (MemberData.xml)."""

from app.pipeline.fetch.house_clerk import parse_caucuses

# Trimmed from MemberData.xml as published 2026-10-01 (ids replaced): an
# independent recorded in the Republican caucus, a major-party member, and
# a vacant seat.
_MEMBER_DATA = b"""<?xml version="1.0" encoding="UTF-8"?>
<MemberData publish-date="October 1, 2026"><members>
<member><statedistrict>CA03</statedistrict><member-info><bioguideID>Z000001</bioguideID>
<party>I</party><caucus>R</caucus></member-info></member>
<member><statedistrict>CA08</statedistrict><member-info><bioguideID>Z000002</bioguideID>
<party>D</party><caucus>D</caucus></member-info></member>
<member><statedistrict>TX23</statedistrict><member-info><bioguideID></bioguideID>
<party></party><caucus></caucus></member-info></member>
</members></MemberData>"""


def test_the_clerk_records_each_members_caucus():
    assert parse_caucuses(_MEMBER_DATA) == {"Z000001": "R", "Z000002": "D"}
    assert parse_caucuses(b"<not xml") == {}
