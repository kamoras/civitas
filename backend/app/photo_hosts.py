"""Where each kind of photo may be fetched from (app/photos.py explains
why, and enforces it). Standard library only: the crawler page reads it
without the database models."""

PHOTO_SOURCE_HOSTS: dict[str, frozenset[str]] = {
    # bioguide_photo_source names one of these: Congress.gov's member
    # image, or bioguide's portrait.
    "bioguide": frozenset({"www.congress.gov", "bioguide.congress.gov"}),
    # Oyez's justice thumbnails (fetch/justice_votes.py): every sitting
    # justice's is on api.oyez.org (checked 2026-10-10).
    "justice": frozenset({"api.oyez.org"}),
    # Where the rights-cleared feed photos are (fetch/news_feeds.py's
    # _rights_cleared_image, which applies this list). Not derivable from
    # the feed list: the rights signal is per item, in any feed. Only Roll
    # Call's feed carries it, and every rights-cleared item there points at
    # rollcall.com (10 of 10, 2026-10-10). A feed that starts granting
    # rights on another host needs that host added here, deliberately.
    "issue": frozenset({"rollcall.com"}),
    # A president's public-domain portrait on Wikimedia Commons
    # (pipeline/fetch/president_portraits.py): the API's thumbnail URLs are
    # on thumb.wikimedia.org, and a thumbnail not yet rendered redirects to
    # upload.wikimedia.org (checked 2026-10-20); a National Portrait Gallery
    # CC0 portrait, where Wikidata's is not public domain, is on the
    # Smithsonian's image service, ids.si.edu.
    "president": frozenset({"thumb.wikimedia.org", "upload.wikimedia.org", "ids.si.edu"}),
}
