# Civitas Operations Runbook

How to run, recover, and rebuild the Civitas production host. Written
2026-07-02 after an ops audit; update when infrastructure changes.

## Topology

One Raspberry Pi 5 runs everything:

| Piece | Where |
|---|---|
| Repo + deploys | `/mnt/nvme/modern-punk` (NVMe) |
| App data (SQLite `civitas.db`, ChromaDB) | docker volume `civitas_app_data` → `/mnt/nvme/docker/volumes/civitas_app_data/_data` |
| Docker data-root | `/mnt/nvme/docker` (`/etc/docker/daemon.json`) |
| containerd root (image layers + container writable layers) | `/mnt/nvme/containerd` (`root = ...` in `/etc/containerd/config.toml`) |
| OS | SD card (`/`, 117G) — keep big data OFF this card |
| Backups | USB drive (exFAT, label `usb`, UUID `448B-5160`) at `/media/usb-backup` |
| LLM | `mp-ollama` container (or llama-server via systemd, per `LLM_BACKEND` in `.env`) |
| Reverse proxy | host nginx, `/etc/nginx/sites-enabled/civitas` (written by `deploy.sh`) |

Containers: `mp-backend-{blue,green}` (:8000/:8001), `mp-frontend-{blue,green}`
(:3000/:3001), `mp-ollama`. Active slots tracked in `.deploy-backend-slot` /
`.deploy-frontend-slot`.

## Routine operations

- **Deploy**: `./deploy.sh [backend|frontend]`. Blue/green with health checks
  and automatic rollback; refuses to deploy a commit whose CI failed
  (override `FORCE_DEPLOY=1`); reuse an already-built image with
  `SKIP_BUILD=1`. Full backend build ≈ 40–60 min on the Pi.
- **Never deploy while a pipeline is running** — check
  `pipeline_runs`/`house_pipeline_runs` latest status, or the admin dashboard.
- **Pipelines**: Senate nightly 3 AM (~4–6 h), House sequential after (~1–2 h),
  action center hourly at :15. Manual triggers: see memory/project docs or
  `app/api/pipeline.py`.
- **Backups**: root cron, 1 AM nightly → `backup.sh` → `/media/usb-backup/civitas-backups`.
  The script resolves the docker volume path at runtime, uses SQLite's online
  `.backup`, then verifies `PRAGMA integrity_check` and a senator count.
  **Check the log occasionally**: `tail /media/usb-backup/civitas-backups/backup.log`.
- **Score quality checks** (inside a backend container):
  - `python3 scripts/rescore.py` — shadow-score all senators, ground-truth table
  - `python3 scripts/benchmark_validation.py` — IV vs Voteview party-unity (baseline r=+0.70)
  - Admin dashboard shows `groundTruthFailures` from the last pipeline run.
- **CI**: GitHub Actions on every push/PR. `gh run list --limit 5` from the
  server. Branch protection is unavailable (private repo, free plan) — the
  deploy script's CI gate is the enforcement point.

## Known failure modes (all observed 2026-07-02)

1. **SD card fills → containers crash at startup (ENOSPC), deploys fail
   health checks.** Historically caused by containerd's store and stray
   backup trees living on `/`. Both were moved; if it recurs check
   `df -h /`, `docker system df`, `du -xh --max-depth=1 /`, then
   `docker builder prune -af`.
2. **USB backup drive silently unmounted after reboot** → backup jobs
   write to a bare directory on the SD card (or fail entirely). Now in
   `/etc/fstab` with `nofail`; verify after reboots: `findmnt /media/usb-backup`.
3. **Dependabot bumps land inconsistent pins** → image build fails at
   deploy time. CI's `backend-image` job now builds the Docker image on
   every PR; don't batch-apply Dependabot updates without CI.
4. **FEC candidate totals arrive with `cycle: null`** — never trust API-side
   sort ordering; `fetch_candidate_financials` sorts client-side.

## Full rebuild (dead Pi / dead SD card)

1. Flash Raspberry Pi OS (64-bit) to a new SD card; boot; restore network.
2. Install: `docker` (+compose plugin), `nginx`, `sqlite3`, `gh`, `node` (v20+),
   `git`, `rsync`.
3. Point docker + containerd at the NVMe **before** pulling anything:
   - `/etc/docker/daemon.json`: `{"data-root": "/mnt/nvme/docker"}`
   - `/etc/containerd/config.toml`: `root = "/mnt/nvme/containerd"`
   - If the NVMe survived, the old images/volumes are already there and
     containers will reappear after `systemctl start containerd docker`.
4. Clone the repo to `/mnt/nvme/modern-punk` (or restore `site-*.tar.gz`
   from the backup drive). Restore `.env` from `env-*.bak` — **it holds all
   API keys and is not in git**.
5. If the NVMe was lost too: create the volume
   (`docker volume create civitas_app_data`), then restore data:
   `sqlite3` copy `civitas-<date>.db` → `<volume>/civitas.db`, and untar
   `chroma-<date>.tar.gz` into the volume.
6. Mount the backup drive: fstab line
   `UUID=448B-5160 /media/usb-backup exfat defaults,nofail 0 0`.
7. Restore root crontab entries (backup at 1 AM; see `crontab -l` snapshot
   in the backup dir if present).
8. `./deploy.sh` (builds both images, writes nginx config, flips traffic).
9. Verify: `curl localhost/api/health`, admin dashboard, then let the
   nightly pipeline run and check `groundTruthFailures == []`.

## Escalation surfaces

- Pipeline state + ground-truth verdicts: `/api/admin/pipeline/status` (Bearer token in `.env`).
- Score drift monitoring: calibration report logs at the end of each run.
- Methodology changelog (user-facing): `/about#changelog`, backed by
  `frontend/src/lib/scoreVersions.ts` — keep in sync with `ALGORITHM_VERSION`
  in `backend/app/pipeline/analyze/score_calculator.py`.
