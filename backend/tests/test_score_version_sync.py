"""The public methodology changelog must name the version scores carry.

frontend/src/lib/scoreVersions.ts says "keep in sync with ALGORITHM_VERSION"
and trend charts mark methodology changes from it; nothing enforced that.
Skipped where the frontend tree isn't present (e.g. the backend container).
"""

import pathlib
import re

import pytest

from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION

_VERSIONS_TS = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "scoreVersions.ts"
)


def test_latest_changelog_entry_matches_algorithm_version():
    if not _VERSIONS_TS.exists():
        pytest.skip("frontend tree not available")
    first = re.search(r'version:\s*"([^"]+)"', _VERSIONS_TS.read_text())
    assert first is not None
    assert first.group(1) == ALGORITHM_VERSION
