"""Every photo the API hands a page is served from this site (AGENTS.md §8).

A visitor's browser loads whatever image URL the API gives it, so an
absolute URL on another host there sends every visitor's address to that
host. The API gives `/photo/<kind>/<id>` paths instead (app/photos.py), and
the frontend's photo route fetches the picture server-side.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.action import _build_issue_response
from app.api.photos import get_photo_source
from app.api.politicians import get_politician, list_politicians
from app.models import ActionIssue, ApiCache, Justice, Senator
from app.photos import PHOTO_SOURCE_HOSTS
from app.pipeline.fetch.congress import member_detail_cache_key
from app.services.justice_service import get_all_justices, get_justice_leaderboard

APP = Path(__file__).resolve().parents[1] / "app"

_PHOTO_PATH = re.compile(r"^/photo/(bioguide|justice|issue)/[A-Za-z0-9_]+$")
_IMAGE_KEY = re.compile(r"(thumbnail|image|photo|portrait|avatar)(_?url)?$", re.I)


def _image_values(node, key=""):
    """Every string under an image-looking key, anywhere in a response."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _image_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _image_values(v, key)
    elif isinstance(node, str) and _IMAGE_KEY.search(key):
        yield key, node


def _assert_same_origin(response) -> int:
    found = list(_image_values(response))
    for key, value in found:
        assert _PHOTO_PATH.match(value), f"{key}={value!r} is not a same-origin photo path"
    return len(found)


@pytest.fixture()
def seeded(db_session):
    db_session.add(Senator(id="sen-a", name="Senator A", state="VT", party="I", bioguide_id="X000001"))
    db_session.add(Justice(
        id="justice_a", name="Justice A", last_name="A", is_active=True,
        thumbnail_url="https://api.oyez.org/sites/default/files/justice_a.png",
    ))
    db_session.add(ActionIssue(
        id=7, date="2026-10-01", rank=1, title="An issue", is_current=True,
        image_url="https://rollcall.com/app/uploads/photo.jpg",
    ))
    db_session.commit()
    return db_session


def test_every_photo_field_is_a_same_origin_path(seeded):
    db = seeded
    responses = [
        json.loads(list_politicians(branch=None, state=None, party=None, q=None, db=db).body),
        json.loads(get_politician("sen-a", db=db).body),
        json.loads(get_politician("justice_a", db=db).body),
        [j.model_dump(by_alias=True) for j in get_all_justices(db)],
        [j.model_dump(by_alias=True) for j in get_justice_leaderboard(db)],
        _build_issue_response(db.get(ActionIssue, 7), db),
    ]
    # Each response carried at least one photo, so the check saw something.
    assert all(_assert_same_origin(r) for r in responses)


def test_the_route_can_look_up_a_stored_source(seeded):
    assert get_photo_source("justice", "justice_a", db=seeded) == {"url": "https://api.oyez.org/sites/default/files/justice_a.png"}
    assert get_photo_source("issue", "7", db=seeded) == {"url": "https://rollcall.com/app/uploads/photo.jpg"}


@pytest.mark.parametrize("kind,photo_id", [
    ("justice", "nobody"), ("issue", "8"), ("issue", "1" * 40), ("issue", "x"), ("senator", "sen-a"),
])
def test_an_unknown_photo_is_a_404(seeded, kind, photo_id):
    with pytest.raises(HTTPException) as err:
        get_photo_source(kind, photo_id, db=seeded)
    assert err.value.status_code == 404


@pytest.mark.parametrize("source", [
    "http://backend:8000/api/admin",            # not https, internal
    "https://images.example.org/a.png",         # https, but not an allowed host
    "https://rollcall.com/app/uploads/a.jpg",   # another kind's host
    "https://api.oyez.org.evil.example/a.png",  # a lookalike
    "https://api.oyez.org:8443/a.png",          # an allowed host, another port
    "https://user@api.oyez.org/a.png",          # credentials in the URL
])
def test_a_stored_source_off_its_kinds_hosts_is_never_handed_out(db_session, source):
    db_session.add(Justice(id="j", name="J", last_name="J", thumbnail_url=source))
    db_session.commit()
    with pytest.raises(HTTPException) as err:
        get_photo_source("justice", "j", db=db_session)
    assert err.value.status_code == 404


def test_the_frontend_route_allows_the_same_hosts():
    """The route re-checks every URL and redirect hop against its own copy
    (it can't import Python); the two lists must not drift apart."""
    ts = (APP.parents[1] / "frontend" / "src" / "lib" / "photos.ts").read_text()
    block = re.search(r"PHOTO_HOSTS[^=]*=\s*\{(.*?)\n\}", ts, re.S).group(1)
    frontend = {
        kind: frozenset(re.findall(r'"([^"]+)"', hosts))
        for kind, hosts in re.findall(r"(\w+):\s*\[([^\]]*)\]", block)
    }
    assert frontend == PHOTO_SOURCE_HOSTS


def _member_record(db, bioguide_id, depiction):
    record = {"bioguideId": bioguide_id}
    if depiction is not None:
        record["depiction"] = depiction
    db.add(ApiCache(tier="congress", cache_key=f"member-detail-{bioguide_id}", data_json=json.dumps(record)))
    db.commit()


_HEADSHOT = "https://www.congress.gov/img/member/x000001_200.jpg"
_BIOGUIDE = "https://bioguide.congress.gov/photo/X000001.jpg"


def test_a_member_photo_is_the_congress_gov_records_headshot(db_session):
    """bioguide's file is for some members a full-length painted portrait;
    the record's depiction is the head-and-shoulders photo."""
    _member_record(db_session, "X000001", {"attribution": "Image courtesy of the Member", "imageUrl": _HEADSHOT})
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": _HEADSHOT}


@pytest.mark.parametrize("depiction", [
    None,                                                                    # the record has none
    {"imageUrl": _HEADSHOT},                                                 # no attribution
    {"attribution": "  ", "imageUrl": _HEADSHOT},                            # blank attribution
    {"attribution": "<p></p>", "imageUrl": _HEADSHOT},                       # markup, no words
    {"attribution": "Image courtesy of the Member",
     "imageUrl": "https://www.congress.gov/img/other/x000001_200.jpg"},     # not the member-image path
    {"attribution": "Image courtesy of the Member",
     "imageUrl": "https://images.example.org/img/member/x000001_200.jpg"},  # another host
    {"attribution": "Image courtesy of the Member",
     "imageUrl": "http://www.congress.gov/img/member/x000001_200.jpg"},     # not https
    "not-a-dict",
])
def test_a_member_photo_falls_back_to_bioguide(db_session, depiction):
    _member_record(db_session, "X000001", depiction)
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": _BIOGUIDE}


def test_a_member_with_no_stored_record_gets_bioguides_portrait(db_session):
    """A bill sponsor from before the pipeline's time has no record."""
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": _BIOGUIDE}


def test_a_depiction_attribution_with_markup_counts(db_session):
    attribution = '<a href="https://www.senate.gov/">Courtesy U.S. Senate Historical Office</a>'
    _member_record(db_session, "X000001", {"attribution": attribution, "imageUrl": _HEADSHOT})
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": _HEADSHOT}


@pytest.mark.parametrize("photo_id", ["x000001", "X00001", "../X000001", "X000001.jpg"])
def test_a_member_photo_needs_a_bioguide_id(db_session, photo_id):
    with pytest.raises(HTTPException) as err:
        get_photo_source("bioguide", photo_id, db=db_session)
    assert err.value.status_code == 404


def test_the_member_photo_key_is_the_one_the_fetcher_writes():
    assert member_detail_cache_key("X000001") == "member-detail-X000001"


# A literal image URL on another host, anywhere the API or its services
# build responses. The pipeline's fetchers (which read sources) are the
# only place one belongs, besides app/photos.py, which names a member
# photo's source for the photo route; app/api/photos.py hands those to the
# route, never to a page.
_ABSOLUTE_IMAGE_URL = re.compile(
    r"""https?://[^\s"'`]+?\.(?:jpe?g|png|gif|webp|avif|svg)\b|bioguide\.congress\.gov/(?:bioguide/)?photo""",
    re.I,
)


def test_no_api_code_builds_a_third_party_image_url():
    offenders = [
        f"{path.relative_to(APP)}: {m.group(0)}"
        for path in APP.rglob("*.py")
        if "pipeline" not in path.relative_to(APP).parts and path != APP / "photos.py"
        for m in _ABSOLUTE_IMAGE_URL.finditer(path.read_text())
    ]
    assert offenders == []
