#!/usr/bin/env bash
# Keep strategy_runtime.py run --yes alive. Restarts on crash/exit.
# Stop gracefully: touch run_data/keeper.stop
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p run_data
LOGF=run_data/runtime_keeper.log
STOP=run_data/keeper.stop
rm -f "$STOP"
echo "keeper start $(date -u +%FT%TZ)" >> "$LOGF"
while :; do
  if [ -f "$STOP" ]; then
    echo "keeper stop marker found $(date -u +%FT%TZ)" >> "$LOGF"
    break
  fi
  echo "[keeper] launching runtime $(date -u +%FT%TZ)" >> "$LOGF"
  .venv/bin/python strategy_runtime.py run --yes >>"$LOGF" 2>&1
  code=$?
  echo "[keeper] runtime exited code=$code $(date -u +%FT%TZ)" >> "$LOGF"
  if [ -f "$STOP" ]; then
    break
  fi
  sleep 30
done