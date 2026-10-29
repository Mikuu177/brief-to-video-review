#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
pg_bin="${REVIEW_PG_BIN:-/opt/homebrew/opt/postgresql@15/bin}"
redis_bin="${REVIEW_REDIS_BIN:-/opt/homebrew/opt/redis/bin}"
mkdir -p data
owned_pids=()
cleanup() {
  if (( ${#owned_pids[@]} )); then
    kill "${owned_pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ ! -d data/postgres ]]; then
  "$pg_bin/initdb" -D data/postgres -U creator -A trust --encoding=UTF8 --locale=C
fi
if ! "$pg_bin/pg_isready" -h 127.0.0.1 -p 55432 -q; then
  "$pg_bin/pg_ctl" -D data/postgres -l data/postgres.log -o '-h 127.0.0.1 -p 55432 -k /tmp' start
fi
if ! "$pg_bin/psql" -h 127.0.0.1 -p 55432 -U creator -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='creator_review'" | rg -q 1; then
  "$pg_bin/createdb" -h 127.0.0.1 -p 55432 -U creator creator_review
fi
if ! "$redis_bin/redis-cli" -p 56379 ping >/dev/null 2>&1; then
  "$redis_bin/redis-server" --bind 127.0.0.1 --port 56379 --dir "$project_root/data" --appendonly yes --daemonize yes --logfile redis.log --pidfile redis.pid
fi
.venv/bin/python scripts/bootstrap.py
if ! curl --noproxy '*' -fsS http://127.0.0.1:8008/api/v1/health/live >/dev/null 2>&1; then
  nohup env PYTHONPATH="$project_root/apps/api" "$project_root/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8008 >data/api.log 2>&1 &
  echo $! >data/api.pid
  owned_pids+=("$(cat data/api.pid)")
fi
if [[ ! -f data/worker.pid ]] || ! kill -0 "$(cat data/worker.pid)" 2>/dev/null; then
  nohup "$project_root/.venv/bin/python" "$project_root/scripts/worker.py" >data/worker.log 2>&1 &
  echo $! >data/worker.pid
  owned_pids+=("$(cat data/worker.pid)")
fi
if [[ ! -f data/recovery.pid ]] || ! kill -0 "$(cat data/recovery.pid)" 2>/dev/null; then
  nohup "$project_root/.venv/bin/python" "$project_root/scripts/recovery_loop.py" >data/recovery.log 2>&1 &
  echo $! >data/recovery.pid
  owned_pids+=("$(cat data/recovery.pid)")
fi
printf '%s\n' 'Local preview: http://127.0.0.1:8008' 'Single-user workspace: no login required by default.'
if (( ${#owned_pids[@]} )); then
  printf '%s\n' 'Keep this terminal open. Ctrl+C stops the API and worker started by this script.'
  wait
fi
