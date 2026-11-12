"""PDF words grouped into visual lines, for the readers that read a page
line by line (Georgia's and Ohio's measure documents, Oregon's abstract,
the House's financial disclosures).

A word joins the current line when its top is within `tolerance` points of
the line's first word, so a line is anchored where it starts and a slow
drift down the page never chains two lines together. That is the
difference from ballot_measure_pdf_geometry.rows, which compares each word
with the one before it.
"""


def group_lines(words: list[dict], tolerance: float) -> list[list[dict]]:
    """pdfplumber's `extract_words()` dicts as lines, top to bottom, each
    line's words left to right."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]
