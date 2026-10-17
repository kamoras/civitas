"""Is a feed item one story, or several? Multi-story digests dropped at ingest.

An outlet's recurring briefing or newsletter is one RSS item covering
several unrelated stories, and every consumer downstream treats it as one
story: the Action Center clusters it into a chimera issue, and the election
coverage ingest attached a newsletter whose lead item named a candidate to
that candidate's race, then posted it (2026-10-17, a Hill "Morning Report"
on NY-14). So every ingest path that reads feed items asks digest_reason
before using one: action_center._filter_policy_relevant and
election_coverage.ingest_race_coverage (which also re-checks what it has
stored).

Mechanical signals only, tuned for precision because a hit drops the whole
article; see AGENTS.md "multi-story digests are dropped at ingest".
_issue_signature lives here because the body check is built on it; the
Action Center imports it back for its own story identity checks.
"""

import re
from urllib.parse import urlparse

from app.person_names import APOSTROPHES as _APOSTROPHES
from app.pipeline.fetch.news_feeds import MAX_SUMMARY_CHARS, NewsArticle

# Tokens that appear in most political stories and therefore carry no
# STORY identity — a signature made of these matches everything. Identity
# comes from specific entities and numbers, never from shared civic
# vocabulary. Calibrated against real production pairs (see
# _issue_signature): with these stripped, two same-story rows measured
# overlap 0.78-1.00 while a different-story pair whose TITLES scored 0.88
# cosine measured 0.0.
_SIGNATURE_GENERIC_TOKENS = {
    "the", "a", "an", "this", "these", "those", "several", "multiple",
    "recent", "new", "over", "under", "after", "before", "while", "with",
    "house", "senate", "congress", "congressional", "president",
    "republicans", "republican", "democrats", "democrat", "democratic",
    "gop", "lawmakers", "federal", "government", "state", "states",
    "white", "washington", "american", "americans", "bill", "act",
    "committee", "administration", "officials", "legislation",
}


# A date names when, not what: on 2026-10-08, 8 of the 23 rows the feed had
# marked duplicates shared little but a date ("2026", "September", "28"),
# one news story hidden as a duplicate of an unrelated Senate vote's draft.
# Removed before the signature is read: a month with its day and year, an
# ISO date, a weekday, and a year standing alone.
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)
_DATE_RE = re.compile(
    rf"\b(?:{_MONTHS})\.?(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?(?:,?\s+\d{{4}})?\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\b(?:\s+\d{{4}})?"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"|\b(?:19|20)\d{2}\b"
)


def _issue_signature(title: str, facts: list[str]) -> set[str]:
    """Identity fingerprint of an issue: specific capitalized entities plus
    digit groups from its title and facts, minus generic civic vocabulary
    (see _SIGNATURE_GENERIC_TOKENS) and dates (_DATE_RE). Two rows about
    the same real-world story share the numbers and named entities that
    define it; two rows that merely sound alike share only the generic
    tokens this strips."""
    text = _DATE_RE.sub(" ", f"{title} {' '.join(facts)}").translate(_APOSTROPHES)
    # A possessive is the same entity: "Trump" and "Trump's" counted as two
    # shared tokens matched unrelated stories about one person (2026-10-08).
    tokens = {
        _POSSESSIVE_SUFFIX_RE.sub("", m.group(0).lower())
        for m in re.finditer(r"\b[A-Z][a-zA-Z'\-]{2,}\b", text)
    } - _SIGNATURE_GENERIC_TOKENS
    numbers = {
        m.group(0).replace(",", "")
        for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)
    }
    return tokens | numbers


# Recurring multi-story products an outlet publishes as ONE feed item. The
# body of such an item is a list of that day's (or week's) unrelated stories,
# so everything downstream — clustering, the LLM prompt, the generated
# summary — treats several separate events as one issue and the summary
# stitches them into a single narrative, often inventing a causal link
# between them (live 2026-07 case, issue 483: "A new agreement involving
# Hamas and Israel has been announced... The U.S. economy saw a slight
# slowdown following the announcement" — two unrelated NPR items from one
# briefing, joined by a "following the announcement" no source stated).
#
# This started as a weekly-roundup title filter. Daily briefings are the
# same shape and far more common — NPR runs "Up First" and "Morning news
# brief" every weekday, and each is a single RSS item covering three to five
# separate stories. Phrases that also appear on ordinary single-topic
# explainers ("what to know", "the latest:", "live updates" — a live blog
# covers one event in many parts, not many events) are deliberately NOT
# here: this filter drops articles outright, so it is tuned for precision on
# the recurring-product class rather than for catching every possible digest.
#
# Matching is split by WHERE the marker has to appear, because several of
# these phrases are also ordinary English somewhere else in a headline:
#   - anywhere: phrases that are digest-specific wherever they land
#   - title-initial: product names that collide with real reporting when
#     they appear mid-headline ("Pentagon holds evening briefing on troop
#     levels", "Varga skipped the President's Daily Brief", "the building
#     blew up first") — a product name leads its own headline, optionally
#     after a source tag like "NPR:"
#   - trailing tag: "in brief" / "and more", which are digest markers only
#     at the end ("spoke in brief remarks", "and more-restrictive rules")
#
# Titles are apostrophe-normalized before matching (digest_reason): feeds
# emit the typographic ’ far more often than ', and "Today’s headlines"
# silently missed the ' spelling.
_DIGEST_TITLE_PATTERNS = re.compile(
    r"\b(week(?:ly)? in (?:politics|review|news)|week(?:'?s)? (?:top |best )?(?:news|stories|headlines)"
    r"|this week in|news of the week|photos of the week|political news this week|what we('re| are) watching"
    r"|week(?:ly)? (?:wrap-?up|round-?up)"
    # "brief" only, never "briefing" — "White House news briefing on the
    # Gaza strikes" is one story about a press conference.
    r"|news brief\b|newscast|politics chat|news round-?up"
    # Not "N takeaways": on 2026-10-17 every such headline stored (11 in
    # the Action Center's 90 days of feed articles, 12 in election coverage)
    # was one event's takeaways, mostly a debate's.
    r"|\d+ things to know)",
    re.IGNORECASE,
)

_DIGEST_TITLE_PREFIX_PATTERNS = re.compile(
    # Optional source tag: "NPR:", "AP —", "Morning Edition - ". A dash only
    # separates a tag when it is spaced; unspaced it is a compound word, and
    # "Wrap-up first look at the budget" is not a product name. Then an
    # optional leading article ("The Up First newsletter: ...", NPR's real
    # title format for that product) — live-missed case (issue 492, 2026-08):
    # "The" is too long to be swallowed as part of the source-tag group (it
    # has no colon/dash of its own within 20 chars of "The"), so without this
    # the whole prefix pattern silently failed to match and the article fell
    # through to _multi_topic_body, which has its own gap for titles that
    # name every bundled story (see that function's docstring). An article
    # is not a source tag, so this is a separate, unconditional skip, not
    # folded into the source-tag group above.
    r"^(?:[A-Za-z][\w.&' ]{0,20}?(?::\s*|\s+[—–|-]\s+))?"
    r"(?:(?:the|a|an)\s+)?"
    r"(up first|first up|top stories|today's headlines|headlines\s*:"
    r"|(?:daily|morning|evening|nightly)\s+"
    r"(?:brief|briefing|digest|rundown|wire|wrap|newsletter))\b",
    re.IGNORECASE,
)

_DIGEST_TITLE_SUFFIX_PATTERNS = re.compile(
    # A dash only counts as the tag boundary when spaced — "and
    # more-restrictive tariffs" is a compound adjective, not a tag.
    r"\b(in brief|and more)(?=\s*[:.…]|\s*$|\s+[-–—])",
    re.IGNORECASE,
)

# Feeds emit the typographic apostrophe far more often than the ASCII one
# (_APOSTROPHES, app.person_names).

# Feed descriptions enumerate their items with sentence breaks, semicolons,
# bullets, or pipes — all four appear in real NPR/AP briefing bodies. The
# delimiter is captured, not consumed, because it decides whether the item's
# first word can be trusted as a name (see _split_body_items).
_DIGEST_ITEM_SPLIT_RE = re.compile(r"((?<=[.!?])\s+|\s*[;•·|]\s*)")

# A digest body must break into at least this many entity-bearing items
# before disjointness means anything: two sentences that happen not to
# share a name is ordinary prose ("Israel and Hamas reached a deal.
# Negotiators met in Cairo."), three or more sharing NOTHING is a list.
_DIGEST_MIN_ITEMS = 3

# Two things in a single story's teaser that the split above turned into
# extra, guaranteed-disjoint "items" — measured 2026-10-10: of 19 articles
# this body check dropped from 627 feed articles, 18 were single stories (14
# of them U.S. policy stories), 16 pushed to three items by one of these
# (now 2 drops, both single stories; docs/research/action-center-relevance.md):
#  - A period that ends an abbreviation is not a sentence break: dotted
#    initials ("U.S.", "D.C.", "Joshua A.") and the abbreviated titles and
#    months written before names and dates ("Rep.", "Sen.", "Sept."). "An
#    investigation is underway after U.S. | Immigration and Customs ..."
#    became two items.
_ABBREVIATION_END_RE = re.compile(
    r"(?:\b[A-Z]\.)+$"
    r"|\b(?:Sens?|Reps?|Gov|Gen|Lt|Col|Sgt|Capt|Adm|Dr|Mrs?|Ms|St|Jr|Sr"
    r"|Jan|Feb|Aug|Sept?|Oct|Nov|Dec|No|Ft|Mt)\.$"
)
#  - Attribution is not an item: a photo credit in parentheses ("(Image
#    credit: ...)", "(AP Photo/...)") anywhere, and a broadcast segment's
#    closing sign-off ("<reporter> reports.", "<host> discussed it with
#    <guest>."), which names people who are not in the story. Dropping an
#    item can only make a body less list-like, so a sign-off pattern that
#    catches a real sentence errs toward keeping the article.
_CREDIT_ITEM_RE = re.compile(r"^\(.*\)?$")
_SIGN_OFF_ITEM_RE = re.compile(
    r"\breports(?: from [^.]*)?\.?$|\b(?:discussed|spoke|talked)\b.*\bwith\b[^.]*\.?$"
    # PBS NewsHour's: "<guest> joined <host> to discuss more."
    r"|\bjoined\b.*\bto discuss\b[^.]*\.?$"
)
# A description the PUBLISHER cut ("... The Cook...", "[…]") ends in a
# fragment just as one cut at MAX_SUMMARY_CHARS does, and is treated the
# same way. The Hill cuts nearly every description so; on 2026-10-17 that
# fragment made 8 single stories in 1,457 stored election-coverage items
# (a Senate endorsement, a debate preview, a member's interview) read as
# lists.
_SOURCE_CUT_RE = re.compile(r"(?:\.\.\.|…|\[…\]|\[\.\.\.\])\s*$")


def _split_body_items(summary: str, truncated: bool = False) -> list[tuple[str, bool]]:
    """Split a feed description into (item, first_word_is_forced_capital).

    Grammar capitalizes the word that opens a sentence whether or not it
    names anything, so "Negotiators met in Cairo." looks like it introduces
    a person. After a semicolon or a bullet nothing is forced, so a
    capitalized word there really is a name ("Brandt defended the cut").
    Which delimiter preceded an item is the only thing that distinguishes
    the two, hence the captured-group split.

    A body news_feeds cut at its length cap (NewsArticle.truncated — a
    plain length check against a hardcoded constant stopped being reliable
    once summaries could come from either a short teaser or full article
    text, each with its own cap) was cut mid-item, and the fragment left
    behind ("...Negotiators from Qatar") names entities that by
    construction appear nowhere else in the text — one more
    guaranteed-disjoint item, enough to push a two-item blurb over the list
    threshold. The cap is the signal, not trailing punctuation: plenty of
    bodies legitimately end without a period (bulleted lists especially),
    and treating those as cut discarded the whole final item.
    """
    parts = _DIGEST_ITEM_SPLIT_RE.split(summary or "")
    delimiters = parts[1::2]
    # Nothing precedes the first item, so its capital is read from the rest
    # of the body: in a bulleted or semicolon-separated list the opening item
    # is a list entry like every other one, and stripping its first word
    # would throw away the only name it has.
    first_forced = all(d.strip() == "" for d in delimiters)
    items: list[tuple[str, bool]] = [(parts[0], first_forced)]
    for delimiter, item in zip(delimiters, parts[2::2]):
        sentence_break = delimiter.strip() == ""
        if sentence_break and _ABBREVIATION_END_RE.search(items[-1][0].rstrip()):
            items[-1] = (f"{items[-1][0]} {item}", items[-1][1])
        else:
            items.append((item, sentence_break))
    if (truncated or _SOURCE_CUT_RE.search(summary or "")) and len(items) > 1:
        items.pop()
    if len(items) > 1 and _SIGN_OFF_ITEM_RE.search(items[-1][0].strip()):
        items.pop()
    return [(item, forced) for item, forced in items if not _CREDIT_ITEM_RE.match(item.strip())]


# Singular and plural possessives both: "Varga's", "Democrats'".
_POSSESSIVE_SUFFIX_RE = re.compile(r"'s?$")


def _topic_tokens(text: str) -> set[str]:
    """Entity signature of one text, for comparing a headline against its
    own body. _issue_signature unifies apostrophes and drops possessives, so
    a possessive-led headline ("Varga's tariff order faces court test", the
    most common headline shape there is) shares {varga} with a body that
    says "Varga", whichever apostrophe the feed emitted."""
    return _issue_signature(text, [])


def _item_entities(item: str, forced_capital: bool) -> set[str]:
    """Named entities in one item of a feed description.

    Reuses the issue-signature extractor (capitalized tokens and digit
    groups, minus generic civic vocabulary). Where the first word's capital
    is forced by grammar it is dropped before extraction — _issue_signature
    was written for whole titles and fact lists, in which sentence-initial
    capitalization is a rounding error, and per-item it would otherwise
    manufacture a distinct "entity" for every sentence, which is exactly the
    noise the disjointness test below cannot tolerate.
    """
    if forced_capital:
        parts = item.split(None, 1)
        item = parts[1] if len(parts) > 1 else ""
    return _topic_tokens(item)


# How many of a body's items the headline may account for before the body
# stops looking like a list. A headline names its story's subject and the
# blurb keeps referring back to it, so a single story's title covers most
# of its own items; a briefing's title can cover at most the one item it
# leads with. Without this, any single story whose blurb hands off between
# actors reads as a list ("Sen. Chuck Grassley released the transcript
# Thursday. The FBI declined to comment. House Judiciary Democrats called
# for hearings." — three sentences, three disjoint entity sets, one story).
_DIGEST_MAX_TITLE_COVERED_ITEMS = 1


def _multi_topic_body(summary: str, title: str, truncated: bool = False) -> bool:
    """True if ``summary`` reads as a list of separate stories.

    Two conditions, both required. The items must be pairwise disjoint in
    the entities they name — a single story's blurb keeps returning to its
    own subject, a briefing's items are about different people in different
    places. And the headline must fail to account for the body: a digest
    headline cannot describe stories it does not know about, which is the
    property that distinguishes a list from a story told through several
    actors (see _DIGEST_MAX_TITLE_COVERED_ITEMS).
    """
    items = [
        entities
        for entities in (
            _item_entities(item, forced)
            for item, forced in _split_body_items(summary, truncated)
        )
        # A bare number is not a topic — an item must name something.
        if any(not token[0].isdigit() for token in entities)
    ]
    if len(items) < _DIGEST_MIN_ITEMS:
        return False
    disjoint = all(
        not (a & b)
        for i, a in enumerate(items)
        for b in items[i + 1:]
    )
    if not disjoint:
        return False
    title_entities = _topic_tokens(title)
    covered = sum(1 for entities in items if entities & title_entities)
    return covered <= _DIGEST_MAX_TITLE_COVERED_ITEMS


def _entity_items(text: str, truncated: bool = False) -> list[set[str]]:
    """The entity set of each entity-bearing item of ``text``
    (_split_body_items, _item_entities)."""
    return [
        entities
        for entities in (_item_entities(item, forced) for item, forced in _split_body_items(text, truncated))
        if any(not token[0].isdigit() for token in entities)
    ]


def _parallel_stories(title: str, summary: str, truncated: bool = False) -> bool:
    """True if the headline strings unrelated stories together and the body
    tells the same ones, one item each.

    _multi_topic_body needs three items, since two disjoint sentences are
    ordinary prose, and a briefing that names its stories in its headline
    passes its second condition by construction. A two-story briefing has
    both shapes at once ("<story A>. And, <story B>" over "<A at more
    length>. And, <B at more length>"): the headline splits into items
    naming disjoint entities, the body's items are disjoint too, and each
    body item names exactly one headline item's entities, every headline
    item being named. A single story told in two headline sentences fails
    the last half: its teaser names both halves in one item, or adds an item
    naming neither ("Lawmakers added $1 to car insurance. The money paid for
    cameras." over a photo caption and the story). Measured 2026-10-15 on
    1,473 stored articles with a headline and body (election coverage and
    the Action Center's claim sources): 28 headlines have two or more
    disjoint items, 5 have this shape, and all 5 are two-story briefings; of
    the 23 left, 11 are multi-story live blogs it doesn't catch and 12 are
    single stories (docs/research/action-center-relevance.md).
    """
    heads = _entity_items(title)
    if len(heads) < 2 or any(a & b for i, a in enumerate(heads) for b in heads[i + 1:]):
        return False
    body = _entity_items(summary, truncated)
    if any(a & b for i, a in enumerate(body) for b in body[i + 1:]):
        return False
    named = [[i for i, head in enumerate(heads) if item & head] for item in body]
    return all(len(n) == 1 for n in named) and {n[0] for n in named} == set(range(len(heads)))


# The outlet's own section for its newsletters. A newsletter is a
# multi-section product ("The Big Story", then other news, with "Welcome to
# ... newsletter" and photo-credit boilerplate in its feed description), so
# it is a digest by the outlet's own filing, whatever its headline says.
# One outlet syndicates them into its news feed (3 of 4,067 items across
# all 49 feeds, 2026-10-08); one was published as an issue whose "fact" was
# four of its section headings run together.
_NEWSLETTER_SECTIONS = frozenset({"newsletter", "newsletters"})


def digest_reason(article: NewsArticle) -> str | None:
    """Why ``article`` is a multi-story digest, or None if it is one story."""
    if _NEWSLETTER_SECTIONS & {s.lower() for s in urlparse(article.url or "").path.split("/")}:
        return "newsletter section"
    title = article.title.translate(_APOSTROPHES)
    if (
        _DIGEST_TITLE_PATTERNS.search(title)
        or _DIGEST_TITLE_PREFIX_PATTERNS.match(title)
        or _DIGEST_TITLE_SUFFIX_PATTERNS.search(title)
    ):
        return "recurring digest title"
    # The body-shape check targets a specific, short-field phenomenon —
    # several unrelated headlines crammed into one RSS <description> — not
    # genuine long-form prose. A full article (see news_feeds'
    # content:encoded handling) legitimately drifts across several named
    # entities in the course of telling ONE story; that's normal narrative
    # structure, not a compilation, and shouldn't be judged by a heuristic
    # built around a one-paragraph teaser.
    if len(article.summary) <= MAX_SUMMARY_CHARS and _multi_topic_body(
        article.summary, article.title, article.truncated,
    ):
        return "body lists unrelated stories"
    if len(article.summary) <= MAX_SUMMARY_CHARS and _parallel_stories(
        article.title.translate(_APOSTROPHES), article.summary, article.truncated,
    ):
        return "headline and body list the same unrelated stories"
    return None

