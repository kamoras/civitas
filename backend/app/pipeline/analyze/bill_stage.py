"""
Bill legislative-stage classification.

Classifies a bill's current position along the introduced -> committee ->
floor -> other chamber -> president -> enacted pipeline using Congress.gov's
own structured action data (GET /bill/{congress}/{type}/{number}/actions),
not free-text inference.

Each action Congress.gov returns carries a `type` (a small controlled
vocabulary: IntroReferral, Committee, Calendars, Floor, President,
BecameLaw, ...) and, for most actions, an `actionCode` from the Library of
Congress's fixed action-code table (e.g. "H11100" = referred to committee,
"E40000" = became public law). `actions[0]` is always the most recent
action (Congress.gov returns the list newest-first).

This is a lookup against an external system's own controlled vocabulary,
not a heuristic guess at the meaning of free text — the same category of
"hard fact from the API" the `is_law` flag already was. An earlier version
of this module ran the free-text `latestAction.text` through an
embedding-similarity classifier; that approach kept surfacing real
misclassifications in production (e.g. "Placed on the Union Calendar" and
committee vote tallies scoring closer to TO_PRESIDENT than IN_COMMITTEE)
because short procedural strings are inherently ambiguous in embedding
space. actionCode has no such ambiguity, so it replaces that approach
entirely rather than layering on top of it.

The actionCode -> stage table below was built empirically: fetched
real actions for a broad random sample of bills already in the database
and read off which (type, actionCode) pairs actually occur as the *latest*
action (see PR discussion). Codes not in the table fall back to a
type + text check, and failing that, to _FALLBACK_STAGE — never a
confident wrong answer.

REFERRED vs. IN_COMMITTEE (2026-07 fix)
----------------------------------------
Being referred to committee is the automatic, universal first step for
essentially every bill — Congress.gov logs it (often bundled with the
introduction itself, e.g. the Senate's "Read twice and referred to the
Committee on X") within days of introduction, before any human has done
anything with the bill at all. A live audit of production data confirmed
this empirically: every one of a sample of Senate bills stuck at what
used to be classified IN_COMMITTEE had exactly two actions ever —
"Introduced in Senate" and the automatic referral — nothing else.
Treating that the same as a bill that actually got a hearing, a markup,
or was reported out of committee (real, non-automatic institutional
engagement) collapsed a meaningful distinction: a sponsor with 100 bills
that all just sit where they were automatically dropped looks identical,
under the old single IN_COMMITTEE bucket, to one whose bills are
genuinely getting worked. REFERRED now captures the former (no credit
beyond bare introduction — see score_calculator.py's _LES_STAGE_ORDER);
IN_COMMITTEE is reserved for confirmed real committee action: a hearing,
a markup, being ordered reported. A House committee's referral to one of
its subcommittees is still REFERRED.

REPORTED (v6.17) is Volden & Wiseman's "action beyond committee": reported
out, discharged, or placed on a calendar (which only follows a report).
scripts/research_les_stage_classifier.py checks these stages bill by bill
against V&W's per-member counts.
"""

import re

from app.config_definitions import BillStage

_FALLBACK_STAGE = BillStage.INTRODUCED

# What a Committee or Calendars action says when the bill has left
# committee: reported out, discharged, or placed on a calendar.
_REPORTED_RE = re.compile(r"\b(reported|placed on|discharged)\b", re.IGNORECASE)

# What a "Floor" action says when the bill itself is being taken up on the
# floor rather than passed, received or presented (those carry mapped
# codes). "Floor" alone is too coarse to act on, so the text has to name
# floor consideration: the Senate's "Considered by Senate", "Cloture on
# the measure ... invoked", "Motion to proceed ...", "Measure laid before
# Senate", and the House's consideration under a rule or suspension.
_FLOOR_CONSIDERATION_RE = re.compile(
    r"\b(considered|cloture|motion to proceed|laid before|suspend the rules"
    r"|under the provisions of rule|failed of passage|motion to recommit)\b",
    re.IGNORECASE,
)

# Empirically observed (type, actionCode) -> stage, keyed by actionCode
# alone (actionCode is unique across types in practice). See module
# docstring for how this was derived.
_ACTION_CODE_STAGE: dict[str, BillStage] = {
    # Introduction
    "1000": BillStage.INTRODUCED,       # Introduced in House
    "10000": BillStage.INTRODUCED,      # Introduced in Senate
    "Intro-H": BillStage.INTRODUCED,    # Introduced in House (House-system code)
    "B00100": BillStage.INTRODUCED,     # Sponsor introductory remarks on measure
    # Referred — automatic, universal, not evidence of real engagement.
    "H11100": BillStage.REFERRED,       # Referred to committee
    "H11000": BillStage.REFERRED,       # Referred to subcommittee
    # Genuine committee action
    "H12410": BillStage.REPORTED,       # Placed on the Union Calendar (only after being reported)
    "H19000": BillStage.IN_COMMITTEE,   # Ordered to be reported (by yeas/nays)
    "H21000": BillStage.IN_COMMITTEE,   # Subcommittee hearings held
    "H15001": BillStage.IN_COMMITTEE,   # Committee consideration / mark-up held
    # Passed the originating chamber
    "17000": BillStage.PASSED_CHAMBER,  # Passed/agreed to in Senate
    "8000": BillStage.PASSED_CHAMBER,   # Passed/agreed to in House
    "H1B000": BillStage.PASSED_CHAMBER, # Considered passed under a self-executing rule
    "H37300": BillStage.PASSED_CHAMBER, # Motion to suspend rules and pass, agreed to
    "H38310": BillStage.PASSED_CHAMBER, # Motion to reconsider laid on the table (post-passage)
    "H38800": BillStage.PASSED_CHAMBER, # Title amended, agreed to (post-passage)
    # Sitting in the other chamber, not yet referred there
    "H14000": BillStage.IN_OTHER_CHAMBER,  # Received in the House
    "H15000": BillStage.IN_OTHER_CHAMBER,  # Held at the desk
    # To the President
    "E20000": BillStage.TO_PRESIDENT,   # Presented to President (House-recorded)
    "28000": BillStage.TO_PRESIDENT,    # Presented to President (LOC-recorded)
    # Enacted
    "36000": BillStage.ENACTED,         # Became Public Law / Signed by President
    # E30000 is not here: Congress.gov gives "Vetoed by President." the same
    # code as "Signed by President.", so the President action's own text
    # decides (_stage_from_type_and_text). Two vetoed bills whose overrides
    # failed were stored as law until v6.32.
    "E40000": BillStage.ENACTED,        # Became Public Law
}


def _stage_from_type_and_text(action_type: str | None, text: str) -> BillStage | None:
    """Fallback for actions whose actionCode is missing or unmapped.

    Congress.gov's `type` is coarser than actionCode (e.g. "Floor" covers
    passage, receipt by the other chamber, AND presentation to the
    President), so it can only disambiguate a few types on its own; the
    rest return None and the caller falls back to _FALLBACK_STAGE rather
    than guess.
    """
    text_lower = text.lower()
    if action_type == "BecameLaw":
        return BillStage.ENACTED
    if action_type == "President":
        if "veto" in text_lower:
            return BillStage.VETOED
        if "public law" in text_lower or "signed" in text_lower:
            return BillStage.ENACTED
        return BillStage.TO_PRESIDENT
    if action_type == "IntroReferral":
        # Senate often combines receipt/introduction and committee
        # referral into one action with no actionCode, e.g. "Read twice
        # and referred to the Committee on ..." or "Received in the
        # Senate and Read twice and referred to the Committee on ...".
        # REFERRED, not IN_COMMITTEE (2026-07 fix, see module docstring):
        # this is the automatic first step, never evidence of real
        # committee engagement — a live audit found Senate bills whose
        # ENTIRE action history was "Introduced in Senate" followed by
        # exactly this, nothing else, ever.
        return BillStage.REFERRED if "referred" in text_lower else BillStage.INTRODUCED
    if action_type == "Floor" and _FLOOR_CONSIDERATION_RE.search(text):
        return BillStage.ON_FLOOR
    if action_type == "Discharge":
        return BillStage.REPORTED
    if action_type in ("Committee", "Calendars") and _REPORTED_RE.search(text) and "ordered to be reported" not in text_lower:
        # Out of committee: reported, discharged, or placed on a calendar
        # (which only follows a report). "Ordered to be reported" is the
        # markup vote, still action in committee.
        return BillStage.REPORTED
    if action_type == "Committee" and text_lower.startswith("referred to"):
        # A House committee passing the bill to a subcommittee ("Referred
        # to the Subcommittee on Health."): as automatic as the first
        # referral, and uncoded. Read as committee action, it put 4,235 of
        # the 118th Congress's House bills in committee that Volden &
        # Wiseman count as having had none.
        return BillStage.REFERRED
    if action_type in ("Committee", "Calendars"):
        # Everything else typed "Committee" is a committee acting on the
        # bill: hearings, markups, the vote to report it.
        return BillStage.IN_COMMITTEE
    return None


# Progression rank for max-over-history classification. Distinct from
# BILL_STAGES' display `order`: VETOED outranks TO_PRESIDENT (a veto is a
# terminal fact about a presented bill) and sits below ENACTED (an
# overridden veto that became law is ENACTED — normally via the is_law
# short-circuit, or a later BecameLaw action).
_STAGE_RANK: dict[BillStage, int] = {
    BillStage.INTRODUCED: 1,
    BillStage.REFERRED: 2,
    BillStage.IN_COMMITTEE: 3,
    BillStage.REPORTED: 4,
    BillStage.ON_FLOOR: 5,
    BillStage.PASSED_CHAMBER: 6,
    BillStage.IN_OTHER_CHAMBER: 7,
    BillStage.TO_PRESIDENT: 8,
    BillStage.VETOED: 9,
    BillStage.ENACTED: 10,
}


# How Congress.gov words the action that makes a bill law. One copy: the
# bill refresh's write-time SQL guard matches the same phrases.
LAW_ACTION_PHRASES = ("became public law", "became private law")


def became_law_action(text: str | None) -> bool:
    """Whether a latest-action text records the bill becoming law —
    Congress.gov's "Became Public Law No: ..." or "Became Private Law No:
    ..." (a private relief bill is a law too, and its BecameLaw action is
    ENACTED to classify_bill_stage_from_actions). Not any mention of a
    Public Law: plenty of ordinary actions cite one (a motion "pursuant to
    section 904 of Public Law 93-344"). The reading for a writer with no
    action history to hand; one with it uses is_enacted."""
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in LAW_ACTION_PHRASES)


def is_enacted(latest_text: str | None, actions: list[dict] | None = None) -> bool:
    """is_law, as every writer of a bill row reads it: the latest action
    says it became law (became_law_action), or — where the action history is to hand —
    that history reaches ENACTED by its structured codes and types (signed
    by the President, before a law number is assigned, included), so is_law
    and the stage classify_bill_stage_from_actions gives can't disagree."""
    if became_law_action(latest_text):
        return True
    return bool(actions) and classify_bill_stage_from_actions(actions) == BillStage.ENACTED


# The furthest a resolution can go. A simple resolution (S.Res., H.Res.)
# is one chamber's business and ends when that chamber agrees to it; a
# concurrent resolution goes to the other chamber but never to the
# President. Actions recorded after agreement ("Motion to reconsider laid
# on the table", messages to the other chamber) read as later stages: 382
# Senate resolutions showed as in the House and two House resolutions as
# sent to the President (2026-10-07).
_RESOLUTION_CEILING = {
    "sres": BillStage.PASSED_CHAMBER, "hres": BillStage.PASSED_CHAMBER,
    "sconres": BillStage.IN_OTHER_CHAMBER, "hconres": BillStage.IN_OTHER_CHAMBER,
}


def classify_bill_stage_from_actions(
    actions: list[dict], is_law: bool = False, bill_type: str | None = None,
) -> BillStage:
    """Classify a bill's stage as the FURTHEST stage reached across its
    full Congress.gov action history.

    `actions` is the raw list from GET .../actions (newest first).
    `is_law` is a hard fact (congress.gov's own "Became Public Law" marker)
    and short-circuits to ENACTED regardless of the action list.

    Max-over-history, not latest-action (2026-07 fix): stages are monotone
    — a bill never un-passes a chamber — but the *latest action* routinely
    reads as an earlier stage. The normal path for every bill that passes
    its originating chamber is "Passed House" followed by "Read twice and
    referred to the Committee on ..." in the second chamber: under
    latest-action classification that regressed the bill from
    PASSED_CHAMBER back to IN_COMMITTEE, docking sponsors a
    cumulative-credit stage in Legislative Effectiveness (_les_bill_stage)
    at the exact moment their bill advanced. Similarly, a vetoed bill
    whose latest action was a failed override vote fell all the way back
    to INTRODUCED. A committee/introduction action that occurs AFTER a
    passage action is the second chamber's referral, so it maps to
    IN_OTHER_CHAMBER rather than merely not regressing.
    """
    if is_law:
        return BillStage.ENACTED

    if not actions:
        return _FALLBACK_STAGE

    best: BillStage | None = None
    passed_seen = False
    # Oldest -> newest so "referral after passage" is detectable.
    for action in reversed(actions):
        code = action.get("actionCode")
        stage = _ACTION_CODE_STAGE.get(code) if code in _ACTION_CODE_STAGE else None
        if stage is None:
            stage = _stage_from_type_and_text(action.get("type"), action.get("text") or "")
        if stage is None:
            continue
        if stage == BillStage.PASSED_CHAMBER:
            passed_seen = True
        elif passed_seen and stage in (
            BillStage.INTRODUCED, BillStage.REFERRED, BillStage.IN_COMMITTEE, BillStage.REPORTED,
            BillStage.ON_FLOOR,
        ):
            stage = BillStage.IN_OTHER_CHAMBER
        if best is None or _STAGE_RANK[stage] > _STAGE_RANK[best]:
            best = stage

    ceiling = _RESOLUTION_CEILING.get((bill_type or "").lower())
    if best is not None and ceiling is not None and _STAGE_RANK[best] > _STAGE_RANK[ceiling]:
        best = ceiling
    return best or _FALLBACK_STAGE
