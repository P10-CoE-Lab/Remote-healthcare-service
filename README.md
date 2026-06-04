# Remote Healthcare Service

Full-stack IoT demo for the Remote Healthcare Monitoring POC.
Ingests simulated patient vitals over MQTT, stores them in InfluxDB, visualises them in Grafana,
and dispatches alerts via the Notification Service when thresholds are breached.

---

## Architecture

```
Human-Vitals-Simulator  →  MQTT (Mosquitto)  →  remote-healthcare (ingestion + alert trigger)
                                                         ↓                        ↓
                                                     InfluxDB              Notification Service
                                                         ↓                        ↓
                                                      Grafana                  Mailhog
```

All services are defined in `docker-compose.yml` and start with a single command.

---

## Prerequisites

- Docker and Docker Compose installed and running

All modules are self-contained inside this repository:
- `notification/` — Notification Service
- `simulator_service/` — Human Vitals Simulator
- `service/` — Remote Healthcare ingestion app

---

## Quick Start

### 1. Go to this folder

```bash
cd /path/to/Remote-healthcare-service
```

### 2. Start the full stack

```bash
docker compose up -d --build
```

This builds and starts all 7 containers:

| Container | Role | Port |
|---|---|---|
| `mosquitto` | MQTT broker | 1883 |
| `influxdb` | Time-series store | 8086 |
| `grafana` | Dashboard | 3000 |
| `mailhog` | Test email sink | 8025 (web), 1025 (SMTP) |
| `remote-healthcare` | Vitals ingestion + alert trigger | — |
| `notification` | Notification dispatch | 8000 |
| `simulator` | Publishes simulated sensor data | — |

### 3. Check all containers are healthy

```bash
docker compose ps
```

Wait until `mosquitto` and `influxdb` show `healthy` — all other services depend on them.

### 4. Open the dashboards

| Service | URL | Credentials |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| InfluxDB | http://localhost:8086 | admin / adminpassword |
| Notification API | http://localhost:8000 | — |
| Test emails (Mailhog) | http://localhost:8025 | — |

---

## Default Simulation

The simulator starts automatically with:

| Setting | Value |
|---|---|
| Persona | `cardiac_patient.yaml` |
| Scenario | `tachycardia_episode.yaml` |
| Compression | 10× (30-minute scenario plays in ~3 minutes) |

---

## Changing Persona / Scenario / Compression

### Option 1 — One-off override (no file editing)

Stop the running simulator, then relaunch it with different arguments:

```bash
# Stop the simulator container
docker compose stop simulator

# Healthcare — tachycardia episode at 10× compression (~3 min)
docker compose run --rm simulator \
  --persona personas/cardiac_patient.yaml \
  --scenario scenarios/health/tachycardia_episode.yaml \
  --compression 10

# Healthcare — low SpO2 event at 10× compression (~3 min)
docker compose run --rm simulator \
  --persona personas/cardiac_patient.yaml \
  --scenario scenarios/health/low_spo2_event.yaml \
  --compression 10

# Worker safety — fatigue escalation at 60× compression (~8 min)
docker compose run --rm simulator \
  --persona personas/welder_factory.yaml \
  --scenario scenarios/worker/fatigue_escalation.yaml \
  --compression 60

# Worker safety — fall incident at 60× compression (~2 min)
docker compose run --rm simulator \
  --persona personas/welder_factory.yaml \
  --scenario scenarios/worker/fall_incident.yaml \
  --compression 60
```

> The infrastructure (`mosquitto`, `influxdb`, `grafana`, etc.) stays running.
> Only the simulator container is replaced.

### Option 2 — Change the permanent default

Edit the `command:` block in `docker-compose.yml` under the `simulator` service:

```yaml
  simulator:
    command: >
      --persona personas/welder_factory.yaml
      --scenario scenarios/worker/fatigue_escalation.yaml
      --compression 60
```

Then restart the simulator:

```bash
docker compose up -d --build simulator
```

---

## Stopping the Stack

```bash
# Stop all containers (keeps volumes — data is preserved)
docker compose down

# Stop and delete all data volumes
docker compose down -v
```

---

## Environment Variables

All variables have safe defaults and work out of the box. Override by creating a `.env` file
in this directory:

```bash
# .env (optional — all values below are the defaults)
INFLUXDB_TOKEN=my-super-secret-token
GRAFANA_PASSWORD=admin
ALERT_EMAIL=test@example.com
ALERT_PHONE=
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

To receive real alert emails via Gmail instead of Mailhog, set `GMAIL_ADDRESS` and
`GMAIL_APP_PASSWORD` and update `notification-config.yaml` accordingly.