# Remote Healthcare Monitoring — Project Overview

## What This Project Is

This project is a remote healthcare monitoring proof-of-concept (POC) built to demonstrate
how IoT wearable devices can continuously track cardiac patients and automatically trigger
alerts when clinical thresholds are crossed.

The system simulates multiple concurrent patients wearing wearable devices that stream
vital signs in real time. A rule engine evaluates the data, scores clinical risk, and
sends email alerts when a patient's condition deteriorates. A Grafana dashboard gives
clinicians a live fleet view, and a React control panel lets a demo operator add patients,
trigger clinical events, and watch the system respond.

The entire pipeline mirrors what a production deployment would look like — the simulator
is the only component that would be swapped out for real hardware.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SIMULATION LAYER                                   │
│                                                                             │
│  Personas (YAML)  +  Scenarios (YAML)  →  Scenario Engine (Python)         │
│  "Who is the patient"   "What happens"     "Generates sensor readings"      │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ MQTT publish
                                    │ iots/healthcare/{persona}/{sensor}
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MESSAGE BROKER                                    │
│                                                                             │
│                    Mosquitto MQTT  (port 1883)                              │
└────────────┬──────────────────────┬────────────────────────────────────────┘
             │                      │
             │ subscribe            │ subscribe
             ▼                      ▼
┌────────────────────┐   ┌──────────────────────────────────────────────────┐
│    Telegraf        │   │                RULE ENGINE                       │
│                    │   │                                                  │
│  Reads MQTT        │   │  Edge Engine — instant threshold checks          │
│  Writes InfluxDB   │   │  Cloud Engine — windowed risk scoring            │
│  (vitals +         │   │  Alert Notifier — email via Notification Service │
│   rule_alerts)     │   │                                                  │
└────────┬───────────┘   │  Publishes alerts to: alerts/cloud/{persona}/…  │
         │               └──────────────────────────────────────────────────┘
         ▼                                      │
┌────────────────────┐                          │ subscribe (Telegraf)
│  InfluxDB          │ ◄────────────────────────┘
│  bucket: iot_poc   │
│  measurements:     │
│   vitals           │
│   rule_alerts      │
└────────┬───────────┘
         │ Flux queries
         ▼
┌────────────────────┐     ┌────────────────────────────────────────────────┐
│  Grafana           │     │  Demo Control UI (React)                       │
│                    │     │                                                │
│  Fleet Overview    │     │  Add/remove patients                           │
│  Patient Detail    │     │  Trigger clinical events                       │
│  (live dashboards) │     │  Watch vitals + alerts in real time            │
└────────────────────┘     │  Deep-link into Grafana per patient            │
                           └────────────────────────────────────────────────┘
                                           │
                                    ┌──────┴──────┐
                                    │ Demo API    │
                                    │ FastAPI     │
                                    │ :8000       │
                                    └─────────────┘
```

---

## Components

### 1. Simulator

The simulator is a scenario-driven engine — it does not model physiology from equations.
Instead, it runs scripted narratives defined in YAML files: who the patient is (persona),
and what happens to them over time (scenario).

**Original purpose:** Built to support both a worker safety POC and a healthcare POC.
For this project, only the healthcare POC is active. Worker safety personas and scenarios
exist in the codebase but are not used.

**Key adaptation for this project:** The simulator was extended to run multiple concurrent
patient engines in a single process (population mode). Each patient gets its own engine
instance, its own MQTT topic namespace, and a human-friendly label (Patient 001, Patient 002,
etc.) that flows all the way through to Grafana.

**How it works:**

1. A persona YAML defines baseline vitals, clinical thresholds, and noise characteristics
   for a patient type (e.g. 65-year-old cardiac patient).
2. A scenario YAML defines phases (time windows with different sensor ranges) and scripted
   events (e.g. "at 16 minutes, trigger tachycardia onset").
3. The scenario engine runs a compressed simulation clock. A 30-minute clinical event
   plays out in 3 minutes at 10x compression.
4. Each tick, every sensor generates a realistic value (with noise, drift, correlation
   between signals) and publishes it to MQTT.

**Sensor streams published per patient:**

| Sensor | MQTT Topic Suffix | Unit |
|---|---|---|
| Heart Rate | `heart_rate` | bpm |
| SpO₂ | `spo2` | % |
| Heart Rate Variability | `heart_rate_variability` | ms |
| Skin Temperature | `skin_temperature` | °C |
| Accel Magnitude | `accel_magnitude` | g |
| Battery Level | `battery_level` | % |

**MQTT payload schema** (same for all sensors):
```json
{
  "timestamp_utc": "2026-06-11T10:42:15.123Z",
  "device_id":     "sim-cardiac_patient-a3f1",
  "patient_label": "Patient 001",
  "persona_id":    "cardiac_patient",
  "poc_type":      "healthcare",
  "sensor_name":   "heart_rate",
  "value":         138.2,
  "unit":          "bpm",
  "phase":         "tachycardia",
  "condition":     "critical",
  "quality":       "good",
  "compression":   10,
  "sequence_number": 4217
}
```

---

### 2. Personas — Who Is Being Monitored

Personas are YAML files in `personas/`. Each defines a patient type with baseline vitals
and clinical alert thresholds.

| Persona File | Description | Baseline HR | SpO₂ | HRV |
|---|---|---|---|---|
| `cardiac_patient.yaml` | Female, 65 — known cardiac risk | 58–75 bpm | 96–99% | 35–65 ms |
| `elderly_hypertensive.yaml` | Male, 72 — hypertension | 72–88 bpm | 95–98% | 20–40 ms |
| `young_arrhythmia.yaml` | Female, 34 — known arrhythmia | 60–75 bpm | 97–99% | 15–35 ms |
| `post_surgery_recovery.yaml` | Male, 58 — post-operative | 65–80 bpm | 93–97% | 25–45 ms |
| `diabetic_cardiac.yaml` | Female, 67 — diabetes + cardiac | 68–82 bpm | 94–97% | 30–50 ms |

Adding a new patient type requires only a new YAML file — no code changes.

---

### 3. Scenarios — What Happens

Scenarios are YAML files in `scenarios/health/`. Each scripts a clinical narrative
with phases and events.

| Scenario | What It Demonstrates | Duration (sim) |
|---|---|---|
| `normal_resting.yaml` | Baseline — all vitals normal | 20 min |
| `tachycardia_episode.yaml` | Sudden HR spike to 138 bpm, SpO₂ drops, recovers | 30 min |
| `low_spo2_event.yaml` | Oxygen drops to 88%, alarm fires, recovery | 30 min |
| `bradycardia_episode.yaml` | Slow HR onset reaching 44 bpm | 30 min |
| `combined_deterioration.yaml` | HR up AND SpO₂ down simultaneously — max risk | 30 min |
| `recovery_progression.yaml` | Starts in alert state, gradually stabilises | 40 min |
| `hrv_stress_pattern.yaml` | HRV degrades steadily — cardiac stress marker | 30 min |
| `night_episode.yaml` | Sleep baseline, sudden nocturnal SpO₂ drop | 30 min |

At 10x compression, a 30-minute scenario plays out in 3 minutes — suitable for a demo.

Adding a new clinical scenario requires only a new YAML file — no code changes.

---

### 4. Rule Engine

The rule engine is a standalone Python package (`rule_engine/`) that runs independently
from the simulator. It subscribes to MQTT, evaluates clinical rules, scores risk, and
publishes structured alerts.

Architecture mirrors a real IoT system with two distinct evaluation layers:

#### Edge Engine

Evaluates threshold rules with no time window — fires as soon as a breach is detected.
Designed to mimic on-device logic on a wearable (e.g. ESP32).

| Rule | Trigger | Severity |
|---|---|---|
| SpO₂ critical | SpO₂ < 93% | Critical |
| Tachycardia | HR > 100 bpm | Warning |
| Bradycardia | HR < 50 bpm | Critical |
| Fall detected | Accel > 3.0 g | Emergency |

Publishes to: `alerts/edge/{persona_id}/{rule_id}`

Noise rejection: a rule must be breached in 3 consecutive readings before it fires.
Cooldown: 30 seconds per (device, rule) pair — prevents alert flooding.

#### Cloud Engine

Evaluates windowed conditions over rolling buffers. Mirrors what a cloud backend would
do after receiving telemetry from multiple devices.

| Rule | Sensor(s) | Window | Condition | Severity |
|---|---|---|---|---|
| Sustained tachycardia | HR | 30 s | 70% of readings > 100 bpm | Warning |
| Sustained bradycardia | HR | 30 s | 70% of readings < 50 bpm | Critical |
| HRV stress | HRV | 120 s | 80% of readings < 25 ms | Warning |
| Combined deterioration | HR + SpO₂ | 30 s | Both conditions met | Critical |

Risk scoring aggregates all active rules into a single numeric score (0–100) and maps
it to a risk level: `none → low → medium → high → critical`.

Publishes to: `alerts/cloud/{persona_id}/{rule_id}`

The cloud alert payload includes the full risk context:
```json
{
  "rule_id":        "H-COMBINED",
  "description":    "Combined HR elevation and SpO2 drop",
  "severity":       "critical",
  "patient_label":  "Patient 001",
  "device_id":      "sim-cardiac_patient-a3f1",
  "sensor_name":    "heart_rate",
  "sensor_value":   138.2,
  "threshold":      100.0,
  "risk_score":     85.0,
  "risk_level":     "critical",
  "conditions_met": ["heart_rate > 100", "spo2 < 93"]
}
```

---

### 5. Notification Service

The Notification Service (`Notification_Service/`) is a reusable email/SMS/webhook
delivery framework. For this project it is used exclusively to send email alerts when
the rule engine detects a clinical deterioration.

**Integration flow:**

```
Rule Engine publishes alert to alerts/cloud/#
        ↓
Alert Notifier (rule_engine/alert_notifier.py)
  subscribes to alerts/cloud/#
  applies cooldown filter (default 120 s per patient+rule pair)
  skips severity=info (only warning/critical/emergency trigger email)
        ↓
POST /notify to Notification Service (http://localhost:8001)
        ↓
Notification Service renders health_alert email template
        ↓
Mailhog (dev) or Gmail (production)
```

**Switching from Mailhog to Gmail:**
1. Set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `Notification_Service/.env`
2. Change `providers.email.default` from `mailhog` to `gmail` in
   `Notification_Service/notification-config.yaml`

No other changes needed.

**Email template variables** available in `Notification_Service/templates/health_alert/`:

| Variable | Example |
|---|---|
| `payload.patient_label` | Patient 001 |
| `payload.description` | Sustained tachycardia detected |
| `payload.severity` | critical |
| `payload.sensor_name` | Heart Rate |
| `payload.sensor_value` | 138.2 |
| `payload.threshold` | 100.0 |
| `payload.risk_score` | 85 |
| `payload.risk_level` | critical |
| `payload.conditions_met` | ["heart_rate > 100", "spo2 < 93"] |

---

### 6. Data Pipeline — MQTT → InfluxDB

Telegraf runs as a Docker container and bridges MQTT to InfluxDB.

It subscribes to two topic groups:

| Topic | InfluxDB Measurement | Tags stored |
|---|---|---|
| `iots/#` | `vitals` | device_id, patient_label, persona_id, poc_type, sensor_name, unit |
| `alerts/#` | `rule_alerts` | rule_id, persona_id, device_id, source, severity, risk_level |

All sensor values are stored as a `value` field. Alert risk scores are stored as a
`risk_score` field. This separation makes Grafana queries straightforward.

---

### 7. Grafana Dashboards

Two dashboards are provisioned automatically from JSON files in
`docker/grafana/dashboards/`. They appear in Grafana under **Vitals Simulator**.

#### Fleet Overview (`healthcare-overview`)

Shows all active patients on the same chart. Useful for spotting which patient
is deteriorating relative to the others.

Panels:
- Heart Rate — all patients (one line per patient, labelled by patient_label)
- SpO₂ — all patients
- HRV — all patients
- Risk Score — all patients (bar chart)

#### Patient Detail (`healthcare-detail`)

Single-patient deep-dive. The `Patient` dropdown at the top dynamically populates
from live InfluxDB data — only patients with recent readings appear.

Panels:
- HR gauge (live, last 15 s)
- SpO₂ gauge (live, last 15 s)
- HRV stat (live, last 15 s)
- Risk score stat (live, last 2 min)
- HR trend (time series)
- SpO₂ trend (time series)
- HRV trend (time series)
- Alert timeline (bar chart of risk score over time)

**Opening per-patient detail from the UI:** Each patient card in the React UI has a
link icon that opens the Grafana Patient Detail dashboard pre-filtered to that
patient. No manual dropdown selection needed.

---

### 8. Demo Control UI

The React UI (`demo/ui/`) is the operator's control panel during a demo.

**URL:** `http://localhost:8000`

**What the UI shows:**
- Header bar — total running patients, patients with active alerts, stable patients
- Patient cards — per-patient vitals (HR, SpO₂, HRV), risk badge, scenario phase,
  progress bar, sparkline trend, battery level, link to Grafana
- Alert feed — scrolling log of all recent clinical alerts with colour coding
- Add patient modal — pick a persona + scenario + compression speed

**What the operator can do from the UI:**
- Add a new patient (choose persona + scenario)
- Remove a patient
- Trigger a scripted clinical event on a running patient (e.g. force tachycardia onset)
- Open Grafana for any patient with one click

**Two views:**
- **Operator view** — full controls (add/remove/trigger events), polling every 2 s
- **Client view** — display-only version for showing on a presentation screen

---

## Configuration Reference

All configuration is in the root `.env` file. Edit this file before starting the project.

```
.env                         ← root config — edit this
Notification_Service/.env    ← email provider config (Mailhog vs Gmail)
config/edge_rules.yaml       ← edge alert thresholds
config/cloud_rules.yaml      ← windowed risk scoring rules
config/mqtt_config.yaml      ← MQTT broker address
personas/*.yaml              ← patient type definitions
scenarios/health/*.yaml      ← clinical scenario scripts
```

**Root `.env` settings:**

| Variable | Default | Purpose |
|---|---|---|
| `ALERT_EMAIL` | _(blank)_ | Recipient for email alerts. Leave blank to disable. |
| `ALERT_EMAIL_NAME` | `Healthcare Monitoring Team` | Display name on emails |
| `ALERT_COOLDOWN_SECONDS` | `120` | Min seconds between emails per patient+rule pair |
| `NOTIFICATION_SERVICE_URL` | `http://localhost:8001` | Notification Service address |
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB address |
| `INFLUXDB_TOKEN` | `my-token` | InfluxDB auth token |

---

## Starting and Stopping

### First-time setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Build the UI (only needed once, or after UI code changes)
cd demo/ui
npm install
npm run build
cd ../..

# 3. Set your alert email (optional)
# Edit .env and set ALERT_EMAIL=you@example.com
```

### Start everything

```bash
./start.sh
```

This single command:
1. Starts Docker services — Mosquitto, InfluxDB, Grafana, Telegraf, Mailhog, Notification Service
2. Waits for MQTT to be ready
3. Starts the simulator in population mode (`run.py --population --demo`)
4. Starts the rule engine service (`python -m rule_engine.service`)

All Python service logs are written to `logs/simulator.log` and `logs/rule_engine.log`.

### Stop everything

```bash
./stop.sh
```

Kills the Python processes and tears down all Docker containers.

### Service URLs after start

| Service | URL | Notes |
|---|---|---|
| Demo Control UI | http://localhost:8000 | Add patients, trigger events |
| Grafana | http://localhost:3000 | Login: admin / admin |
| Mailhog | http://localhost:8025 | View captured alert emails |
| InfluxDB | http://localhost:8086 | Login: admin / adminpassword |

### Follow logs

```bash
tail -f logs/simulator.log logs/rule_engine.log
```

---

## Running a Demo

1. Open **http://localhost:8000** in a browser
2. Open **http://localhost:3000** in a second tab (Grafana Fleet Overview)
3. In the UI, click **Add Patient** → choose a persona and scenario (e.g. cardiac patient + tachycardia episode)
4. The patient card appears with live vitals updating every 2 seconds
5. Grafana begins charting the patient's heart rate and SpO₂
6. After the scenario's onset phase (a few seconds at 10x compression), the rule engine
   fires an alert — the risk badge on the card turns red
7. If `ALERT_EMAIL` is set, an email arrives in Mailhog within 2 minutes
8. Click the link icon on the patient card to open the Grafana Patient Detail dashboard
   pre-filtered to that patient
9. Add a second patient with a different scenario to demonstrate fleet monitoring

---

## Project Structure

```
Vitals_simulator/
│
├── start.sh                    ← START: one command to run everything
├── stop.sh                     ← STOP: one command to stop everything
├── .env                        ← root configuration (edit before starting)
├── run.py                      ← simulator entry point
├── requirements.txt
├── docker-compose.dev.yml      ← all Docker services (infra + notification)
│
├── personas/                   ← patient type definitions (YAML — no code needed)
│   ├── cardiac_patient.yaml
│   ├── elderly_hypertensive.yaml
│   ├── young_arrhythmia.yaml
│   ├── post_surgery_recovery.yaml
│   └── diabetic_cardiac.yaml
│
├── scenarios/health/           ← clinical scenario scripts (YAML — no code needed)
│   ├── tachycardia_episode.yaml
│   ├── low_spo2_event.yaml
│   ├── bradycardia_episode.yaml
│   ├── combined_deterioration.yaml
│   ├── recovery_progression.yaml
│   ├── hrv_stress_pattern.yaml
│   ├── night_episode.yaml
│   └── normal_resting.yaml
│
├── config/                     ← rule and infrastructure config
│   ├── edge_rules.yaml         ← immediate threshold rules
│   ├── cloud_rules.yaml        ← windowed risk scoring rules
│   └── mqtt_config.yaml
│
├── simulator/                  ← simulation engine (scenario + sensors + MQTT)
│   ├── core/
│   ├── sensors/
│   └── transport/
│
├── rule_engine/                ← clinical rule evaluation service
│   ├── service.py              ← entry point
│   ├── alert_notifier.py       ← MQTT → email bridge
│   ├── edge/                   ← threshold engine
│   ├── cloud/                  ← windowed risk engine
│   └── shared/
│
├── demo/
│   ├── demo_api.py             ← FastAPI control layer (fleet endpoints)
│   └── ui/                     ← React + TypeScript dashboard
│       └── src/
│
├── docker/
│   ├── telegraf.conf           ← MQTT → InfluxDB pipeline config
│   └── grafana/
│       ├── dashboards/
│       │   ├── healthcare_overview.json   ← Fleet view (all patients)
│       │   └── healthcare_detail.json     ← Per-patient drill-down
│       └── provisioning/
│
├── Notification_Service/       ← email/SMS notification framework
│   ├── notification-config.yaml
│   ├── templates/
│   │   └── health_alert/
│   │       ├── email.html
│   │       └── email.txt
│   └── .env                    ← email provider config
│
└── logs/                       ← runtime logs (simulator, rule engine)
```

---

## Extending the Project

### Add a new patient type (no code required)
Copy `personas/_template.yaml`, fill in the baseline ranges and thresholds, save with
a descriptive name. It is immediately available in the UI scenario picker.

### Add a new clinical scenario (no code required)
Copy `scenarios/health/normal_resting.yaml`, define phases and events, reference an
existing persona. It appears in the UI automatically.

### Add a new alert rule
Edit `config/edge_rules.yaml` (for immediate threshold alerts) or
`config/cloud_rules.yaml` (for windowed risk scoring). No code changes needed.

### Add a new email template
Create a folder under `Notification_Service/templates/{profile_name}/` with
`email.html` and `email.txt`. No code changes needed.

### Switch email from Mailhog to Gmail
1. Add Gmail app password to `Notification_Service/.env`
2. Change `providers.email.default: mailhog` → `gmail` in
   `Notification_Service/notification-config.yaml`
3. Restart: `./stop.sh && ./start.sh`
