#!/usr/bin/env bash
# Civitas backup script — backs up the SQLite database, ChromaDB, and site code.
#
# CONFIGURE THESE PATHS for your deployment:
BACKUP_DIR="/media/usb-backup/civitas-backups"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="/var/lib/docker/volumes/modern-punk_app_data/_data"   # Docker volume mount
KEEP_DAYS=7

# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DATE=$(date +%Y-%m-%d)
LOGFILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"; }

log "=== Starting Civitas backup ==="

if ! mountpoint -q "$(dirname "$BACKUP_DIR")"; then
  log "ERROR: Backup destination not mounted — aborting"
  exit 1
fi

avail_mb=$(df --output=avail -m "$BACKUP_DIR" | tail -1 | tr -d ' ')
if [ "$avail_mb" -lt 2048 ]; then
  log "ERROR: Less than 2GB free ($avail_mb MB) — aborting"
  exit 1
fi

# --- Back up SQLite database (online-safe copy via .backup command) ---
DB_BACKUP="$BACKUP_DIR/civitas-$DATE.db"
log "Backing up SQLite database..."
sqlite3 "$DATA_DIR/modern-punk.db" ".backup '$DB_BACKUP'"
log "  DB backup: $(du -h "$DB_BACKUP" | cut -f1)"

# --- Back up ChromaDB data ---
CHROMA_BACKUP="$BACKUP_DIR/chroma-$DATE.tar.gz"
log "Backing up ChromaDB..."
tar -czf "$CHROMA_BACKUP" -C "$DATA_DIR" chroma 2>/dev/null || true
log "  ChromaDB backup: $(du -h "$CHROMA_BACKUP" | cut -f1)"

# --- Back up site code (excluding transient files) ---
SITE_BACKUP="$BACKUP_DIR/site-$DATE.tar.gz"
log "Backing up site code..."
tar -czf "$SITE_BACKUP" \
  -C "$(dirname "$REPO_DIR")" \
  --exclude="$(basename "$REPO_DIR")/node_modules" \
  --exclude="$(basename "$REPO_DIR")/frontend/node_modules" \
  --exclude="$(basename "$REPO_DIR")/frontend/.next" \
  --exclude="$(basename "$REPO_DIR")/backend/.venv" \
  --exclude="$(basename "$REPO_DIR")/backend/__pycache__" \
  --exclude="$(basename "$REPO_DIR")/.git" \
  "$(basename "$REPO_DIR")"
log "  Site backup: $(du -h "$SITE_BACKUP" | cut -f1)"

# --- Back up .env (contains API keys — critical to preserve) ---
if [ -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env" "$BACKUP_DIR/env-$DATE.bak"
  log "  .env backed up"
fi

# --- Rotate old backups ---
log "Rotating backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -maxdepth 1 -name "civitas-*.db"    -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "chroma-*.tar.gz" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "site-*.tar.gz"   -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "env-*.bak"       -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true

log "=== Backup complete ==="
