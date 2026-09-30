"""robots.txt as RFC 9309 reads it (app/pipeline/fetch/robots.py)."""

from app.pipeline.fetch import robots

AGENT = "Civitas"


def _allows(text, path, agent=AGENT):
    return robots.parse(text).allows(agent, path)


class TestGroups:
    def test_our_group_replaces_the_star_group(self):
        text = "User-agent: *\nDisallow: /\n\nUser-agent: Civitas\nAllow: /\n"
        assert _allows(text, "/results") is True
        assert _allows(text, "/results", agent="Otherbot") is False

    def test_the_star_group_applies_when_none_names_us(self):
        assert _allows("User-agent: *\nDisallow: /x/\n", "/x/y") is False

    def test_a_named_group_is_matched_by_product_token_not_substring(self):
        """urllib.robotparser applied a group for "vita" to "Civitas"."""
        text = "User-agent: vita\nDisallow: /\n\nUser-agent: CivitasBot\nDisallow: /\n"
        assert _allows(text, "/anything") is True

    def test_a_group_naming_our_token_with_a_version_applies(self):
        assert _allows("User-agent: Civitas/1.0\nDisallow: /r/\n", "/r/1") is False

    def test_a_mozilla_group_is_not_ours(self):
        assert _allows("User-agent: Mozilla\nDisallow: /\n", "/anything") is True

    def test_consecutive_agent_lines_share_one_group(self):
        text = "User-agent: Otherbot\nUser-agent: civitas\nDisallow: /r/\n"
        assert _allows(text, "/r/1") is False

    def test_groups_naming_us_are_combined(self):
        text = "User-agent: Civitas\nDisallow: /a/\n\nUser-agent: Civitas\nDisallow: /b/\n"
        assert _allows(text, "/a/1") is False
        assert _allows(text, "/b/1") is False


class TestRules:
    def test_the_longest_match_wins_not_the_first(self):
        """urllib.robotparser applied "Allow: /" and blocked nothing."""
        text = "User-agent: *\nAllow: /\nDisallow: /results/\n"
        assert _allows(text, "/results/live") is False
        assert _allows(text, "/elections") is True

    def test_an_allow_wins_a_tie(self):
        assert _allows("User-agent: *\nDisallow: /r\nAllow: /r\n", "/r/1") is True

    def test_star_and_dollar(self):
        text = "User-agent: *\nDisallow: /*.csv$\nDisallow: /tmp*/private\n"
        assert _allows(text, "/results/live.csv") is False
        assert _allows(text, "/results/live.csv?x=1") is True
        assert _allows(text, "/tmp123/private/x") is False

    def test_an_empty_disallow_disallows_nothing(self):
        assert _allows("User-agent: *\nDisallow:\n", "/anything") is True

    def test_comments_and_unknown_lines_are_ignored(self):
        text = "# hi\nUser-agent: * # everyone\nCrawl-delay: 5\nDisallow: /x # no\n"
        assert _allows(text, "/x/1") is False

    def test_robots_txt_itself_is_always_allowed(self):
        assert _allows("User-agent: *\nDisallow: /\n", "/robots.txt") is True

    def test_no_file_rules_mean_allowed(self):
        assert _allows("", "/anything") is True


def test_the_blanket_answers():
    assert robots.ALLOW_ALL.allows(AGENT, "/x") is True
    assert robots.DISALLOW_ALL.allows(AGENT, "/x") is False
