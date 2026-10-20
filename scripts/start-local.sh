#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
test -f apps/web/.next/BUILD_ID || { echo 'Run npm run build first.' >&2; exit 1; }
test -x services/market/.venv/bin/uvicorn || { echo 'Install the market service with uv sync --frozen first.' >&2; exit 1; }
mkdir -p .runtime
backend_pid=''
web_pid=''
cleanup() {
  if [[ -n "$web_pid" ]]; then kill "$web_pid" 2>/dev/null || true; fi
  if [[ -n "$backend_pid" ]]; then kill "$backend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
services/market/.venv/bin/uvicorn app:app --app-dir services/market --host 127.0.0.1 --port 8000 > .runtime/market.log 2>&1 &
backend_pid=$!
node node_modules/next/dist/bin/next start apps/web --hostname 0.0.0.0 --port 3000 > .runtime/web.log 2>&1 &
web_pid=$!
for attempt in {1..30}; do
  kill -0 "$backend_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null || { echo 'A service stopped; inspect .runtime/market.log and .runtime/web.log.' >&2; exit 1; }
  if curl --silent --fail http://127.0.0.1:8000/health -o /dev/null && curl --silent --fail http://127.0.0.1:3000 -o /dev/null; then
    echo 'Dashboard and market API are ready. Logs: .runtime/web.log, .runtime/market.log.'
    wait -n "$backend_pid" "$web_pid"
    exit 1
  fi
  sleep 0.5
done
echo 'Services did not pass readiness within 15 seconds.' >&2
exit 1
