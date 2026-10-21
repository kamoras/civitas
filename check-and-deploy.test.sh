#!/usr/bin/env bash
# Self-check for check-and-deploy.sh's pipeline-busy race fix (2026-07-23
# incident: a deploy's build step took long enough for the nightly
# pipeline to start mid-build, sailing past the one-and-only pre-build
# check). Sandboxes the real script with stub docker/curl/gh in a scratch
# git repo — no real Docker Swarm or network calls.
#
# Run: ./check-and-deploy.test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*"; exit 1; }

# --- scratch repo, standing in for the Pi's checkout ---
REPO="$WORK/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q
git -C "$REPO" commit -q --allow-empty -m base
BASE_SHA=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" commit -q --allow-empty -m new
NEW_SHA=$(git -C "$REPO" rev-parse HEAD)

REMOTE="$WORK/remote.git"
git clone -q --bare "$REPO" "$REMOTE"
git -C "$REPO" remote add origin "$REMOTE"
git -C "$REPO" checkout -q "$BASE_SHA" -b main
git -C "$REPO" reset -q --hard "$BASE_SHA"

cp "$SCRIPT_DIR/check-and-deploy.sh" "$REPO/check-and-deploy.sh"
cat > "$REPO/.env" <<'EOF'
ADMIN_TOKEN=test-token
EOF
cat > "$REPO/docker-compose.yml" <<'EOF'
EOF
cat > "$REPO/docker-compose.swarm.yml" <<'EOF'
EOF

# stub PATH: docker/curl/gh replaced with controllable fakes
STUBS="$WORK/stubs"
mkdir -p "$STUBS"

run_scenario() {
  local desc="$1" busy="$2" build_seconds_marker="$3"
  echo "--- $desc ---"

  # Advance the bare remote to NEW_SHA so the script sees "new commit".
  git -C "$REPO" update-ref refs/heads/main "$NEW_SHA"
  # Reset the working checkout back to base + rewind any marker so each
  # scenario starts clean, then push base as what the remote *was*.
  git -C "$REPO" checkout -q "$BASE_SHA"
  git -C "$REPO" push -q -f "$REMOTE" "$BASE_SHA:refs/heads/main"
  git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
  rm -f "$REPO/.last-deployed-sha"

  cat > "$STUBS/curl" <<EOF
#!/usr/bin/env bash
if [[ "$busy" == "1" ]]; then
  echo '{"isRunning":true}'
else
  echo '{"isRunning":false}'
fi
EOF
  cat > "$STUBS/docker" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "compose" ]]; then
  # touch a marker so we can tell the build step ran
  touch "$REPO/.build-ran"
  if [[ "\$*" == *"config"* ]]; then echo "services: {}"; fi
  exit 0
fi
if [[ "\$1" == "stack" ]]; then
  touch "$REPO/.stack-deployed"
  exit 0
fi
if [[ "\$1" == "service" ]]; then
  # A converged one-replica service, answering each question
  # wait_for_rollout/service_is_up ask in the shape Docker does. (One
  # "completed" for every question, as this stub said until 2026-10, made
  # service_is_up fail and every deploy scenario wait out 180 s a service.)
  case "\$*" in
    *Replicas*) echo 1 ;;
    *ContainerSpec.Image*) echo "civitas-backend:sha-test" ;;
    *UpdateStatus.State*) echo completed ;;
    *StartedAt*) echo "" ;;
    "service ps"*) echo "civitas-backend:sha-test|Running 5 seconds ago" ;;
    *) echo completed ;;
  esac
  exit 0
fi
if [[ "\$1" == "image" || "\$1" == "builder" ]]; then
  exit 0
fi
exit 0
EOF
  cat > "$STUBS/gh" <<'EOF'
#!/usr/bin/env bash
echo "success"
EOF
  chmod +x "$STUBS"/*
  rm -f "$REPO/.build-ran" "$REPO/.stack-deployed"

  ( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
}

# Scenario 1: pipeline busy at the (only) check before build — should
# defer, never touch docker, marker stays absent.
run_scenario "busy before build: must defer, not build, not deploy" 1 0
[[ -f "$REPO/.build-ran" ]] && fail "built despite pipeline being busy"
[[ -f "$REPO/.last-deployed-sha" ]] && fail "marker written despite deferring"

# Scenario 2: pipeline idle throughout — should build, stack-deploy, and
# record the marker.
run_scenario "idle throughout: must build, deploy, record marker" 0 0
[[ -f "$REPO/.build-ran" ]] || fail "did not build when pipeline was idle"
[[ -f "$REPO/.stack-deployed" ]] || fail "did not stack-deploy when pipeline was idle"
[[ "$(cat "$REPO/.last-deployed-sha")" == "$NEW_SHA" ]] || fail "marker not set to deployed sha"

# Scenario 3: the regression this fix targets. Pipeline idle at the first
# check (so the build runs) but busy by the time of the second check
# (simulating the nightly job starting mid-build). Must build, but must
# NOT stack-deploy, and must NOT write the marker — so the next cron tick
# retries instead of treating this commit as handled.
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
state_file="__STATE__"
if [[ ! -f "$state_file" ]]; then
  echo '{"isRunning":false}'
  echo 1 > "$state_file"
else
  echo '{"isRunning":true}'
fi
EOF
sed -i "s#__STATE__#$WORK/curl-call-state#" "$STUBS/curl"
chmod +x "$STUBS/curl"
rm -f "$WORK/curl-call-state" "$REPO/.build-ran" "$REPO/.stack-deployed" "$REPO/.last-deployed-sha"
git -C "$REPO" update-ref refs/heads/main "$NEW_SHA"
git -C "$REPO" checkout -q "$BASE_SHA" 2>/dev/null
git -C "$REPO" push -q -f "$REMOTE" "$BASE_SHA:refs/heads/main"
git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
[[ -f "$REPO/.build-ran" ]] || fail "mid-build regression check: build never ran"
[[ -f "$REPO/.stack-deployed" ]] && fail "mid-build regression check: deployed THROUGH a pipeline that started mid-build (this is the 2026-07-23 bug)"
[[ -f "$REPO/.last-deployed-sha" ]] && fail "mid-build regression check: marker written despite deferring — cron would never retry"

# Scenario: EVERY gated pipeline must defer, one key at a time. The tuple
# in check-and-deploy.sh was short by "electionIsRunning" until
# 2026-09-22, and the omission cost a real run — a deploy landed 5 minutes
# into election run #39 and the restart killed it. Election is the LAST
# phase of the nightly chain, so it is the one most likely to still be
# going when a day's deploys get their turn. Checked per-key rather than
# as one blob so a future addition to the endpoint cannot be silently
# left out of the guard again.
for key in isRunning houseIsRunning stockTradesIsRunning supplementaryIsRunning electionIsRunning; do
  cat > "$STUBS/curl" <<EOF
#!/usr/bin/env bash
echo '{"$key":true}'
EOF
  chmod +x "$STUBS/curl"
  rm -f "$REPO/.build-ran" "$REPO/.stack-deployed" "$REPO/.last-deployed-sha"
  git -C "$REPO" update-ref refs/heads/main "$NEW_SHA"
  git -C "$REPO" checkout -q "$BASE_SHA" 2>/dev/null
  git -C "$REPO" push -q -f "$REMOTE" "$BASE_SHA:refs/heads/main"
  git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
  ( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
  [[ -f "$REPO/.build-ran" ]] && fail "$key: built despite that pipeline running"
  [[ -f "$REPO/.stack-deployed" ]] && fail "$key: DEPLOYED THROUGH a running pipeline — it would be killed"
  echo "  $key: correctly deferred"
done

# Scenario: a fresh host (docs/self-hosting.md). The clone's HEAD is
# already origin/main, there is no marker, no stack is deployed, and the
# status endpoint can't answer because nothing is running. Must deploy:
# reading HEAD as "deployed" or "status unreachable" as "busy" would leave
# a new server with no stack forever.
echo "--- fresh host: no marker, no stack, HEAD == origin/main ---"
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
exit 7
EOF
cat > "$STUBS/docker" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "compose" ]]; then
  touch "$REPO/.build-ran"
  if [[ "\$*" == *"config"* ]]; then echo "services: {}"; fi
  exit 0
fi
if [[ "\$1" == "stack" ]]; then touch "$REPO/.stack-deployed"; exit 0; fi
if [[ "\$1 \$2" == "service ls" ]]; then
  # empty before the stack deploy, the stack's services after it
  [[ -f "$REPO/.stack-deployed" ]] && echo abc123
  exit 0
fi
if [[ "\$1" == "service" ]]; then
  [[ -f "$REPO/.stack-deployed" ]] || exit 1
  case "\$*" in
    *Replicas*) echo 1 ;;
    *ContainerSpec.Image*) echo "civitas-backend:sha-test" ;;
    "service ps"*) echo "civitas-backend:sha-test|Running 5 seconds ago" ;;
    *StartedAt*) echo "" ;;
    *) echo completed ;;
  esac
  exit 0
fi
exit 0
EOF
chmod +x "$STUBS"/*
rm -f "$REPO/.build-ran" "$REPO/.stack-deployed" "$REPO/.last-deployed-sha"
git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
git -C "$REPO" checkout -q "$NEW_SHA" 2>/dev/null
( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh ) || true
[[ -f "$REPO/.build-ran" ]] || fail "fresh host: never built"
[[ -f "$REPO/.stack-deployed" ]] || fail "fresh host: never deployed (HEAD read as already deployed, or no stack read as busy)"
echo "  fresh host: deployed"

# The production host's state, against the fresh-host branches above: a
# marker is present and the civitas stack is deployed. Neither new branch
# may fire there — the marker decides what is deployed, and an unreachable
# status endpoint beside a running pipeline task still defers.
# pi_stub <status reachable: 1|0> <pipeline task: Running|Shutdown>
pi_stub() {
  local reachable="$1" task_state="$2"
  if [[ "$reachable" == "1" ]]; then
    printf '#!/usr/bin/env bash\necho %s\n' "'{\"isRunning\":false}'" > "$STUBS/curl"
  else
    printf '#!/usr/bin/env bash\nexit 7\n' > "$STUBS/curl"
  fi
  cat > "$STUBS/docker" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$WORK/docker-calls"
case "\$*" in
  compose*) touch "$REPO/.build-ran"; [[ "\$*" == *config* ]] && echo "services: {}"; exit 0 ;;
  stack*) touch "$REPO/.stack-deployed"; exit 0 ;;
  "service ls"*) echo "svc1"; echo "svc2"; exit 0 ;;
  *Replicas*) echo 1 ;;
  *ContainerSpec.Image*) echo "civitas-backend:sha-test" ;;
  *StartedAt*) echo "" ;;
  *UpdateStatus.State*) echo completed ;;
  "service ps"*Image*) echo "civitas-backend:sha-test|Running 5 seconds ago" ;;
  "service ps"*CurrentState*) echo "$task_state 2 hours ago" ;;
  "service inspect"*) exit 0 ;;
  *) exit 0 ;;
esac
exit 0
EOF
  chmod +x "$STUBS"/*
  rm -f "$REPO/.build-ran" "$REPO/.stack-deployed" "$WORK/docker-calls"
}

echo "--- production: marker == origin/main, stack deployed ---"
pi_stub 1 Running
git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
echo "$NEW_SHA" > "$REPO/.last-deployed-sha"
( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
[[ -f "$REPO/.build-ran" ]] && fail "production, nothing new: built anyway"
echo "  nothing new: no build"

echo "--- production: new commit, status unreachable, pipeline task running ---"
pi_stub 0 Running
git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
echo "$BASE_SHA" > "$REPO/.last-deployed-sha"
( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
[[ -f "$REPO/.build-ran" ]] && fail "production: built while the pipeline might be running"
[[ "$(cat "$REPO/.last-deployed-sha")" == "$BASE_SHA" ]] || fail "production: marker moved while deferring"
echo "  deferred, as before the fresh-host change"

echo "--- production: new commit, idle: deploys, prunes only the stack's images ---"
pi_stub 1 Running
git -C "$REPO" push -q -f "$REMOTE" "$NEW_SHA:refs/heads/main"
echo "$BASE_SHA" > "$REPO/.last-deployed-sha"
( cd "$REPO" && PATH="$STUBS:$PATH" bash check-and-deploy.sh )
[[ -f "$REPO/.stack-deployed" ]] || fail "production idle: did not deploy"
[[ "$(cat "$REPO/.last-deployed-sha")" == "$NEW_SHA" ]] || fail "production idle: marker not advanced"
grep -q "image prune" "$WORK/docker-calls" && fail "pruned every image on the host"
for repo in civitas-backend civitas-frontend civitas-nginx ghcr.io/ggml-org/llama.cpp; do
  grep -q "image ls --no-trunc --filter reference=$repo " "$WORK/docker-calls" \
    || fail "never looked for unused $repo images"
done
echo "  deployed; image prune scoped to the stack's repositories"

echo
echo "ALL SCENARIOS PASSED"
