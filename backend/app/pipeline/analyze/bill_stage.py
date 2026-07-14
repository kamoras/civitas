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
"""

_FALLBACK_STAGE = "INTRODUCED"

# Empirically observed (type, actionCode) -> stage, keyed by actionCode
# alone (actionCode is unique across types in practice). See module
# docstring for how this was derived.
_ACTION_CODE_STAGE: dict[str, str] = {
    # Introduction
    "1000": "INTRODUCED",       # Introduced in House
    "10000": "INTRODUCED",      # Introduced in Senate
    "Intro-H": "INTRODUCED",    # Introduced in House (House-system code)
    "B00100": "INTRODUCED",     # Sponsor introductory remarks on measure
    # Committee
    "H11100": "IN_COMMITTEE",   # Referred to committee
    "H11000": "IN_COMMITTEE",   # Referred to subcommittee
    "H12410": "IN_COMMITTEE",   # Placed on the Union Calendar
    "H19000": "IN_COMMITTEE",   # Ordered to be reported (by yeas/nays)
    "H21000": "IN_COMMITTEE",   # Subcommittee hearings held
    "H15001": "IN_COMMITTEE",   # Committee consideration / mark-up held
    # Passed the originating chamber
    "17000": "PASSED_CHAMBER",  # Passed/agreed to in Senate
    "8000": "PASSED_CHAMBER",   # Passed/agreed to in House
    "H1B000": "PASSED_CHAMBER", # Considered passed under a self-executing rule
    "H37300": "PASSED_CHAMBER", # Motion to suspend rules and pass, agreed to
    "H38310": "PASSED_CHAMBER", # Motion to reconsider laid on the table (post-passage)
    "H38800": "PASSED_CHAMBER", # Title amended, agreed to (post-passage)
    # Sitting in the other chamber, not yet referred there
    "H14000": "IN_OTHER_CHAMBER",  # Received in the House
    "H15000": "IN_OTHER_CHAMBER",  # Held at the desk
    # To the President
    "E20000": "TO_PRESIDENT",   # Presented to President (House-recorded)
    "28000": "TO_PRESIDENT",    # Presented to President (LOC-recorded)
    # Enacted
    "36000": "ENACTED",         # Became Public Law / Signed by President
    "E30000": "ENACTED",        # Signed by President
    "E40000": "ENACTED",        # Became Public Law
}


def _stage_from_type_and_text(action_type: str | None, text: str) -> str | None:
    """Fallback for actions whose actionCode is missing or unmapped.

    Congress.gov's `type` is coarser than actionCode (e.g. "Floor" covers
    passage, receipt by the other chamber, AND presentation to the
    President), so it can only disambiguate a few types on its own; the
    rest return None and the caller falls back to _FALLBACK_STAGE rather
    than guess.
    """
    text_lower = text.lower()
    if action_type == "BecameLaw":
        return "ENACTED"
    if action_type == "President":
        if "veto" in text_lower:
            return "VETOED"
        if "public law" in text_lower or "signed" in text_lower:
            return "ENACTED"
        return "TO_PRESIDENT"
    if action_type == "IntroReferral":
        # Senate often combines receipt/introduction and committee
        # referral into one action with no actionCode, e.g. "Read twice
        # and referred to the Committee on ..." or "Received in the
        # Senate and Read twice and referred to the Committee on ...".
        return "IN_COMMITTEE" if "referred" in text_lower else "INTRODUCED"
    if action_type in ("Committee", "Calendars"):
        return "IN_COMMITTEE"
    return None


def classify_bill_stage_from_actions(actions: list[dict], is_law: bool = False) -> str:
    """Classify a bill's current stage from its Congress.gov action history.

    `actions` is the raw list from GET .../actions (newest first).
    `is_law` is a hard fact (congress.gov's own "Became Public Law" marker)
    and short-circuits to ENACTED regardless of the action list.
    """
    if is_law:
        return "ENACTED"

    if not actions:
        return _FALLBACK_STAGE

    latest = actions[0]
    code = latest.get("actionCode")
    if code in _ACTION_CODE_STAGE:
        return _ACTION_CODE_STAGE[code]

    fallback = _stage_from_type_and_text(latest.get("type"), latest.get("text") or "")
    return fallback or _FALLBACK_STAGE
