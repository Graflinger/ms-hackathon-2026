#!/usr/bin/env bash
set -euo pipefail

# Fresh named volumes can inherit root ownership from the workspace bind mount.
sudo chown "$(id -u):$(id -g)" /var/lib/goldenloop .venv apps/web/node_modules
uv sync --frozen --all-packages
npm --prefix apps/web ci
printf '%s\n' \
  'Dependencies ready. Database creation is explicit and never resets existing data:' \
  '  bash .devcontainer/bootstrap.sh' \
  'Start the API: uv run --frozen --all-packages uvicorn goldenloop_api.main:app --host 127.0.0.1 --port 8000 --workers 1' \
  'Start the UI: npm --prefix apps/web run dev -- --host 127.0.0.1'
