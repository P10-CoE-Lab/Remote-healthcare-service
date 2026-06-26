#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load config from .env
if [ -f .env ]; then
  set -a
  source .env
  set +a
else
  echo "Warning: .env not found — using defaults. Copy .env and set ALERT_EMAIL."
fi

mkdir -p logs

# ── 1. Docker infrastructure ──────────────────────────────────────────────────
echo "Starting Docker services (MQTT, InfluxDB, Grafana, Mailhog, Notification)..."
docker-compose -f docker-compose.dev.yml up -d --build

echo "Waiting for MQTT broker to be ready..."
sleep 4

# ── 2. Simulator ──────────────────────────────────────────────────────────────
echo "Starting simulator..."
nohup python run.py --population --demo > logs/simulator.log 2>&1 &
echo $! > .pid.simulator

# ── 3. Rule engine + alert notifier ──────────────────────────────────────────
echo "Starting rule engine..."
nohup python -m rule_engine.service > logs/rule_engine.log 2>&1 &
echo $! > .pid.rule_engine

echo ""
echo "All services running:"
echo "  Demo UI      →  http://localhost:8000"
echo "  Grafana      →  http://localhost:3000  (admin / admin)"
echo "  Mailhog      →  http://localhost:8025"
echo "  InfluxDB     →  http://localhost:8086"
echo ""

if [ -z "${ALERT_EMAIL:-}" ]; then
  echo "  Email alerts  → DISABLED (set ALERT_EMAIL in .env to enable)"
else
  echo "  Email alerts  → ${ALERT_EMAIL} (via Mailhog at http://localhost:8025)"
fi

echo ""
echo "Logs:  tail -f logs/simulator.log logs/rule_engine.log"
echo "Stop:  ./stop.sh"
