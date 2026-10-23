# OCR of scanned financial disclosures: measured, not adopted (2026-10)

Thirty-four sitting members' latest annual disclosures can't be read into the
holdings chart: 32 House reports scanned with no text layer and 2 Senate paper
filings. Their pages are shown on each member's page as filed (PR #1230). This
note records whether OCR could also turn them into the chart, under the
Action Center's precision rule: the exact lower 95% bound of row-level
precision on accepted readings must be at least 0.80. **It can't, and the
reason is structural, not a tuning gap.**

## The population is not the electronic form

Page 2 of every scan, the first Schedule A page on a Form A, was looked at (2026-10-22):

| What the report is | Reports |
|---|---|
| House paper Form A, typed into the pre-printed grid | 10 |
| House paper Form A, handwritten (one filed upside down) | 19 |
| A letter or typed amendment attachment, no Schedule A grid | 3 |
| Senate paper form (eFD page images), typed into its grid | 2 |

On both paper forms a holding's value and income are **marks in pre-printed
bracket columns** (an X under "$15,001 - $50,000"), not printed text, and
neither form has an asset-type column (the House code list, `[ST]` and so on,
exists only on the electronic form). Every House scan is a 1-bit page image at
200 dpi.

So the gates the plan set (each value and income cell is exactly one of the
form's printed bracket labels; each row's asset type is a form code; aligned
columns with no orphan cells) accept **no row of any of the 34 reports by
construction**. Measured rather than assumed: the electronic-layout reader
(house_fd.parse_schedule_a over tesseract's words) read **0 rows** from the 32
House scans (every page up to 20 per report; 31 have no Schedule A header at
all). Bracket text appeared in only two: one member's typed amendment (50 strings,
10 exactly in the vocabulary) and another's amendment letter (1, none).

## OCR of the electronic layout (the plan's labelled set)

Thirty electronically filed 2025 House reports (850 Schedule A rows), read from
their text layer as the truth by a copy of `parse_schedule_a` that also keeps
the two income columns (it agreed with house_fd's own output, asset, type and
value, on 30 of 30 reports). Each page was rendered at 200 dpi, skewed by up to
1 degree, given Gaussian noise and specks, and thresholded to 1 bit, as the
House scans are; then deskewed, despeckled and read by tesseract 5.5.0 (the
Debian package already in the image, `--psm 6`), and the same parser read the
words. The form's own glyphs were mapped back (the nesting arrow tesseract
reads as `>`; detail labels such as "Description:" by the nearest of the form's
labels). The bracket vocabularies are the labels the text layers print, not
typed.

| | scan-like | clean render, no noise or skew |
|---|---|---|
| row precision (all five fields right) | 99/745 = 0.13 | 495/880 = 0.56 |
| row recall | 99/850 = 0.12 | 495/850 = 0.58 |
| asset name, precision / recall | 0.21 / 0.19 | 0.77 / 0.80 |
| asset type code | 0.24 / 0.21 | 0.96 / 1.00 |
| value bracket | 0.79 / 0.69 | 0.97 / 1.00 |
| income type | 0.71 / 0.62 | 0.69 / 0.71 |
| income bracket | 0.85 / 0.74 | 0.97 / 1.00 |
| **rows the row gate accepts** | 96, 56 right: **0.58, lower bound 0.48** | 407, 237 right: **0.58, lower bound 0.53** |
| rows the page gate accepts (every row on the page) | 0 | 60, 35 right: 0.58, lower bound 0.45 |

What fails: at 200 dpi, 1 bit, tesseract misreads the bracketed type code that
closes each row (`[ST]` as `{ST}`, `|ST]`, `(S1|`), which merges rows, and
reads `x` as `s` in ordinary words ("Tas-Deferred", "Marv", "Indes"). The
gates catch the vocabulary errors but not a misspelled asset name, which is
why accepted precision stays near 0.58 even on a clean render. Neither result
is near 0.80, and the scans themselves are not this layout.

## What would have to be built instead

A reader for the paper forms' grid: find the ruled table, place each mark in
its bracket column by geometry, and OCR only the typed asset name, with every
asset's type "not stated" (as the president's 278e is). It could apply to the
10 typed House Form A reports and the 2 Senate filings; the 19 handwritten
ones would need handwriting recognition, which nothing in the image does.
Its accuracy can't be measured on this note's labelled set, because no
electronic report is a grid of marks. A labelled set for it, with no values
typed by hand, could come from electronic reports' known rows drawn into the
blank paper form's grid at the scans' resolution. That is a separate decision.

The measurement scripts were run from a scratch directory and are not kept in
the repository (AGENTS.md: no one-off code).
