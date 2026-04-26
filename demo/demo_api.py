"""
demo/demo_api.py
-----------------
FastAPI control layer for the Vitals Simulator demo.

Three endpoints:

  GET  /status           — current scenario, phase, elapsed time
  POST /scenario/load    — load a scenario by ID, resets engine
  POST /event/trigger    — fire a named event immediately

No authentication — runs on localhost only.
Never exposes stack traces to the browser.
Serves the static index.html for the demo control UI.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from simulator.utils.logger import get_logger

logger = get_logger(__name__)

# The engine instance is injected at startup by run.py
_engine = None


# ---------------------------------------------------------------------------
# Request models — defined at module level so Pydantic v2 can resolve
# type annotations correctly regardless of from __future__ import annotations
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


def create_app(engine) -> FastAPI:
    """
    Create the FastAPI application with the engine injected.

    Args:
        engine: ScenarioEngine instance.

    Returns:
        Configured FastAPI app.
    """
    global _engine
    _engine = engine

    app = FastAPI(
        title="Vitals Simulator Demo API",
        description="Control panel for the health and safety simulator",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/")
    async def index():
        """Serve the demo control UI."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"message": "Demo UI not found. Place index.html in demo/static/"})

    @app.get("/status")
    async def get_status():
        """Return current engine state."""
        try:
            status = _engine.get_status()
            rules_status = _engine.get_rules_status()
            
            return {
                "running":              status.running,
                "scenario_id":          status.scenario_id,
                "persona_id":           status.persona_id,
                "current_phase":        status.current_phase,
                "elapsed_sim_minutes":  round(status.elapsed_sim_minutes, 2),
                "total_sim_minutes":    status.total_sim_minutes,
                "progress_pct":         round(
                    (status.elapsed_sim_minutes / status.total_sim_minutes * 100)
                    if status.total_sim_minutes > 0 else 0, 1
                ),
                "compression":          status.compression,
                "events_fired":         status.events_fired,
                "sequence_number":      status.sequence_number,
                "available_events":     status.available_events,
                "rules":                rules_status,
            }
        except Exception as exc:
            logger.error("Status endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get engine status")
    
    @app.get("/rules/status")
    async def get_rules_status():
        """Return detailed rules engine status."""
        try:
            return _engine.get_rules_status()
        except Exception as exc:
            logger.error("Rules status endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get rules status")
    
    @app.get("/events")
    async def get_fired_events():
        """Return list of events that have been fired, including rule violations."""
        try:
            status = _engine.get_status()
            events_fired = status.events_fired or []
            
            # Add descriptions for events
            event_details = []
            for event_name in events_fired:
                if event_name.startswith("rule_violation_"):
                    # Get rule details for violations
                    rule_id = event_name.replace("rule_violation_", "")
                    rule_name = rule_id.replace("_", " ").title()
                    event_details.append({
                        "type": event_name,
                        "description": f"Rule Violation: {rule_name}",
                        "category": "violation"
                    })
                else:
                    # Regular scenario events  
                    event_details.append({
                        "type": event_name,
                        "description": event_name.replace("_", " ").title(),
                        "category": "scenario"
                    })
            
            return {"events": event_details}
        except Exception as exc:
            logger.error("Events endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get events")

    @app.get("/rules/violations")
    async def get_recent_violations():
        """Return recent rule violations."""
        try:
            rules_engine = _engine._rules_engine
            if not rules_engine:
                return {"violations": [], "message": "Rules engine not enabled"}
            
            violations = rules_engine.get_recent_violations(limit=20)
            return {"violations": violations}
        except Exception as exc:
            logger.error("Rules violations endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get rule violations")
    
    @app.get("/events/recent")
    async def get_recent_events():
        """Return recent events including violations for demo UI log."""
        try:
            status = _engine.get_status()
            events = []
            
            # Add regular scenario events
            for event in status.events_fired[-10:]:  # Last 10 events
                if event.startswith("rule_violation_"):
                    # This is a violation event
                    rule_id = event.replace("rule_violation_", "")
                    events.append({
                        "type": "violation", 
                        "name": event,
                        "description": f"Rule violation: {rule_id}",
                        "timestamp": time.time()
                    })
                else:
                    # Regular scenario event
                    events.append({
                        "type": "scenario",
                        "name": event,
                        "description": f"Scenario event: {event}",
                        "timestamp": time.time()
                    })
            
            return {"events": events}
        except Exception as exc:
            logger.error("Recent events endpoint error", extra={"event": "api_error", "error": str(exc)})
            raise HTTPException(status_code=500, detail="Failed to get recent events")

    @app.post("/scenario/load")
    async def load_scenario(req: LoadScenarioRequest):
        """Load a new scenario. Auto-resolves persona if mismatched."""
        try:
            persona_path  = Path(req.persona_path)
            scenario_path = Path(req.scenario_path)

            if not scenario_path.exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"Scenario file not found: {req.scenario_path}",
                )

            # Auto-resolve persona: read the scenario's required persona and
            # find the matching file rather than rejecting with a 400 error.
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
                raise HTTPException(
                    status_code=400,
                    detail=f"Persona file not found: {req.persona_path}",
                )

            await _engine.reload(persona_path, scenario_path)

            if req.compression is not None:
                if req.compression <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail="compression must be greater than 0",
                    )
                _engine.set_compression(req.compression)

            msg = "Scenario loaded successfully"
            if auto_resolved:
                msg += f" (auto-matched persona: {persona_path.stem})"
            return {"success": True, "message": msg}

        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Load scenario error",
                extra={"event": "api_load_error", "error": str(exc)},
            )
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/event/trigger")
    async def trigger_event(req: TriggerEventRequest):
        """Manually fire a named event immediately."""
        try:
            if not req.event_type or not req.event_type.strip():
                raise HTTPException(status_code=400, detail="event_type cannot be empty")
            duration = req.duration_seconds if (
                req.duration_seconds is not None and req.duration_seconds > 0
            ) else None
            found = _engine.trigger_event(req.event_type.strip(), duration)
            if not found:
                raise HTTPException(
                    status_code=404,
                    detail=f"Event type '{req.event_type}' not found in current scenario",
                )
            return {"success": True, "message": f"Event '{req.event_type}' triggered"}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Trigger event error",
                extra={"event": "api_trigger_error", "error": str(exc)},
            )
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/compression/set")
    async def set_compression(req: SetCompressionRequest):
        """Update time compression (speed) at runtime."""
        try:
            if req.compression <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="compression must be greater than 0",
                )
            _engine.set_compression(req.compression)
            return {"success": True, "compression": req.compression}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/scenarios")
    async def list_scenarios():
        """List available scenario YAML files with their required persona."""
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
            files = sorted(personas_dir.glob("*.yaml"))
            return {
                "personas": [
                    str(f.relative_to(Path(".")))
                    for f in files
                    if not f.name.startswith("_")
                ]
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return app
