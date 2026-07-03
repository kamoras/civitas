#!/usr/bin/env bash
# Civitas backup script — backs up the SQLite database, ChromaDB, and site code.
#
# CONFIGURE THESE PATHS for your deployment:
BACKUP_DIR="/media/usb-backup/civitas-backups"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolve the docker volume mountpoint at runtime — a hardcoded path here
# went stale when the docker data-root moved (2026-03), and backups
# silently produced nothing for months (found 2026-07-02).
DATA_DIR="$(docker volume inspect civitas_app_data --format '{{.Mountpoint}}' 2>/dev/null || true)"
if [ -z "$DATA_DIR" ] || [ ! -f "$DATA_DIR/civitas.db" ]; then
  echo "ERROR: cannot resolve civitas_app_data volume (got '$DATA_DIR')" >&2
  exit 1
fi
KEEP_DAYS=7

# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DATE=$(date +%Y-%m-%d)
LOGFILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"; }

log "=== Starting Civitas backup ==="

if ! mountpoint -q "$(dirname "$BACKUP_DIR")"; then
  # Abort — a local (same-Pi) copy adds little: the host dying takes both
  # copies with it, and the documented rebuild path (RUNBOOK.md) is the
  # accepted recovery story. What must never happen again is a SILENT
  # abort: backups stopped unnoticed for four months (2026-03..07) when
  # the USB drive unmounted after a reboot. Shout to syslog so the
  # failure is visible outside this log file; fstab (nofail) remounts
  # the drive automatically at next boot.
  log "ERROR: $(dirname "$BACKUP_DIR") is not a mounted drive — aborting (no offsite target)"
  logger -t civitas-backup -p user.err "Civitas backup SKIPPED: backup drive not mounted at $(dirname "$BACKUP_DIR")"
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
sqlite3 "$DATA_DIR/civitas.db" ".backup '$DB_BACKUP'"
log "  DB backup: $(du -h "$DB_BACKUP" | cut -f1)"

# --- Verify the backup is restorable (a backup that can't restore is
#     a false sense of security, not a backup) ---
log "Verifying backup integrity..."
integrity=$(sqlite3 "$DB_BACKUP" "PRAGMA integrity_check;")
if [ "$integrity" != "ok" ]; then
  log "ERROR: backup failed integrity check: $integrity"
  exit 1
fi
senator_count=$(sqlite3 "file:$DB_BACKUP?mode=ro" "SELECT count(*) FROM senators;")
if [ "$senator_count" -lt 90 ]; then
  log "ERROR: backup looks incomplete — only $senator_count senators"
  exit 1
fi
log "  Verified: integrity ok, $senator_count senators"

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
