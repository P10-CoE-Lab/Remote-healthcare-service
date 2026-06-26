# Context Brief — Start Here in New Chat

## What to read first (in this order)

1. `Remote_Health_Care_Overview.md` — full project overview, architecture, all components
2. `CLAUDE.md` — coding conventions, design principles, build order
3. `config/cloud_rules.yaml` — current windowed rule structure (you will extend this)
4. `config/edge_rules.yaml` — current edge thresholds (you will extend this)
5. `rule_engine/cloud/rule_analyzer.py` — where rule evaluation lives (feature 1 touches this)
6. `rule_engine/cloud/analyzer.py` — Analyzer ABC (feature 1 adds a new Analyzer implementation)
7. `rule_engine/alert_notifier.py` — MQTT → email bridge (feature 2 adds LLM call here)
8. `rule_engine/shared/models.py` — RuleAlert and RuleReading dataclasses
9. `simulator/core/scenario_engine.py` — how sensor readings are produced
10. `demo/demo_api.py` — the FastAPI control layer (feature 2 adds a /summary endpoint)

## Current stack (running with ./start.sh)

| Service | Port | Purpose |
|---|---|---|
| Demo API (FastAPI) | 8000 | Fleet control + patient management |
| Grafana | 3000 | Fleet Overview + Patient Detail dashboards |
| Mailhog | 8025 | Catches alert emails (dev) |
| InfluxDB | 8086 | Time-series storage |
| MQTT (Mosquitto) | 1883 | Message broker |
| Notification Service | 8001 | Email delivery (Mailhog or Gmail) |

## What has already been built

- **Multi-patient simulator** — `run.py --population --demo` spawns a fleet, patients added via API
- **Rule engine** (`rule_engine/`) — edge (instant thresholds) + cloud (windowed risk scoring)
  - Edge: SpO₂ < 93%, HR > 100, HR < 50, fall detection
  - Cloud: 30s/120s windows, risk scoring 0–100, levels: none/low/medium/high/critical
  - Cloud alerts include `patient_label`, `risk_score`, `risk_level`, `conditions_met`
- **Alert notifier** (`rule_engine/alert_notifier.py`) — subscribes to `alerts/cloud/#`,
  calls Notification Service HTTP API, 120s cooldown, severity filter
- **Notification Service** — Mailhog (dev) / Gmail (prod), Jinja2 HTML email templates
- **React UI** (`demo/ui/`) — fleet dashboard, add/remove patients, trigger events, Grafana deeplinks
- **Grafana** — two provisioned dashboards (fleet overview + per-patient detail)
- **InfluxDB pipeline** — Telegraf bridges MQTT → InfluxDB (`vitals` + `rule_alerts` measurements)

## Data in InfluxDB (what you can query for baseline learning)

```
measurement: vitals
tags: device_id, patient_label, persona_id, poc_type, sensor_name, unit
field: value (float)
```

Query example — last 5 minutes of heart rate for Patient 001:
```flux
from(bucket: "iot_poc")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "vitals")
  |> filter(fn: (r) => r["sensor_name"] == "heart_rate")
  |> filter(fn: (r) => r["patient_label"] == "Patient 001")
  |> filter(fn: (r) => r["_field"] == "value")
```

InfluxDB token: `my-token`, org: `iot_org`, bucket: `iot_poc`, URL: `http://localhost:8086`

## Feature 1 — Personalized AI Baseline

**Problem:** Population-level thresholds (HR > 100, SpO₂ < 93%) fire false alarms on
patients whose personal normal is different. An athlete at resting HR 48 should not get
bradycardia alerts. A COPD patient at chronic SpO₂ 93% should not alarm constantly.

**What to build:** A baseline learner that:
1. Reads each patient's recent history from InfluxDB (first N minutes of stable data)
2. Computes a personalised normal range (mean ± k·std, or percentile-based)
3. Replaces or adjusts the static YAML thresholds with patient-specific ones
4. Updates dynamically as more stable readings accumulate

**Where it fits architecturally:**
- A new `Analyzer` implementation (subclass of `rule_engine/cloud/analyzer.py:Analyzer`)
- `PersonalisedAnalyzer` wraps `RuleAnalyzer` but adjusts effective thresholds per device
- The `CloudEngine` already accepts any `Analyzer` via dependency injection — zero engine changes needed
- Baseline state lives in a per-device dict (in-memory for POC, persistent later)

**Key design questions to resolve in planning:**
- How many readings / how long until baseline is trusted? (suggest: first 2 real-minutes of stable phase)
- What statistical model? (suggest: mean ± 2·std, clamped by YAML min/max safety bounds)
- What happens when scenario transitions out of stable phase? (freeze baseline, don't relearn during event)
- How does the UI surface "this threshold is personalized"? (patient card annotation, Grafana panel)

## Feature 2 — Explainable AI + LLM-Generated Clinical Summaries

**Problem:** Raw alerts ("HR > 100 for 30s, risk score 85") mean nothing to a non-clinical
client audience. A clinician needs "why did this fire." A client evaluating the POC needs
"can I trust this system."

**What to build:**
1. **Per-alert explanation** — when an alert fires, generate a short clinical narrative
   (3–5 sentences) using an LLM: what triggered it, how it compares to this patient's
   baseline/history, what it means clinically
2. **On-demand patient summary** — a `GET /patients/{patient_id}/summary` API endpoint
   that returns a paragraph-length clinical briefing: patient status, recent trends,
   active alerts, notable changes in the last session

**Where it fits architecturally:**
- LLM call lives in `rule_engine/alert_notifier.py` — it already sees every cloud alert
  and has the full alert context (sensor_value, threshold, risk_score, conditions_met)
- For history context: fetch last 5 min from InfluxDB before calling the LLM
- The generated explanation is added to the email body AND returned in a new
  `explanation` field on the alert object
- The `/summary` endpoint in `demo/demo_api.py` queries InfluxDB for recent vitals +
  alerts and calls the LLM

**Model to use:** `claude-haiku-4-5-20251001` for per-alert explanations (fast, cheap),
`claude-sonnet-4-6` for on-demand summaries (richer output). Use the Anthropic SDK.

**Key design questions to resolve in planning:**
- What context do you give the LLM for per-alert explanations?
  (suggest: alert fields + last 5 readings of that sensor + patient persona baseline)
- What format for the summary? (suggest: structured sections — Current Status, Recent Trends,
  Active Alerts, Recommendation)
- Where does the explanation appear in the UI? (patient card expanded view? alert feed tooltip?)
- Does the explanation go into the Grafana alert annotation?

## Files to NOT modify (stable, working)
- `docker/telegraf.conf` — the MQTT→InfluxDB pipeline works; don't touch
- `docker/grafana/dashboards/` — dashboards work; extend by adding panels, not replacing
- `Notification_Service/` — the notification framework is complete; only add templates
- `simulator/` — the simulation layer is stable; feature work is in rule_engine and demo

## Running the project
```bash
./start.sh          # starts everything
./stop.sh           # stops everything
tail -f logs/*.log  # follow Python service logs
```
