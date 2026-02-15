#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[modern-punk]${NC} $1"; }
warn() { echo -e "${YELLOW}[modern-punk]${NC} $1"; }
err()  { echo -e "${RED}[modern-punk]${NC} $1"; }

usage() {
    echo "Usage: ./scripts/start.sh [OPTIONS]"
    echo ""
    echo "Start the Modern Punk application stack."
    echo ""
    echo "Options:"
    echo "  --dev           Dev mode: hot reload, bind mounts, no nginx"
    echo "  --no-ollama     Skip the Ollama container (API works, pipeline won't)"
    echo "  --build         Force rebuild all images"
    echo "  --down          Stop and remove all containers"
    echo "  --logs          Tail logs from all services"
    echo "  --pull-model    Pull the Gemma 2 model into Ollama after start"
    echo "  -h, --help      Show this help"
    echo ""
    echo "Examples:"
    echo "  ./scripts/start.sh                  # Production mode (all 4 services)"
    echo "  ./scripts/start.sh --dev            # Dev mode with hot reload"
    echo "  ./scripts/start.sh --no-ollama      # Without LLM (lighter, for UI work)"
    echo "  ./scripts/start.sh --dev --build    # Dev mode, rebuild images"
    echo "  ./scripts/start.sh --down           # Stop everything"
}

# ── Parse args ────────────────────────────────────────────────────────
DEV_MODE=false
NO_OLLAMA=false
BUILD=false
DOWN=false
LOGS=false
PULL_MODEL=false

for arg in "$@"; do
    case $arg in
        --dev)        DEV_MODE=true ;;
        --no-ollama)  NO_OLLAMA=true ;;
        --build)      BUILD=true ;;
        --down)       DOWN=true ;;
        --logs)       LOGS=true ;;
        --pull-model) PULL_MODEL=true ;;
        -h|--help)    usage; exit 0 ;;
        *)            err "Unknown option: $arg"; usage; exit 1 ;;
    esac
done

# ── Stop ──────────────────────────────────────────────────────────────
if $DOWN; then
    log "Stopping all containers..."
    docker compose --profile full down --remove-orphans
    exit 0
fi

# ── Logs ──────────────────────────────────────────────────────────────
if $LOGS; then
    docker compose --profile full logs -f
    exit 0
fi

# ── Ensure .env exists ────────────────────────────────────────────────
if [ ! -f .env ]; then
    warn ".env file not found — copying from .env.example"
    cp .env.example .env
    warn "Edit .env and set your DATA_GOV_API_KEY (free: https://api.data.gov/signup/)"
fi

# Check for placeholder values
if grep -q "your-api-data-gov-key" .env 2>/dev/null; then
    warn "DATA_GOV_API_KEY is still the placeholder value in .env"
    warn "The API will work but the pipeline won't fetch real data."
    warn "Get a free key at: https://api.data.gov/signup/"
fi

# Ensure PIPELINE_TRIGGER_TOKEN is set (generate if missing)
if ! grep -q "PIPELINE_TRIGGER_TOKEN" .env 2>/dev/null; then
    TOKEN=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")
    echo "" >> .env
    echo "PIPELINE_TRIGGER_TOKEN=$TOKEN" >> .env
    log "Generated PIPELINE_TRIGGER_TOKEN in .env"
fi

# ── Build compose command ─────────────────────────────────────────────
COMPOSE_CMD="docker compose"
COMPOSE_FILES="-f docker-compose.yml"
PROFILES=""

if $DEV_MODE; then
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.dev.yml"
fi

UP_FLAGS="-d"
if $BUILD; then
    UP_FLAGS="$UP_FLAGS --build"
fi

# ── Handle Ollama via profiles ────────────────────────────────────────
if $NO_OLLAMA; then
    log "Starting without Ollama (--no-ollama)"
    PROFILE_FLAGS=""
else
    PROFILE_FLAGS="--profile full"
fi

# ── Start ─────────────────────────────────────────────────────────────
log "Starting Modern Punk..."
if $DEV_MODE; then
    log "Mode: ${CYAN}development${NC} (hot reload, bind mounts)"
else
    log "Mode: ${CYAN}production${NC}"
fi

if $NO_OLLAMA; then
    log "Ollama: ${YELLOW}skipped${NC}"
else
    log "Ollama: ${GREEN}enabled${NC}"
fi

echo ""
$COMPOSE_CMD $COMPOSE_FILES $PROFILE_FLAGS up $UP_FLAGS

# ── Wait for services ─────────────────────────────────────────────────
log "Waiting for services to come up..."
echo ""

# Wait for backend
TRIES=0
MAX_TRIES=60
while [ $TRIES -lt $MAX_TRIES ]; do
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 2
done

if [ $TRIES -ge $MAX_TRIES ]; then
    err "Backend did not become healthy within 2 minutes."
    err "Check logs: ./scripts/start.sh --logs"
    exit 1
fi

# Check health response
HEALTH=$(curl -sf http://localhost:8000/api/health 2>/dev/null || echo '{}')
log "Backend: ${GREEN}healthy${NC}"

OLLAMA_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ollama','unknown'))" 2>/dev/null || echo "unknown")
if [ "$OLLAMA_STATUS" = "ok" ]; then
    log "Ollama:  ${GREEN}connected${NC}"
elif $NO_OLLAMA; then
    log "Ollama:  ${YELLOW}skipped${NC}"
else
    warn "Ollama:  ${RED}not ready yet${NC} (may still be starting)"
fi

# Wait for frontend/nginx
TRIES=0
while [ $TRIES -lt 30 ]; do
    if curl -sf http://localhost:80 > /dev/null 2>&1 || curl -sf http://localhost:3000 > /dev/null 2>&1; then
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 2
done

log "Frontend: ${GREEN}ready${NC}"

# ── Pull model if requested ───────────────────────────────────────────
if $PULL_MODEL && ! $NO_OLLAMA; then
    echo ""
    log "Pulling Gemma 2 9B model (this may take a while)..."
    docker exec mp-ollama ollama pull gemma2:9b-instruct-q4_K_M
    log "Model pulled successfully."
fi

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN} Modern Punk is running!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Site:${NC}       http://localhost"
echo -e "  ${CYAN}API:${NC}        http://localhost/api/health"
echo -e "  ${CYAN}API direct:${NC} http://localhost:8000/api/health"
if ! $NO_OLLAMA; then
echo -e "  ${CYAN}Ollama:${NC}     http://localhost:11434"
fi
echo ""
echo -e "  ${YELLOW}Stop:${NC}       ./scripts/start.sh --down"
echo -e "  ${YELLOW}Logs:${NC}       ./scripts/start.sh --logs"
if ! $NO_OLLAMA; then
echo -e "  ${YELLOW}Pull model:${NC} ./scripts/start.sh --pull-model"
fi
echo ""
