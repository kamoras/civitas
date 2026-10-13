"""Which official photo each member's `/photo/bioguide/<id>` serves.

A member has two official photos: the headshot on the Congress.gov record
(its credited `depiction`, app/photos.py) and bioguide.congress.gov's
portrait. Usually they agree, or the depiction is the better avatar:
bioguide's file is, for some members, a full-length painted committee-chair
portrait or a full-length photo, mostly background once cropped to a square.
But for a few members it is the other way round: their own depiction is a
small full-figure shot in front of the Capitol, a wide outdoor photo, or a
painting, and bioguide has the headshot.

So the choice is measured, not assumed. Once a night (the stock-trades run,
after the member pipelines) both images are fetched and a face detector
(YuNet, OpenCV's DNN face detector; model in app/data/) measures the largest
face's height as a share of the image's shorter side, which is the square an
avatar shows. Bioguide's portrait is chosen only when the depiction's face is
below a head-and-shoulders share and bioguide's is clearly larger; otherwise
the depiction stays. Both numbers come from
app/data/member_photo_calibration.json (scripts/calibrate_member_photos.py),
fitted to a hand-labelled comparison of every current member's two photos.

The choice is stored per member (models.MemberPhoto) for the photo-sources
endpoint to read. Nothing here runs on a page request: OpenCV loads only in
the pipeline process. A fetch that fails (anything but a 404) leaves the
member's stored choice as it was, since an outage is not a missing photo.
"""

import asyncio
import json
import logging
import time
from datetime import timedelta
from functools import cache
from pathlib import Path

import cv2
import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiCache, MemberPhoto, Representative, Senator
from app.photos import bioguide_portrait_url, credited_depiction, photo_source_allowed
from app.pipeline.fetch.congress import member_detail_cache_key
from app.pipeline.fetch.http_utils import BROWSER_HEADERS
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[2] / "data"
CALIBRATION_PATH = DATA / "member_photo_calibration.json"
MODEL_PATH = DATA / "face_detection_yunet_2023mar.onnx"

# A choice is measured again after this long even if the record's depiction
# is unchanged: bioguide replaces a portrait without changing its URL.
RECHECK_AFTER = timedelta(days=30)
# Between members, so a first night's ~530 pairs reach the two hosts as a
# trickle (about four minutes), not a burst; later nights fetch only new or
# changed records and the monthly recheck (~20 a night).
PAUSE_S = 0.25
# Photos are a few hundred KB; bioguide's largest current file is 1.2 MB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# The detector reads a downscaled copy: a face share doesn't depend on
# resolution, and decoding a 5000 px original is what costs memory.
DETECT_MAX_SIDE = 640
# YuNet's own confidence cut. Every current member's photos (1,060 images)
# held exactly one face at this setting, paintings included.
DETECT_SCORE = 0.7


@cache
def calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text())


@cache
def _detector():
    return cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), score_threshold=DETECT_SCORE)


def face_share(image: bytes) -> float | None:
    """The largest face's height over the image's shorter side, or None
    for something that isn't a decodable image. 0.0 when no face is found."""
    img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, DETECT_MAX_SIDE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    sh, sw = img.shape[:2]
    detector = _detector()
    detector.setInputSize((sw, sh))
    _, faces = detector.detect(img)
    tallest = max((float(f[3]) for f in faces), default=0.0) if faces is not None else 0.0
    return tallest / min(sh, sw)


def prefers_bioguide(bioguide_share: float | None, depiction_share: float | None) -> bool:
    """True when bioguide's portrait is the better avatar: the depiction's
    face is below a head-and-shoulders share (`depiction_ceiling`) and
    bioguide's is larger by `margin`. Otherwise the depiction stays,
    including when either measurement is missing."""
    if bioguide_share is None or depiction_share is None:
        return False
    cal = calibration()
    return depiction_share < cal["depiction_ceiling"] and bioguide_share >= cal["margin"] * depiction_share


class _FetchFailed(Exception):
    """Anything but the image or a 404: try again another night."""


async def _image(client: httpx.AsyncClient, url: str) -> bytes | None:
    """The image's bytes; None when the host says there is none (404/410)."""
    if not photo_source_allowed("bioguide", url):
        raise _FetchFailed(f"not on the photo hosts: {url}")
    try:
        # Neither host redirects these URLs (checked 2026-10-13); one that
        # starts to is a failure to look at, not a hop to follow blind.
        resp = await client.get(url, headers=BROWSER_HEADERS, timeout=15.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise _FetchFailed(str(exc)) from exc
    if resp.status_code in (404, 410):
        return None
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
        raise _FetchFailed(f"{url}: {resp.status_code} {resp.headers.get('content-type')}")
    if len(resp.content) > MAX_IMAGE_BYTES:
        raise _FetchFailed(f"{url}: {len(resp.content)} bytes")
    return resp.content


def _members_to_check(db: Session) -> list[tuple[str, str]]:
    """(bioguide id, credited depiction URL) for every stored member whose
    choice is missing, made against another depiction, or due a recheck."""
    ids = {b for (b,) in db.execute(select(Senator.bioguide_id))} | {
        b for (b,) in db.execute(select(Representative.bioguide_id))
    }
    stored = {row.bioguide_id: row for row in db.scalars(select(MemberPhoto))}
    due_before = utcnow() - RECHECK_AFTER
    out = []
    for bioguide_id in sorted(b for b in ids if b):
        record = db.get(ApiCache, ("congress", member_detail_cache_key(bioguide_id)))
        try:
            depiction = credited_depiction(json.loads(record.data_json)) if record else None
        except ValueError:
            depiction = None
        row = stored.get(bioguide_id)
        if depiction is None:
            # No credited depiction: bioguide's portrait, nothing to choose.
            if row is not None:
                db.delete(row)
            continue
        if row is None or row.depiction_url != depiction or row.checked_at < due_before:
            out.append((bioguide_id, depiction))
    db.commit()
    return out


async def refresh_member_photos(db: Session, client: httpx.AsyncClient, budget: timedelta) -> int:
    """Measure and store the photo choice for members that need one, until
    `budget` runs out (the rest wait for the next night). Returns how many
    choices were stored."""
    deadline = time.monotonic() + budget.total_seconds()
    stored = failed = 0
    for bioguide_id, depiction in _members_to_check(db):
        if time.monotonic() > deadline:
            logger.info("Member photos: budget spent, the rest wait for the next run")
            break
        portrait = bioguide_portrait_url(bioguide_id)
        try:
            depiction_bytes = await _image(client, depiction)
            portrait_bytes = await _image(client, portrait)
        except _FetchFailed as exc:
            failed += 1
            logger.info("Member photos: %s not measured (%s)", bioguide_id, exc)
            continue
        finally:
            await asyncio.sleep(PAUSE_S)
        if depiction_bytes is None:
            # The record names a photo Congress.gov doesn't have; bioguide's
            # is served (the route 404s the depiction otherwise).
            depiction_share, portrait_share, source = None, None, portrait
        else:
            depiction_share = await asyncio.to_thread(face_share, depiction_bytes)
            portrait_share = await asyncio.to_thread(face_share, portrait_bytes) if portrait_bytes else None
            source = portrait if prefers_bioguide(portrait_share, depiction_share) else depiction
        db.merge(MemberPhoto(
            bioguide_id=bioguide_id, source_url=source, depiction_url=depiction,
            bioguide_face_share=portrait_share, depiction_face_share=depiction_share, checked_at=utcnow(),
        ))
        db.commit()
        stored += 1
    if failed:
        logger.warning("Member photos: %d members not measured this run (fetch failed)", failed)
    return stored
