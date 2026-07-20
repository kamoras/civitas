FROM node:24-alpine AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
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
