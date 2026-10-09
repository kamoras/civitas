"""The year-in-review timeline's one-story-one-entry rule."""


def without_repeat_leads(entries: list) -> list:
    """TimelineEntry rows less any whose lead source is the previous day's
    entry's: a story that led two days running is one entry, on the day it
    first led. action_center._save_timeline_entry applies the rule as it
    writes, but only to new days; rows written before it (two pairs in
    2026-10, measured 2026-10-09) listed one story twice in a row. Every
    reader of the timeline goes through this. Keeps the input's order."""
    newest_first = len(entries) > 1 and entries[0].date > entries[-1].date
    ordered = list(reversed(entries)) if newest_first else list(entries)
    kept = []
    prev_url = None
    for e in ordered:
        if not (e.source_url and e.source_url == prev_url):
            kept.append(e)
        prev_url = e.source_url
    return kept[::-1] if newest_first else kept
