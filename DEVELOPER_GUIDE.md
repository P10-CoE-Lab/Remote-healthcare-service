# Remote Healthcare Monitoring Simulator — Developer Guide

Complete reference for any developer working on or integrating this simulator. Covers architecture, module responsibilities, YAML schemas, rule engine layers, LLM integration, demo API, and extension points.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [How a Simulation Run Works](#2-how-a-simulation-run-works)
3. [File Reference](#3-file-reference)
4. [Signal Generation](#4-signal-generation)
5. [Noise Models](#5-noise-models)
6. [Sensor Correlation](#6-sensor-correlation)
7. [Time Compression](#7-time-compression)
8. [Rule Engine — Edge Layer](#8-rule-engine--edge-layer)
9. [Rule Engine — Cloud Layer](#9-rule-engine--cloud-layer)
10. [LLM Integration](#10-llm-integration)
11. [Alert Notification](#11-alert-notification)
12. [MQTT Contract](#12-mqtt-contract)
13. [Demo API Reference](#13-demo-api-reference)
14. [YAML Schema Reference](#14-yaml-schema-reference)
15. [Extending the Simulator](#15-extending-the-simulator)
16. [Running Locally](#16-running-locally)
17. [Docker Integration](#17-docker-integration)
18. [Environment Variables Reference](#18-environment-variables-reference)
19. [Error Handling Rules](#19-error-handling-rules)

---

## 1. Architecture Overview

### Full system stack

```
┌──────────────────────────────────────────────────────────┐
│  run.py  — simulator entry point (--population --demo)    │
│  demo/demo_api.py  — FastAPI backend (port 8000)         │
│  demo/ui/          — React + TypeScript UI               │
├──────────────────────────────────────────────────────────┤
│  simulator/core/scenario_engine.py                        │
│   ├─ persona_loader.py   (YAML → Persona dataclass)      │
│   ├─ scenario_loader.py  (YAML → Scenario dataclass)     │
│   └─ condition_mapper.py (phase → SensorParams)          │
├──────────────────────────────────────────────────────────┤
│  simulator/sensors/         simulator/engine/             │
│   ├─ heart_rate_sensor.py    ├─ signal.py                │
│   ├─ spo2_sensor.py          ├─ noise.py                 │
│   ├─ hrv_sensor.py           ├─ correlation.py           │
│   └─ registry.py             └─ fault.py                 │
├──────────────────────────────────────────────────────────┤
│  simulator/transport/mqtt_publisher.py                    │
└──────────────────────────────────────────────────────────┘
          │ MQTT publish (iots/healthcare/...)
          ▼
   ┌─────────────┐    ┌────────────────────────────────┐
   │ MQTT broker │    │  rule_engine/service.py         │
   │ (Mosquitto) │───▶│   ├─ edge/engine.py             │
   └─────────────┘    │   ├─ cloud/engine.py            │
          │           │   ├─ cloud/personalised_analyzer│
          ▼           │   └─ alert_notifier.py          │
   ┌─────────────┐    └────────────┬───────────────────┘
   │  Telegraf   │                 │ MQTT alerts
   └──────┬──────┘                 ▼
          ▼                 demo_api._on_message()
   ┌─────────────┐           → _alert_buffer
   │  InfluxDB   │           → _patient_alert_history
   └──────┬──────┘                 │
          ▼                        ▼
   ┌─────────────┐       React UI (alerts, briefings)
   │   Grafana   │
   └─────────────┘
```

### Key design principles

- **Scenario-first.** The simulator tells a scripted story. Phases are narrative arcs; events are scripted moments. Demos are predictable and repeatable.
- **Config over code.** Personas, scenarios, and rules all live in YAML. No Python changes are needed to run a new demo or add new alert rules.
- **Demo-safe reliability.** Sensor errors are logged silently and the last known good value is returned. MQTT disconnects are retried with backoff.
- **Dual-layer detection.** Edge rules catch known conditions immediately. Cloud rules detect patient-specific deviations that would not trigger fixed thresholds.

---

## 2. How a Simulation Run Works

### Startup sequence

`start.sh` starts three processes:

1. **Docker stack** — Mosquitto, InfluxDB, Telegraf, Grafana, Mailhog (via `docker-compose.dev.yml`)
2. **Simulator** — `python run.py --population --demo`
   - `--population` mode loads all patients registered with the demo API and runs them concurrently
   - `--demo` starts the FastAPI backend on `DEMO_API_PORT`
3. **Rule engine** — `python -m rule_engine.service`
   - Subscribes to `iots/healthcare/#` on MQTT
   - Evaluates each incoming message through edge + cloud rule engines
   - Publishes alerts to `iots/healthcare/{patient_id}/alerts`

### Per-patient simulation loop

Each active patient in the fleet runs a `ScenarioEngine` instance:

1. Loads persona YAML → `Persona` dataclass
2. Loads scenario YAML → `Scenario` dataclass
3. Runs three concurrent asyncio tasks:
   - **`_heartbeat_loop()`** — advances simulated clock, fires phase transitions and scenario events
   - **`_sensor_loop(sensor)`** — one per sensor; generates value, applies noise/correlation/fault, publishes to MQTT
   - **`_manual_event_loop()`** — processes events triggered via the demo API

### Alert flow

```
MQTT sensor message
  → rule_engine/service.py
     → EdgeEngine.evaluate()     — deterministic threshold check
     → CloudEngine.evaluate()    — Isolation Forest anomaly check
        → PersonalisedAnalyzer   — per-patient model (LEARNING → ACTIVE)
  → alert published to MQTT alerts topic
  → demo_api._on_message() picks it up
     → deduplication fingerprint (source + rule_id + patient + 30s bucket)
     → _alert_buffer.appendleft(alert)
     → _patient_alert_history[patient_id].append(alert)  ← never wiped
  → React UI polls GET /alerts
```

`_patient_alert_history` is a permanent per-patient accumulator. It survives `DELETE /alerts` (which only clears `_alert_buffer`). This ensures the AI Briefing always sees the full session history.

---

## 3. File Reference

### Entry point and scripts

| File | Role |
|---|---|
| [run.py](run.py) | Simulator CLI entry point. Supports `--persona/--scenario` (single run) and `--population` (fleet mode). Handles SIGTERM graceful shutdown. |
| [start.sh](start.sh) | Starts Docker stack, simulator, and rule engine. Writes PIDs to `.pid.*` files. |
| [stop.sh](stop.sh) | Kills simulator and rule engine processes, stops Docker stack. |

### Core layer — `simulator/core/`

| File | Role |
|---|---|
| [scenario_engine.py](simulator/core/scenario_engine.py) | Main orchestrator. Owns simulation clock, phase transitions, event firing, sensor task management. |
| [persona_loader.py](simulator/core/persona_loader.py) | Reads persona YAML → `Persona` dataclass. Raises `ConfigurationError` on any missing field. |
| [scenario_loader.py](simulator/core/scenario_loader.py) | Reads scenario YAML → `Scenario` dataclass. Validates phase contiguity, compression > 0, event times within bounds. |
| [condition_mapper.py](simulator/core/condition_mapper.py) | Converts scenario phase + persona baseline → `SensorParams`. Classifies readings as normal/warning/critical. |

### Engine layer — `simulator/engine/`

| File | Role |
|---|---|
| [signal.py](simulator/engine/signal.py) | Five signal behavior generators: `oscillating_stable`, `monotonic_rising`, `monotonic_falling`, `mean_reverting`, `step`. |
| [noise.py](simulator/engine/noise.py) | Four noise models: `gaussian`, `drift`, `burst`, `quantization`. |
| [correlation.py](simulator/engine/correlation.py) | Time-lagged leader/follower coupling. Maintains per-leader lag buffers. |
| [fault.py](simulator/engine/fault.py) | Fault injection: `dropout`, `flatline`, `spike`, `noise_burst`. |

### Sensor layer — `simulator/sensors/`

| File | Role |
|---|---|
| [base.py](simulator/sensors/base.py) | `BaseSensor` abstract class and `SensorReading` dataclass. |
| [registry.py](simulator/sensors/registry.py) | `SENSOR_REGISTRY` — maps sensor name strings to sensor classes. The extension point for new sensors. |
| [heart_rate_sensor.py](simulator/sensors/heart_rate_sensor.py) | PPG heart rate (bpm). Leads temperature and SpO₂ in correlation. |
| [spo2_sensor.py](simulator/sensors/spo2_sensor.py) | Pulse oximeter SpO₂ (%). Quantization noise. Follows HR. |
| [hrv_sensor.py](simulator/sensors/hrv_sensor.py) | Heart rate variability (ms). Inversely coupled to HR — tachycardia drops HRV. |
| [battery_sensor.py](simulator/sensors/battery_sensor.py) | Device battery level (%). Slow monotonic drain over the session. |

### Transport layer

| File | Role |
|---|---|
| [transport/mqtt_publisher.py](simulator/transport/mqtt_publisher.py) | Async paho-mqtt wrapper. Builds topic from `prefix/poc_type/persona_id/sensor_name`. Reconnects with exponential backoff. |

### Rule engine — `rule_engine/`

| File | Role |
|---|---|
| [service.py](rule_engine/service.py) | MQTT microservice entrypoint. Subscribes to all healthcare topics, routes messages through edge + cloud engines, publishes alerts. |
| [edge/engine.py](rule_engine/edge/engine.py) | Deterministic threshold rule evaluator. Loads rules from `config/edge_rules.yaml`. Fires immediately when a threshold is crossed. |
| [edge/config.py](rule_engine/edge/config.py) | Loads and validates edge rule YAML. |
| [cloud/engine.py](rule_engine/cloud/engine.py) | Orchestrates personalised anomaly detection. Manages per-patient model state. |
| [cloud/personalised_analyzer.py](rule_engine/cloud/personalised_analyzer.py) | Per-patient Isolation Forest model. LEARNING phase collects training vectors; ACTIVE phase detects anomalies and computes SHAP values. |
| [cloud/rule_analyzer.py](rule_engine/cloud/rule_analyzer.py) | Applies cloud rule thresholds on top of anomaly scores. |
| [cloud/config.py](rule_engine/cloud/config.py) | Loads and validates cloud rule YAML. |
| [llm/provider.py](rule_engine/llm/provider.py) | Provider-agnostic LLM abstraction. Supports `mock`, `anthropic`, `openai`, `gemini`. Selected via `LLM_PROVIDER` env var. |
| [llm/summariser.py](rule_engine/llm/summariser.py) | Two prompt templates: per-alert explanation (fast tier) and on-demand patient summary (quality tier). |
| [llm/context_builder.py](rule_engine/llm/context_builder.py) | Builds the structured context dict sent to the LLM. Queries InfluxDB for session vitals history. Computes trend directions. |
| [alert_notifier.py](rule_engine/alert_notifier.py) | Routes alerts to the Notification Service HTTP API. Respects per-patient cooldown to prevent flooding. |
| [shared/models.py](rule_engine/shared/models.py) | Shared dataclasses: `Alert`, `VitalReading`, `RuleMatch`. |
| [shared/logger.py](rule_engine/shared/logger.py) | Structured JSON logger used across the rule engine. |

### Demo layer

| File | Role |
|---|---|
| [demo/demo_api.py](demo/demo_api.py) | FastAPI backend. Fleet management, patient registry, alert buffer, AI summary endpoint. Subscribes to MQTT alerts from rule engine. Also runs an embedded edge scan (`_scan_fleet_violations`) for direct detection. |
| [demo/ui/](demo/ui/) | React + TypeScript + Tailwind source. Two views: `OperatorView` (fleet) and `ClientView` (clinician). Build output goes to `demo/static/`. |

### Config files

| File | Role |
|---|---|
| [config/edge_rules.yaml](config/edge_rules.yaml) | Threshold rules for the edge engine: conditions, severities, cooldowns. |
| [config/cloud_rules.yaml](config/cloud_rules.yaml) | Anomaly score thresholds and feature weights for the cloud engine. |

---

## 4. Signal Generation

Signal generators live in [simulator/engine/signal.py](simulator/engine/signal.py). Each receives `phase_min`, `phase_max`, and `phase_progress` (0.0–1.0) and returns a base value before noise.

| Behavior | What it models | Example use |
|---|---|---|
| `oscillating_stable` | Oscillates around midpoint ± amplitude. Steady state with natural variation. | Resting HR, normal SpO₂ |
| `monotonic_rising` | Rises linearly from min toward max as phase progresses. | HR during tachycardia onset, SpO₂ recovering |
| `monotonic_falling` | Falls from max toward min. | HR during recovery, SpO₂ dropping |
| `mean_reverting` | Deviates with volatility, gets pulled back to midpoint. | HR during peak tachycardia — erratic but bounded |
| `step` | Jumps to max at phase start and holds with minimal jitter. | Discrete state change |

---

## 5. Noise Models

| Model | What it models | Default for |
|---|---|---|
| `gaussian` | Zero-mean white noise. Clean electronic noise. | HR, HRV |
| `drift` | Accumulated random walk with weak reversion. Calibration drift. | Temperature |
| `burst` | Background noise with occasional large transients. | High-activity phases |
| `quantization` | Stepwise output rounded to nearest step size. | SpO₂ (reports in 1% increments) |

Scenario YAML specifies `noise_intensity: low | medium | high`. This maps to a dimensionless float that scales with the sensor's range width.

---

## 6. Sensor Correlation

Physiological coupling between sensors via time-lagged leader/follower pairs, implemented in [simulator/engine/correlation.py](simulator/engine/correlation.py).

### Healthcare correlations

| Leader | Follower | Strength | Lag | Why |
|---|---|---|---|---|
| `heart_rate` | `spo2` | −0.25 | 60s | Tachycardia slightly reduces blood oxygen |
| `heart_rate` | `heart_rate_variability` | −0.4 | 10s | High HR compresses beat-to-beat variation |

`coupling_strength` can be negative — a rising leader causes a falling follower.

---

## 7. Time Compression

```
simulated_seconds_elapsed = real_seconds_elapsed × compression
```

| Component | Effect |
|---|---|
| Phase durations | `start_minute` / `end_minute` are simulated minutes. At 10×, a 30-minute scenario runs in 3 real minutes. |
| Sensor sampling | `sampling_interval_ms` is divided by compression. |
| Event `at_minute` | Simulated minute — fires when the simulated clock reaches it. |
| Event `duration_seconds` | **Real seconds**, not simulated. An event lasts the same wall-clock time at any compression. |
| Training window (cloud rules) | Scaled by compression so `min_training_window_seconds` means simulated seconds, not real seconds. |

---

## 8. Rule Engine — Edge Layer

**Location:** `rule_engine/edge/`  
**Config:** `config/edge_rules.yaml`

Edge rules are deterministic YAML-configured threshold checks. They fire immediately when a vital crosses a boundary.

### Rule structure (`edge_rules.yaml`)

```yaml
rules:
  - rule_id: tachycardia_warning
    description: "Heart rate above warning threshold"
    sensor: heart_rate
    condition: gt              # gt | lt | gte | lte
    threshold: 100
    severity: warning          # warning | critical
    cooldown_seconds: 60       # minimum gap between same rule + same patient
```

Edge rules are stateless per tick — no learning required. They are evaluated synchronously on every incoming MQTT message.

---

## 9. Rule Engine — Cloud Layer

**Location:** `rule_engine/cloud/`  
**Config:** `config/cloud_rules.yaml`

The cloud layer uses Isolation Forest to detect anomalies relative to each patient's individual baseline. This catches deviations that would not trigger fixed thresholds — for example, a HR of 90 bpm may be normal for one patient but anomalous for another.

### PersonalisedAnalyzer states

```
LEARNING  → collects training vectors (min 40 samples + min training window)
ACTIVE    → model trained; evaluates anomaly score on each new vector
```

The training window is scaled by the simulation compression factor, so at 10× compression the model activates after ~4 real minutes.

### SHAP explanations

When the model fires an alert, SHAP values are computed to explain which features contributed most to the anomaly score. These are passed to the LLM and shown in the UI alert detail panel.

### Cloud rule config (`cloud_rules.yaml`)

```yaml
rules:
  - rule_id: personalised_anomaly
    description: "Multi-variate personalised anomaly"
    features: [heart_rate, spo2, heart_rate_variability]
    anomaly_threshold: 0.6     # Isolation Forest anomaly score threshold
    severity: warning
    cooldown_seconds: 30
```

---

## 10. LLM Integration

**Location:** `rule_engine/llm/`

Two modes of LLM use:

### Mode A — Per-alert explanation (fast tier)

Called automatically when an alert is generated. Produces a 3–4 sentence clinical narrative explaining the alert relative to the patient's personal baseline. Shown in the alert detail panel in the UI.

### Mode B — On-demand patient summary (quality tier)

Called when the clinician clicks "AI Briefing" in the patient view. Produces a four-section structured briefing: Current Status, Session Trend, Alert Pattern, Recommendation.

### Context structure

The context dict sent to the LLM (`build_summary_context`):

```python
{
    "patient": { "label", "persona_id", "description", "monitoring_session_minutes" },
    "personal_baseline": { "hr_normal", "spo2_normal", "hrv_normal", "personalised" },
    "current_vitals": {
        "heart_rate": { "value", "unit", "trend" },
        "spo2":       { "value", "unit", "trend" },
        "hrv":        { "value", "unit", "trend" },
    },
    "alert_history": {
        "total_alerts_fired": int,
        "alerts": [ { "time_ago", "rule", "description", "severity", "source" } ]
    },
    "compression": float,
}
```

`alert_history.alerts` is sourced from `_patient_alert_history` — the permanent per-patient accumulator that survives `DELETE /alerts`.

### Provider selection

Set `LLM_PROVIDER` in `.env`. The `make_provider(tier)` factory in `provider.py` returns the correct provider. Both tiers can be different providers (though in practice the same provider is used with different model sizes).

---

## 11. Alert Notification

**Location:** `rule_engine/alert_notifier.py`

When an alert is generated, `AlertNotifier` calls the Notification Service HTTP API (`POST /notify`). A per-patient + per-rule cooldown prevents flooding.

The Notification Service (`Notification_Service/`) is a separate process that handles:
- Email delivery (via Mailhog in dev, configurable SMTP in prod)
- SMS (via Twilio or AWS SNS)
- Webhook (Slack, generic)

Configure channels in `Notification_Service/notification-config.yaml`.

---

## 12. MQTT Contract

### Topic pattern

```
iots/healthcare/{persona_id}/{sensor_name}
```

Examples:
```
iots/healthcare/cardiac_patient/heart_rate
iots/healthcare/cardiac_patient/spo2
iots/healthcare/cardiac_patient/heart_rate_variability
iots/healthcare/{patient_id}/alerts          ← alert messages from rule engine
```

### Sensor payload schema

```json
{
  "timestamp_utc":   "2026-04-06T10:42:15.123456+00:00",
  "device_id":       "sim-cardiac_patient-001",
  "persona_id":      "cardiac_patient",
  "poc_type":        "healthcare",
  "sensor_name":     "heart_rate",
  "value":           84.2,
  "unit":            "bpm",
  "phase":           "resting_normal",
  "condition":       "normal",
  "quality":         "good",
  "fault_active":    false,
  "sequence_number": 4217
}
```

`condition`: `normal` | `warning` | `critical`  
`quality`: `good` | `uncertain` | `bad`

---

## 13. Demo API Reference

Base URL: `http://localhost:8000`. Interactive docs: `/docs`.

### Patient / Fleet endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /fleet` | GET | All active patients with current vitals and status |
| `POST /patients` | POST | Add a patient to the fleet (starts simulation) |
| `DELETE /patients/{patient_id}` | DELETE | Remove a patient and stop their simulation |
| `GET /patients/{patient_id}` | GET | Single patient status, vitals, and phase |

**POST /patients request:**
```json
{
  "patient_id":    "patient_001",
  "label":         "Patient 001",
  "persona_path":  "personas/cardiac_patient.yaml",
  "scenario_path": "scenarios/health/tachycardia_episode.yaml",
  "compression":   10
}
```

### Alert endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /alerts` | GET | Recent alerts from buffer (last 100, across all patients) |
| `GET /alerts/{patient_id}` | GET | Alerts for a specific patient |
| `DELETE /alerts` | DELETE | Clear the display buffer (does NOT clear per-patient history) |

### Summary / LLM endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /summary/{patient_id}` | GET | Generate AI clinical briefing for a patient (quality LLM tier) |

### Scenario / Event endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /scenarios` | GET | List all available scenario YAML files |
| `GET /personas` | GET | List all available persona YAML files |
| `POST /event/trigger` | POST | Fire a named event immediately for a patient |
| `POST /compression/set` | POST | Change compression factor at runtime |

### Status endpoint

| Endpoint | Method | Description |
|---|---|---|
| `GET /status` | GET | Engine health, running patient count, uptime |

---

## 14. YAML Schema Reference

### Persona YAML

```yaml
schema_version: "1.0"
persona_id: cardiac_patient         # must match filename stem
description: "Female, 65 yrs, known cardiac risk"
poc_type: healthcare                 # healthcare | worker_safety

baseline:
  heart_rate:
    min: 58
    max: 75
    unit: bpm
    sampling_interval_ms: 1000
  spo2:
    min: 96
    max: 99
    unit: percent
    sampling_interval_ms: 2000
  heart_rate_variability:
    min: 35
    max: 65
    unit: ms
    sampling_interval_ms: 5000

thresholds:
  heart_rate_high: 100              # tachycardia
  heart_rate_low:  50               # bradycardia
  spo2_low:        93               # low oxygen
  hrv_low:         20               # low HRV

noise_profile:
  heart_rate:           gaussian
  spo2:                 quantization
  heart_rate_variability: gaussian

correlations:
  - leader_sensor:    heart_rate
    follower_sensor:  spo2
    coupling_strength: -0.25
    lag_seconds: 60
  - leader_sensor:    heart_rate
    follower_sensor:  heart_rate_variability
    coupling_strength: -0.4
    lag_seconds: 10
```

### Scenario YAML

```yaml
schema_version: "1.0"
scenario_id: tachycardia_episode
description: "Patient at rest, sudden tachycardia onset, peaks, recovers"
persona: cardiac_patient
poc_type: healthcare

total_duration_minutes: 30
compression: 10                     # 30 simulated min → 3 real min

phases:
  - name: resting_normal
    start_minute: 0
    end_minute: 12
    sensors:
      heart_rate:
        min: 60
        max: 72
        behavior: oscillating_stable
      spo2:
        min: 97
        max: 99
        behavior: oscillating_stable
    noise_intensity: low

  - name: tachycardia
    start_minute: 16
    end_minute: 24
    sensors:
      heart_rate:
        min: 118
        max: 145
        behavior: mean_reverting
      spo2:
        min: 93
        max: 96
        behavior: monotonic_falling
    noise_intensity: medium

  # Phases must be contiguous: end_minute of phase N == start_minute of phase N+1
  # Last phase must end at total_duration_minutes

events:
  - at_minute: 16
    type: tachycardia_onset
    description: "Sudden tachycardia episode begins"
    overrides:
      heart_rate: 138
    duration_seconds: 20            # real seconds (not simulated)
```

---

## 15. Extending the Simulator

### Adding a new persona (no code required)

1. Copy `personas/_template.yaml`
2. Set `persona_id` to match the filename stem
3. Fill in `baseline`, `thresholds`, `noise_profile`, and optionally `correlations`
4. Save in `personas/` — it appears automatically in the UI

### Adding a new scenario (no code required)

1. Copy `scenarios/_template.yaml` to `scenarios/health/`
2. Set `scenario_id`, `persona`, `total_duration_minutes`, `compression`
3. Write contiguous phases: first starts at 0, last ends at `total_duration_minutes`
4. Write events for scripted moments
5. Save — appears automatically in the UI

**Behavior choice guide:**
- Steady state → `oscillating_stable`
- Gradual change → `monotonic_rising` or `monotonic_falling`
- Erratic peak → `mean_reverting`
- Sudden jump → `step`

### Adding a new edge rule (no code required)

Add a rule block to `config/edge_rules.yaml`. The rule engine picks it up on next restart.

### Adding a new sensor type (code required)

1. Create `simulator/sensors/new_sensor.py` implementing `BaseSensor`
2. Add to `SENSOR_REGISTRY` in [simulator/sensors/registry.py](simulator/sensors/registry.py)
3. Add to relevant persona YAML under `baseline`

### Adding a new LLM provider (code required)

1. Add a new provider class in [rule_engine/llm/provider.py](rule_engine/llm/provider.py) extending `LLMProvider`
2. Add a branch in the `make_provider()` factory function
3. Set `LLM_PROVIDER=your_provider` in `.env`

---

## 16. Running Locally

### Full stack (recommended)

```bash
cp .env.example .env    # edit as needed
./start.sh
./stop.sh
```

### Manual startup (for development)

```bash
# 1. Start infrastructure
docker compose -f docker-compose.dev.yml up -d

# 2. Start simulator
python run.py --population --demo

# 3. Start rule engine (separate terminal)
python -m rule_engine.service

# 4. Start React dev server (separate terminal, with hot reload)
cd demo/ui && npm run dev
```

### Logs

```bash
tail -f logs/simulator.log logs/rule_engine.log
```

---

## 17. Docker Integration

The simulator is designed to be embedded in a POC project's Docker stack as one service.

### Adding to a POC compose file

```yaml
services:
  simulator:
    build: ./vitals-simulator
    ports:
      - "8000:8000"
    environment:
      MQTT_HOST: mosquitto
      MQTT_PORT: 1883
      INFLUXDB_URL: http://influxdb:8086
      INFLUXDB_TOKEN: ${INFLUXDB_TOKEN}
      INFLUXDB_ORG: ${INFLUXDB_ORG}
      INFLUXDB_BUCKET: ${INFLUXDB_BUCKET}
      LLM_PROVIDER: ${LLM_PROVIDER}
      LLM_API_KEY: ${LLM_API_KEY}
      DEMO_API_PORT: 8000
    command: --population --demo
    depends_on:
      - mosquitto
      - influxdb
```

The simulator does not own MQTT, InfluxDB, or Grafana. Those are supplied by the POC project. The simulator only needs the MQTT broker and InfluxDB addresses at runtime.

---

## 18. Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | _(empty)_ | MQTT auth username |
| `MQTT_PASSWORD` | _(empty)_ | MQTT auth password |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB base URL |
| `INFLUXDB_TOKEN` | `my-token` | InfluxDB auth token |
| `INFLUXDB_ORG` | `iot_org` | InfluxDB organisation |
| `INFLUXDB_BUCKET` | `iot_poc` | InfluxDB bucket |
| `LLM_PROVIDER` | `mock` | `mock` \| `anthropic` \| `openai` \| `gemini` |
| `LLM_API_KEY` | _(empty)_ | API key for the selected provider |
| `LLM_FAST_MODEL` | _(provider default)_ | Model override for per-alert explanations |
| `LLM_QUALITY_MODEL` | _(provider default)_ | Model override for patient summaries |
| `ALERT_EMAIL` | _(empty)_ | Email for alert notifications (blank = disabled) |
| `ALERT_EMAIL_NAME` | — | Display name for notification emails |
| `ALERT_COOLDOWN_SECONDS` | `120` | Min gap between emails for same patient + rule |
| `NOTIFICATION_SERVICE_URL` | `http://localhost:8001` | Notification Service base URL |
| `DEMO_API_PORT` | `8000` | Port for the FastAPI demo backend |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

---

## 19. Error Handling Rules

| Situation | Behaviour |
|---|---|
| YAML loading error (missing field, wrong type) | Raise `ConfigurationError` with file path and field name. Never silently use defaults. |
| Sensor tick error | Log warning, return last known good value. Never crash the engine on a single bad tick. |
| MQTT publish error | Log error, buffer the point, retry next tick. Reconnect with exponential backoff. |
| Rule engine evaluation error | Log warning, skip the alert for this tick. Never crash the service. |
| LLM call failure | Log warning, return the fallback template from `summariser.py`. Never surface errors to the UI. |
| Notification Service unreachable | Log error, skip this notification. Do not crash the rule engine. |
| Demo API error | Return HTTP 400/404/500 with a plain English message. Never expose Python stack traces to the browser. |
| SIGTERM / SIGINT | Graceful shutdown within 5 seconds. Stop all sensor tasks, disconnect MQTT, exit cleanly. |
