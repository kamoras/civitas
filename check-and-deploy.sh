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
# deploy.sh already refuses to ship a commit whose CI failed (gh run list
# --workflow CI, override with FORCE_DEPLOY=1) — that check is reused
# unmodified here, no separate CI gate needed in this script.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

LOCK=/tmp/civitas-deploy-check.lock
exec 200>"$LOCK"
flock -n 200 || exit 0   # a previous check/deploy is still running

git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0   # nothing new
fi

# Deploying restarts the currently-running backend container, which kills
# any pipeline run in progress (observed 2026-07: a deploy landed 11
# minutes into a manually-triggered House pipeline run, which then failed
# with "Cleared by admin (container restart)" — that particular case was
# an intentional deploy-over, but an *unintended* collision with the
# nightly scheduled run is exactly this same failure mode). Skip this
# cycle if a pipeline is currently running; cron retries every 5 min, so
# the commit deploys as soon as the pipeline is idle.
cur_be_slot=$(cat .deploy-backend-slot 2>/dev/null || echo blue)
if [[ "$cur_be_slot" == "blue" ]]; then cur_be_port=8000; else cur_be_port=8001; fi
admin_token=$(grep '^ADMIN_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
if [[ -n "$admin_token" ]]; then
  pipeline_status=$(curl -fsS --max-time 5 \
    -H "Authorization: Bearer $admin_token" \
    "http://localhost:${cur_be_port}/api/admin/pipeline/status" 2>/dev/null || echo '{}')
  if echo "$pipeline_status" | grep -Eq '"(isRunning|houseIsRunning|stockTradesIsRunning|supplementaryIsRunning)":true'; then
    echo "$(date -Iseconds) new commit ${REMOTE:0:8} available but a pipeline is running — deferring" >> deploy-poll.log
    exit 0
  fi
fi

echo "$(date -Iseconds) new commit on main: ${REMOTE:0:8} (was ${LOCAL:0:8})" >> deploy-poll.log
git reset --hard origin/main

if ./deploy.sh >> deploy-poll.log 2>&1; then
  echo "$(date -Iseconds) deploy OK" >> deploy-poll.log
else
  status=$?
  echo "$(date -Iseconds) deploy FAILED (exit $status)" >> deploy-poll.log
  # Reuse the same ntfy alert channel ops_alerts.py already uses, if configured.
  ntfy_url=$(grep '^ALERT_NTFY_URL=' .env 2>/dev/null | cut -d= -f2-)
  if [[ -n "$ntfy_url" ]]; then
    curl -fsS -d "civitas deploy failed (exit $status) — check deploy-poll.log on the Pi" "$ntfy_url" >/dev/null || true
  fi
fi
