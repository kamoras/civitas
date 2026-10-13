"""Face size in member photos, measured in a process of its own.

Run as `python -m app.pipeline.fetch.face_detection`, reading a JSON list of
image file paths on stdin and writing {path: share} as JSON on stdout. Never
imported by the app: member_photos.py starts it for a night's batch, so
OpenCV, a native library, is loaded by no long-lived process. A fault in it
ends this process, not page serving or the pipeline (a from-source wheel
once SIGILL'd on the Pi and crash-looped production, 2026-07-12; this
wheel is prebuilt and was run on the Pi's own CPU before adoption).

The share is the largest face's height over the image's shorter side, which
is the square an avatar shows. Detector: YuNet (OpenCV Zoo, MIT licence),
app/data/face_detection_yunet_2023mar.onnx; its source and hash are in
app/data/member_photo_calibration.json.
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "face_detection_yunet_2023mar.onnx"
# The detector reads a downscaled copy: a face share doesn't depend on
# resolution, and decoding a 5000 px original is what costs memory.
DETECT_MAX_SIDE = 640
# YuNet's own confidence cut. Every current member's photos (1,060 images)
# held exactly one face at this setting, paintings included (2026-10-13).
DETECT_SCORE = 0.7

_detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), score_threshold=DETECT_SCORE)


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
    _detector.setInputSize((sw, sh))
    _, faces = _detector.detect(img)
    tallest = max((float(f[3]) for f in faces), default=0.0) if faces is not None else 0.0
    return tallest / min(sh, sw)


def main() -> None:
    paths = json.load(sys.stdin)
    json.dump({p: face_share(Path(p).read_bytes()) for p in paths}, sys.stdout)


if __name__ == "__main__":
    main()
