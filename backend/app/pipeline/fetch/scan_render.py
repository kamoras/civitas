"""Render a scanned disclosure's pages to WebP, in a process of its own.

Run as `python -m app.pipeline.fetch.scan_render`, reading one JSON job on
stdin — {"kind": "pdf" | "images", "inputs": [file paths], "out": dir} — and
writing the pages' pixel sizes, [[width, height], ...], as JSON on stdout.
Never imported by the app: scan_pages.py starts it for each filing, so
PDFium and the image decoders (native code) run in no long-lived process,
and a 353-page report or a stack of 15-megapixel page images is decoded
here, not in the pipeline's memory (the face_detection.py pattern).

Every page is written at 200 dpi as a lossless 1-bit WebP. Both sources are
bilevel scans: every House scan on file (2026-10, 32 reports) embeds 1-bit
page images at 200 dpi, so at 200 dpi the render is the filed pixels; the
Senate's eFD page images are 1-bit at 400 dpi (3400 x 4400 for a letter
page) and are halved. Measured on those 989 House pages and two Senate
pages: about 30 KiB a page (lossy grayscale WebP at 150 dpi, quality 50,
was 100 KiB a page and blurred the print).
"""

import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

DPI = 200
# The eFD page images' resolution (a letter page is 3400 x 4400 pixels).
SENATE_IMAGE_DPI = 400
# A render's anti-aliased edges, back to black and white: below this is ink.
INK_BELOW = 160
MAX_PIXELS = 60_000_000  # a page image larger than this is not a scan of a page


def _bilevel(gray: Image.Image) -> Image.Image:
    page = gray.point(lambda v: 255 if v > INK_BELOW else 0).convert("1")
    # No transparency carried over: eFD's GIFs mark a palette entry
    # transparent, and Pillow kept it through the conversions, so the WebP
    # came out with the ink see-through (2026-10, every Senate page).
    page.info = {}
    return page


def _save(image: Image.Image, out: Path, number: int) -> list[int]:
    image.save(out / f"{number}.webp", "WEBP", lossless=True, method=4)
    return [image.width, image.height]


def render_pdf(path: str, out: Path) -> list[list[int]]:
    pdf = pdfium.PdfDocument(path)
    try:
        return [
            _save(_bilevel(pdf[i].render(scale=DPI / 72, grayscale=True).to_pil()), out, i + 1)
            for i in range(len(pdf))
        ]
    finally:
        pdf.close()


def render_images(paths: list[str], out: Path) -> list[list[int]]:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    sizes = []
    for number, path in enumerate(paths, start=1):
        with Image.open(path) as image:
            gray = image.convert("L")
        scale = DPI / SENATE_IMAGE_DPI
        if scale < 1:
            gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)
        sizes.append(_save(_bilevel(gray), out, number))
    return sizes


def main() -> None:
    job = json.load(sys.stdin)
    out = Path(job["out"])
    render = render_pdf(job["inputs"][0], out) if job["kind"] == "pdf" else render_images(job["inputs"], out)
    json.dump(render, sys.stdout)


if __name__ == "__main__":
    main()
