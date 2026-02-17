#!/usr/bin/env bash
set -euo pipefail

echo "Starting verbose monitor (DEBUG). Press Ctrl+C to stop."

while true; do
  date '+%H:%M:%S'
  curl -s http://localhost:8000/api/pipeline/status | python3 -c 'import sys,json;d=json.load(sys.stdin); lr=d.get("lastRun"); print("isRunning=", d.get("isRunning"), "| llmCalls=", lr.get("llmCalls") if lr else None, "| status=", lr.get("status") if lr else None)'

  echo '--- recent pipeline log lines ---'
  docker compose logs --no-color backend --tail 200 | grep -Ei 'Phase|Fetching senators|Fetching member details|Discovering significant bills|Fetching roll call|Fetching FEC|FEC API failed|Classifying bills|analyz|Ollama|call_llm|Processing|SAVE TO DATABASE|Upsert' || docker compose logs --no-color backend --tail 60

  echo '--- recent api_cache entries (top 8) ---'
  docker compose exec backend python -c "import sqlite3; c=sqlite3.connect('/data/modern-punk.db'); rows=c.execute(\"select tier || ' | ' || cache_key || ' | ' || cached_at from api_cache order by cached_at desc limit 8\").fetchall(); print('\n'.join(r[0] for r in rows)); c.close()" 2>/dev/null || true

  echo
  sleep 4
done
