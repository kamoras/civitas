"""English ordinals ("1st", "102nd", "113th"), the one implementation.

Congress.gov's bill URLs embed the congress as one ("119th-congress"), and a
wrong suffix is a dead link: an inline f"{n}th" once built "93th" and
"101th" for every congress but those ending 4-0 or 11-13.
"""


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"
