"""The backend image must not send usage telemetry off the box."""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_hugging_face_telemetry_is_off():
    """huggingface_hub, transformers and hf_xet (from 1.7.0) report by default."""
    assert re.search(r"^ENV HF_HUB_DISABLE_TELEMETRY=1$", DOCKERFILE.read_text(), re.M)
