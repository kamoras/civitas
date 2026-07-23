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
  echo "completed"
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

echo
echo "ALL SCENARIOS PASSED"
