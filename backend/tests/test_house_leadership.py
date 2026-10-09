"""House party leadership from the Clerk's Members page."""

from app.pipeline.fetch import house_leadership as hl

# Trimmed from clerk.house.gov/Members as served 2026-10-09 (names replaced):
# each post is a list item with the member's name in bold and the post as a
# link, under a "... Leadership" heading.
_PAGE = """<html><body><nav>
<ul aria-label="majority leadership"><li><h3 class="menu-subtitle">Republican Leadership</h3></li>
<li><b>Rep. Ann Alder</b><br /><a href="https://www.speaker.gov/">Speaker of the House</a></li>
<li><b>Rep. Ben Birch</b><br /><a href="#">Majority Leader</a></li>
<li><b>Rep. Cal Cedar</b><br /><a href="#">Majority Whip</a></li>
<li><b>Rep. Dee Q. Dogwood</b><br /><a href="#">Republican Policy Committee Chair</a></li></ul>
<ul aria-label="republican leadership"><li><h3 class="menu-subtitle">Democratic Leadership</h3></li>
<li><b>Rep. Eve Elm</b><br /><a href="#">Minority Leader</a></li>
<li><b>Rep. Fay Fir</b><br /><a href="#">Minority Whip</a></li></ul>
<ul><li><h3>Committee Information</h3></li><li><b>Not a post</b><a href="#">Agriculture</a></li></ul>
</nav></body></html>"""
_MEMBER_DATA = b"""<?xml version="1.0" encoding="UTF-8"?><MemberData><members>
""" + b"".join(
    f"<member><member-info><bioguideID>{b}</bioguideID><official-name>{n}</official-name></member-info></member>".encode()
    for b, n in [("A1", "Ann Alder"), ("B1", "Ben Birch"), ("C1", "Cal Cedar"), ("D1", "Dee Q. Dogwood"),
                 ("E1", "Eve Elm"), ("F1", "Fay Fir"), ("G1", "Gus Gum")]
) + b"</members></MemberData>"


def test_the_clerks_posts_decide_who_holds_them():
    posts = hl.parse_clerk_leadership(_PAGE)
    assert ("Dee Q. Dogwood", "Republican Policy Committee Chair") in posts and len(posts) == 6
    # congress-legislators still names the previous holder of one post, and
    # has a title the Clerk doesn't list.
    roles = {"G1": "House Republican Policy Committee Chair", "B1": "House Majority Leader",
             "Z9": "Assistant House Minority Leader", "S1": "Senate Majority Leader"}
    out = hl.apply_clerk_leadership(roles, posts, hl.official_names(_MEMBER_DATA))
    assert out["D1"] == "House Republican Policy Committee Chair"
    assert "G1" not in out
    assert out["A1"] == "Speaker of the House" and out["E1"] == "House Minority Leader"
    assert out["Z9"] == "Assistant House Minority Leader" and out["S1"] == "Senate Majority Leader"


def test_a_page_that_does_not_read_whole_changes_nothing():
    roles = {"G1": "House Republican Policy Committee Chair"}
    names = hl.official_names(_MEMBER_DATA)
    posts = hl.parse_clerk_leadership(_PAGE)
    assert hl.apply_clerk_leadership(roles, posts[:3], names) == roles
    assert hl.apply_clerk_leadership(roles, posts + [("Nobody Known", "Majority Leader")], names) == roles
    assert hl.parse_clerk_leadership("") == []
