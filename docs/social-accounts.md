# Threads and Instagram accounts

How to get the four values that turn on posting to Threads and Instagram
(`THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`,
`INSTAGRAM_ACCESS_TOKEN`). What gets posted and when is in
[publishing.md](publishing.md); this page is only the one-time setup. Each
channel is off until both of its values are set, and while it is off nothing
is requested from Meta.

You do this once. After that the pipeline refreshes each token itself
(weekly, well inside its 60 days), keeps the current one in
`/data/social_tokens.json`, and raises an ops alert (`social-token-threads`,
`social-token-instagram`) if a refresh fails or a token is within a week of
expiring. You only come back here if that alert says to.

Checked against Meta's developer documentation on 2026-10-29 (links at each
step). Meta renames dashboard menus often; where a name below doesn't match
what you see, the linked page is the authority.

## 1. One Meta developer app

1. Sign in at <https://developers.facebook.com/apps> with the Facebook account
   that will own the app, and click **Create App**.
2. You can put both use cases in one app or make one app per platform. Meta's
   Instagram guide asks for a **Business** type app ("Other", then
   "Business"), so if you want one app, make that one.
   [Instagram: create a Meta app](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram)
3. Add the use cases:
   - **Access the Threads API** (the Threads use case).
     [Threads: get started](https://developers.facebook.com/docs/threads/get-started)
   - **Instagram**, **API setup with Instagram login** (the "Instagram API with
     Instagram Login").
4. A Threads app gets its own Threads app id and secret beside the Meta ones;
   use the Threads pair for anything Threads asks for.

You don't need App Review to post to your own accounts: Threads lets an
account with the **Threads Tester** role grant the permissions, and
Instagram's Standard Access covers professional accounts you own and have
added to the app. App Review and a published app are only needed to act for
other people's accounts.
([Threads: get started](https://developers.facebook.com/docs/threads/get-started),
[Instagram: Business Login, access levels](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login))

## 2. Threads

1. **Permissions.** In the Threads use case, add `threads_basic` (needed for
   everything) and `threads_content_publish` (needed to post).
2. **Tester.** In the App Dashboard, **App roles > Roles**, **Add People**,
   choose **Threads Tester** and add the Threads account Civitas will post as.
   Accept the invitation from that account: threads.com, **Settings >
   Account > Website permissions** (<https://www.threads.com/settings/account>).
3. **A token.** Generate a user token for the tester account with both
   permissions: the Threads use case's token generator for testers, or the
   authorization window (`https://threads.com/oauth/authorize?client_id=…
   &redirect_uri=…&scope=threads_basic,threads_content_publish&response_type=code`,
   then `POST https://graph.threads.com/oauth/access_token` with the code).
   [Get access tokens and permissions](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions)
4. **Make it long-lived.** A token from the authorization window lasts an
   hour. Paste it into the [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/);
   if it expires within hours, exchange it (from a shell, not a browser, since
   the request carries the app secret):

   ```bash
   curl -s "https://graph.threads.com/access_token?grant_type=th_exchange_token&client_secret=<THREADS_APP_SECRET>&access_token=<SHORT_LIVED_TOKEN>"
   ```

   The answer's `access_token` is good for 60 days (`expires_in` about
   5,184,000 seconds).
   [Long-lived access tokens](https://developers.facebook.com/docs/threads/get-started/long-lived-tokens)
5. **The user id.**

   ```bash
   curl -s "https://graph.threads.com/v1.0/me?fields=id,username&access_token=<LONG_LIVED_TOKEN>"
   ```

   `id` is `THREADS_USER_ID`; check `username` is the account you meant.
   [Profiles](https://developers.facebook.com/docs/threads/threads-profiles)

## 3. Instagram

1. **A Professional account.** The Instagram API with Instagram Login works
   with professional accounts only: in the Instagram app, **Settings > Account
   type and tools > Switch to professional account** (Business or Creator).
   Meta's setup page also asks for the test account to be public.
2. **Add the account.** In the app, **Instagram > API setup with Instagram
   login**, under **Generate access tokens**, **Add account** and log in as
   the Instagram account Civitas will post as (or add it under **App roles >
   Roles** as an Instagram tester and accept the invite in the Instagram app).
3. **Permissions.** The token needs `instagram_business_basic` and
   `instagram_business_content_publish`.
   [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing)
4. **A token.** Click **Generate token** beside the account. Tokens generated
   in the App Dashboard last 60 days, so this is already the long-lived token
   (check it in the [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/)).
   A token from the login flow instead lasts an hour; exchange it with
   `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=<INSTAGRAM_APP_SECRET>&access_token=<SHORT_LIVED_TOKEN>`.
   [Get started](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started),
   [Business Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login)
5. **The user id.**

   ```bash
   curl -s "https://graph.instagram.com/v26.0/me?fields=user_id,username&access_token=<LONG_LIVED_TOKEN>"
   ```

   `user_id` is `INSTAGRAM_USER_ID`.

## 4. Put them on the Pi

`.env` on the Pi is hand-edited (AGENTS.md, CI/CD). SSH in and add the four
lines to the deploy checkout's `.env`:

```
THREADS_USER_ID=<id from step 2.5>
THREADS_ACCESS_TOKEN=<long-lived token from step 2.4>
INSTAGRAM_USER_ID=<user_id from step 3.5>
INSTAGRAM_ACCESS_TOKEN=<token from step 3.4>
```

Then redeploy (`./check-and-deploy.sh`, or wait for the next cron tick) so
the pipeline service reads them. Either pair can be left empty to keep that
channel off. Don't paste the tokens anywhere else: the pipeline never logs
them, and the copy it refreshes lives only in `/data/social_tokens.json`
(readable by its owner only).

The next post that goes out adopts the tokens. The first refresh is a day
later (Meta refuses to refresh a token under a day old), and weekly after
that. If you ever need a new token (an alert says refresh failed and keeps
failing, or the token was revoked), repeat step 2.3-2.4 or 3.4 and replace the
line in `.env`: a changed value replaces the kept one at the next send.

## What Meta needs from the site

- Meta fetches each picture itself, from a public URL, and Instagram takes
  only JPEG. The site's cards are PNG, so Civitas gives Meta the post's JPEG
  copy at `https://<site>/api/cards/posts/<id>.jpg`. That address must be
  reachable from the internet (it is, through nginx and Cloudflare, like the
  rest of `/api/`).
- Instagram allows 100 API posts per account in 24 hours; Civitas posts a few
  a day at most, and only the posts that carry a picture.
  ([Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing))
