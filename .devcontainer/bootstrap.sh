#!/usr/bin/env bash
set -euo pipefail

# Explicit, idempotent migration and unapproved synthetic seed; never deletes data.
uv run --frozen --all-packages python -m goldenloop_api.bootstrap --seed
