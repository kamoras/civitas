"""The member-photo choice (pipeline/fetch/member_photos.py): which official
photo a member's avatar shows, measured nightly by the size of the face in
each candidate and read by the photo-sources endpoint."""

import ast
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from app.api.photos import get_photo_source
from app.models import ApiCache, MemberPhoto, Senator
from app.pipeline.fetch import member_photos
from app.pipeline.fetch.member_photos import (
    CALIBRATION_PATH,
    MODEL_PATH,
    calibration,
    face_share,
    prefers_bioguide,
    refresh_member_photos,
)

DEPICTION = "https://www.congress.gov/img/member/x000001_200.jpg"
PORTRAIT = "https://bioguide.congress.gov/photo/X000001.jpg"
NOW = datetime(2026, 10, 13, 6, 0)


def _member(db, depiction=DEPICTION, attribution="Image courtesy of the Member"):
    db.add(Senator(id="sen-x", name="Senator X", state="VT", party="I", bioguide_id="X000001"))
    record = {"bioguideId": "X000001", "depiction": {"imageUrl": depiction, "attribution": attribution}}
    db.add(ApiCache(tier="congress", cache_key="member-detail-X000001", data_json=json.dumps(record)))
    db.commit()


def _client(answers: dict[str, httpx.Response]) -> httpx.AsyncClient:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return answers[str(request.url)]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.seen = seen
    return client


def _jpeg(tag: bytes) -> httpx.Response:
    return httpx.Response(200, content=b"\xff\xd8" + tag, headers={"content-type": "image/jpeg"})


@pytest.fixture()
def measured(monkeypatch):
    """face_share stubbed: each fake image's bytes name its face share."""
    monkeypatch.setattr(member_photos, "face_share", lambda body: float(body[2:].decode()))
    monkeypatch.setattr(member_photos, "PAUSE_S", 0)


def test_calibration_is_the_fitted_file():
    cal = calibration()
    assert set(cal) >= {"margin", "depiction_ceiling", "labels", "confusion", "detector"}
    # The fitted setting sits inside the window every label agreed with.
    assert cal["feasible"]["margin"][0] <= cal["margin"] <= cal["feasible"]["margin"][1]
    lo, hi = cal["feasible"]["depiction_ceiling"]
    assert lo <= cal["depiction_ceiling"] <= hi
    # Every decisive label is chosen as labelled.
    confusion = cal["confusion"]["label -> chosen"]
    assert set(confusion["bioguide"]) == {"bioguide"}
    assert set(confusion["depiction"]) == {"depiction"}
    assert "bioguide" not in confusion["either"]


def test_the_detector_model_is_the_one_the_calibration_measured_with():
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == json.loads(CALIBRATION_PATH.read_text())["detector"]["sha256"]


def test_face_share_reads_an_image_and_refuses_anything_else():
    blank = cv2.imencode(".jpg", np.full((250, 200, 3), 128, np.uint8))[1].tobytes()
    assert face_share(blank) == 0.0
    assert face_share(b"<html>challenge</html>") is None


@pytest.mark.parametrize("bio,dep,expected", [
    (0.75, 0.25, True),    # a small full-figure depiction, bioguide's headshot
    (0.45, 0.45, False),   # the same photo
    (0.65, 0.42, False),   # two headshots, bioguide's tighter: the depiction stays
    (0.30, 0.29, False),   # the same non-headshot in both
    (0.10, 0.50, False),   # bioguide's full-length painting
    (None, 0.20, False),   # bioguide has no portrait
    (0.50, None, False),   # nothing measured on the depiction
])
def test_prefers_bioguide(bio, dep, expected):
    assert prefers_bioguide(bio, dep) is expected


async def test_a_small_depiction_face_chooses_bioguides_headshot(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session)
    async with _client({DEPICTION: _jpeg(b"0.25"), PORTRAIT: _jpeg(b"0.75")}) as client:
        assert await refresh_member_photos(db_session, client, timedelta(minutes=1)) == 1
    row = db_session.get(MemberPhoto, "X000001")
    assert (row.source_url, row.depiction_face_share, row.bioguide_face_share) == (PORTRAIT, 0.25, 0.75)
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": PORTRAIT}


async def test_a_headshot_depiction_stays(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session)
    async with _client({DEPICTION: _jpeg(b"0.45"), PORTRAIT: _jpeg(b"0.10")}) as client:
        await refresh_member_photos(db_session, client, timedelta(minutes=1))
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": DEPICTION}


async def test_bioguide_without_a_portrait_keeps_the_depiction(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session)
    async with _client({DEPICTION: _jpeg(b"0.20"), PORTRAIT: httpx.Response(404)}) as client:
        await refresh_member_photos(db_session, client, timedelta(minutes=1))
    row = db_session.get(MemberPhoto, "X000001")
    assert (row.source_url, row.bioguide_face_share) == (DEPICTION, None)


@pytest.mark.parametrize("failure", [
    httpx.Response(503),
    httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),   # a challenge page
    httpx.Response(301, headers={"location": "https://elsewhere.example/x.jpg"}),
])
async def test_a_failed_fetch_keeps_the_stored_choice(db_session, measured, freeze_utcnow, failure):
    """An outage is not a missing photo: last month's choice stands."""
    freeze_utcnow(NOW)
    _member(db_session)
    db_session.add(MemberPhoto(bioguide_id="X000001", source_url=PORTRAIT, depiction_url=DEPICTION,
                               checked_at=NOW - timedelta(days=40)))
    db_session.commit()
    async with _client({DEPICTION: _jpeg(b"0.45"), PORTRAIT: failure}) as client:
        assert await refresh_member_photos(db_session, client, timedelta(minutes=1)) == 0
    assert db_session.get(MemberPhoto, "X000001").source_url == PORTRAIT


async def test_a_recent_choice_on_the_same_depiction_is_not_fetched_again(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session)
    db_session.add(MemberPhoto(bioguide_id="X000001", source_url=DEPICTION, depiction_url=DEPICTION,
                               checked_at=NOW - timedelta(days=3)))
    db_session.commit()
    async with _client({}) as client:
        assert await refresh_member_photos(db_session, client, timedelta(minutes=1)) == 0
        assert client.seen == []


async def test_a_new_depiction_is_measured_again(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session)
    db_session.add(MemberPhoto(bioguide_id="X000001", source_url=PORTRAIT,
                               depiction_url="https://www.congress.gov/img/member/old_200.jpg",
                               checked_at=NOW - timedelta(days=3)))
    db_session.commit()
    async with _client({DEPICTION: _jpeg(b"0.45"), PORTRAIT: _jpeg(b"0.40")}) as client:
        assert await refresh_member_photos(db_session, client, timedelta(minutes=1)) == 1
    assert db_session.get(MemberPhoto, "X000001").source_url == DEPICTION


async def test_a_member_with_no_credited_depiction_has_no_choice(db_session, measured, freeze_utcnow):
    freeze_utcnow(NOW)
    _member(db_session, attribution="")
    db_session.add(MemberPhoto(bioguide_id="X000001", source_url=DEPICTION, depiction_url=DEPICTION, checked_at=NOW))
    db_session.commit()
    async with _client({}) as client:
        assert await refresh_member_photos(db_session, client, timedelta(minutes=1)) == 0
    assert db_session.get(MemberPhoto, "X000001") is None
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": PORTRAIT}


def test_a_stored_choice_off_the_photo_hosts_is_never_handed_out(db_session):
    _member(db_session)
    db_session.add(MemberPhoto(bioguide_id="X000001", source_url="https://elsewhere.example/x.jpg",
                               depiction_url=DEPICTION, checked_at=NOW))
    db_session.commit()
    # Falls back to the depiction-first rule, never the stored URL.
    assert get_photo_source("bioguide", "X000001", db=db_session) == {"url": DEPICTION}


def test_the_api_never_loads_opencv():
    """The read path serves the stored choice; the detector is pipeline-only."""
    app = Path(member_photos.__file__).resolve().parents[2]
    for path in (app / "photos.py", app / "api" / "photos.py"):
        imported = {
            (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not {m for m in imported if m == "cv2" or m.endswith("member_photos")}, path
