"""State names: postal code -> name, the one copy.

A naming fact (the USPS codes and the states' own names), not data any
pipeline computes; kept once here so every module translating between
codes and names reads the same table. Which jurisdictions are *states*
is not decided here: that is election_calendar.federal_states(), read from
the Senate's own member list. DC is named so its ballot page can be.
"""

STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
    # Not a state, but it has a House delegate and its own ballot page, and
    # every lookup here falls back to the bare code — which put "DC" in
    # front of readers wherever a full name belonged.
    "DC": "District of Columbia",
}


# "New York" -> "NY"
STATE_NAME_TO_CODE: dict[str, str] = {name: code for code, name in STATE_NAMES.items()}
