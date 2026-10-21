# Standing up a Civitas server

This guide takes a blank machine to a running copy of Civitas: the same
stack, the same nightly pipeline and the same pages as the production
site. It is written for two readers: the maintainers, if the production
Raspberry Pi is ever lost, and anyone who wants to run the open source
code themselves.

Everything here was followed step by step on a fresh clone (October 2026,
on an amd64 Linux workstation with Docker 29 and Compose v5). Figures
marked **measured** come from that run or from the repository's own run
history; figures marked **estimate** are not measured and say so.

Contents:

1. [What you need](#1-what-you-need)
2. [Accounts and keys](#2-accounts-and-keys)
3. [The LLM](#3-the-llm)
4. [Two ways to run](#4-two-ways-to-run)
5. [First data load](#5-first-data-load)
6. [Optional: a public domain, Cloudflare and phone alerts](#6-optional-a-public-domain-cloudflare-and-phone-alerts)
7. [Recovering from a lost server](#7-recovering-from-a-lost-server)
8. [Updating, logs and troubleshooting](#8-updating-logs-and-troubleshooting)

---

## 1. What you need

### Hardware

Production is one **Raspberry Pi 5 with 16 GB of memory and an NVMe SSD**.
Any 64-bit Linux machine of that size or larger works.

- **Memory: 16 GB.** Measured on production on 2026-10-14
  (`backend/app/scheduler.py`, `_hourly_action_refresh`): the pipeline
  process peaked at 3.5 GB over a day that held the nightly chain and the
  hourly refreshes, and the llama-server, API and frontend processes used
  5.7 GB more. The compose files cap the containers at 2 GB (llama-server),
  4 GB (backend), 4 GB (pipeline, Swarm only) and 512 MB (frontend). Idle
  after start, the measured use on the test machine was about 780 MB for
  llama-server, 610 MB for the backend and 60 MB for the frontend. 8 GB is
  **not** enough for a full nightly run (estimate, from the figures above).
- **Disk: an SSD, with at least 30 GB free before the first data load.**
  Measured sizes of the built images on amd64: backend 4.3 GB (it carries
  CPU-only PyTorch and a Chromium for page rendering), llama-server 1.2 GB,
  frontend 460 MB, nginx 93 MB, plus the 731 MB model file. The build cache
  adds about 5 GB, and `check-and-deploy.sh` keeps up to 15 GB of build
  cache between deploys. The database grows with every night's records;
  its production size is not recorded in the repository (estimate: budget
  tens of GB). An SD card is too slow and wears out under SQLite's writes;
  use NVMe or another SSD.
- **Network:** outbound HTTPS to the government APIs, news feeds and
  Hugging Face. Inbound only if you publish the site (section 6).

### Operating system and software

- A 64-bit Linux. Production runs Raspberry Pi OS (Debian based); any
  distribution with a current Docker works.
- **Docker Engine** and the **Compose v2 plugin** (`docker compose`, not the
  old `docker-compose`). Install from Docker's own instructions for your
  distribution, then let your user run Docker without sudo:

  ```bash
  sudo usermod -aG docker "$USER"   # log out and back in afterwards
  docker version
  docker compose version
  ```

- **git**, **curl** and **python3** on the host (the deploy script uses all
  three).
- For the production path only: the **GitHub CLI** (`gh`), used by the
  deploy poller to refuse a commit whose CI failed.

### arm64 and amd64

Both work. The images are **built on the machine that runs them**, never
pulled from a registry, and that is deliberate. In July 2026 images built on
GitHub's arm64 runners crashed on the Pi with an illegal-instruction error
(exit 132): a dependency compiled from source on the build machine had baked
in that CPU's instructions, which the Pi's Cortex-A76 lacks. Building on the
host itself can't produce that mismatch, so do the same (AGENTS.md, "CI/CD").
The one image that is pulled, `ghcr.io/ggml-org/llama.cpp:server`, is
published by the llama.cpp project for generic arm64 and amd64 and is pinned
by digest in `docker-compose.yml`. Python packages install from wheels only
(`--only-binary=:all:`), so a package with no wheel for your architecture
fails the build instead of compiling something unsafe.

---

## 2. Accounts and keys

`.env.example` in the repository root is the source of truth for every
setting; this section says what each one is for, where to get it, and what
happens without it. Copy it first:

```bash
cp .env.example .env
chmod 600 .env
```

Never commit `.env`. On production it is a hand-edited file on the Pi and
nothing syncs it; keep your own copy of the values somewhere safe (a
password manager), since a lost server takes its `.env` with it.

### Required

| Variable | Where to get it | Without it |
|---|---|---|
| `DATA_GOV_API_KEY` | Free at <https://api.data.gov/signup/>. One key covers Congress.gov, the FEC, GovInfo and Regulations.gov. | Nothing loads. Congress.gov answers 403 and the Senate run stops at the member list (measured: `Pipeline failed: the current-member list`); GovInfo answers 401. |
| `CONTACT_EMAIL` | An address you read. It is named in the User-Agent of every request the server sends, so a source can reach you about the traffic. | Requests carry no contact, SEC fetches are refused with an ops alert (stock-trade and PAC-sponsor industries stop refreshing), and the pipeline warns at start. |
| `ADMIN_TOKEN` | Make one: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` | The admin panel and admin API answer 503. |
| `PIPELINE_TRIGGER_TOKEN` | Make another the same way. | The token-protected trigger routes (`/api/pipeline/trigger` and friends, reachable from the internet through nginx) answer 503. |

The template ships both tokens as `change-me-to-a-random-token`. That value
is public, so the server treats it as **no token at all** and refuses it
(`backend/app/api/auth.py`, `EXAMPLE_TOKEN`). Replace both.

### Optional

| Variable | What it is for | Without it |
|---|---|---|
| `BLS_API_KEY` | Free at <https://data.bls.gov/registrationEngine/>. Raises the Bureau of Labor Statistics limit from 25 to 500 requests a day. | Jobs data for the president pages still loads, under the lower limit. |
| `GOOGLE_CIVIC_API_KEY` | Free in the Google Cloud console (Civic Information API). Town-level sample ballots on a few state pages, queried only with fixed public building addresses. | The town selector doesn't appear; the statewide pages are unaffected. |
| `LDA_API_KEY` | Free at <https://lda.gov/api/register/>. Raises the lobbying registry's anonymous limit (about 15 requests a minute). | Lobbying lookups run anonymously and slower. |
| `BSKY_HANDLE`, `BSKY_APP_PASSWORD` | A Bluesky account and an App Password (bsky.app, Settings, Privacy and Security, App Passwords). | Posts go to the site's Atom feeds only. **Leave these empty** unless the account is yours to post from. |
| `FEEDBACK_TOKEN`, `GITHUB_FEEDBACK_REPO` | A fine-grained GitHub token with Issues read and write on one repository. Point `GITHUB_FEEDBACK_REPO` at **your own** repository; the default is the upstream project's. | `/feedback` answers 503 rather than dropping submissions. |
| `ALERT_NTFY_URL` | An ntfy topic URL (section 6). | Alerts are still logged and listed on the admin Overview; nothing reaches a phone. |
| `SITE_URL` | Your site's public address, if not the production one (section 6). | Every link, feed, share card and canonical tag names the production site. |
| `REAL_IP_FROM`, `REAL_IP_HEADER` | The proxy in front of nginx, Swarm only (section 6). **Required behind a reverse proxy.** | Only loopback is trusted: behind a proxy, every visitor counts as the proxy's address. |
| `CORS_ORIGINS` | Only if a browser on another origin calls the API. | Leave empty when the site and API share an origin behind nginx (the normal case). |

### Leave at their defaults

- `LLM_BACKEND`, `LLAMA_SERVER_URL`: `docker-compose.yml` sets them for the
  backend anyway, so `.env` doesn't change them.
- `LLAMA_MODELS_DIR`, `LLAMA_MODEL_FILE`: where the model file is (section 3).
- `OLLAMA_*`: only for `LLM_BACKEND=ollama`, which the stack doesn't run.
- `PIPELINE_CACHE_TTL_HOURS`, `PIPELINE_LOG_LEVEL`, the `*_RPS` rate limits,
  `PIPELINE_OVERRUN_ALERT_HOURS`, `PIPELINE_STALE_ALERT_DAYS`.
- `PIPELINE_CRON_SCHEDULE`: `0 3 * * *`, 03:00 UTC nightly.
- `DATABASE_URL`: the compose files set `sqlite:////data/civitas.db`, on the
  data volume.
- `PROCESS_ROLE`: leave unset. The Swarm overlay sets it per service.
- **`CURRENT_CONGRESS`: never set it.** It follows the clock. A value here
  pins both the scored windows and the House district lines to one Congress
  for good.

---

## 3. The LLM

The language model is **LFM2.5-1.2B-Instruct** (Liquid AI), quantized to
Q4_K_M, served by the official llama.cpp server image
(`ghcr.io/ggml-org/llama.cpp:server`, pinned by digest in
`docker-compose.yml`). The image is pulled; the model file is not in the
repository or the image, so download it once:

```bash
mkdir -p llama-models
curl -fL -o llama-models/lfm2.5-1.2b-instruct-q4_k_m.gguf \
  https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-Q4_K_M.gguf
ls -l llama-models   # about 731 MB
```

The file is published under Liquid AI's own LFM Open License (read it on the
model page before running a public site). The filename on disk must match
`LLAMA_MODEL_FILE` in `.env`, and the folder must match `LLAMA_MODELS_DIR`
(default `./llama-models`, relative to the repository). The container mounts
that folder read-only at `/models`.

Two small embedding models (Snowflake Arctic-XS and all-MiniLM-L6-v2, about
22 million parameters each) are separate: sentence-transformers downloads
them from Hugging Face the first time the backend needs them, into the
container's own cache, so they are fetched again whenever the container is
recreated. Nothing to do by hand; the host only needs to reach
huggingface.co.

**What runs without the LLM.** Scoring never uses it: the Senate, House,
stock-trade, election and Supreme Court runs make no model call. If
llama-server is down or the model file is missing, each caller degrades on
its own: the Action Center publishes no issue from a news cluster it can't
read (never an unverified one), national monitors fall back to a headline
for their name, and on-request Explore summaries are unavailable. Every page
built from official records still fills in.

---

## 4. Two ways to run

Clone the repository first (HTTPS needs no GitHub account):

```bash
git clone https://github.com/kamoras/civitas.git
cd civitas
```

Then fill in `.env` (section 2) and download the model (section 3).

### 4a. A quick local trial with Docker Compose

One machine, one backend process doing both the website API and the
pipeline (`PROCESS_ROLE=all`), no nginx. Good for trying it out and for
development.

1. Check you have the memory to build (one image at a time is gentler):

   ```bash
   free -h
   docker compose build backend
   docker compose build frontend
   ```

   Each build took a few minutes on the test workstation, a fast amd64
   machine on a fast connection (measured); expect much longer on a Pi
   (estimate). Most of the backend's time is downloading PyTorch and a
   Chromium.

2. Start everything:

   ```bash
   docker compose up -d
   docker compose ps   # wait for all three to say (healthy), about 30 s
   ```

3. Check it answers:

   ```bash
   curl -s http://127.0.0.1:8000/api/health
   # {"status":"ok","database":"ok","ollama":"ok","lastPipelineRun":null}
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/
   # 200
   ```

   (`ollama` is a historical key name; it reports llama-server.)

4. Open <http://localhost:3000>. The API docs are at
   <http://localhost:8000/docs>. All three ports are bound to `127.0.0.1`
   only; to reach the trial from another machine, use the production path
   below or your own reverse proxy.

Notes:

- Containers are named from the project (`civitas-backend-1` and so on),
  and the host ports are 8000, 3000 and 8070. To run a second copy beside
  another, give it a project name (`docker compose -p civitas-trial ...`)
  and an override file with different `ports: !override` lists.
- The data lives in the volume `civitas_app_data` (Compose names it from
  the project name `civitas`). `docker compose down` keeps it;
  `docker compose down -v` deletes it.
- On start, the backend begins filling an empty database by itself: it runs
  the Explore document ingest once, and the hourly and nightly schedules
  are live (section 5).

### 4b. Production with Docker Swarm

This is how the production site runs: a single-node Docker Swarm with five
services. nginx is the only one published to the host (port 8081);
`backend` (the read-only API, two workers), `pipeline` (the scheduler, every
pipeline run and the admin API), `frontend` and `llama-server` are reachable
only inside the overlay network. Swarm gives zero-downtime rolling updates
and rolls a release back if its health check fails. See the header comment
of `docker-compose.swarm.yml` for why each setting is what it is.

Do this in a checkout that stays put (production uses the deploy user's home
directory), with `.env` and `llama-models/` filled in as above. If a reverse
proxy (a host-level nginx, Caddy, a tunnel) will sit in front of port 8081,
set `REAL_IP_FROM` to its address now (section 6), or every visitor counts
as that one address.

1. **Turn on Swarm mode** (once per host):

   ```bash
   docker swarm init
   # on a host with several addresses: docker swarm init --advertise-addr <LAN address>
   ```

2. **Create the data volume.** The stack declares it `external`, so it must
   exist under exactly this name, and no stack deploy will ever recreate or
   delete it:

   ```bash
   docker volume create civitas_app_data
   ```

   If you ran the Compose trial on the same host, it already exists and
   holds that trial's data; the stack will use it.

3. **Authenticate `gh`** for the user that will run the deploy poller, so
   it can read your repository's CI results:

   ```bash
   gh auth login
   gh run list --workflow CI --limit 1   # should print the latest CI run
   ```

   Without `gh`, or with `gh` signed out, the poller skips the CI check and
   deploys anyway. For your own fork, the check reads the fork's CI.

4. **First deploy.** Run the deploy script by hand:

   ```bash
   ./check-and-deploy.sh
   tail -n 40 deploy-poll.log
   ```

   On a host with no `civitas` stack and no `.last-deployed-sha` marker it
   builds the backend, frontend and nginx images (tagged
   `sha-<short commit>`), deploys the stack, waits for each service to roll
   out, and writes the marker. Building on a Pi takes a while; the script
   logs to `deploy-poll.log` in the checkout, not to the terminal.

   The same deploy without the script, if you want to see each step:

   ```bash
   export IMAGE_TAG="sha-$(git rev-parse --short=7 HEAD)"
   docker compose -f docker-compose.yml -f docker-compose.swarm.yml build backend frontend nginx
   docker compose -f docker-compose.yml -f docker-compose.swarm.yml config \
     | grep -v '^name:' \
     | sed -E 's/published: "([0-9]+)"/published: \1/' > /tmp/civitas-stack.yml
   docker stack deploy -c /tmp/civitas-stack.yml civitas --prune
   ```

   Don't pass the two compose files to `docker stack deploy` directly
   (`docker stack deploy -c docker-compose.yml -c docker-compose.swarm.yml
   civitas`): its own merge ignores the `!reset` tags that take the host
   ports off backend, frontend and llama-server, and it rejects the
   top-level `name:` key ("Additional property name is not allowed").
   `docker compose config` resolves both, which is why the script does it.

5. **Check it:**

   ```bash
   docker stack services civitas        # every service 1/1
   curl -s http://localhost:8081/api/health
   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081/
   ```

6. **Install the poller** so every new commit on `main` deploys itself
   within five minutes. As the deploy user, `crontab -e`, and add (with
   your checkout's path):

   ```bash
   */5 * * * * /home/<you>/civitas/check-and-deploy.sh
   ```

   It polls `origin/main`, refuses a commit whose CI failed (override once
   with `FORCE_DEPLOY=1 ./check-and-deploy.sh`), waits while any pipeline,
   chain or Action Center refresh is running (a deploy restarts the
   pipeline service and would kill it), and pushes a failure to
   `ALERT_NTFY_URL`. After a good deploy it removes the unused images of the
   stack's own repositories (`civitas-backend`, `civitas-frontend`,
   `civitas-nginx` and the llama.cpp server image) and trims the build cache
   to 15 GB; other projects' images on the host are left alone, and so are
   the base images (`python`, `node`, `nginx`) the builds start from.

The admin panel and `/api/admin/` answer only requests from private
addresses (127.0.0.1, 10/8, 172.16/12, 192.168/16; `nginx/civitas.conf`)
and a 404 to everyone else. From the host itself, `http://localhost:8081`
qualifies.

---

## 5. First data load

### What an empty database shows

Every page loads; the frame is there and the data isn't. The homepage says
the Senate's and House's next meetings are "not on record yet", member and
bill lists are empty, and the run-status band has no runs to report.
Things that ship in the repository show at once, such as the voter
registration deadlines ("Registration closes in the next 10 days in 10
states" on the test run) and the bundled reference data.
`/api/health` reports `"lastPipelineRun": null`.

### Trigger the first chain

The nightly chain starts by itself at 03:00 UTC. To start it now, send the
admin token (it is read from `.env` here, so it never appears on screen):

```bash
ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2-)
# Compose trial: the backend is on 8000. Swarm: use http://localhost:8081.
curl -s -X POST http://127.0.0.1:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# {"message":"Pipeline triggered","senatorFilter":null,"fetchOnly":false}

curl -s http://127.0.0.1:8000/api/admin/pipeline/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

The admin panel at `/admin` (enter the same token) shows the same status
with each step's progress. A cheap first test before committing to a full
run is `?fetch_only=true` (the Senate pipeline's fetch phase alone); with a
wrong `DATA_GOV_API_KEY` it fails within seconds on Congress.gov's 403,
which is a quick way to check the key.

A full trigger runs the same five-link chain as the nightly job. If a chain
is already running, the trigger answers 409 and the nightly run leaves the
night to it.

### The order, and how long it takes

The chain runs one link at a time: **Senate, Supplementary, House, Stock
trades, Election.** Each link reads what the database already holds, so one
that fails is alerted and the next still runs.

| Link | What it fills | Steady night (measured) | First night on an empty database (estimate) |
|---|---|---|---|
| Senate | Senators, their bills, votes, FEC money, scores | Not recorded separately | Several hours: every current-Congress bill and every senator's FEC record is fetched cold |
| Supplementary | Explore documents, the Supreme Court, presidents, district lean | Weekly Court-refresh day: over 5 hours (run 69, an uncached Oyez crawl) | Several hours |
| House | Representatives, their bills, votes, money, scores | About 110 minutes (runs in September 2026) | 6 hours or more: the sponsorship step's cache warm-up of about 19,000 bills alone took 6 hours on 2026-10-16 |
| Stock trades | STOCK Act trades, annual holdings, member photos | Under 90 minutes | Longer; the holdings phases are time-boxed and spread over a few nights by design |
| Election | Candidates, FEC totals, ballot measures, race coverage | Candidate money is refreshed 500 a night, about 33 minutes at the FEC's rate limit | The whole candidate list takes several nights to cycle through |

The whole chain measured 13 hours on 2026-10-14. Most of the time is spent
waiting on rate limits (Congress.gov 1.2 requests a second, the FEC 0.25),
not computing, so request count is run time. Expect the **first chain to
take one to two days** (estimate), with the nightly runs that follow
catching up the rest.

### What the scheduled jobs fill around it

These run on their own in the pipeline process (the backend in the Compose
trial), with no trigger:

| Job | When (UTC) | Fills |
|---|---|---|
| Explore bootstrap | Once at start, if no documents | The first Explore documents (Federal Register, Congressional Record) |
| Action Center refresh | Hourly at :15 | Today's issues from news feeds (needs the LLM); the first run reads a 48-hour window |
| Congress activity | :10 and :40 | Floor days, roll calls, the Congress reports |
| Bill refresh | Hourly at :45 | Bills that changed since the last pass |
| Site search index | Every 10 minutes | The header search and `/search` |
| Agency sync | Tried hourly, reads weekly | Federal agencies and their budgets |
| Nominations | Daily 14:50 | Presidential nominations; the first backfill of a Congress took about **eight days** (2,285 citations in the 119th, measured) |
| Data refresh | Daily 15:40 | Regenerates the bundled data files on their own cadences |
| Election ballot sync, coverage, results | Every 6 hours, every 15 minutes, every 5 minutes near an election | Ballots, race coverage, election-night counts |
| Election map | Every 3 hours before an election | The Senate and House map |

### When it is fully populated

- **Within the first hour:** agencies, site search and (with the LLM up) the
  first Action Center issues.
- **After the first chain (one to two days, estimate):** every member page,
  bill, vote and score, the Court and the presidents.
- **After about a week (estimate):** nominations (about eight days,
  measured), annual holdings, and the full candidate money cycle.
- **Only over time, never at once:** anything that is a history rather than
  a record: score trends, the Action Center timeline and monitors, the
  election archive (section 7).

---

## 6. Optional: a public domain, Cloudflare and phone alerts

### A public domain

nginx in the stack listens on **plain HTTP on port 8081**. Put something in
front of it that terminates TLS: Cloudflare (production), Caddy, or a
host-level nginx. Production has a host-level nginx (outside this
repository) that takes Cloudflare's connection on port 80 and proxies to
8081.

Two `.env` settings describe your domain and your network; no file needs
editing:

1. **`SITE_URL`**: your site's public address, for example
   `SITE_URL=https://civic.example.org` (no trailing slash). Every link the
   site writes uses it: canonical tags, share images, feeds, the sitemap and
   `robots.txt`, the public API's `siteUrl`, posts. Unset, it is the
   production site. The backend reads it at start; the frontend has it
   baked in at build (Compose passes it as `NEXT_PUBLIC_SITE_URL`), so
   rebuild the frontend after changing it. The `www.` name redirects to the
   bare domain whatever it is. The site's Bluesky link names your domain as
   the handle (Bluesky accepts a domain as a handle). Measured on the test
   run with `SITE_URL=https://civic.example.net`: `robots.txt`, the
   homepage and the Atom feed named only that address, and nothing named
   the production domain.
2. **`REAL_IP_FROM`**: which proxy in front of nginx may tell it the
   visitor's address (in `X-Real-IP`, or `REAL_IP_HEADER`). Unset, nginx
   trusts loopback only. **A host behind a reverse proxy must set
   `REAL_IP_FROM` to that proxy's address**, as nginx sees it. Even a proxy
   on the same machine usually counts: under Swarm, a connection to the
   published port 8081 arrives from the host's LAN address or a Docker
   gateway address (172.x), not from 127.0.0.1. Production's host nginx
   proxies to the Pi's LAN address, so production's `.env` has
   `REAL_IP_FROM=192.168.0.101`. To find yours, send one request through
   the proxy and read the first field of the line
   `docker service logs civitas_nginx` prints for it. Several addresses or
   CIDRs can be listed, separated by spaces. If Cloudflare connects to
   nginx directly, set its published ranges and
   `REAL_IP_HEADER=CF-Connecting-IP`. With nothing in front, leave it unset
   (or `none`). If it is missing or names the wrong hop, every visitor
   looks like one address and the daily visitor count collapses.
   `nginx/real-ip.sh` writes the setting into nginx's config at every
   start.

### Cloudflare

If you proxy through Cloudflare, it rewrites pages on the way out by
default: it injects its Web Analytics beacon and a bot-detection script that
sets a year-long cookie. nginx already marks every page
`Cache-Control: ..., no-transform`, which Cloudflare honours, so neither is
injected; keep that header (`$cache_control_no_transform` in
`nginx/civitas.conf`, enforced by `backend/tests/test_nginx_config.py`). The
privacy page (`/about/data#cloudflare`) describes what Cloudflare still sees;
keep it true for your setup.

### Phone alerts with ntfy

1. Install the ntfy app (<https://ntfy.sh>) and subscribe to a topic with a
   long, unguessable name. The topic name is the only secret: anyone who
   knows it can read your alerts.
2. Put the URL in `.env`:

   ```bash
   ALERT_NTFY_URL=https://ntfy.sh/<your-long-random-topic>
   ```

3. Redeploy (Compose: `docker compose up -d`; Swarm: the next deploy, or
   `./check-and-deploy.sh` after a new commit).

Pipeline failures, skipped chain links, overruns, stale data and failed
deploys then reach the phone, deduplicated so a recurring problem notifies
once.

---

## 7. Recovering from a lost server

Production keeps **no backups**, by the owner's choice. Recovery is
therefore a rebuild: follow sections 1 to 5 on a new machine, restore `.env`
from wherever you kept its values, and let the pipelines refetch.

### Rebuilt from public sources

Nearly everything, given time and the rate limits in section 5: members,
bills, votes, roll calls, scores and their breakdowns, campaign finance,
stock trades and holdings, the Court, presidents, nominations, agencies,
Explore documents, candidates and ballot measures for the current cycle, the
search indexes, and the bundled data files (which ship in the repository and
regenerate themselves daily). The learning store (`learned_classifications`)
and the analysis cache start empty and rebuild themselves as the pipelines
classify again; that is the same thing that happens after any change to the
analysis code.

### Lost for good

The code's own list of what can't be refetched is `RESET_KEEPS` in
`backend/app/database.py` (what the admin data reset refuses to delete).
On a lost server all of it goes, plus the visit counters:

- **Action Center history:** past issues (`action_issues`), the
  year-in-review timeline (`timeline_entries`), national monitors and their
  updates (`national_monitors`, `monitor_updates`), and the week, month and
  year summaries. News feeds carry only recent items, so history before the
  new server's first refresh can't be rebuilt.
- **What was published:** every post (`broadcast_posts`, which also holds
  the site's Atom feeds' entries), the member-spotlight rotation
  (`bsky_senator_spotlights`), and the trend-post audit
  (`trend_candidates`, `trend_observations`). The posts themselves stay on
  Bluesky; the server's record of them doesn't.
- **Election archives** (`election_archives`): each held election's frozen
  pre-election map, ballot and count. Nothing re-reads a past cycle's
  sources. A special election's count stored on the FEC calendar
  (`federal_election_dates`) goes the same way.
- **Run history:** pipeline runs, phase timings and rate-limit stats.
- **LLM generations kept as fine-tuning data** (`llm_generation_samples`).
- **Score history:** each night's stored score (`score_snapshots`) behind the
  score-trend charts. The new server's trends start on its first run.
- **Old member addresses** (`member_id_aliases`): links to a member's
  previous id stop redirecting.
- **Visit counts:** daily visitors, page-load timings, issue views,
  action-tap and public-API counters. These are counts of what happened and
  exist nowhere else.

### On the first days back

- **Leave `BSKY_HANDLE` empty until the first chain has finished** if you
  reuse the production account. With `broadcast_posts` empty, the posting
  modules have no record of what they already posted, and the Congress days
  and races of the last few days would be posted a second time
  (`RESET_KEEPS`'s note in `database.py`).
- Ops alerts about staleness stay quiet on a server with no run history (it
  reads as a fresh deployment); watch the first chain on the admin panel.

---

## 8. Updating, logs and troubleshooting

### Updating

- **Swarm:** nothing to do; the poller deploys each new commit on `main`.
  To deploy now: `./check-and-deploy.sh`. To see what is live: `cat
  .last-deployed-sha` and `docker stack services civitas`.
- **Compose:**

  ```bash
  git pull
  docker compose build backend && docker compose build frontend
  docker compose up -d
  ```

- Database schema changes are Alembic revisions applied automatically when
  the backend starts.
- **Don't upgrade Docker itself mid-chain.** Upgrading `docker-ce` or
  `containerd.io` restarts every container. Do it while
  `pipelineChainIsRunning` is false in the admin status.

### Logs

```bash
# Swarm
docker service logs --since 1h civitas_pipeline   # scheduler and pipelines
docker service logs --since 1h civitas_backend    # page API
docker service ps civitas_pipeline --no-trunc     # task history, exit codes, health kills
tail -f deploy-poll.log                           # the deploy poller

# Compose
docker compose logs --since 1h backend
```

Container logs rotate at 30 MB each (about a day), so an overnight problem is
easier to find on the admin Overview's **Ops alerts** panel, or on your
phone with ntfy, than in the logs.

### Common failures

These come from the incidents recorded in AGENTS.md and the code.

- **The pipeline container was killed by its own health check.** Swarm
  restarts a task whose `/api/live` stops answering. Blocking work on the
  event loop (an embedding pass, a long CPU loop) freezes it, and the run in
  that container dies with it. It shows in `docker service ps
  civitas_pipeline --no-trunc` as a task that ended with a non-zero exit or
  "unhealthy", not as an error in the logs. A run that died this way is
  marked stale and the chain resumes from the link it was on, once
  (`scheduler.resume_interrupted_chain`).
- **A run says "held by another process" after a crash or deploy.** Leases
  and run locks are released automatically: a lease ten minutes without a
  heartbeat is taken over, and a run row with no progress for six hours (or
  older than 24) counts as dead. If you can't wait, the admin API has
  `POST /api/admin/pipeline/clear-stuck-senate` (and `-house`,
  `-supplementary`, `-stock-trades`, `-election`).
- **Docker restarted mid-chain** (a host upgrade, a reboot). The chain
  records the link it was on and resumes from it once when the pipeline
  process starts again, within a day of the chain's start. A resumed chain
  cut short again is alerted (`chain-cut-again`) rather than resumed; then
  trigger it again by hand.
- **The disk filled up.** Image builds are the usual cause. The deploy
  script prunes unused images and keeps at most 15 GB of build cache after a
  good deploy; after a failed one, nothing is pruned. Check with `df -h` and
  `docker system df`; `docker builder prune` frees the build cache.
- **"database is locked".** Two writers met on SQLite's single write lock.
  The backend logs any write transaction held longer than ten seconds with
  the line that opened it; look for that warning before the error.
- **Congress.gov answers 403, GovInfo 401.** `DATA_GOV_API_KEY` is missing or
  wrong.
- **SEC fetches refused, `contact-email-unset` alert.** Set `CONTACT_EMAIL`.
- **llama-server won't stay up.** The model file isn't where
  `LLAMA_MODELS_DIR` and `LLAMA_MODEL_FILE` say. Everything except the
  Action Center keeps working (section 3).
- **A first chain looks stuck on one step for hours.** It is probably
  fetching, not hung: steps that loop over thousands of requests write a
  heartbeat with their request count and rate-limit wait, shown in the admin
  panel. A run is treated as hung only after six hours with no progress
  write at all.
- **The poller never deploys.** Read `deploy-poll.log`. "deferring" means a
  pipeline or refresh is running (it retries every five minutes); "CI
  failure" means the commit's CI failed (fix it, or `FORCE_DEPLOY=1`).
