"""Promise quality cleaning, shared by the pipeline and the read path.

Historically these corrections lived only in the API read path
(senator_service._filter_promises), which meant the Promise Persistence
score was computed from the *raw* stored alignments while users saw the
*corrected* ones — the 2026-07 adversarial audit flagged that the promise
list and the PP score could visibly disagree. The pipeline now cleans
promises once (before scoring and persistence) using this module; the
read-time filter reuses the same rules as a safety net for legacy rows.

Operates on the pipeline's dict shape:
  {promiseText, category, alignment, relatedVotes, relatedBills,
   analysis, confidence?, partyAlignment?}
"""

import re
from collections import Counter

from app.models import PromiseAlignment

_FILLER_RE = re.compile(
    r"has received funding from|(?:^|[,.])\s*a political PAC"
    r"|opposes the removal of the United States Army"
    r"|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated)",
    re.IGNORECASE,
)

_KEPT_RE = re.compile(
    r"align(?:s|ed|ing|ment)|support(?:s|ing|ed)?(?:\s+(?:for|of|this))?"
    r"|consistent|keeping|kept|match|fulfill|advance[sd]?|further[sd]?",
    re.IGNORECASE,
)
_BROKEN_RE = re.compile(
    r"contradict|voted\s+against|oppos(?:es|ing|ed)|undermin"
    r"|broke[n]?|fail(?:s|ed|ing)|violat|inconsistent"
    r"|does not (?:support|align|match)",
    re.IGNORECASE,
)

_ERROR_PAGE_RE = re.compile(
    r"(?:404\s*error|page\s*not\s*found|page\s*requested|"
    r"search\s+senate\.gov|e-?mail\s+webmaster|broken\s+link)",
    re.IGNORECASE,
)

_BILL_ID_RE = re.compile(
    r"(?:H\.R\.|S\.|H\.J\.Res\.|S\.J\.Res\.|S\.Res\.|H\.Res\.|Roll-|Amdt\.)"
)

_PROMISE_ARTIFACT_RE = re.compile(
    r"^On the (?:Amendment|Joint Resolution|Resolution|Bill|Motion|Cloture)"
    r"|^Pursuant to Senate Policy"
    r"|^Learn About \w+"
    r"|^See \w+'s Position",
    re.IGNORECASE,
)

_EMBED_ARTIFACT_SIGS = (
    "browser does not support",
    "twitter feed",
    "skip to content",
    "menu menu menu",
    "javascript",
    "cookie",
)


def clean_promises(promises: list[dict]) -> list[dict]:
    """Filter artifacts and correct alignment claims. Returns new dicts."""
    result: list[dict] = []
    seen_texts: set[str] = set()

    for p in promises or []:
        promise_text = p.get("promiseText") or ""
        analysis = p.get("analysis") or ""
        alignment = p.get("alignment") or PromiseAlignment.UNCLEAR
        related = list(p.get("relatedVotes") or [])
        related_sp = list(p.get("relatedBills") or [])

        if _ERROR_PAGE_RE.search(promise_text) or _ERROR_PAGE_RE.search(analysis):
            continue

        promise_lower = promise_text.lower()
        if any(sig in promise_lower for sig in _EMBED_ARTIFACT_SIGS):
            continue

        if len(promise_text.strip()) < 10:
            continue

        if _PROMISE_ARTIFACT_RE.search(promise_text.strip()):
            continue

        text_key = promise_text.strip().lower()
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        if _FILLER_RE.search(analysis):
            analysis = ""

        kept = len(_KEPT_RE.findall(analysis))
        broken = len(_BROKEN_RE.findall(analysis))
        if kept > 0 and broken > 0:
            alignment = PromiseAlignment.UNCLEAR
        elif alignment == PromiseAlignment.BROKEN and kept > 0 and broken == 0:
            alignment = PromiseAlignment.KEPT
        elif alignment == PromiseAlignment.KEPT and broken > 0 and kept == 0:
            alignment = PromiseAlignment.BROKEN

        # Downgrade bold claims that lack specific evidence
        if (
            alignment in (PromiseAlignment.KEPT, PromiseAlignment.BROKEN)
            and analysis
            and not _BILL_ID_RE.search(analysis)
            and not related
            and not related_sp
        ):
            alignment = PromiseAlignment.UNCLEAR
            analysis = ""

        result.append({
            **p,
            "promiseText": promise_text,
            "alignment": alignment,
            "relatedVotes": related,
            "relatedBills": related_sp,
            "analysis": analysis,
        })

    # Promises that all cite the identical (non-empty) vote set are
    # usually one topical embedding match fanned out across topics —
    # not real per-promise evidence.
    if len(result) >= 2:
        bill_sets = [tuple(sorted(p["relatedVotes"])) for p in result]
        counts = Counter(bill_sets)
        overused = {bs for bs, cnt in counts.items() if cnt >= 2 and bs}
        if overused:
            for p in result:
                if tuple(sorted(p["relatedVotes"])) in overused:
                    p["alignment"] = PromiseAlignment.UNCLEAR
                    p["relatedVotes"] = []
                    p["analysis"] = ""

    return result
