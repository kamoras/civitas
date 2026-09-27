from types import SimpleNamespace

from app.api.action import _extract_country_mentions


def _article(title, summary=""):
    return SimpleNamespace(title=title, summary=summary, url=f"https://x/{title}", source_name="AP", published=None)


def test_country_names_match_whole_words_only():
    found = {c["country"] for c in _extract_country_mentions([
        _article("Indiana Senate passes redistricting map"),
        _article("UKRAINE AID VOTE SET FOR TUESDAY"),
    ])}
    assert "India" not in found
    assert "United Kingdom" not in found


def test_names_and_aliases_still_match():
    found = {c["country"]: c["articleCount"] for c in _extract_country_mentions([
        _article("India and Pakistan agree to talks"),
        _article("Iranian officials meet in Tehran", "Talks with Iran's envoy"),
        _article("UK, France sign accord"),
    ])}
    assert found["India"] == 1 and found["Pakistan"] == 1
    assert found["Iran"] == 1  # one article, however many ways it names the country
    assert found["United Kingdom"] == 1 and found["France"] == 1
