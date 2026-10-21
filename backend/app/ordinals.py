"""English ordinals ("1st", "102nd", "113th"), the one implementation.

Congress.gov's bill URLs embed the congress as one ("119th-congress"), and a
wrong suffix is a dead link: an inline f"{n}th" once built "93th" and
"101th" for every congress but those ending 4-0 or 11-13.
"""

from bisect import bisect_left, bisect_right


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# A rank's words for a figure where higher is just higher, and for a
# left-right position (Martin-Quinn: positive is conservative). Neither says
# better or worse.
HIGHEST = ("highest", "lowest", "{} highest")
RIGHTMOST = ("furthest right", "furthest left", "{} from the right")


def rank_shown(value: float, ascending: list[float], words: tuple[str, str, str] = HIGHEST) -> str:
    """Where `value` falls among `ascending` (sorted, and including it), in
    the words a strip's hover label prints: "3rd highest of 15", "Highest of
    15", "Tied for lowest of 433". Competition ranking: ties share the place.
    Bisected, since a House strip asks it 435 times."""
    top, bottom, nth = words
    above = len(ascending) - bisect_right(ascending, value)
    below = bisect_left(ascending, value)
    tied = len(ascending) - above - below > 1
    place = top if above == 0 else bottom if below == 0 else nth.format(ordinal(above + 1))
    text = f"{'tied for ' if tied else ''}{place} of {len(ascending):,}"
    return text[0].upper() + text[1:]
