"""Fetch the frontend's web fonts from Google Fonts into the repo.

Writes frontend/src/app/fonts/*.woff2 and fonts/manifest.json, which
frontend/src/app/fonts.ts loads through next/font/local. The site used to
load them with next/font/google, which downloads them during `next build`;
about one build in five failed there, because Google intermittently answers
with a `fonts.gstatic.com/l/font?kit=...` URL that Turbopack's loader
rejects. Committing the files takes the network out of the build.

What this fetches is exactly what next/font/google fetched: the same CSS2
request, with next's own User-Agent (Google picks the file format from it),
one woff2 per script subset, each behind Google's unicode-range. The
manifest records those ranges and the preload rule, and
frontend/src/app/fonts.test.ts fails if fonts.ts drifts from it.

The files are committed byte-for-byte as Google serves them. One thing is
computed here rather than left to next/font/local: Archivo's metric-matched
fallback (the Arial face, resized to Archivo's proportions, that text
renders in until the font arrives). next/font/local measures a variable
font at its default instance, and Archivo's default is wght 600, so the
fallback it generated was sized for SemiBold — about 4% too wide for the
400-weight prose it stands in for, which then reflows when the font loads.
Moving the file's default to 400 fixes the measurement but grows its
variation data by half (+9 KB on a preloaded file), so instead this script
measures the 400 instance with next/font's own formula and writes the
fallback face to fonts/fallback.css.

Rerun when a face should move to a newer Google release:

    pip install -r requirements.txt -r scripts/requirements-research.txt
    python scripts/fetch_site_fonts.py
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONTS_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "fonts"

# next/font/google's own User-Agent (next/dist/compiled/@next/font/dist/
# google/fetch-resource.js), so Google answers with the same woff2 files.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36"
)

# The same families and axes the root layout asked next/font/google for.
FAMILIES = {
    "archivo": "Archivo:wght@400;600;800",
    "press-start-2p": "Press+Start+2P",
    "share-tech-mono": "Share+Tech+Mono",
}

# next/font/google was called with subsets: ["latin"] — only these preload.
PRELOADED_SUBSET = "latin"

# Only the files served from /s/<family>/ are Google's release files; the
# /l/font?kit= form is the answer that broke the build, so ask again.
_STATIC_SRC = re.compile(r"https://fonts\.gstatic\.com/s/[^)]+\.woff2")
_BLOCK = re.compile(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*{([^}]*)}")
_ATTEMPTS = 5


def _css(client: httpx.Client, query: str) -> str:
    url = f"https://fonts.googleapis.com/css2?family={query}&display=swap"
    for attempt in range(_ATTEMPTS):
        css = client.get(url).raise_for_status().text
        if "/l/font?" not in css:
            return css
        time.sleep(2**attempt)
    sys.exit(f"Google kept answering {url} with /l/font URLs")


def _faces(css: str) -> dict[str, dict]:
    """subset -> {src, unicodeRange, weights} from one CSS2 response."""
    faces: dict[str, dict] = {}
    for subset, body in _BLOCK.findall(css):
        src = _STATIC_SRC.search(body)
        weight = re.search(r"font-weight:\s*(\d+)", body)
        ranges = re.search(r"unicode-range:\s*([^;]+);", body)
        if not (src and weight and ranges):
            sys.exit(f"unexpected @font-face for {subset}: {body.strip()}")
        face = faces.setdefault(
            subset,
            {
                "src": src.group(0),
                "unicodeRange": ranges.group(1).strip(),
                "weights": [],
            },
        )
        if face["src"] != src.group(0):
            sys.exit(
                f"{subset}: one file per subset expected, got {face['src']} and {src.group(0)}"
            )
        face["weights"].append(weight.group(1))
    return faces


# next/font's fallback-metric formula (next/dist/compiled/@next/font/dist/
# local/get-fallback-metrics-from-font-file.js): the sample string it
# averages advance widths over, and its measured Arial average. Reproducing
# it here gives the same numbers next/font/local emits at wght 600 (102.80%
# size-adjust), so the 400 figures below are like for like.
_AVG_CHARACTERS = "aaabcdeeeefghiijklmnnoopqrrssttuvwxyz      "
_ARIAL_AVG_WIDTH_PER_EM = 934.5116279069767 / 2048

FALLBACK_FAMILY = "Archivo Fallback"


def _fallback_css(data: bytes, weight: int) -> str:
    """An @font-face that resizes local Arial to the given instance's
    average width and vertical metrics."""
    font = instancer.instantiateVariableFont(TTFont(io.BytesIO(data)), {"wght": weight})
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    upem, hhea = font["head"].unitsPerEm, font["hhea"]
    avg = sum(hmtx[cmap[ord(c)]][0] for c in _AVG_CHARACTERS) / len(_AVG_CHARACTERS)
    size_adjust = avg / upem / _ARIAL_AVG_WIDTH_PER_EM

    def pct(value: float) -> str:
        # Trailing zeros dropped, as prettier (format:check) writes numbers.
        return f"{abs(value * 100):.2f}".rstrip("0").rstrip(".") + "%"

    return (
        f'@font-face {{\n  font-family: "{FALLBACK_FAMILY}";\n  src: local("Arial");\n'
        f"  ascent-override: {pct(hhea.ascent / (upem * size_adjust))};\n"
        f"  descent-override: {pct(hhea.descent / (upem * size_adjust))};\n"
        f"  line-gap-override: {pct(hhea.lineGap / (upem * size_adjust))};\n"
        f"  size-adjust: {pct(size_adjust)};\n}}\n"
    )


def main() -> None:
    manifest: dict = {
        "_source": (
            "Google Fonts CSS2 API with next/font/google's User-Agent, fetched by "
            f"backend/scripts/fetch_site_fonts.py on {datetime.now(UTC).date().isoformat()}"
        ),
        "fonts": {},
    }
    fallback = ""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        for name, query in FAMILIES.items():
            entries = []
            for subset, face in _faces(_css(client, query)).items():
                data = client.get(face["src"]).raise_for_status().content
                if name == "archivo" and subset == PRELOADED_SUBSET:
                    fallback = _fallback_css(data, 400)
                file = f"{name}-{subset}.woff2"
                (FONTS_DIR / file).write_bytes(data)
                entries.append(
                    {
                        "subset": subset,
                        "file": file,
                        "weights": face["weights"],
                        "unicodeRange": face["unicodeRange"],
                        "preload": subset == PRELOADED_SUBSET,
                    }
                )
                print(f"{file}: {len(data)} bytes, weights {face['weights']}")
            manifest["fonts"][name] = entries
    (FONTS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (FONTS_DIR / "fallback.css").write_text(
        "/* Generated by backend/scripts/fetch_site_fonts.py - do not edit.\n"
        "   Archivo's fallback, measured at wght 400 (see the script). */\n" + fallback
    )


if __name__ == "__main__":
    main()
