"""Meta's publishing API, as Threads and Instagram share it.

A post is two calls and a wait: create a media container
(`POST /{user-id}/threads` or `/media`), poll the container until Meta
says it is FINISHED (it fetches the image itself, from the URL given), then
publish it (`/threads_publish`, `/media_publish`). Meta's own guidance is
to check a container's status once a minute for no more than five minutes
(developers.facebook.com/docs/threads/troubleshooting,
.../docs/instagram-platform/content-publishing), so that is the bound here.

Every request goes through make_async_client, so it names and signs
Civitas and keeps app/crawl_policy.py's manners like any other request.
A refusal raises SourceUnavailable (an ExpectedSourceFailure): one line,
the platform's own error type and message, never a token.

Tokens. A long-lived token lasts 60 days and can be refreshed once it is a
day old, each refresh good for 60 days from then. The owner sets one in
.env once; the first send adopts it, and from then the token in use is the
one in /data/social_tokens.json, refreshed weekly (REFRESH_EVERY), a
failed refresh retried daily. A failed refresh, or a token within
EXPIRY_WARNING of expiring, raises an ops alert (`social-token-<platform>`),
closed by the next good refresh. A new token pasted into .env replaces the
kept one (its hash differs from the one recorded when the kept one was
adopted). Nothing here logs a token, and the file holds only the tokens
and their dates.
"""

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from app.atomic_write import runtime_data_path, update_json_file
from app.config import settings
from app.error_utils import SourceUnavailable, exception_summary
from app.http_client import make_async_client
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

TOKENS_FILE = "social_tokens.json"
# Well inside the token's 60 days, so a run of failed refreshes has weeks
# to be noticed and fixed before posting stops.
REFRESH_EVERY = timedelta(days=7)
# Meta refuses to refresh a token under a day old; a failed refresh is
# also tried again no sooner than this.
RETRY_EVERY = timedelta(days=1)
TOKEN_LIFETIME = timedelta(days=60)
EXPIRY_WARNING = timedelta(days=7)
# Container status polling (module docstring).
POLL_INTERVAL_S = 60.0
POLL_TIMEOUT_S = 300.0
REQUEST_TIMEOUT_S = 60.0
# Set only by tests: an httpx transport that answers in place of Meta.
TRANSPORT: httpx.AsyncBaseTransport | None = None


@dataclass(frozen=True)
class Platform:
    name: str            # "threads" | "instagram": the delivery row's channel
    label: str           # for alerts and logs
    api: str             # versioned API root
    refresh_url: str
    refresh_grant: str
    create_edge: str     # POST {api}/{user}/{create_edge} makes a container
    publish_edge: str
    status_field: str    # the container's status field
    user_setting: str
    token_setting: str

    @property
    def host(self) -> str:
        return httpx.URL(self.api).host

    def user_id(self) -> str:
        return getattr(settings, self.user_setting, "").strip()

    def env_token(self) -> str:
        return getattr(settings, self.token_setting, "").strip()

    def configured(self) -> bool:
        return bool(self.user_id() and self.env_token())


class Skip(Exception):
    """The channel can't carry this post (Instagram and a post with no
    picture): not a failure, nothing to retry."""


def _path() -> str:
    return runtime_data_path(TOKENS_FILE)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _update(platform: Platform, change) -> dict:
    """Apply `change(entry) -> entry` to the platform's entry under the
    file's lock, adopting the .env token first when it is new. Returns the
    entry written."""
    env = platform.env_token()
    written: dict = {}

    def apply(doc: dict) -> dict:
        entry = doc.get(platform.name) or {}
        if entry.get("env_sha256") != _sha(env):
            now = _iso(utcnow())
            entry = {"token": env, "env_sha256": _sha(env), "adopted_at": now, "last_try_at": now,
                     "refreshed_at": None, "expires_at": None, "last_error": None}
        entry = change(dict(entry))
        doc[platform.name] = entry
        written.update(entry)
        return doc

    update_json_file(_path(), apply, indent=2)
    try:
        os.chmod(_path(), 0o600)
    except OSError:
        pass
    return written


def token(platform: Platform) -> str:
    """The token in use: the kept one, or the .env one when it is new."""
    return _update(platform, lambda e: e)["token"]


def _error_line(platform: Platform, resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    parts = [str(err[k]) for k in ("type", "code", "error_subcode") if err.get(k) not in (None, "")]
    message = " ".join(str(err.get("error_user_msg") or err.get("message") or "").split())[:200]
    return f"{platform.label} answered HTTP {resp.status_code}" + (
        f" ({', '.join(parts)})" if parts else "") + (f": {message}" if message else "")


async def _call(client: httpx.AsyncClient, platform: Platform, method: str, url: str, params: dict) -> dict:
    """One API call with the token as a parameter (in the body of a POST,
    so it is never in a URL), its JSON answer, or SourceUnavailable."""
    args = {"access_token": token(platform), **params}
    try:
        if method == "POST":
            resp = await client.post(url, data=args)
        else:
            resp = await client.get(url, params=args)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{platform.label} unreachable ({type(exc).__name__})",
                                host=platform.host) from None
    if resp.status_code != 200:
        kind = "refused" if resp.status_code < 500 else "unavailable"
        raise SourceUnavailable(_error_line(platform, resp), host=platform.host, kind=kind)
    try:
        return resp.json()
    except ValueError:
        raise SourceUnavailable(f"{platform.label} answered with no JSON", host=platform.host) from None


def _client() -> httpx.AsyncClient:
    return make_async_client(timeout=REQUEST_TIMEOUT_S, transport=TRANSPORT)


async def _wait_finished(client: httpx.AsyncClient, platform: Platform, container: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + POLL_TIMEOUT_S
    while True:
        fields = f"{platform.status_field},error_message" if platform.name == "threads" else platform.status_field
        body = await _call(client, platform, "GET", f"{platform.api}/{container}", {"fields": fields})
        status = body.get(platform.status_field)
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            why = body.get("error_message") or status
            raise SourceUnavailable(f"{platform.label} could not prepare the post ({why})",
                                    host=platform.host, kind="refused")
        if loop.time() + POLL_INTERVAL_S > deadline:
            raise SourceUnavailable(f"{platform.label} post not ready after {POLL_TIMEOUT_S:g} s (status {status})",
                                    host=platform.host)
        await asyncio.sleep(POLL_INTERVAL_S)


async def _publish(platform: Platform, params: dict) -> str:
    user = platform.user_id()
    async with _client() as client:
        made = await _call(client, platform, "POST", f"{platform.api}/{user}/{platform.create_edge}", params)
        container = str(made.get("id") or "")
        if not container:
            raise SourceUnavailable(f"{platform.label} made no container", host=platform.host)
        await _wait_finished(client, platform, container)
        done = await _call(client, platform, "POST", f"{platform.api}/{user}/{platform.publish_edge}",
                           {"creation_id": container})
    media = str(done.get("id") or "")
    if not media:
        raise SourceUnavailable(f"{platform.label} published nothing", host=platform.host)
    return media


def publish(platform: Platform, params: dict) -> str:
    """Create, wait for and publish one container; the published post's id.
    Run from a thread with no event loop (broadcast's sender thread)."""
    return asyncio.run(_publish(platform, params))


# ---- token refresh -----------------------------------------------------------

async def _refresh(platform: Platform, current: str) -> dict:
    async with _client() as client:
        return await _call(client, platform, "GET", platform.refresh_url,
                           {"grant_type": platform.refresh_grant, "access_token": current})


def keep_token_fresh(platform: Platform) -> None:
    """Refresh the platform's token when due (module docstring), and alert
    on a failed refresh or a token near expiry. Never raises."""
    if not platform.configured():
        return
    try:
        entry = _update(platform, lambda e: e)
        now = utcnow()
        refreshed = _when(entry.get("refreshed_at"))
        due = (now - _when(entry["last_try_at"]) >= RETRY_EVERY
               and (refreshed is None or now - refreshed >= REFRESH_EVERY))
        if due:
            _update(platform, lambda e: {**e, "last_try_at": _iso(now)})
            try:
                body = asyncio.run(_refresh(platform, entry["token"]))
                new = str(body.get("access_token") or "")
                if not new:
                    raise SourceUnavailable(f"{platform.label} refresh returned no token", host=platform.host)
                life = timedelta(seconds=int(body.get("expires_in") or TOKEN_LIFETIME.total_seconds()))
                entry = _update(platform, lambda e: {**e, "token": new, "refreshed_at": _iso(now),
                                                     "expires_at": _iso(now + life), "last_error": None})
                logger.info("%s token refreshed; good until %s", platform.label, entry["expires_at"])
                resolve_ops_alert(f"social-token-{platform.name}")
            except Exception as exc:
                line = exception_summary(exc)
                _update(platform, lambda e: {**e, "last_error": line})
                logger.warning("%s token refresh failed: %s", platform.label, line)
                send_ops_alert(
                    f"{platform.label} token refresh failed",
                    f"{line}. Tried again in a day. If it keeps failing, generate a new long-lived token "
                    f"and set {platform.token_setting} in .env (docs/social-accounts.md).",
                    dedupe_key=f"social-token-{platform.name}-failed-{now.date().isoformat()}",
                    condition=f"social-token-{platform.name}",
                )
                entry = _update(platform, lambda e: e)
        expires = _when(entry.get("expires_at")) or (_when(entry["adopted_at"]) + TOKEN_LIFETIME
                                                    if not entry.get("refreshed_at") else None)
        if expires is not None and expires - now <= EXPIRY_WARNING:
            send_ops_alert(
                f"{platform.label} token expires {expires.date().isoformat()}",
                f"Posts to {platform.label} stop when it does. Set a new long-lived token as "
                f"{platform.token_setting} in .env (docs/social-accounts.md).",
                dedupe_key=f"social-token-{platform.name}-expiring-{expires.date().isoformat()}",
                condition=f"social-token-{platform.name}",
            )
    except Exception:
        logger.exception("%s token upkeep failed", platform.label)
