FROM python:3.13-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ libxml2-dev libxslt-dev curl gosu && \
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
