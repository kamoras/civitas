#!/usr/bin/env bash
#
# Pull-based deploy check — run via cron every 5 min.
#
# Replaces the old self-hosted-GitHub-Actions-runner deploy path (cd.yml,
# removed 2026-07). Once this repo went public, a self-hosted runner
# meant any workflow file a PR could introduce — not just cd.yml's own,
# correctly-gated trigger — was a potential path to arbitrary code
# execution on this machine (GitHub's own guidance: self-hosted runners
# "should almost never be used for public repositories"). This script
# has the Pi pull from GitHub on its own schedule instead; GitHub Actions
# never executes anything here anymore. See AGENTS.md "CI/CD".
#
# 2026-07: deploy.sh (hand-rolled blue/green: slot bookkeeping, dynamic
# nginx templating, manual health-check-then-flip) is gone. Docker Swarm
# mode (single-node — `docker swarm init` was a one-time setup step, not
# part of this script) now provides all of that natively via
# `docker stack deploy`'s update_config (start-first = zero-downtime,
# failure_action: rollback = automatic revert on a failed health check).
# The only things left here are policy checks Swarm has no concept of:
# don't ship a commit whose CI failed, and don't deploy over a running
# pipeline.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

LOCK=/tmp/civitas-deploy-check.lock
exec 200>"$LOCK"
flock -n 200 || exit 0   # a previous check/deploy is still running

log() { echo "$(date -Iseconds) $*" >> deploy-poll.log; }

git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0   # nothing new
fi

# Deploying restarts the backend service, which kills any pipeline run in
# progress (observed 2026-07: a deploy landed 11 minutes into a manually-
# triggered House pipeline run, which then failed with "Cleared by admin
# (container restart)" — that particular case was an intentional deploy-
# over, but an *unintended* collision with the nightly scheduled run is
# exactly this same failure mode). Skip this cycle if a pipeline is
# currently running; cron retries every 5 min, so the commit deploys as
# soon as the pipeline is idle. Backend is always on a fixed port under
# Swarm (no more blue/green slot file to read).
admin_token=$(grep '^ADMIN_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
if [[ -n "$admin_token" ]]; then
  pipeline_status=$(curl -fsS --max-time 5 \
    -H "Authorization: Bearer $admin_token" \
    "http://localhost:8000/api/admin/pipeline/status" 2>/dev/null || echo '{}')
  if echo "$pipeline_status" | grep -Eq '"(isRunning|houseIsRunning|stockTradesIsRunning|supplementaryIsRunning)":true'; then
    log "new commit ${REMOTE:0:8} available but a pipeline is running — deferring"
    exit 0
  fi
fi

log "new commit on main: ${REMOTE:0:8} (was ${LOCAL:0:8})"
git reset --hard origin/main

# Branch protection isn't available on this repo (private, free plan), so
# this is the enforcement point: refuse to ship a commit whose CI failed.
# Override with FORCE_DEPLOY=1.
if [[ -z "${FORCE_DEPLOY:-}" ]] && command -v gh >/dev/null 2>&1; then
  ci_conclusion=$(gh run list --commit "$REMOTE" --workflow CI \
    --json conclusion --jq '.[0].conclusion' 2>/dev/null || true)
  case "$ci_conclusion" in
    failure|cancelled|timed_out)
      log "CI $ci_conclusion for $REMOTE — not deploying (FORCE_DEPLOY=1 to override)"
      exit 1
      ;;
  esac
fi

wait_for_rollout() {
  local service="$1" timeout="${2:-180}"
  for i in $(seq 1 "$timeout"); do
    local state
    state=$(docker service inspect "$service" --format '{{.UpdateStatus.State}}' 2>/dev/null || echo "")
    case "$state" in
      completed|"")
        log "$service rollout complete after ${i}s"
        return 0
        ;;
      rollback_started|rollback_completed|paused)
        log "$service rollout failed (state=$state) — Swarm auto-rolled back"
        return 1
        ;;
    esac
    sleep 1
  done
  log "$service rollout did not converge within ${timeout}s"
  return 1
}

deploy_ok=1
IMAGE_TAG="sha-${REMOTE:0:7}"
export IMAGE_TAG

# `docker stack deploy -c a -c b` does its own, more limited multi-file
# merge than `docker compose config` — live-verified two ways this breaks:
# it doesn't apply the compose-spec `!reset` tag (docker-compose.swarm.yml
# relies on it to clear backend/frontend/ollama's published ports — without
# it they silently keep the base file's ports, live-tested), and it
# rejects a couple of `docker compose config`'s own output quirks (a
# top-level `name:` key, and `ports[].published` written as a quoted
# string). Pre-resolving with `docker compose config` (which does handle
# `!reset` correctly) and patching those two output quirks, then feeding
# `docker stack deploy` a single already-merged file, sidesteps all of it.
RESOLVED=/tmp/civitas-resolved-stack.yml
{
  docker compose -f docker-compose.yml -f docker-compose.swarm.yml build backend frontend nginx
  docker compose -f docker-compose.yml -f docker-compose.swarm.yml config \
    | grep -v '^name:' \
    | sed -E 's/published: "([0-9]+)"/published: \1/' \
    > "$RESOLVED"
  docker stack deploy -c "$RESOLVED" civitas --detach=true
} >> deploy-poll.log 2>&1 || deploy_ok=0

if [[ "$deploy_ok" == "1" ]]; then
  for svc in civitas_backend civitas_frontend civitas_nginx; do
    wait_for_rollout "$svc" 180 || deploy_ok=0
  done
fi

if [[ "$deploy_ok" == "1" ]]; then
  log "deploy OK"

  # Every deploy builds a new set of backend/frontend/nginx images tagged
  # with this commit's SHA and leaves the previous commit's images behind
  # (each backend image is ~9.5GB) — nothing ever pruned them. Found
  # 2026-07-21 investigating an 85%-full disk: 92.7GB in unused images +
  # 19.4GB in stale BuildKit layer cache, 0% of it live application data.
  # Safe to run right here: Swarm's own automatic rollback-on-failed-
  # healthcheck (update_config.failure_action: rollback) happens *during*
  # the stack deploy above, using images already present — by the time
  # this runs, wait_for_rollout has already confirmed the new image is
  # healthy, so the previous commit's image is no longer needed for that.
  # `docker image prune -a` only removes images with zero containers
  # (running or stopped), so the image actually backing every current
  # service is never at risk regardless of timing.
  { docker image prune -a -f; docker builder prune -a -f; } >> deploy-poll.log 2>&1 || true
else
  log "deploy FAILED"
  ntfy_url=$(grep '^ALERT_NTFY_URL=' .env 2>/dev/null | cut -d= -f2-)
  if [[ -n "$ntfy_url" ]]; then
    curl -fsS -d "civitas deploy failed — check deploy-poll.log on the Pi" "$ntfy_url" >/dev/null || true
  fi
fi
