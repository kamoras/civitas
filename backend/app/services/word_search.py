"""Word search over titles: every typed word, stemmed, as the start of a word.

A reader types "flag burning" for the "Flag Burners Act" and "student loans"
for a "Student Loan" bill. Until 2026-10 the bill list and a member's vote
search matched the query as one unbroken substring, and both of those found
nothing. Here each word is matched on its own, in any order, as the start of
a word in any of the texts, after the Porter stemmer (Porter 1980) has taken
both the query and the text to their stems — so "burning" finds "Burners",
"loans" finds "Loan" and "energy" finds "Energy" — and the last word may be
half typed.

The stemming is SQLite FTS5's own `porter` tokenizer over an in-memory table
(no extra dependency): FTS5 stems a prefix term the way it stems the text.
"""

import re
import sqlite3
from collections.abc import Iterable

# Explore's keyword index's unicode61 with the Porter stemmer over it:
# letters and digits are word characters, everything else separates words,
# and diacritics are folded ("Luján" finds "Lujan").
_TOKENIZE = "porter unicode61 remove_diacritics 2"


def match_expression(q: str) -> str | None:
    """The FTS5 query for `q`: each word as a quoted prefix term, all
    required. None when `q` has no word characters at all."""
    words = re.findall(r"\w+", q.lower())
    return " ".join(f'"{w}"*' for w in words) or None


def build_index(rows: Iterable[tuple[str, ...]], n_texts: int) -> sqlite3.Connection:
    """An in-memory index of `rows`, each (key, text_1 … text_n), keys
    strings. The connection may be read from any thread; callers serialise
    access."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    texts = ", ".join(f"t{i}" for i in range(n_texts))
    conn.execute(f"CREATE VIRTUAL TABLE docs USING fts5(k UNINDEXED, {texts}, tokenize='{_TOKENIZE}')")
    marks = ", ".join("?" for _ in range(n_texts + 1))
    conn.executemany(f"INSERT INTO docs VALUES ({marks})", rows)
    return conn


def search(conn: sqlite3.Connection, q: str) -> set[str]:
    """The keys of the indexed rows matching every word of `q`."""
    expression = match_expression(q)
    if expression is None:
        return set()
    return {k for (k,) in conn.execute("SELECT k FROM docs WHERE docs MATCH ?", (expression,))}


def matching_keys(rows: Iterable[tuple[str, ...]], n_texts: int, q: str) -> set[str]:
    """`search` over a throwaway index of `rows`: for a few hundred rows (a
    member's votes) building one costs about a millisecond."""
    if match_expression(q) is None:
        return set()
    conn = build_index(rows, n_texts)
    try:
        return search(conn, q)
    finally:
        conn.close()
