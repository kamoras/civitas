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


class TestGroupBoundaries:
    """Any record after the user-agent lines ends the group (RFC 9309
    §2.1): the next user-agent line starts another."""

    def test_an_empty_disallow_closes_its_group(self):
        """The commonest real file: everyone allowed but one bot."""
        text = "User-agent: *\nDisallow:\n\nUser-agent: BadBot\nDisallow: /\n"
        assert _allows(text, "/x") is True
        assert _allows(text, "/x", agent="BadBot") is False

    def test_an_allow_all_for_us_is_not_merged_into_the_next_group(self):
        text = "User-agent: Civitas\nDisallow:\n\nUser-agent: *\nDisallow: /\n"
        assert _allows(text, "/x") is True

    def test_a_crawl_delay_closes_its_group(self):
        text = "User-agent: *\nCrawl-delay: 5\n\nUser-agent: BadBot\nDisallow: /\n"
        assert _allows(text, "/x") is True

    def test_a_sitemap_line_belongs_to_no_group(self):
        text = "User-agent: Civitas\nSitemap: https://x.gov/s.xml\nUser-agent: Other\nDisallow: /r/\n"
        assert _allows(text, "/r/1") is False

    def test_a_token_with_trailing_punctuation_still_names_us(self):
        assert _allows("User-agent: Civitas;\nDisallow: /\n", "/x") is False


class TestNormalization:
    """§2.2.2: a path and a pattern are compared after the same
    percent-encoding normalisation."""

    def test_an_encoded_unreserved_character_is_the_character(self):
        assert _allows("User-agent: *\nDisallow: /~joe\n", "/%7Ejoe") is False
        assert _allows("User-agent: *\nDisallow: /%7ejoe\n", "/~joe") is False

    def test_non_ascii_is_its_utf8_encoding(self):
        assert _allows("User-agent: *\nDisallow: /café\n", "/caf%C3%A9") is False
        assert _allows("User-agent: *\nDisallow: /caf%c3%a9\n", "/café") is False

    def test_hex_case_does_not_matter(self):
        assert _allows("User-agent: *\nDisallow: /a%3cb\n", "/a%3Cb") is False

    def test_a_reserved_character_stays_encoded(self):
        """%2F is not "/": decoding it would change the path."""
        assert _allows("User-agent: *\nDisallow: /a/b\n", "/a%2Fb") is True


class TestHostileFiles:
    def test_many_stars_match_in_linear_time(self):
        """A backtracking regex took minutes on this; the file is
        external input and matching runs on the event loop."""
        import time

        pattern = "/" + "*a" * 40 + "X"
        started = time.perf_counter()
        assert _allows(f"User-agent: *\nDisallow: {pattern}\n", "/" + "a" * 2000) is True
        assert time.perf_counter() - started < 1.0

    def test_a_byte_order_mark_does_not_hide_the_first_line(self):
        assert _allows("﻿User-agent: *\nDisallow: /private\n", "/private") is False

    def test_only_the_first_max_bytes_are_read(self):
        padding = "#" * robots.MAX_BYTES + "\n"
        assert _allows(f"User-agent: *\n{padding}Disallow: /\n", "/x") is True
