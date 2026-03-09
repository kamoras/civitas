#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/media/usb-backup/civitas-backups"
REPO_DIR="/mnt/nvme/modern-punk"
DATA_DIR="/mnt/nvme/docker/volumes/modern-punk_app_data/_data"
KEEP_DAYS=7
DATE=$(date +%Y-%m-%d)
LOGFILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"; }

log "=== Starting Civitas backup ==="

if ! mountpoint -q /media/usb-backup; then
  log "ERROR: USB stick not mounted at /media/usb-backup — aborting"
  exit 1
fi

avail_mb=$(df --output=avail -m /media/usb-backup | tail -1 | tr -d ' ')
if [ "$avail_mb" -lt 2048 ]; then
  log "ERROR: Less than 2GB free on USB ($avail_mb MB) — aborting"
  exit 1
fi

# --- Back up SQLite database (online-safe copy via .backup command) ---
DB_BACKUP="$BACKUP_DIR/modern-punk-$DATE.db"
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
  -C /mnt/nvme \
  --exclude='modern-punk/node_modules' \
  --exclude='modern-punk/frontend/node_modules' \
  --exclude='modern-punk/frontend/.next' \
  --exclude='modern-punk/backend/.venv' \
  --exclude='modern-punk/backend/__pycache__' \
  --exclude='modern-punk/.git' \
  modern-punk
log "  Site backup: $(du -h "$SITE_BACKUP" | cut -f1)"

# --- Back up .env (contains API keys — critical to preserve) ---
if [ -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env" "$BACKUP_DIR/env-$DATE.bak"
  log "  .env backed up"
fi

# --- Rotate old backups ---
log "Rotating backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -maxdepth 1 -name "modern-punk-*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "chroma-*.tar.gz" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "site-*.tar.gz" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name "env-*.bak" -mtime +$KEEP_DAYS -delete 2>/dev/null || true

log "=== Backup complete ==="
