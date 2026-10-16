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
endpoint to read. Nothing here runs on a page request, and OpenCV is never
loaded by this module or anything that imports it: the detector runs in a
child process for the night's batch (face_detection.py), so a native fault
there ends that process, not the pipeline or page serving. A fetch that fails (anything but a 404) leaves the
member's stored choice as it was, since an outage is not a missing photo.
"""

import asyncio
import json
import logging
import sys
import tempfile
import time
from datetime import timedelta
from functools import cache
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import calibration_drift
from app.models import ApiCache, MemberPhoto, Representative, Senator
from app.photos import bioguide_portrait_url, credited_depiction, photo_source_allowed
from app.pipeline.fetch.congress import member_detail_cache_key
from app.pipeline.fetch.http_utils import BROWSER_HEADERS
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[3]
CALIBRATION_PATH = BACKEND / "app" / "data" / "member_photo_calibration.json"

# A choice is measured again after this long even if the record's depiction
# is unchanged: bioguide replaces a portrait without changing its URL.
RECHECK_AFTER = timedelta(days=30)
# Between members, so a first night's ~530 pairs reach the two hosts as a
# trickle (about four minutes), not a burst; later nights fetch only new or
# changed records and the monthly recheck (~20 a night).
PAUSE_S = 0.25
# Photos are a few hundred KB; bioguide's largest current file is 1.2 MB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# The detector measured all ~1,060 current photos in under 4 s on a desktop,
# and a 1000 px portrait in 0.06 s on the Pi (2026-10-13); past this
# something is wrong.
MEASURE_TIMEOUT_S = 300


@cache
def calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text())


class _MeasureFailed(Exception):
    """The detector process failed: nothing stored, try another night."""


async def measure(paths: list[str]) -> dict[str, float | None]:
    """{path: face share} for image files, from the detector process
    (face_detection.py; its face_share says what a share is)."""
    if not paths:
        return {}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.pipeline.fetch.face_detection",
        cwd=BACKEND, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(paths).encode()), MEASURE_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise _MeasureFailed(f"face detection took over {MEASURE_TIMEOUT_S}s") from None
    if proc.returncode != 0:
        raise _MeasureFailed(f"face detection exited {proc.returncode}: {err.decode(errors='replace')[-500:]}")
    return json.loads(out)


def prefers_bioguide(bioguide_share: float | None, depiction_share: float | None) -> bool:
    """True when bioguide's portrait is the better avatar: the depiction's
    face is below a head-and-shoulders share (`depiction_ceiling`) and
    bioguide's is larger by `margin`. Otherwise the depiction stays,
    including when either measurement is missing."""
    if bioguide_share is None or depiction_share is None:
        return False
    cal = calibration()
    prefers = depiction_share < cal["depiction_ceiling"] and bioguide_share >= cal["margin"] * depiction_share
    calibration_drift.observe("member_photo", [float(prefers)], 0.5)
    return prefers


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
    """Measure and store the photo choice for members that need one. Fetching
    stops when `budget` runs out (the rest wait for the next night); the
    fetched pairs are then measured in one detector run. Returns how many
    choices were stored."""
    deadline = time.monotonic() + budget.total_seconds()
    failed = 0
    with tempfile.TemporaryDirectory(prefix="member-photos-") as tmp:
        fetched: list[tuple[str, str, str | None, str | None]] = []
        for bioguide_id, depiction in _members_to_check(db):
            if time.monotonic() > deadline:
                logger.info("Member photos: budget spent, the rest wait for the next run")
                break
            try:
                depiction_bytes = await _image(client, depiction)
                portrait_bytes = await _image(client, bioguide_portrait_url(bioguide_id))
            except _FetchFailed as exc:
                failed += 1
                logger.info("Member photos: %s not measured (%s)", bioguide_id, exc)
                continue
            finally:
                await asyncio.sleep(PAUSE_S)
            paths = []
            for kind, body in (("depiction", depiction_bytes), ("bioguide", portrait_bytes)):
                path = None
                if body is not None:
                    path = str(Path(tmp) / f"{bioguide_id}_{kind}")
                    Path(path).write_bytes(body)
                paths.append(path)
            fetched.append((bioguide_id, depiction, *paths))
        shares = await measure([p for f in fetched for p in f[2:] if p])
    for bioguide_id, depiction, depiction_path, portrait_path in fetched:
        portrait = bioguide_portrait_url(bioguide_id)
        depiction_share = shares.get(depiction_path) if depiction_path else None
        portrait_share = shares.get(portrait_path) if portrait_path else None
        if depiction_path is None:
            # The record names a photo Congress.gov doesn't have: bioguide's.
            source = portrait
        else:
            source = portrait if prefers_bioguide(portrait_share, depiction_share) else depiction
        db.merge(MemberPhoto(
            bioguide_id=bioguide_id, source_url=source, depiction_url=depiction,
            bioguide_face_share=portrait_share, depiction_face_share=depiction_share, checked_at=utcnow(),
        ))
    db.commit()
    if failed:
        logger.warning("Member photos: %d members not measured this run (fetch failed)", failed)
    return len(fetched)
