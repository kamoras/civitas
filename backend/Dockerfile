# Pinned to the exact patch version validated in prod (2026-07) rather
# than the floating python:3.13-slim-bookworm tag — the deploy script's
# disk-cleanup step (check-and-deploy.sh) prunes unused base-image layers
# after every deploy, so a floating tag gets silently re-resolved against
# whatever Docker Hub currently serves on the very next commit, with no
# review. Bump this deliberately (like the PVI/population data refresh)
# when a newer patch is actually wanted, not automatically.
FROM python:3.13.14-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ libxml2-dev libxslt-dev curl gosu \
        tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# hnswlib has no prebuilt aarch64 wheel and compiles from source. Left to its
# own defaults, gcc auto-detects the *build host's* CPU (-march=native-ish
# tuning), which on GitHub's ubuntu-24.04-arm runners (server-grade
# Ampere/Cobalt) bakes in SIMD instructions absent on a Pi 5 (Cortex-A76) ->
# SIGILL at runtime. Pin to the portable ARMv8-A baseline so the same image
# runs on any aarch64 target regardless of build host — but ONLY on arm64:
# -march=armv8-a is an invalid flag on amd64 gcc ("Compiler cc cannot
# compile programs" from meson/numpy's build, not an obvious error) and
# broke the CI amd64 smoke-build job. TARGETARCH is auto-populated by
# BuildKit from the actual build platform, no --platform flag needed.
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        export CFLAGS="-march=armv8-a" CXXFLAGS="-march=armv8-a"; \
    fi; \
    pip install --no-cache-dir -r requirements.txt

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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
