# Pinned to the exact version validated in prod (2026-07), not the
# floating node:24-alpine tag — see backend/Dockerfile's FROM comment for
# why (check-and-deploy.sh prunes base-image layers every deploy, so a
# floating tag re-resolves silently on the next unrelated commit).
FROM node:24.18.0-alpine AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# The site's public address (SITE_URL in .env, passed by docker-compose.yml).
# NEXT_PUBLIC_ values are inlined at build time; empty means the production
# site (src/lib/site.ts).
ARG NEXT_PUBLIC_SITE_URL=""
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs
# Distrusted roots (AGENTS.md "Distrusted roots"): emSign ECC Root CA - G3,
# eight revoked intermediates with compromised keys (GHSA-6q48-6c7w-9gw2).
# Node's built-in root store can't drop a root, so Node is pointed at the
# system store (--use-openssl-ca reads /etc/ssl/cert.pem, the bundle
# update-ca-certificates writes) and the root is removed there. Through
# NODE_OPTIONS so any child process inherits it: if something else needs
# NODE_OPTIONS, append to it, never replace it. check-trust-stores.sh
# asserts the built image.
RUN apk add --no-cache ca-certificates && \
    sed -i 's|^mozilla/emSign_ECC_Root_CA_-_G3\.crt$|!&|' /etc/ca-certificates.conf && \
    update-ca-certificates
ENV NODE_OPTIONS=--use-openssl-ca
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
# Next's standalone-output file tracer (@vercel/nft) follows require()
# calls but not sharp's dlopen()'d libvips-cpp.so — verified live: sharp
# 0.34.5's traced output happened to include it, 0.35.x's does not (empty
# @img/sharp-libvips-linuxmusl-x64/lib/), so next/image silently breaks
# at request time despite `docker build` succeeding (and so would the
# /photo/<kind>/<id>/<width> route, which resizes with sharp). Copying @img
# straight from deps (the un-traced, un-pruned npm ci output) sidesteps
# the tracer gap instead of depending on it staying lucky across sharp
# releases.
COPY --from=deps --chown=nextjs:nodejs /app/node_modules/@img ./node_modules/@img
USER nextjs
EXPOSE 3000
ENV PORT=3000
# Docker auto-injects HOSTNAME as the container's own ID into every
# container's environment, unconditionally — Next.js's standalone
# server.js binds to exactly `process.env.HOSTNAME`, so without this it
# never listens on loopback at all, only on the container's own network
# interface (live-verified on a Swarm deploy: `10.0.1.39:3000 LISTEN`,
# nothing on `127.0.0.1:3000` — a well-known Next-standalone-in-Docker
# gotcha). `environment: HOSTNAME=...` in a compose/stack file does NOT
# fix this — Docker's own injection wins regardless (also live-verified).
# `unset` in the container's own shell before `exec node` is the only
# thing that actually works, letting server.js's own `0.0.0.0` fallback
# apply. `exec` (not a bare `node server.js` at the end) replaces the
# shell process rather than spawning a child, so node stays PID 1 and
# still receives SIGTERM directly for graceful shutdown.
CMD ["sh", "-c", "unset HOSTNAME; exec node server.js"]
