"""Presidents' portraits: public domain only, served from this site."""

import pytest
from fastapi import HTTPException

from app.api.photos import get_photo_source
from app.models import President
from app.photos import portrait_credit, president_photo
from app.pipeline.cache import api_cache_set
from app.pipeline.fetch.president_portraits import npg_portrait_from_row, portrait_from_imageinfo, sitter_is


def _page(license_name: str, artist: str = "Jane Roe") -> dict:
    return {"imageinfo": [{
        "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/500px-X.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:X.jpg",
        "extmetadata": {"LicenseShortName": {"value": license_name}, "Artist": {"value": f"<a>{artist}</a>"}},
    }]}


def test_only_a_public_domain_file_is_used():
    assert portrait_from_imageinfo(_page("Public domain"))["artist"] == "Jane Roe"
    assert portrait_from_imageinfo(_page("PD-USGov")) is not None
    assert portrait_from_imageinfo(_page("CC BY-SA 4.0")) is None
    assert portrait_from_imageinfo({"imageinfo": [{}]}) is None


def _npg_row(sitters, access="CC0", unit="NPG"):
    names = [{"label": "Artist", "content": "Ann Poe, 1875 - 1941"}] + [
        {"label": "Sitter", "content": s} for s in sitters]
    return {"content": {
        "freetext": {"name": names},
        "descriptiveNonRepeating": {
            "unit_code": unit, "record_link": "https://npg.si.edu/object/npg_X",
            "online_media": {"media": [{"type": "Images", "usage": {"access": access},
                                        "content": "https://ids.si.edu/ids/deliveryService?id=NPG-X"}]},
        },
    }}


def test_the_gallery_stands_in_with_cc0_portraits_of_the_president_alone():
    got = npg_portrait_from_row(_npg_row(["Jane Quinn Doe, 1 Jan 1882 - 1 Jan 1945"]), "Jane Q. Doe")
    assert got["url"] == "https://ids.si.edu/ids/deliveryService?id=NPG-X&max=480"
    assert portrait_credit(got)["text"] == (
        "Portrait: Ann Poe. CC0, via the National Portrait Gallery, Smithsonian Institution.")
    # Another person of the same surname, a group, a restricted image, another museum: none.
    assert npg_portrait_from_row(_npg_row(["John Doe, 1858 - 1919"]), "Jane Q. Doe") is None
    assert npg_portrait_from_row(_npg_row(["Jane Quinn Doe", "John Roe"]), "Jane Q. Doe") is None
    assert npg_portrait_from_row(_npg_row(["Jane Quinn Doe"], access="usage conditions apply"), "Jane Q. Doe") is None
    assert npg_portrait_from_row(_npg_row(["Jane Quinn Doe"], unit="SAAM"), "Jane Q. Doe") is None
    assert sitter_is("Jane Q. Doe, Jr.", "Jane Quinn Doe, 1900")


def test_the_credit_names_the_author_only_when_known():
    assert portrait_credit({"artist": "Jane Roe", "license": "Public domain", "page": "p"})["text"] == (
        "Portrait: Jane Roe. Public domain, via Wikimedia Commons.")
    assert portrait_credit({"artist": "Unknown author", "license": "Public domain"})["text"] == (
        "Portrait. Public domain, via Wikimedia Commons.")
    assert portrait_credit(None) is None


def test_the_photo_route_gets_the_stored_portrait_and_nothing_else(db_session):
    db_session.add(President(id="doe-9", name="Jane Doe", party="D", number=9, term_start="1841-03-04",
                             term_end="1841-04-04", is_current=False))
    db_session.add(President(id="roe-10", name="John Roe", party="D", number=10, term_start="1841-04-04",
                             term_end="1845-03-04", is_current=False))
    db_session.commit()
    url = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/500px-X.jpg"
    api_cache_set(db_session, "president-portraits", "by-number", {"portraits": {"9": {"url": url}}})
    assert get_photo_source("president", "doe-9", db_session) == {"url": url}
    # No portrait stored for the other: a 404, and its page keeps the placeholder.
    with pytest.raises(HTTPException):
        get_photo_source("president", "roe-10", db_session)
    assert president_photo(db_session.get(President, "doe-9"), {"9": {}}) == "/photo/president/doe-9"
    assert president_photo(db_session.get(President, "roe-10"), {"9": {}}) is None
