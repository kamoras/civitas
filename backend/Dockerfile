# Pinned to the exact patch version validated in prod (2026-07) rather
# than the floating python:3.13-slim-bookworm tag — the deploy script's
# disk-cleanup step (check-and-deploy.sh) prunes unused base-image layers
# after every deploy, so a floating tag gets silently re-resolved against
# whatever Docker Hub currently serves on the very next commit, with no
# review. Bump this deliberately (like the PVI/population data refresh)
# when a newer patch is actually wanted, not automatically.
#
# Debian trixie, not bookworm: scdb.la.psu.edu (the justice score's source,
# behind Pantheon's CDN) answers 403 to httpx's TLS handshake under
# bookworm's OpenSSL 3.0 and 200 under trixie's 3.5, same IP and headers
# (found 2026-09-29, when every justice went unscored). curl and urllib pass
# on both, so the block is keyed to that one handshake, not to automated
# clients.
FROM python:3.13.15-slim-trixie

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gosu tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Wheels only: nothing is compiled from source in this image. Compiling here
# is how the backend once crash-looped in production — hnswlib (ChromaDB's
# vector index, gone since the sqlite-vec migration) had no aarch64 wheel,
# and gcc left to its defaults tuned it for the *build host's* CPU; built on
# a GitHub arm runner (server-grade Ampere/Cobalt), that baked in SIMD
# instructions a Pi 5's Cortex-A76 lacks -> SIGILL (2026-07-12). Every pin
# ships a wheel for both linux/arm64 and linux/amd64 on this base image, so
# the old CFLAGS workaround and the gcc/g++/libxml2-dev/libxslt-dev
# toolchain it needed are gone; a future dependency without a wheel now
# fails this step loudly instead of compiling quietly. If one is ever
# genuinely needed, build it deliberately for armv8-a and say why.
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# Chromium for senate_ptr.py's search_ptr_filings — the one PTR fetcher
# that needs a real browser to pass efdsearch.senate.gov's Akamai
# bot-management gate on its search endpoint (confirmed live 2026-07-25:
# httpx, requests, and curl_cffi's Chrome-TLS-impersonation are all
# blocked regardless of headers or transplanted session cookies; only a
# genuine OS-trusted UI event from a real browser gets through). Installed
# to a fixed, world-readable path — the app runs as the unprivileged
# civitas user below, not root, and Playwright's default cache path lives
# under $HOME.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && \
    chmod -R a+rX /ms-playwright

COPY . .

RUN groupadd -r civitas && useradd -r -g civitas -d /app civitas \
    && mkdir -p /data /app/.cache && chown -R civitas:civitas /data /app/.cache

EXPOSE 8000

COPY <<'ENTRY' /entrypoint.sh
#!/bin/sh
chown -R civitas:civitas /data /app/.cache
exec gosu civitas "$@"
ENTRY
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
# Worker processes: uvicorn reads WEB_CONCURRENCY when --workers isn't given.
# One by default (plain `docker compose up`, which also runs the pipeline in
# this process — PROCESS_ROLE=all). docker-compose.swarm.yml raises it for
# the read-only API service, whose per-client limits live in a store every
# worker shares (app/api/throttle.py), not in any one process.
ENV WEB_CONCURRENCY=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
