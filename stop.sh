#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Python services..."
for pid_file in .pid.simulator .pid.rule_engine; do
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    kill "$pid" 2>/dev/null && echo "  Stopped PID $pid ($pid_file)" || true
    rm -f "$pid_file"
  fi
done

echo "Stopping Docker services..."
docker-compose -f docker-compose.dev.yml down

echo "Done."
