"""Direct ballot-PDF ingestion — parses a jurisdiction's own official
sample-ballot PDF, for jurisdictions confirmed to publish one as a plain,
freely-fetchable static file (see ballot_pdf_sources.py). No API key, no
approval-gated signup, no address lookup: the PDF is public.

This is a NARROWER, harder-won alternative to ballot_measures.py (Vote
Smart) and civic_info.py (Google Civic representative-address): most
jurisdictions gate their sample ballot behind an address/voter-registration
lookup and have no static file to fetch at all (confirmed during research:
Cambridge MA, Ann Arbor MI). Only jurisdictions in ballot_pdf_sources.json
have been individually verified to publish one.

PARSING REALITY, not assumed: built directly against a real, currently-
published PDF (Somerville MA's 2026-09-01 state primary ballot), not a
guessed format. Two things a naive approach misses:

1. These ballots are laid out in visual COLUMNS (multiple offices side by
   side). pdfplumber's plain extract_text() interleaves words from
   different columns onto the same line — office headers read correctly,
   but candidate rows from adjacent columns run together into unusable
   text. Fixed by cropping each column separately via page.crop() before
   extracting text (see column_bounds below) — extracting per-column
   turns out to be necessary, not just cleaner.

2. The extracted text carries real artifacts: stray single characters
   glued onto otherwise-clean lines (e.g. "NORTHERN DISTRICT eVote for..."),
   almost certainly the ballot's decorative oval/checkbox glyphs being
   misread as letters by the font's custom encoding — not a Somerville
   typo, a rasterization artifact of this specific PDF's font. Observed
   only on office/qualifier lines and as standalone junk lines, never
   glued onto a candidate's actual name — so the candidate-line pattern
   doesn't try to strip a leading character (an earlier version did,
   "tolerantly", and it silently ate the first real letter of every
   single name instead — verified as a real bug against this exact PDF,
   not assumed safe). Anything that doesn't match a known line shape is
   dropped, never guessed at — same verbatim-only discipline as AGENTS.md
   Core Design Principle 7.

Column boundaries are HAND-VERIFIED per source (ballot_pdf_sources.json's
`column_bounds`, as fractions of page width), not auto-detected. Tried
detecting gutters from word x-position density first; it unreliably
merged columns on this exact real PDF, because the full-width header/
instructions text pollutes any density-based gutter search enough to
hide the real gap. A wrong guess here silently produces garbage (offices
and candidates from different columns glued together) rather than an
honest failure, which is worse than the extra hand-verification step —
same reasoning `ballot_pdf_sources.json`'s URL itself is hand-curated
rather than auto-discovered.

Only CANDIDATE CONTESTS are handled here — no ballot QUESTION/measure
pattern has been verified yet, because Massachusetts state primaries
(the only real ballot available to build against right now, three months
out from the general) don't carry ballot questions; those are a
November-general-only feature under MA law. Extending this to measures
needs a real November PDF to build the pattern against, the same "don't
guess the format" rule this whole module follows — not implemented yet.
"""

import logging
import re

import httpx
import pdfplumber
import io

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.ballot_pdf_sources import source_for_town

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 12

# A candidate line: NAME (all-caps words, permitting mixed-case runs like
# "DiZOGLIO"/"McLAUGHLIN"/"DeCRISTOFARO") then a street address (starts
# with a house number), then optionally one or more "+" ballot-oval marks
# — optional because an uncontested candidate (verified on the real PDF:
# Attorney General, single candidate) has none at all, unlike every
# contested race, which has one "+" per candidate slot on the line.
_CANDIDATE_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z.'-]*(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z.'-]*))*)"
    r"\s+(?P<address>\d+\s+.+?)"
    r"(?:\s+(?:\+\s*)+)?$",
)

# An office header: an all-caps line (letters/spaces/periods/commas/
# hyphens/apostrophes only), at least 2 characters, that is NOT one of the
# fixed boilerplate lines. District-qualifier lines ("SEVENTH DISTRICT",
# "TWENTY-SIXTH MIDDLESEX") are also all-caps and get folded into the
# office name that precedes them rather than treated as a new office.
_OFFICE_LINE_RE = re.compile(r"^[A-Z][A-Z .,'\-]+[A-Z]$")

_VOTE_FOR_RE = re.compile(r"vote for not more than", re.IGNORECASE)

_TRAILER_MARKERS = (
    "DO NOT VOTE IN THIS SPACE",
    "USE BLANK LINE BELOW FOR WRITE-IN",
    "WRITE-IN SPACE ONLY",
)

# Lines that are structurally all-caps but are the ballot's own fixed
# chrome, not an office name — must not be treated as a new office.
_BOILERPLATE_ALLCAPS = {
    "OFFICIAL", "BALLOT", "DEMOCRATIC PARTY", "REPUBLICAN PARTY",
    "EARLY/ABSENTEE",
}


def is_configured(town: str) -> bool:
    """Whether `town` has a verified, hand-curated PDF source. Unlike
    ballot_measures.py/civic_info.py this isn't gated on an API key —
    there is none — it's gated on a human having confirmed the town
    publishes a plain, address-free ballot PDF at all."""
    return source_for_town(town) is not None


def _column_bounds_px(page_width: float, column_bounds_frac: list[list[float]]) -> list[tuple[float, float]]:
    """Hand-verified fractional column bounds (from ballot_pdf_sources.json)
    scaled to this page's actual width in points."""
    return [(f0 * page_width, f1 * page_width) for f0, f1 in column_bounds_frac]


def _parse_column(text: str) -> list[dict]:
    """One column's extracted text -> a list of {"office", "candidates"}.

    Line-by-line state machine: accumulate all-caps lines as the current
    office name (handles multi-line headers like "REPRESENTATIVE IN
    GENERAL COURT" / "TWENTY-SIXTH MIDDLESEX DISTRICT"), then candidate
    lines until a trailer marker closes the office out. Anything that
    doesn't match a known line shape is dropped silently — never guessed
    into a field it might not belong in.
    """
    contests = []
    office_parts: list[str] = []
    candidates: list[dict] = []
    started = False  # true once the first real office line is seen —
    # skips the full-width title/instructions text every column crop
    # also picks up above the actual ballot content.

    def _flush():
        nonlocal office_parts, candidates
        office = " ".join(office_parts).strip()
        if office and candidates:
            contests.append({"office": office, "candidates": candidates})
        office_parts = []
        candidates = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if any(marker in line.upper() for marker in _TRAILER_MARKERS):
            if started and candidates:
                _flush()
            continue

        vote_for = _VOTE_FOR_RE.search(line)
        if vote_for:
            # A district qualifier sometimes shares its line with "Vote
            # for not more than ONE" (real example: "SECOND MIDDLESEX
            # DISTRICT Vote for not more than ONE") — capture the clean
            # part before it as part of the office name rather than
            # discarding the whole line. Only when that prefix is itself
            # a well-formed all-caps line: a corrupted prefix (a stray
            # artifact character glued on, e.g. "NORTHERN DISTRICT e...")
            # is dropped, not guessed at, same as everywhere else here.
            prefix = line[: vote_for.start()].strip()
            if prefix and _OFFICE_LINE_RE.match(prefix):
                office_parts.append(prefix)
            started = True
            continue

        if not started and any(c.islower() for c in line):
            # Still in the ballot's full-width title/instructions block,
            # which every column crop also picks up a fragment of (e.g.
            # "STATE PRIMARY" / "Y SOMERVILLE" as separate all-caps
            # fragments either side of a column boundary). Those fragments
            # DO match the office-line pattern below and would otherwise
            # get glued onto the first real office name. A lowercase-
            # containing line only ever appears in that header block or
            # in a candidate's bio (which is skipped separately, below,
            # and only reachable once `started`) — so seeing one before
            # `started` means everything accumulated so far was header
            # junk, not a real office name.
            office_parts = []
            continue

        cand = _CANDIDATE_RE.match(line)
        if cand and started:
            candidates.append({
                "name": cand.group("name").strip(),
                "address": cand.group("address").strip(),
            })
            continue

        if _OFFICE_LINE_RE.match(line) and line not in _BOILERPLATE_ALLCAPS:
            if candidates:
                # A new office header while candidates are pending means
                # the previous office had no "Vote for" line we could
                # detect (shouldn't happen on real ballots, but drop
                # rather than merge two offices together).
                _flush()
                started = False
            office_parts.append(line)
            continue

        # A bio/description line (mixed case) directly follows a
        # candidate and is deliberately NOT captured — this parser
        # extracts who's running and for what office, not their platform
        # text, and MA primary ballots don't put anything ballot-measure-
        # shaped here to lose by skipping it.

    if started and candidates:
        _flush()
    return contests


async def fetch_town_ballot_pdf(
    client: httpx.AsyncClient, db, town: str,
) -> dict | None:
    """Contests parsed from `town`'s real official ballot PDF, or None on
    missing config or a fetch/parse failure. Same None-vs-empty-list
    discipline as ballot_measures.fetch_state_measures: a genuinely empty
    ballot is different from a PDF we couldn't read, and only the second
    one should ever say so."""
    source = source_for_town(town)
    if source is None:
        return None

    cache_key = f"ballot-pdf-{source['url']}"
    cached = api_cache_get(db, "ballot_pdf", cache_key, max_age_hours=CACHE_TTL_HOURS)
    if cached is not None:
        contests = cached.get("contests")
        return {"contests": contests, "sourceUrl": source["url"]} if contests is not None else None

    try:
        response = await client.get(source["url"], timeout=30.0)
        response.raise_for_status()
        pdf_bytes = response.content
    except httpx.HTTPStatusError as exc:
        logger.warning("Ballot PDF fetch failed for %s: HTTP %d", town, exc.response.status_code)
        return None
    except Exception:
        logger.exception("Ballot PDF fetch failed for %s", town)
        return None

    try:
        contests = _parse_pdf(pdf_bytes, source["column_bounds"])
    except Exception:
        logger.exception("Ballot PDF parse failed for %s", town)
        return None

    if not contests:
        # A real ballot PDF with zero parseable contests almost certainly
        # means the format shifted (new election, new layout) rather than
        # a genuinely contest-less ballot — never cache a null result as
        # if it were confirmed-empty.
        logger.warning("Ballot PDF for %s parsed to zero contests — not caching", town)
        return None

    api_cache_set(db, "ballot_pdf", cache_key, {"contests": contests}, normal_ttl_hours=CACHE_TTL_HOURS)
    return {"contests": contests, "sourceUrl": source["url"]}


def _parse_pdf(pdf_bytes: bytes, column_bounds_frac: list[list[float]]) -> list[dict]:
    """All contests across all pages of a ballot PDF, column-aware."""
    contests: list[dict] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            bounds = _column_bounds_px(page.width, column_bounds_frac)
            for x0, x1 in bounds:
                crop = page.crop((x0, 0, x1, page.height))
                text = crop.extract_text()
                if text:
                    contests.extend(_parse_column(text))
    return contests
