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
# The pinned mapshaper the district shape builders run (scripts/mapshaper/,
# npm's lockfile with each package's integrity hash): installed in a stage
# of its own so the image gets its JavaScript and no npm. --ignore-scripts:
# nothing compiles, and the parts that would (better-sqlite3, for GeoPackage
# files) are never used. Run below by the Node that Playwright already ships
# in this image, the same major version.
FROM node:24.21.0-trixie-slim AS mapshaper
WORKDIR /opt/mapshaper
COPY scripts/mapshaper/package.json scripts/mapshaper/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.13.15-slim-trixie

# No usage reports to Hugging Face. huggingface_hub, transformers and hf_xet
# (which from 1.7.0 reports every model download, with the server's IP added
# on receipt) are all on by default; this one variable turns them all off.
# The project's premise is that nothing about the deployment leaves the box
# beyond the requests it has to make.
ENV HF_HUB_DISABLE_TELEMETRY=1

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

# Distrusted roots: removed from the system store (curl, OpenSSL, anything
# not on certifi). After every apt step above, since a ca-certificates
# upgrade rebuilds the store, and as a "!" line in ca-certificates.conf,
# which later rebuilds keep. emSign ECC Root CA - G3: eight revoked
# intermediates with compromised keys (GHSA-6q48-6c7w-9gw2); emSign's other
# roots are unaffected and stay. To add a root, see AGENTS.md "Distrusted
# roots"; check-trust-stores.sh asserts the built image.
RUN sed -i 's|^mozilla/emSign_ECC_Root_CA_-_G3\.crt$|!&|' /etc/ca-certificates.conf && \
    update-ca-certificates --fresh
# Chromium doesn't read the system store: its root store is compiled in
# (G3 is in it, checked on amd64 and arm64), so the same roots are
# distrusted by managed policy (CADistrustedCertificates, the DER certs in
# base64). Playwright's Chromium is a Chrome for Testing build, which reads
# /etc/opt/chrome_for_testing; /etc/chromium is where a plain Chromium
# build would look. The headless shell reads no policy at all, which is
# why senate_ptr launches channel="chromium".
COPY chromium-distrusted-roots.json /etc/opt/chrome_for_testing/policies/managed/distrusted-roots.json
COPY chromium-distrusted-roots.json /etc/chromium/policies/managed/distrusted-roots.json

# mapshaper for the district shapes the data refresh rebuilds
# (scripts/build_district_shapes.py), on Playwright's bundled Node.
COPY --from=mapshaper /opt/mapshaper /opt/mapshaper
RUN node_bin="$(python -c 'import os, playwright; print(os.path.join(os.path.dirname(playwright.__file__), "driver", "node"))')" && \
    test -x "$node_bin" && ln -s "$node_bin" /usr/local/bin/node && node --version
ENV MAPSHAPER="/opt/mapshaper/node_modules/.bin/mapshaper"

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
