"""Instagram (Instagram API with Instagram Login, graph.instagram.com): a
stored post as an image post.

Instagram publishes nothing without media, so only a post that carries its
own picture (image_url with alt_text: the trend posts' record cards) goes;
every other post is skipped, recorded as such, never failed. Meta takes
only JPEG, fetched by Meta from a public URL, so the picture is the post's
JPEG copy (GET /api/cards/posts/<id>.jpg, converted from the card's PNG;
broadcast passes its URL in). The caption is the post's text and its link
as text (a caption's links are not clickable, but the address is still
the way to the record), within Instagram's 2,200 characters; the alt text
goes in Instagram's own `alt_text`.
Reference: developers.facebook.com/docs/instagram-platform/content-publishing,
developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media.
"""

from app.models import BroadcastPost
from app.social import graph
from app.social.threads import compose

PLATFORM = graph.Platform(
    name="instagram", label="Instagram", api="https://graph.instagram.com/v26.0",
    refresh_url="https://graph.instagram.com/refresh_access_token", refresh_grant="ig_refresh_token",
    create_edge="media", publish_edge="media_publish", status_field="status_code",
    user_setting="INSTAGRAM_USER_ID", token_setting="INSTAGRAM_ACCESS_TOKEN",
)
NAME = PLATFORM.name
MAX_CAPTION_CHARS = 2200
MAX_ALT_CHARS = 1000


def configured() -> bool:
    return PLATFORM.configured()


def keep_token_fresh() -> None:
    graph.keep_token_fresh(PLATFORM)


def params(post: BroadcastPost, image_url: str | None) -> dict:
    if not image_url or not post.alt_text:
        raise graph.Skip("no picture to post (Instagram needs one)")
    return {"image_url": image_url, "caption": compose(post.text, post.url, MAX_CAPTION_CHARS),
            "alt_text": post.alt_text[:MAX_ALT_CHARS]}


def send(post: BroadcastPost, image_url: str | None) -> str:
    """Publish the post; the Instagram media id. graph.Skip without a picture."""
    return graph.publish(PLATFORM, params(post, image_url))
