"""Threads (Threads API, graph.threads.com): a stored post as a thread.

Text and link, fitted to Threads' 500-character limit the way the Bluesky
path fits its 300 (the text cut on a sentence or word boundary, the link
kept whole). Threads counts a character outside ASCII by its UTF-8 bytes,
so the fit is measured in bytes. A post with a picture goes as an image
post with its alt text; one without as a text post whose link Threads
shows as a preview (`link_attachment`).
Reference: developers.facebook.com/docs/threads/posts,
developers.facebook.com/docs/threads/reference/publishing.
"""

from app.models import BroadcastPost
from app.pipeline.analyze.bluesky_utils import truncate_on_boundary
from app.social import graph

PLATFORM = graph.Platform(
    name="threads", label="Threads", api="https://graph.threads.com/v1.0",
    refresh_url="https://graph.threads.com/refresh_access_token", refresh_grant="th_refresh_token",
    create_edge="threads", publish_edge="threads_publish", status_field="status",
    user_setting="THREADS_USER_ID", token_setting="THREADS_ACCESS_TOKEN",
)
NAME = PLATFORM.name
MAX_CHARS = 500
MAX_ALT_CHARS = 1000


def configured() -> bool:
    return PLATFORM.configured()


def keep_token_fresh() -> None:
    graph.keep_token_fresh(PLATFORM)


def compose(text: str, url: str, limit: int = MAX_CHARS) -> str:
    """`text` then `url`, at most `limit` UTF-8 bytes, the URL whole."""
    full = f"{text} {url}"
    if len(full.encode()) <= limit:
        return full
    head = text.encode()[: limit - len(url.encode()) - 1].decode("utf-8", "ignore")
    return f"{truncate_on_boundary(head, len(head))} {url}"


def params(post: BroadcastPost, image_url: str | None) -> dict:
    body = compose(post.text, post.url)
    if image_url and post.alt_text:
        return {"media_type": "IMAGE", "image_url": image_url, "text": body,
                "alt_text": post.alt_text[:MAX_ALT_CHARS]}
    return {"media_type": "TEXT", "text": body, "link_attachment": post.url}


def send(post: BroadcastPost, image_url: str | None) -> str:
    """Publish the post; the thread's id."""
    return graph.publish(PLATFORM, params(post, image_url))
