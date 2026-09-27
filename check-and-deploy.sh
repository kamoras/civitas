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

# What we last actually finished deploying — not `git rev-parse HEAD`.
# HEAD gets reset to origin/main below *before* the build/deploy runs, so
# using HEAD as "already handled" would make a deferred cycle (pipeline
# busy) look like "nothing new" forever after that reset, and cron would
# never retry it. Only updated on a confirmed-successful deploy, below.
DEPLOYED_MARKER=.last-deployed-sha
LOCAL=$(cat "$DEPLOYED_MARKER" 2>/dev/null || git rev-parse HEAD)

git fetch origin main --quiet
REMOTE=$(git rev-parse origin/main)

if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0   # nothing new
fi

# Deploying restarts the pipeline service (docker-compose.swarm.yml; it was
# the backend service until the API and pipeline were split into two), which
# kills any pipeline run in progress (observed 2026-07: a deploy landed 11 minutes into a manually-
# triggered House pipeline run, which then failed with "Cleared by admin
# (container restart)" — that particular case was an intentional deploy-
# over, but an *unintended* collision with the nightly scheduled run is
# exactly this same failure mode). Skip this cycle if a pipeline is
# currently running; cron retries every 5 min, so the commit deploys as
# soon as the pipeline is idle.
#
# Found 2026-07-21 investigating a House pipeline run that died mid-flight
# three separate times with no obvious cause: this check has been a
# complete no-op since the Swarm migration (#176, 2026-07-19) and never
# caught it. It queries http://localhost:8000, but docker-compose.swarm.yml
# resets backend's published ports to none (`ports: !reset []` — see that
# file's own comment on why) — that port hasn't been reachable from the
# host since. The curl failure fell through to `|| echo '{}'`, which reads
# as "no pipeline running" and deploys anyway: exactly the three restarts
# (18:47, 19:01, 19:16 UTC that day) that killed the House run, each one
# lining up second-for-second with an ordinary "deploy OK" log entry.
# civitas_nginx is the only service still `deploy`-published under Swarm
# (host port 8081 — see docker-compose.swarm.yml), and it proxies
# /api/admin/ to the pipeline service on the overlay network — the process
# whose memory holds the run flags this reads. Also fail closed now: an unreachable
# admin API is ambiguous, not evidence nothing is running, so a curl error
# defers the same as a confirmed-running pipeline instead of deploying
# through it blind.
#
# Checked twice, not once (found 2026-07-23): the image build below takes
# several minutes on Pi-class hardware, so a clean check here can go stale
# before `docker stack deploy` actually restarts backend — a nightly
# pipeline run that starts mid-build sails right through. Second call
# sits immediately before the stack deploy, as close to the actual
# restart as this script gets.
_busy_reason=""
pipeline_is_busy() {
  local admin_token status
  _busy_reason=""
  admin_token=$(grep '^ADMIN_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
  [[ -z "$admin_token" ]] && return 1   # not configured — can't check, don't block
  if ! status=$(curl -fsS --max-time 5 \
    -H "Authorization: Bearer $admin_token" \
    "http://localhost:8081/api/admin/pipeline/status" 2>/dev/null); then
    # The status lives in the pipeline service. If that service exists and
    # has no task running at all, nothing can be running in it either —
    # and deferring would block the very deploy that fixes it, forever.
    if docker service inspect civitas_pipeline >/dev/null 2>&1 \
      && [[ "$(docker service ps civitas_pipeline --filter desired-state=running --format '{{.CurrentState}}' 2>/dev/null | grep -c '^Running' || true)" == "0" ]]; then
      log "pipeline status unreachable and civitas_pipeline has no running task — nothing to wait for"
      return 1
    fi
    _busy_reason="couldn't reach pipeline status"
    return 0
  fi
  # Checks the five heavy/nightly pipelines this guard exists for, by
  # parsing the JSON and reading top-level keys, not a flat text match.
  # A plain grep for `"isRunning":true` also matched the unrelated,
  # lightweight actionRefresh.isRunning field nested in the same JSON blob
  # — both use the literal key name "isRunning", so no substring regex can
  # tell them apart. Found 2026-07-27: a wedged action-center refresh
  # (is_running stuck true in-memory after an uncaught exception — see
  # action_center.py) blocked deploys for 5+ hours with no real pipeline
  # running.
  #
  # electionIsRunning was MISSING until 2026-09-22, and the omission cost
  # a real run: a deploy landed 5 minutes into election run #39 and the
  # restart killed it. Election is the LAST phase of the nightly chain
  # (senate -> supplementary -> house -> stock -> election), so it is the
  # phase most likely to still be going when a day's deploys finally get
  # their turn — exactly the case this guard exists for. The admin
  # endpoint has always published the field; only this tuple was short.
  #
  # A killed run's row is swept on the next startup
  # (main._invalidate_orphaned_pipelines, every pipeline's table since
  # 2026-09-27); before that only the Senate's was, and a killed election
  # run read "running" until STALE_PIPELINE_TIMEOUT aged it out.
  if echo "$status" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if any(d.get(k) for k in
    ("isRunning", "houseIsRunning", "stockTradesIsRunning",
     "supplementaryIsRunning", "electionIsRunning", "dataResetIsRunning")
) else 1)
'; then
    _busy_reason="a pipeline or data reset is running"
    return 0
  fi
  # The hourly action refresh is waited for too, but only while it is
  # young. A deploy kills it (it runs in a thread, so SIGTERM skips its
  # cleanup), and at ~20 minutes a run it occupies a third of every hour:
  # on a day of steady deploys (22 on 2026-09-26) most refreshes died and
  # nothing published. The age cap keeps the 2026-07-27 failure out: an
  # is_running flag wedged true must not hold deploys for hours.
  # ACTION_REFRESH_WAIT_MIN is twice the longest run measured
  # (1205s, 2026-09-27).
  if echo "$status" | ACTION_REFRESH_WAIT_MIN=40 python3 -c '
import json, os, sys
from datetime import datetime, timedelta, timezone
try:
    ar = json.load(sys.stdin).get("actionRefresh") or {}
    started = datetime.fromisoformat(ar["startedAt"])
except (ValueError, KeyError, TypeError):
    sys.exit(1)
if started.tzinfo is None:
    started = started.replace(tzinfo=timezone.utc)
age = datetime.now(timezone.utc) - started
sys.exit(0 if ar.get("isRunning") and age < timedelta(minutes=int(os.environ["ACTION_REFRESH_WAIT_MIN"])) else 1)
'; then
    _busy_reason="the action refresh is running"
    return 0
  fi
  return 1
}

if pipeline_is_busy; then
  log "new commit ${REMOTE:0:8} available but ${_busy_reason} — deferring"
  exit 0
fi

log "new commit on main: ${REMOTE:0:8} (was ${LOCAL:0:8})"
git reset --hard origin/main

# The enforcement point whatever GitHub's branch settings say (this was
# written when the repo was private on a free plan, where branch protection
# wasn't available): refuse to ship a commit whose CI failed.
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

# Whether every replica `service` wants is running. With a HEALTHCHECK,
# Swarm holds a task in "starting" until it passes, so "Running" here means
# healthy.
service_is_up() {
  local service="$1" desired running
  desired=$(docker service inspect "$service" --format '{{.Spec.Mode.Replicated.Replicas}}' 2>/dev/null) || return 1
  running=$(docker service ps "$service" --filter desired-state=running --format '{{.CurrentState}}' 2>/dev/null | grep -c '^Running' || true)
  [[ "$desired" =~ ^[0-9]+$ && "$desired" -gt 0 && "$running" -ge "$desired" ]]
}

wait_for_rollout() {
  local service="$1" timeout="${2:-180}"
  for i in $(seq 1 "$timeout"); do
    local state
    state=$(docker service inspect "$service" --format '{{.UpdateStatus.State}}' 2>/dev/null || echo "")
    case "$state" in
      completed|"")
        # Empty is also what a service has on the deploy that creates it
        # (it was never updated), so an empty state proves nothing on its
        # own: wait for its replicas to be up and healthy too.
        if service_is_up "$service"; then
          log "$service rollout complete after ${i}s"
          return 0
        fi
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
# relies on it to clear backend/frontend/llama-server's published ports — without
# it they silently keep the base file's ports, live-tested), and it
# rejects a couple of `docker compose config`'s own output quirks (a
# top-level `name:` key, and `ports[].published` written as a quoted
# string). Pre-resolving with `docker compose config` (which does handle
# `!reset` correctly) and patching those two output quirks, then feeding
# `docker stack deploy` a single already-merged file, sidesteps all of it.
RESOLVED=/tmp/civitas-resolved-stack.yml
{
  docker compose -f docker-compose.yml -f docker-compose.swarm.yml build backend frontend nginx
} >> deploy-poll.log 2>&1 || deploy_ok=0

if [[ "$deploy_ok" == "1" ]] && pipeline_is_busy; then
  log "commit ${REMOTE:0:8} built but ${_busy_reason} — deferring stack deploy"
  exit 0
fi

if [[ "$deploy_ok" == "1" ]]; then
  {
    docker compose -f docker-compose.yml -f docker-compose.swarm.yml config \
      | grep -v '^name:' \
      | sed -E 's/published: "([0-9]+)"/published: \1/' \
      > "$RESOLVED"
    # --prune: without it, a service removed from the compose files (e.g.
    # the ollama removal in #448) keeps running indefinitely as an orphan
    # instead of being torn down on the next deploy — live-verified: the
    # first post-#448 deploy left civitas_ollama at 1/1 with no prune.
    docker stack deploy -c "$RESOLVED" civitas --prune --detach=true
  } >> deploy-poll.log 2>&1 || deploy_ok=0
fi

if [[ "$deploy_ok" == "1" ]]; then
  for svc in civitas_backend civitas_pipeline civitas_frontend civitas_nginx; do
    wait_for_rollout "$svc" 180 || deploy_ok=0
  done
fi

if [[ "$deploy_ok" == "1" ]]; then
  log "deploy OK"
  echo "$REMOTE" > "$DEPLOYED_MARKER"

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
  #
  # Build cache is different from images: `docker builder prune -a` (the
  # first version of this fix, 2026-07-21) wiped ALL of it every deploy,
  # not just old entries — live-observed forcing the very next deploy back
  # to a from-scratch build (recompiling numpy/chroma-hnswlib, reinstalling
  # every pip package) instead of reusing unchanged dependency layers,
  # turning what should be a ~1min incremental build into a 10+ min one on
  # Pi-class hardware. `--keep-storage` caps total cache size instead of
  # clearing it, so BuildKit's own LRU eviction — not this script — decides
  # what to drop, preserving the most-recently-used layers that make
  # consecutive deploys (usually just an app-code change, not a
  # requirements.txt change) fast.
  { docker image prune -a -f; docker builder prune -f --keep-storage=15GB; } >> deploy-poll.log 2>&1 || true
else
  log "deploy FAILED"
  ntfy_url=$(grep '^ALERT_NTFY_URL=' .env 2>/dev/null | cut -d= -f2-)
  if [[ -n "$ntfy_url" ]]; then
    curl -fsS -d "civitas deploy failed — check deploy-poll.log on the Pi" "$ntfy_url" >/dev/null || true
  fi
fi
