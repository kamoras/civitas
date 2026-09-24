

class TestStateNewsFeeds:
    """Per-state political outlets, for attaching news to RACES.

    The national eight (AP, NPR, PBS, BBC, The Hill, Politico, Roll Call)
    cannot cover 50 states' House and Senate races. That gap is what the
    open Bluesky candidate-name search was filling, with 7,740 items of
    which Minnesota's included "Dave Hughes still a whiny cunt" and a
    post about the Australian comedian of the same name. Four successive
    filters failed to clean it, because a name mention is not coverage —
    the fix is more SOURCES, not a harder filter.

    Every URL here was fetched live on 2026-09-24 and returned a
    populated feed. These tests check the structure, not the network: a
    unit test must not depend on 41 third-party sites being up.
    """

    def test_every_entry_is_complete(self):
        from app.pipeline.fetch.news_feeds import STATE_NEWS_FEEDS
        for feed in STATE_NEWS_FEEDS:
            assert feed["state"] and len(feed["state"]) == 2, feed
            assert feed["name"], feed
            assert feed["url"].startswith("https://"), feed

    def test_one_outlet_per_state(self):
        """Two feeds for one state would double that state's apparent
        coverage breadth, which is a ranking signal elsewhere."""
        from app.pipeline.fetch.news_feeds import STATE_NEWS_FEEDS
        states = [f["state"] for f in STATE_NEWS_FEEDS]
        assert len(states) == len(set(states)), "duplicate state"

    def test_states_are_real(self):
        from app.election_calendar import CLASS_I_STATES, CLASS_II_STATES, CLASS_III_STATES
        from app.pipeline.fetch.news_feeds import STATE_NEWS_FEEDS
        valid = CLASS_I_STATES | CLASS_II_STATES | CLASS_III_STATES
        assert {f["state"] for f in STATE_NEWS_FEEDS} <= valid

    def test_the_list_is_actually_populated(self):
        """Guards the guard: a rename or a bad merge emptying the list
        would make every assertion above pass over nothing."""
        from app.pipeline.fetch.news_feeds import STATE_NEWS_FEEDS
        assert len(STATE_NEWS_FEEDS) >= 40

    def test_state_feeds_are_not_in_the_national_list(self):
        """They are read by election_coverage only. Adding ~400 state
        articles to the national clustering would change which national
        issues rank — a separate decision from covering races."""
        from app.pipeline.fetch.news_feeds import NEWS_FEEDS, STATE_NEWS_FEEDS
        national = {f["url"] for f in NEWS_FEEDS}
        assert not national & {f["url"] for f in STATE_NEWS_FEEDS}
