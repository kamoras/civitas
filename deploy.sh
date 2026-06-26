#!/usr/bin/env bash
#
# Zero-downtime blue/green deployment for CIVITAS
#
# Usage:
#   ./deploy.sh              Deploy frontend + backend
#   ./deploy.sh frontend     Deploy frontend only
#   ./deploy.sh backend      Deploy backend only
#
# Blue slot: frontend=3000, backend=8000
# Green slot: frontend=3001, backend=8001
#
# Requires: docker, docker compose, sudo (for nginx reload)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

NETWORK="civitas_default"
VOLUME_DATA="civitas_app_data"
NGINX_CONF="/etc/nginx/sites-enabled/civitas"

BLUE_FE_PORT=3000; BLUE_BE_PORT=8000
GREEN_FE_PORT=3001; GREEN_BE_PORT=8001

FE_SLOT_FILE="$PROJECT_DIR/.deploy-frontend-slot"
BE_SLOT_FILE="$PROJECT_DIR/.deploy-backend-slot"

# Read LLM_BACKEND from .env (default: ollama)
LLM_BACKEND=$(grep -E '^LLM_BACKEND=' .env 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" || true)
LLM_BACKEND="${LLM_BACKEND:-ollama}"

# ── Helpers ──────────────────────────────────────────────────────────
slot_port_fe() { [[ "$1" == "blue" ]] && echo $BLUE_FE_PORT || echo $GREEN_FE_PORT; }
slot_port_be() { [[ "$1" == "blue" ]] && echo $BLUE_BE_PORT || echo $GREEN_BE_PORT; }
flip()         { [[ "$1" == "blue" ]] && echo green || echo blue; }

read_slot() {
  local file="$1" default="${2:-blue}"
  if [[ -f "$file" ]]; then cat "$file"; else echo "$default"; fi
}

log() { printf "\033[1;32m>>>\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m!!!\033[0m %s\n" "$*" >&2; }

wait_healthy() {
  local url="$1" label="$2" timeout="${3:-60}"
  for i in $(seq 1 "$timeout"); do
    if curl -sf -o /dev/null --max-time 3 "$url" 2>/dev/null; then
      log "$label healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  err "$label failed health check after ${timeout}s"
  return 1
}

# ── Nginx config writer ─────────────────────────────────────────────
write_nginx() {
  local fe_port="$1" be_port="$2"
  sudo tee "$NGINX_CONF" > /dev/null <<NGINX_EOF
proxy_cache_path /var/cache/nginx/civitas levels=1:2
    keys_zone=civitas_cache:10m max_size=256m inactive=30m
    use_temp_path=off;

limit_req_zone \$binary_remote_addr zone=api_limit:10m rate=30r/s;
limit_req_zone \$binary_remote_addr zone=search_limit:5m rate=5r/s;

upstream frontend_app {
    server 127.0.0.1:${fe_port};
    keepalive 16;
}

upstream backend_app {
    server 127.0.0.1:${be_port};
    keepalive 32;
}

server {
    listen 8081;
    listen [::]:8081;
    server_name _;

    access_log /var/log/nginx/civitas.access.log;
    error_log  /var/log/nginx/civitas.error.log;

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 5;
    gzip_min_length 256;
    gzip_types
        application/json
        application/javascript
        text/css
        text/plain
        text/xml
        application/xml
        image/svg+xml;

    # -- Cached read-only API endpoints (short TTL, massive concurrency win) --

    location = /api/config {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 1h;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/senators/leaderboard {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/senators/states {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/presidents/leaderboard {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/explore/stats {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/justices/leaderboard {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location ~ ^/api/justices/[^/]+\$ {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 2m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location = /api/senators {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 2m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location ~ ^/api/senators/[^/]+\$ {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 2m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location ~ ^/api/presidents/[^/]+\$ {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 2m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    # -- Admin: local network only (returns 404 to public) --

    location /api/admin/ {
        allow 127.0.0.1;
        allow ::1;
        allow 192.168.0.0/16;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        deny all;
        error_page 403 =404 /404.html;

        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 120s;
    }

    location /admin {
        allow 127.0.0.1;
        allow ::1;
        allow 192.168.0.0/16;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        deny all;
        error_page 403 =404 /404.html;

        proxy_pass http://frontend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # -- Action Center API (cached, changes at most daily) --

    location /api/action/issues {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location /api/action/elections {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 1h;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location /api/action/monitors {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location /api/action/timeline {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 5m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    location /api/action/country-news {
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 10m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    # -- Explore search (rate-limited, CPU-intensive) --

    location = /api/explore {
        limit_req zone=search_limit burst=10 nodelay;
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        proxy_cache_lock on;
        add_header X-Cache-Status \$upstream_cache_status;
    }

    # -- Other API endpoints (rate-limited) --

    location /api/ {
        limit_req zone=api_limit burst=60 nodelay;
        proxy_pass http://backend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 120s;
        proxy_buffering on;
        proxy_buffer_size 8k;
        proxy_buffers 16 16k;
    }

    # -- Next.js static assets (immutable hashed filenames) --

    location /_next/static/ {
        proxy_pass http://frontend_app;
        proxy_set_header Host \$host;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_cache civitas_cache;
        proxy_cache_valid 200 30d;
        add_header Cache-Control "public, max-age=31536000, immutable";
        add_header X-Cache-Status \$upstream_cache_status;
    }

    # -- Frontend (HTML pages, SSR) --

    location / {
        proxy_pass http://frontend_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }
}
NGINX_EOF
  sudo nginx -t 2>&1 || { err "nginx config test failed"; return 1; }
  sudo nginx -s reload
  log "nginx reloaded → frontend:${fe_port} backend:${be_port}"
}

# ── Deploy backend ───────────────────────────────────────────────────
deploy_backend() {
  local cur_slot new_slot new_port container_name

  cur_slot=$(read_slot "$BE_SLOT_FILE")
  new_slot=$(flip "$cur_slot")
  new_port=$(slot_port_be "$new_slot")
  container_name="mp-backend-${new_slot}"

  log "Backend: $cur_slot → $new_slot (port $new_port)"

  log "Building backend image..."
  docker compose build backend

  docker rm -f "$container_name" 2>/dev/null || true

  # Stop any container blocking the target port (e.g. docker-compose containers)
  local blocking
  blocking=$(docker ps --filter "publish=${new_port}" --format '{{.Names}}' 2>/dev/null || true)
  if [[ -n "$blocking" ]]; then
    log "Stopping container(s) on port ${new_port}: $blocking"
    docker rm -f $blocking 2>/dev/null || true
    sleep 1
  fi

  log "Starting $container_name..."
  docker run -d \
    --name "$container_name" \
    --network "$NETWORK" \
    --network-alias backend \
    --env-file .env \
    --add-host=host.docker.internal:host-gateway \
    -e DATABASE_URL=sqlite:////data/civitas.db \
    -e OLLAMA_BASE_URL=http://mp-ollama:11434 \
    -e LLM_BACKEND="${LLM_BACKEND}" \
    -e LLAMA_SERVER_URL=http://host.docker.internal:8070 \
    -v "${VOLUME_DATA}:/data" \
    -v /sys/class/net/eth0/statistics:/host/net/eth0:ro \
    $(br=$(docker network inspect "$NETWORK" --format '{{.Id}}' 2>/dev/null | head -c 12); \
      [ -d "/sys/class/net/br-${br}/statistics" ] && echo "-v /sys/class/net/br-${br}/statistics:/host/net/docker-br:ro") \
    -p "127.0.0.1:${new_port}:8000" \
    --memory=4g \
    --restart unless-stopped \
    civitas-backend:latest

  if ! wait_healthy "http://localhost:${new_port}/api/health" "Backend" 180; then
    err "Rolling back backend"
    docker rm -f "$container_name"
    return 1
  fi

  echo "$new_slot" > "$BE_SLOT_FILE"
  BE_ACTIVE_PORT=$new_port

  # Clean up old
  local old_name="mp-backend-${cur_slot}"
  docker rm -f "$old_name" 2>/dev/null || true
  docker rm -f mp-backend 2>/dev/null || true
}

# ── Deploy frontend ──────────────────────────────────────────────────
deploy_frontend() {
  local cur_slot new_slot new_port container_name

  cur_slot=$(read_slot "$FE_SLOT_FILE")
  new_slot=$(flip "$cur_slot")
  new_port=$(slot_port_fe "$new_slot")
  container_name="mp-frontend-${new_slot}"

  log "Frontend: $cur_slot → $new_slot (port $new_port)"

  log "Building frontend image..."
  docker compose build frontend

  docker rm -f "$container_name" 2>/dev/null || true

  # Stop any container blocking the target port (e.g. docker-compose containers)
  local blocking
  blocking=$(docker ps --filter "publish=${new_port}" --format '{{.Names}}' 2>/dev/null || true)
  if [[ -n "$blocking" ]]; then
    log "Stopping container(s) on port ${new_port}: $blocking"
    docker rm -f $blocking 2>/dev/null || true
    sleep 1
  fi

  log "Starting $container_name..."
  docker run -d \
    --name "$container_name" \
    --network "$NETWORK" \
    -e NEXT_PUBLIC_API_URL=/api \
    -p "127.0.0.1:${new_port}:3000" \
    --memory=512m \
    --restart unless-stopped \
    civitas-frontend:latest

  if ! wait_healthy "http://localhost:${new_port}/" "Frontend" 60; then
    err "Rolling back frontend"
    docker rm -f "$container_name"
    return 1
  fi

  echo "$new_slot" > "$FE_SLOT_FILE"
  FE_ACTIVE_PORT=$new_port

  # Clean up old
  local old_name="mp-frontend-${cur_slot}"
  docker rm -f "$old_name" 2>/dev/null || true
  docker rm -f mp-frontend 2>/dev/null || true
}

# ── Main ─────────────────────────────────────────────────────────────
TARGET="${1:-all}"

# Resolve current active ports (before deploy changes the slot files)
FE_ACTIVE_PORT=$(slot_port_fe "$(read_slot "$FE_SLOT_FILE")")
BE_ACTIVE_PORT=$(slot_port_be "$(read_slot "$BE_SLOT_FILE")")

# Ensure LLM backend is running
if [ "${LLM_BACKEND}" = "llama-server" ]; then
  if ! curl -sf http://localhost:8070/health >/dev/null 2>&1; then
    log "Starting llama-server via systemd..."
    sudo systemctl start llama-server 2>/dev/null || true
    wait_healthy "http://localhost:8070/health" "llama-server" 30
  fi
else
  if ! docker inspect mp-ollama --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy; then
    log "Starting infrastructure (ollama)..."
    docker compose up -d ollama
    log "Waiting for ollama..."
    wait_healthy "http://localhost:11434/" "Ollama" 120
  fi
fi

case "$TARGET" in
  all)
    deploy_backend
    deploy_frontend
    ;;
  frontend)
    deploy_frontend
    ;;
  backend)
    deploy_backend
    ;;
  *)
    err "Usage: $0 [all|frontend|backend]"
    exit 1
    ;;
esac

# Switch nginx to the new active ports
write_nginx "$FE_ACTIVE_PORT" "$BE_ACTIVE_PORT"

log "Deploy complete!"
log "  Frontend: port $FE_ACTIVE_PORT (slot $(read_slot "$FE_SLOT_FILE"))"
log "  Backend:  port $BE_ACTIVE_PORT (slot $(read_slot "$BE_SLOT_FILE"))"
