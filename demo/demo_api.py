"""
demo/demo_api.py
-----------------
FastAPI control layer for the Vitals Simulator demo.

Single-engine endpoints (backwards-compatible):
  GET  /status                    — current scenario, phase, elapsed time
  POST /scenario/load             — load a scenario by ID, resets engine
  POST /event/trigger             — fire a named event immediately

Fleet endpoints (population mode):
  GET  /patients                          — list all active patient engines
  POST /patients/add                      — spawn new engine, returns patient_id
  DELETE /patients/{patient_id}           — stop and remove engine
  GET  /patients/{patient_id}/status      — status for one patient
  POST /patients/{patient_id}/event/trigger  — trigger event for one patient
  GET  /patients/{patient_id}/vitals      — recent sensor readings for sparkline
  GET  /scenarios/catalog                 — list scenarios with human-readable labels

No authentication — runs on localhost only.
Serves demo/static/index.html for the control UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import paho.mqtt.client as mqtt
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from simulator.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Fleet of running engines — keyed by patient_id.
# Single-engine mode adds the engine under the key "primary".
_fleet: dict[str, dict[str, Any]] = {}   # patient_id → {engine, label, task}

# Explicit device_id → patient_id map, maintained alongside _fleet for both
# simulated and hardware entries. Used by _resolve_patient() instead of
# parsing device_id strings, which breaks for shapes that aren't
# sim-{persona_id}-{patient_id} (e.g. hardware devices like hw-cardiac-01).
_device_to_patient: dict[str, str] = {}

# Hardware patients are capped at exactly 1 in the fleet.
_MAX_HARDWARE_PATIENTS = 1

# Shared configs for spawning new engines in population mode.
_sim_config: dict = {}
_mqtt_config: dict = {}
_shared_publisher = None   # MQTTPublisher shared across all fleet engines

# ---------------------------------------------------------------------------
# Clinical alert buffer — filled from two sources:
#   1. MQTT subscriber on alerts/# (external rule_engine cloud service)
#   2. Per-patient EdgeEngine alerts read directly from memory
# ---------------------------------------------------------------------------
_alert_buffer: deque[dict[str, Any]] = deque(maxlen=100)
_alert_mqtt_client: mqtt.Client | None = None
_seen_violation_fps: set[str] = set()

# Per-patient full-session alert history — never wiped by DELETE /alerts.
# The UI's "Dismiss all" clears _alert_buffer (the visual feed) but the AI
# Briefing summary reads from here so it always sees the full session.
_patient_alert_history: dict[str, list[dict[str, Any]]] = {}

# Latest risk score/level per patient — updated when a cloud alert arrives via MQTT
_patient_risk: dict[str, dict] = {}   # patient_id → {risk_score, risk_level}

# Personalised AI baseline per device — updated when rule engine publishes to baselines/#
_patient_baselines: dict[str, dict] = {}   # device_id → baseline dict

# Human-readable labels for the scenario catalog.
_SCENARIO_CATALOG = [
    {
        "label":       "Normal Resting",
        "path":        "scenarios/health/normal_resting.yaml",
        "description": "Patient at home, resting normally, all vitals within range",
    },
    {
        "label":       "Tachycardia Episode",
        "path":        "scenarios/health/tachycardia_episode.yaml",
        "description": "Sudden rapid heart rate onset, peaks, then recovers",
    },
    {
        "label":       "Low SpO₂ Event",
        "path":        "scenarios/health/low_spo2_event.yaml",
        "description": "Oxygen saturation drops gradually, alarm fires, recovers",
    },
    {
        "label":       "Bradycardia Episode",
        "path":        "scenarios/health/bradycardia_episode.yaml",
        "description": "Heart rate slows progressively, drops below threshold, recovers",
    },
    {
        "label":       "Combined Deterioration",
        "path":        "scenarios/health/combined_deterioration.yaml",
        "description": "HR rises and SpO₂ drops simultaneously — maximum risk score",
    },
    {
        "label":       "Recovery Progression",
        "path":        "scenarios/health/recovery_progression.yaml",
        "description": "Starts in high-alert state, progressively stabilizes over 40 minutes",
    },
    {
        "label":       "HRV Stress Pattern",
        "path":        "scenarios/health/hrv_stress_pattern.yaml",
        "description": "HRV degrades steadily, indicating sustained cardiac stress",
    },
    {
        "label":       "Nocturnal Episode",
        "path":        "scenarios/health/night_episode.yaml",
        "description": "Sleep-baseline SpO₂ desaturation event, then arousal and recovery",
    },
]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoadScenarioRequest(BaseModel):
    persona_path:  str
    scenario_path: str
    compression:   float | None = None


class TriggerEventRequest(BaseModel):
    event_type:       str
    duration_seconds: float | None = None


class SetCompressionRequest(BaseModel):
    compression: float


class AddPatientRequest(BaseModel):
    label:         str
    source:        str = "simulated"   # "simulated" | "hardware"
    scenario_path: str | None = None
    compression:   float | None = None
    device_id:     str | None = None   # hardware only — the device's fixed id
    persona_id:    str | None = None   # hardware only — which persona/thresholds to classify against


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _primary_engine():
    """Return the engine to use for single-engine legacy endpoints.

    Prefers the engine under key 'primary'; falls back to the first running
    entry; then any entry.  Returns None if the fleet is empty or only
    contains hardware entries (which have no engine).
    """
    if not _fleet:
        return None
    if "primary" in _fleet:
        return _fleet["primary"]["engine"]
    for entry in _fleet.values():
        engine = entry.get("engine")
        if engine is not None and engine.get_status().running:
            return engine
    for entry in _fleet.values():
        if entry.get("engine") is not None:
            return entry["engine"]
    return None


def _build_patient_status(patient_id: str) -> dict:
    """Build the full patient status dict for a fleet entry."""
    entry = _fleet[patient_id]
    risk  = _patient_risk.get(patient_id, {})

    if entry.get("hardware"):
        device_id = entry["device_id"]
        baseline  = _patient_baselines.get(device_id, {})
        return {
            "patient_id":          patient_id,
            "device_id":           device_id,
            "label":               entry["label"],
            "status":              "running",
            "source":              "hardware",
            "scenario_id":         "",
            "persona_id":          entry["persona_id"],
            "current_phase":       "",
            "elapsed_sim_minutes": 0,
            "total_sim_minutes":   0,
            "progress_pct":        0,
            "compression":         1,
            "available_events":    [],
            "risk_score":          min(float(risk.get("risk_score", 0)), 100.0),
            "risk_level":          risk.get("risk_level", "none"),
            "alert_count":         len(_patient_alert_history.get(patient_id, [])),
            "device_paused":       False,
            "baseline_state":      baseline.get("state", "learning"),
            "baseline_info":       baseline if baseline else None,
        }

    engine = entry["engine"]
    status = engine.get_status()

    progress = round(
        (status.elapsed_sim_minutes / status.total_sim_minutes * 100)
        if status.total_sim_minutes > 0 else 0,
        1,
    )

    # device_id matches what the simulator publishes to MQTT and InfluxDB
    device_id = entry.get("device_id") or f"sim-{status.persona_id}-{patient_id}"

    baseline = _patient_baselines.get(device_id, {})

    return {
        "patient_id":          patient_id,
        "device_id":           device_id,
        "label":               entry["label"],
        "status":              "running" if status.running else "stopped",
        "source":              "simulated",
        "scenario_id":         status.scenario_id,
        "persona_id":          status.persona_id,
        "current_phase":       status.current_phase,
        "elapsed_sim_minutes": round(status.elapsed_sim_minutes, 2),
        "total_sim_minutes":   status.total_sim_minutes,
        "progress_pct":        progress,
        "compression":         status.compression,
        "available_events":    status.available_events,
        "risk_score":          min(float(risk.get("risk_score", 0)), 100.0),
        "risk_level":          risk.get("risk_level", "none"),
        "alert_count":         len(status.events_fired),
        "device_paused":       status.device_paused,
        "baseline_state":      baseline.get("state", "learning"),
        "baseline_info":       baseline if baseline else None,
    }


async def _spawn_engine(
    patient_id: str,
    label: str,
    scenario_path: Path,
    compression: float | None,
) -> None:
    """Create, load, and start one engine; register it in the fleet."""
    from simulator.core.scenario_engine import ScenarioEngine
    from simulator.transport.mqtt_publisher import MQTTPublisher
    from simulator.sensors.base import SensorReading

    # Auto-resolve persona from scenario YAML
    raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8")) or {}
    persona_id = raw.get("persona", "")
    if not persona_id:
        raise ValueError(f"Scenario '{scenario_path}' has no 'persona' field")

    persona_path = Path("personas") / f"{persona_id}.yaml"
    if not persona_path.exists():
        raise ValueError(f"Persona file not found: {persona_path}")

    engine = ScenarioEngine(
        sim_config=  _sim_config,
        mqtt_config= _mqtt_config,
        instance_id= patient_id,
    )
    engine.load(persona_path=persona_path, scenario_path=scenario_path)

    if compression is not None:
        engine.set_compression(compression)

    # Use shared publisher if available, otherwise create a dedicated one
    publisher = _shared_publisher
    if publisher is None:
        publisher = MQTTPublisher(_mqtt_config)
        publisher.connect()

    engine.set_mqtt_publisher(publisher)

    async def _publish(
        reading:    SensorReading,
        persona_id: str,
        poc_type:   str,
        device_id:  str,
    ) -> None:
        status = engine.get_status()
        publisher.publish(
            reading=        reading,
            persona_id=     persona_id,
            poc_type=       poc_type,
            device_id=      device_id,
            sequence_num=   status.sequence_number,
            compression=    status.compression,
            patient_label=  label,
        )

    task = asyncio.create_task(engine.start(_publish))

    device_id = f"sim-{persona_id}-{patient_id}"

    _fleet[patient_id] = {
        "engine":    engine,
        "label":     label,
        "task":      task,
        "publisher": publisher if _shared_publisher is None else None,  # owned publisher to clean up
        "device_id": device_id,
    }
    _device_to_patient[device_id] = patient_id

    logger.info(
        "Patient engine spawned",
        extra={
            "event":      "patient_spawned",
            "patient_id": patient_id,
            "label":      label,
            "scenario":   str(scenario_path),
        },
    )


def _register_hardware_patient(patient_id: str, label: str, device_id: str, persona_id: str) -> None:
    """Register a hardware-backed patient in the fleet.

    No ScenarioEngine and no dedicated MQTTPublisher — the physical device
    publishes vitals to MQTT on its own; this just adds a fleet entry and
    device_id mapping so the demo API can build a status card for it and
    resolve incoming cloud-engine alerts back to it.
    """
    _fleet[patient_id] = {
        "engine":    None,
        "hardware":  True,
        "device_id": device_id,
        "persona_id": persona_id,
        "label":     label,
        "task":      None,
        "publisher": None,
    }
    _device_to_patient[device_id] = patient_id

    # Push the operator's chosen label down to the physical device over a
    # retained config topic. The device subscribes to this at boot (and
    # stays subscribed) so Grafana/Influx see the real patient identity
    # instead of whatever placeholder is baked into the firmware.
    if _shared_publisher is not None:
        _shared_publisher.publish_raw(
            topic=   f"config/{device_id}/patient_label",
            payload= label,
            qos=     0,
            retain=  True,
        )

    logger.info(
        "Hardware patient registered",
        extra={
            "event":      "hardware_patient_registered",
            "patient_id": patient_id,
            "device_id":  device_id,
            "persona_id": persona_id,
        },
    )


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

def _fp(rule_id: str, persona_id: str, ts: float) -> str:
    """Short fingerprint for alert deduplication."""
    return hashlib.md5(f"{rule_id}:{persona_id}:{int(ts)}".encode()).hexdigest()[:12]


def _resolve_patient(device_id: str, persona_id: str) -> tuple[str, str]:
    """Return (patient_id, patient_label) for an incoming alert.

    Uses the explicit device_id → patient_id map maintained alongside _fleet
    (populated at registration time for both simulated and hardware patients)
    instead of parsing the device_id string. String-splitting on the last
    "-" breaks for device_id shapes that aren't sim-{persona_id}-{patient_id}
    — e.g. hardware devices like hw-cardiac-01 — and for any patient_id that
    itself contains a hyphen.

    Falls back to a persona_id scan (simulated patients only) if the device_id
    isn't in the map yet.
    """
    if device_id and device_id in _device_to_patient:
        candidate_id = _device_to_patient[device_id]
        if candidate_id in _fleet:
            return candidate_id, _fleet[candidate_id]["label"]

    # Fallback: first simulated patient whose engine reports this persona_id
    for pid, entry in _fleet.items():
        engine = entry.get("engine")
        if engine is None:
            continue
        try:
            if engine.get_status().persona_id == persona_id:
                return pid, entry["label"]
        except Exception:
            pass

    return "", persona_id


def _start_alert_subscriber(mqtt_cfg: dict) -> None:
    """Subscribe to alerts/# and push normalised alert dicts into _alert_buffer."""
    global _alert_mqtt_client
    broker = mqtt_cfg.get("broker", {})
    host   = os.environ.get("MQTT_HOST", broker.get("host", "localhost"))
    port   = int(os.environ.get("MQTT_PORT", broker.get("port", 1883)))

    client = mqtt.Client(client_id="demo-api-alert-sub", clean_session=True)

    def _on_connect(c, _u, _f, rc):
        if rc == 0:
            c.subscribe("alerts/#", qos=0)
            c.subscribe("baselines/#", qos=0)
            logger.info("Alert subscriber connected", extra={"event": "alert_sub_connected"})

    def _on_message(_c, _u, msg):
        try:
            payload: dict = json.loads(msg.payload.decode("utf-8"))

            # Handle baseline messages from PersonalisedAnalyzer
            if msg.topic.startswith("baselines/"):
                device_id = msg.topic.split("/", 1)[1] if "/" in msg.topic else ""
                if device_id:
                    _patient_baselines[device_id] = payload
                return

            parts   = msg.topic.split("/")   # alerts / edge|cloud / persona_id / rule_id
            source  = parts[1] if len(parts) > 1 else "unknown"

            # Only accept the two real sources — drop anything unexpected
            if source not in ("edge", "cloud"):
                return

            persona_id = payload.get("persona_id", "")
            device_id  = payload.get("device_id", "")
            patient_id, patient_label = _resolve_patient(device_id, persona_id)

            # Fingerprint on (source + rule_id, patient_id, 30s-bucket).
            # Include source so edge and cloud alerts for the same sensor
            # do NOT deduplicate each other — they are distinct alert types.
            fp_key = f"{source}-{payload.get('rule_id', '')}"

            bucket = int(time.time() / 30)
            fp     = _fp(fp_key, patient_id or persona_id, bucket)
            if fp in _seen_violation_fps:
                return
            _seen_violation_fps.add(fp)

            alert = {
                "id":            f"{source}-{fp}",
                "timestamp":     time.time(),
                "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
                "patient_id":    patient_id,
                "patient_label": patient_label,
                "persona_id":    persona_id,
                "device_id":     device_id,
                "rule_id":       payload.get("rule_id", ""),
                "rule_name":     payload.get("description", ""),
                "severity":      payload.get("severity", "warning"),
                "source":        source,
                "sensor_name":   payload.get("sensor_name", ""),
                "sensor_value":  payload.get("sensor_value"),
                "threshold":     payload.get("threshold"),
                "risk_score":    payload.get("risk_score", 0),
                "risk_level":    payload.get("risk_level", "none"),
                "conditions_met": payload.get("conditions_met", []),
            }
            _alert_buffer.appendleft(alert)
            if patient_id:
                _patient_alert_history.setdefault(patient_id, []).append(alert)

            # Cache risk score/level for cloud alerts so patient cards stay current
            if source == "cloud" and patient_id:
                _patient_risk[patient_id] = {
                    "risk_score": payload.get("risk_score", 0),
                    "risk_level": payload.get("risk_level", "none"),
                }
        except Exception:
            pass

    client.on_connect = _on_connect
    client.on_message = _on_message

    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
        _alert_mqtt_client = client
    except Exception as exc:
        logger.warning(
            "Alert subscriber could not connect — edge/cloud alerts will not appear",
            extra={"event": "alert_sub_error", "error": str(exc)},
        )


def _scan_fleet_violations() -> None:
    """Read each patient engine's EdgeEngine alert cache and surface to the alert buffer.

    Deduplication: a (sensor_name + direction, patient_id, 30-second bucket) fingerprint
    prevents the same alert appearing more than once and prevents double-counting when
    the EdgeEngine also transmits the alert via MQTT.
    """
    if len(_seen_violation_fps) > 5000:
        _seen_violation_fps.clear()

    for patient_id, entry in list(_fleet.items()):
        if entry.get("engine") is None:
            continue  # hardware patients have no EdgeEngine to scan
        try:
            alerts = entry["engine"].get_edge_alerts(limit=30)
        except Exception:
            continue

        for a in alerts:
            sensor_name  = a.get("sensor_name", "")
            sensor_value = a.get("sensor_value")
            threshold    = a.get("threshold")
            rule_id      = a.get("rule_id", "")

            # Fingerprint on (source + rule_id, patient_id, 30s-bucket).
            # Matches the scheme used by _start_alert_subscriber so that an
            # edge alert arriving via MQTT is not double-counted with the
            # same alert read directly from the EdgeEngine cache.
            fp_key = f"edge-{rule_id}"

            ts     = 0.0
            ts_utc = a.get("timestamp_utc", "")
            try:
                from datetime import datetime, timezone as _tz
                ts = datetime.fromisoformat(ts_utc).timestamp()
            except Exception:
                ts = time.time()
            bucket = int(ts / 30)

            fp = _fp(fp_key, patient_id, bucket)
            if fp in _seen_violation_fps:
                continue
            _seen_violation_fps.add(fp)

            alert = {
                "id":            f"edge-{fp}",
                "timestamp":     ts,
                "timestamp_utc": ts_utc,
                "patient_id":    patient_id,
                "patient_label": entry["label"],
                "persona_id":    a.get("persona_id", ""),
                "device_id":     f"sim-{a.get('persona_id', '')}-{patient_id}",
                "rule_id":       rule_id,
                "rule_name":     a.get("description", rule_id),
                "severity":      a.get("severity", "warning"),
                "source":        "edge",
                "sensor_name":   sensor_name,
                "sensor_value":  sensor_value,
                "threshold":     threshold,
                "risk_score":    a.get("risk_score", 0),
                "risk_level":    a.get("risk_level", "none"),
                "conditions_met": a.get("conditions_met", []),
            }
            _alert_buffer.appendleft(alert)
            _patient_alert_history.setdefault(patient_id, []).append(alert)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    engine=None,
    *,
    sim_config: dict | None = None,
    mqtt_config: dict | None = None,
    shared_publisher=None,
) -> FastAPI:
    """Create the FastAPI application.

    Pass `engine` for single-engine (legacy) mode.
    Pass config kwargs for population mode (engine=None).
    """
    global _sim_config, _mqtt_config, _shared_publisher

    if sim_config is not None:
        _sim_config = sim_config
    if mqtt_config is not None:
        _mqtt_config = mqtt_config
    if shared_publisher is not None:
        _shared_publisher = shared_publisher

    # Single-engine mode: register it as "primary"
    if engine is not None:
        _fleet["primary"] = {"engine": engine, "label": "Primary", "task": None, "publisher": None}

    app = FastAPI(
        title="Vitals Simulator Demo API",
        description="Control panel for the health and safety simulator",
        version="2.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ======================================================================
    # Legacy single-engine endpoints (unchanged contract)
    # ======================================================================

    @app.get("/")
    async def index():
        """Serve the client monitoring UI (clean view for real clients)."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"message": "UI not found. Run: cd demo/ui && npm run build"})

    @app.get("/operator")
    async def operator():
        """Serve the operator control panel (demo staff only)."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"message": "UI not found. Run: cd demo/ui && npm run build"})

    @app.get("/status")
    async def get_status():
        """Return current engine state (primary engine)."""
        eng = _primary_engine()
        if eng is None:
            return {"running": False, "message": "No engine loaded. Use /patients/add to start one."}
        try:
            status = eng.get_status()
            return {
                "running":             status.running,
                "scenario_id":         status.scenario_id,
                "persona_id":          status.persona_id,
                "current_phase":       status.current_phase,
                "elapsed_sim_minutes": round(status.elapsed_sim_minutes, 2),
                "total_sim_minutes":   status.total_sim_minutes,
                "progress_pct":        round(
                    (status.elapsed_sim_minutes / status.total_sim_minutes * 100)
                    if status.total_sim_minutes > 0 else 0, 1
                ),
                "compression":         status.compression,
                "events_fired":        status.events_fired,
                "sequence_number":     status.sequence_number,
                "available_events":    status.available_events,
            }
        except Exception as exc:
            logger.error("Status endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get engine status")

    @app.get("/events")
    async def get_fired_events():
        """Return events fired so far (primary engine)."""
        eng = _primary_engine()
        if eng is None:
            return {"events": []}
        try:
            status = eng.get_status()
            event_details = [
                {
                    "type":        event_name,
                    "description": event_name.replace("_", " ").title(),
                    "category":    "scenario",
                }
                for event_name in (status.events_fired or [])
            ]
            return {"events": event_details}
        except Exception as exc:
            logger.error("Events endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get events")

    @app.get("/events/recent")
    async def get_recent_events():
        """Return recent events for demo UI log (primary engine)."""
        eng = _primary_engine()
        if eng is None:
            return {"events": []}
        try:
            status = eng.get_status()
            events = [
                {
                    "type":        "scenario",
                    "name":        event,
                    "description": f"Scenario event: {event}",
                    "timestamp":   time.time(),
                }
                for event in status.events_fired[-10:]
            ]
            return {"events": events}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/scenario/load")
    async def load_scenario(req: LoadScenarioRequest):
        """Load a new scenario into the primary engine."""
        eng = _primary_engine()
        if eng is None:
            raise HTTPException(status_code=404, detail="No engine loaded. Use /patients/add in population mode.")
        try:
            persona_path  = Path(req.persona_path)
            scenario_path = Path(req.scenario_path)
            if not scenario_path.exists():
                raise HTTPException(status_code=400, detail=f"Scenario file not found: {req.scenario_path}")

            auto_resolved = False
            try:
                raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8")) or {}
                required_persona = raw.get("persona", "")
            except Exception:
                required_persona = ""

            if required_persona:
                auto_path = Path("personas") / f"{required_persona}.yaml"
                if auto_path.exists() and auto_path != persona_path:
                    persona_path  = auto_path
                    auto_resolved = True

            if not persona_path.exists():
                raise HTTPException(status_code=400, detail=f"Persona file not found: {req.persona_path}")

            await eng.reload(persona_path, scenario_path)
            if req.compression is not None:
                if req.compression <= 0:
                    raise HTTPException(status_code=400, detail="compression must be > 0")
                eng.set_compression(req.compression)

            msg = "Scenario loaded successfully"
            if auto_resolved:
                msg += f" (auto-matched persona: {persona_path.stem})"
            return {"success": True, "message": msg}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/event/trigger")
    async def trigger_event(req: TriggerEventRequest):
        """Manually fire a named event on the primary engine."""
        eng = _primary_engine()
        if eng is None:
            raise HTTPException(status_code=404, detail="No engine loaded")
        try:
            if not req.event_type or not req.event_type.strip():
                raise HTTPException(status_code=400, detail="event_type cannot be empty")
            duration = req.duration_seconds if (req.duration_seconds is not None and req.duration_seconds > 0) else None
            found = eng.trigger_event(req.event_type.strip(), duration)
            if not found:
                raise HTTPException(status_code=404, detail=f"Event type '{req.event_type}' not found")
            return {"success": True, "message": f"Event '{req.event_type}' triggered"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/compression/set")
    async def set_compression(req: SetCompressionRequest):
        """Update time compression on the primary engine."""
        eng = _primary_engine()
        if eng is None:
            raise HTTPException(status_code=404, detail="No engine loaded")
        try:
            if req.compression <= 0:
                raise HTTPException(status_code=400, detail="compression must be > 0")
            eng.set_compression(req.compression)
            return {"success": True, "compression": req.compression}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/scenarios")
    async def list_scenarios():
        """List available scenario YAML files."""
        try:
            scenarios_dir = Path("scenarios")
            if not scenarios_dir.exists():
                return {"scenarios": []}
            files = sorted(scenarios_dir.rglob("*.yaml"))
            result = []
            for f in files:
                if f.name.startswith("_"):
                    continue
                try:
                    raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                    persona_required = raw.get("persona", "")
                except Exception:
                    persona_required = ""
                result.append({
                    "path":             str(f.relative_to(Path("."))),
                    "persona_required": persona_required,
                })
            return {"scenarios": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/personas")
    async def list_personas():
        """List available persona YAML files."""
        try:
            personas_dir = Path("personas")
            if not personas_dir.exists():
                return {"personas": []}
            return {
                "personas": [
                    str(f.relative_to(Path(".")))
                    for f in sorted(personas_dir.glob("*.yaml"))
                    if not f.name.startswith("_")
                ]
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/scenario/content")
    async def get_scenario_content():
        """Return the currently loaded scenario's phases, sensors, and events (primary engine)."""
        eng = _primary_engine()
        if eng is None:
            return {"loaded": False}
        try:
            scenario = eng._scenario
            if not scenario:
                return {"loaded": False}
            phases = []
            for ph in scenario.phases:
                phases.append({
                    "name": ph.name,
                    "start_minute": ph.start_minute,
                    "end_minute": ph.end_minute,
                    "noise_intensity": ph.noise_intensity,
                    "activity": ph.activity,
                    "sensors": {
                        name: {"min": cfg.min_value, "max": cfg.max_value, "behavior": cfg.behavior}
                        for name, cfg in ph.sensors.items()
                    },
                })
            events = [
                {
                    "at_minute": ev.at_minute,
                    "event_type": ev.event_type,
                    "description": ev.description,
                    "duration_seconds": ev.duration_seconds,
                }
                for ev in scenario.events
            ]
            return {
                "loaded": True,
                "scenario_id": scenario.scenario_id,
                "description": scenario.description,
                "poc_type": scenario.poc_type,
                "total_duration_minutes": scenario.total_duration_minutes,
                "compression": scenario.compression,
                "phases": phases,
                "events": events,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to get scenario content")

    # ======================================================================
    # Fleet endpoints (new)
    # ======================================================================

    @app.get("/patients")
    async def list_patients():
        """List all active patient engines in the fleet."""
        try:
            patients = []
            for patient_id in list(_fleet.keys()):
                try:
                    patients.append(_build_patient_status(patient_id))
                except Exception as exc:
                    logger.error(
                        "Error building patient status",
                        extra={"event": "fleet_status_error", "patient_id": patient_id, "error": str(exc)},
                    )
            return {"patients": patients, "total": len(patients)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/patients/add", status_code=201)
    async def add_patient(req: AddPatientRequest):
        """Add a new patient to the fleet — simulated (spawns an engine, max 10)
        or hardware (registers a fleet entry for a real device, capped at 1)."""
        try:
            if req.source not in ("simulated", "hardware"):
                raise HTTPException(status_code=400, detail="source must be 'simulated' or 'hardware'")

            if req.source == "hardware":
                hw_count = sum(1 for e in _fleet.values() if e.get("hardware"))
                if hw_count >= _MAX_HARDWARE_PATIENTS:
                    raise HTTPException(
                        status_code=400,
                        detail="A hardware patient is already registered. Remove it before adding another.",
                    )
                if not req.device_id or not req.persona_id:
                    raise HTTPException(
                        status_code=400,
                        detail="device_id and persona_id are required for hardware patients",
                    )
                if req.device_id in _device_to_patient:
                    raise HTTPException(status_code=400, detail=f"Device '{req.device_id}' is already registered")

                persona_path = Path("personas") / f"{req.persona_id}.yaml"
                if not persona_path.exists():
                    raise HTTPException(status_code=400, detail=f"Persona file not found: {persona_path}")

                patient_id = uuid.uuid4().hex[:8]
                # Default label deliberately avoids device_id/persona_id — neither
                # should leak a pre-configured condition profile to a demo audience.
                label = req.label or f"Hardware Patient {patient_id[:4].upper()}"
                _register_hardware_patient(patient_id, label, req.device_id, req.persona_id)

                return {
                    "success":    True,
                    "patient_id": patient_id,
                    "label":      label,
                    "message":    f"Hardware patient '{label}' registered",
                }

            # source == "simulated" — existing path, unchanged
            if len(_fleet) >= 10:
                raise HTTPException(
                    status_code=400,
                    detail="Fleet is full (max 10 patients). Remove one before adding another.",
                )
            if not req.scenario_path:
                raise HTTPException(status_code=400, detail="scenario_path is required for simulated patients")

            scenario_path = Path(req.scenario_path)
            if not scenario_path.exists():
                raise HTTPException(status_code=400, detail=f"Scenario not found: {req.scenario_path}")

            patient_id = uuid.uuid4().hex[:8]

            await _spawn_engine(
                patient_id=    patient_id,
                label=         req.label or f"Patient {patient_id[:4].upper()}",
                scenario_path= scenario_path,
                compression=   req.compression,
            )

            return {
                "success":    True,
                "patient_id": patient_id,
                "label":      _fleet[patient_id]["label"],
                "message":    f"Patient '{req.label}' added to fleet",
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Add patient error", extra={"event": "fleet_add_error", "error": str(exc)})
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/patients/{patient_id}")
    async def remove_patient(patient_id: str):
        """Stop and remove a patient engine from the fleet, or unregister a
        hardware entry (frees up the single hardware slot)."""
        if patient_id not in _fleet:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found")
        try:
            entry = _fleet.pop(patient_id)

            device_id = entry.get("device_id")
            if device_id and _device_to_patient.get(device_id) == patient_id:
                del _device_to_patient[device_id]

            if entry.get("engine") is not None:
                engine = entry["engine"]
                await engine.stop()

                task = entry.get("task")
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if entry.get("publisher"):
                    entry["publisher"].stop()

            logger.info(
                "Patient removed",
                extra={"event": "patient_removed", "patient_id": patient_id},
            )
            return {"success": True, "message": f"Patient '{patient_id}' removed"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/patients/{patient_id}/status")
    async def get_patient_status(patient_id: str):
        """Return full status for one patient."""
        if patient_id not in _fleet:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found")
        try:
            return _build_patient_status(patient_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/patients/{patient_id}/event/trigger")
    async def trigger_patient_event(patient_id: str, req: TriggerEventRequest):
        """Fire a named event on a specific patient engine."""
        if patient_id not in _fleet:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found")
        try:
            engine = _fleet[patient_id]["engine"]
            if not req.event_type or not req.event_type.strip():
                raise HTTPException(status_code=400, detail="event_type cannot be empty")
            duration = req.duration_seconds if (req.duration_seconds is not None and req.duration_seconds > 0) else None
            found = engine.trigger_event(req.event_type.strip(), duration)
            if not found:
                raise HTTPException(status_code=404, detail=f"Event type '{req.event_type}' not found in scenario")
            return {"success": True, "message": f"Event '{req.event_type}' triggered for patient '{patient_id}'"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/vitals/{device_id}")
    async def get_vitals(device_id: str):
        """Return current vitals and 30-reading sparkline history from InfluxDB.

        device_id format: sim-{persona_id}-{patient_id}
        Returns the same shape the UI expects:
          { heart_rate, spo2, hrv, heart_rate_history, spo2_history, battery_level }
        """
        influx_url    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
        influx_token  = os.environ.get("INFLUXDB_TOKEN",  "my-token")
        influx_org    = os.environ.get("INFLUXDB_ORG",    "iot_org")
        influx_bucket = os.environ.get("INFLUXDB_BUCKET", "iot_poc")

        sensors = ["heart_rate", "spo2", "heart_rate_variability", "battery"]
        sensor_filter = " or ".join(
            f'r["sensor_name"] == "{s}"' for s in sensors
        )

        flux = f"""
from(bucket: "{influx_bucket}")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "vitals")
  |> filter(fn: (r) => r["device_id"] == "{device_id}")
  |> filter(fn: (r) => r["_field"] == "value")
  |> filter(fn: (r) => {sensor_filter})
  |> tail(n: 30)
"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(
                    f"{influx_url}/api/v2/query",
                    params={"org": influx_org},
                    headers={
                        "Authorization": f"Token {influx_token}",
                        "Content-Type":  "application/vnd.flux",
                        "Accept":        "application/csv",
                    },
                    content=flux,
                )
                resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"InfluxDB unreachable: {exc}")

        # Parse annotated CSV — build per-sensor list of {timestamp, value}.
        # Column positions are derived from the header row so extra Telegraf
        # tags (e.g. "topic") don't break the parser.
        buffers: dict[str, list[dict]] = {}
        col_time = col_value = col_sensor = -1
        from datetime import datetime, timezone as _tz

        for line in resp.text.splitlines():
            if not line or line.startswith("#"):
                continue
            cols = line.split(",")
            # Header row: ,result,table,_start,_stop,_time,_value,_field,...
            if cols[1] == "result":
                col_time   = cols.index("_time")   if "_time"        in cols else -1
                col_value  = cols.index("_value")  if "_value"       in cols else -1
                col_sensor = cols.index("sensor_name") if "sensor_name" in cols else -1
                continue
            if col_time < 0 or col_value < 0 or col_sensor < 0:
                continue
            try:
                time_str    = cols[col_time]
                value_str   = cols[col_value]
                sensor_name = cols[col_sensor]
                if not sensor_name or not value_str:
                    continue
                ts = datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp()
                buffers.setdefault(sensor_name, []).append(
                    {"timestamp": ts, "value": float(value_str)}
                )
            except Exception:
                continue

        # Flux returns one table per distinct tag-set survivor (e.g. a
        # patient_label change mid-window splits a sensor across tables),
        # and tables are concatenated in whatever order Flux emits them —
        # not guaranteed chronological. Sort before trusting "last".
        for readings in buffers.values():
            readings.sort(key=lambda r: r["timestamp"])

        def _last(name: str) -> float | None:
            readings = buffers.get(name, [])
            return readings[-1]["value"] if readings else None

        return {
            "heart_rate":         _last("heart_rate"),
            "spo2":               _last("spo2"),
            "hrv":                _last("heart_rate_variability"),
            "heart_rate_history": buffers.get("heart_rate", []),
            "spo2_history":       buffers.get("spo2", []),
            "battery_level":      _last("battery"),
        }

    @app.get("/scenarios/catalog")
    async def get_scenario_catalog():
        """Return all available scenarios with human-readable labels for the UI."""
        # Annotate each entry with existence check so UI can hide missing files
        catalog = []
        for entry in _SCENARIO_CATALOG:
            catalog.append({
                **entry,
                "available": Path(entry["path"]).exists(),
            })
        return {"catalog": catalog}

    # ======================================================================
    # Clinical alerts endpoint
    # ======================================================================

    @app.get("/alerts")
    async def get_alerts(limit: int = 50):
        """Return recent clinical alerts from all sources.

        Sources:
          - "edge"   — immediate threshold breach from rule_engine edge service
          - "cloud"  — sustained/windowed rule from rule_engine cloud service
          - "device" — per-patient embedded rules engine violation
        """
        try:
            limit = max(1, min(limit, 100))
            _scan_fleet_violations()   # read EdgeEngine alert cache → edge alerts
            valid = [a for a in _alert_buffer if a.get("source") in ("edge", "cloud")]
            sorted_alerts = sorted(valid, key=lambda a: a.get("timestamp", 0), reverse=True)
            return {"alerts": sorted_alerts[:limit], "total": len(sorted_alerts)}
        except Exception as exc:
            logger.error("Alerts endpoint error", extra={"event": "alerts_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get alerts")

    @app.delete("/alerts")
    async def clear_alerts():
        """Clear the in-memory alert buffer and deduplication state."""
        _alert_buffer.clear()
        _seen_violation_fps.clear()
        return {"success": True}

    # ======================================================================
    # LLM endpoints (Feature 2)
    # ======================================================================

    @app.get("/alerts/{alert_id}/explanation")
    async def get_alert_explanation(alert_id: str):
        """Return (or lazily generate) an LLM clinical explanation for one alert."""
        alert = next((a for a in _alert_buffer if a.get("id") == alert_id), None)
        if alert is None:
            raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

        if alert.get("explanation"):
            return {"explanation": alert["explanation"]}

        try:
            from rule_engine.llm.context_builder import build_alert_context
            from rule_engine.llm.summariser import explain_alert

            device_id  = alert.get("device_id", "")
            patient_id = alert.get("patient_id", "")
            baseline   = _patient_baselines.get(device_id, {})
            recent_alerts = [
                a for a in _alert_buffer
                if a.get("patient_id") == patient_id and a.get("id") != alert_id
            ][:5]

            context     = await build_alert_context(alert, baseline, recent_alerts)
            explanation = await explain_alert(context)
            alert["explanation"] = explanation
            return {"explanation": explanation}
        except Exception as exc:
            logger.error(
                "Alert explanation error",
                extra={"event": "llm_error", "alert_id": alert_id, "error": str(exc)},
            )
            raise HTTPException(status_code=500, detail=f"LLM explanation failed: {exc}")

    @app.get("/patients/{patient_id}/summary")
    async def get_patient_summary(patient_id: str):
        """Generate a fresh on-demand clinical briefing for a patient (quality LLM)."""
        if patient_id not in _fleet:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found")
        try:
            from rule_engine.llm.context_builder import build_summary_context
            from rule_engine.llm.summariser import generate_summary

            entry = _fleet[patient_id]

            if entry.get("hardware"):
                device_id   = entry["device_id"]
                persona_id  = entry["persona_id"]
                compression = 1
                elapsed_min = 0
            else:
                status      = entry["engine"].get_status()
                device_id   = entry.get("device_id") or f"sim-{status.persona_id}-{patient_id}"
                persona_id  = status.persona_id
                compression = status.compression
                elapsed_min = status.elapsed_sim_minutes

            baseline = _patient_baselines.get(device_id, {})

            # Ensure edge-engine alerts are in the buffer and history
            _scan_fleet_violations()

            # Use the per-patient history (never wiped by DELETE /alerts) so
            # "Dismiss all" in the visual feed does not blank out the AI Briefing.
            recent_alerts = list(_patient_alert_history.get(patient_id, []))
            logger.info(
                "Summary alert filter",
                extra={
                    "event":          "summary_alert_filter",
                    "patient_id":     patient_id,
                    "buffer_size":    len(_alert_buffer),
                    "history_size":   len(recent_alerts),
                },
            )

            context = await build_summary_context(
                patient_id=    patient_id,
                device_id=     device_id,
                persona_id=    persona_id,
                label=         entry["label"],
                compression=   compression,
                elapsed_min=   elapsed_min,
                baseline=      baseline,
                session_alerts=recent_alerts,
            )
            summary = await generate_summary(context)
            return {
                "summary":    summary,
                "patient_id": patient_id,
                "label":      entry["label"],
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "baseline_personalised": bool(baseline),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Patient summary error",
                extra={"event": "llm_error", "patient_id": patient_id, "error": str(exc)},
            )
            raise HTTPException(status_code=500, detail=f"Summary generation failed: {exc}")

    # Start alert MQTT subscriber if we have broker config
    if _mqtt_config:
        _start_alert_subscriber(_mqtt_config)

    return app
