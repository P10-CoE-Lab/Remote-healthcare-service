"""
rule_engine/llm/context_builder.py
------------------------------------
Builds the structured context dict consumed by summariser.py.

Queries InfluxDB for the last 5 real minutes of HR, SpO₂, and HRV.
Computes trend direction per sensor (recent vs earlier window).
Reads persona descriptions from YAML files (cached).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

_INFLUX_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
_INFLUX_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "my-token")
_INFLUX_ORG    = os.environ.get("INFLUXDB_ORG",    "iot_org")
_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "iot_poc")

# Cache persona descriptions at module level (they're static)
_persona_cache: dict[str, str] = {}


def _get_persona_description(persona_id: str) -> str:
    if persona_id in _persona_cache:
        return _persona_cache[persona_id]
    path = Path("personas") / f"{persona_id}.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        desc = raw.get("description", persona_id)
    except Exception:
        desc = persona_id
    _persona_cache[persona_id] = desc
    return desc


async def _fetch_history(device_id: str, elapsed_real_min: float = 5.0) -> dict[str, list[dict]]:
    """Query InfluxDB for the full monitoring session worth of vitals."""
    # Floor at 5 min so very early briefings still have some data; cap at 60 min
    # to keep the query lightweight.
    window = max(5.0, min(elapsed_real_min, 60.0))
    flux = f"""
from(bucket: "{_INFLUX_BUCKET}")
  |> range(start: -{window:.0f}m)
  |> filter(fn: (r) => r["_measurement"] == "vitals")
  |> filter(fn: (r) => r["device_id"] == "{device_id}")
  |> filter(fn: (r) => r["_field"] == "value")
  |> filter(fn: (r) =>
      r["sensor_name"] == "heart_rate" or
      r["sensor_name"] == "spo2" or
      r["sensor_name"] == "heart_rate_variability")
"""
    buffers: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_INFLUX_URL}/api/v2/query",
                params={"org": _INFLUX_ORG},
                headers={
                    "Authorization": f"Token {_INFLUX_TOKEN}",
                    "Content-Type":  "application/vnd.flux",
                    "Accept":        "application/csv",
                },
                content=flux,
            )
            resp.raise_for_status()
    except Exception:
        return {}

    col_time = col_value = col_sensor = -1
    from datetime import datetime

    for line in resp.text.splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split(",")
        if cols[1] == "result":
            col_time   = cols.index("_time")       if "_time"       in cols else -1
            col_value  = cols.index("_value")      if "_value"      in cols else -1
            col_sensor = cols.index("sensor_name") if "sensor_name" in cols else -1
            continue
        if col_time < 0 or col_value < 0 or col_sensor < 0:
            continue
        try:
            ts = datetime.fromisoformat(cols[col_time].replace("Z", "+00:00")).timestamp()
            v  = float(cols[col_value])
            sn = cols[col_sensor]
            buffers.setdefault(sn, []).append({"ts": ts, "value": v})
        except Exception:
            continue
    return buffers


def _trend(readings: list[dict]) -> str:
    """Classify trend using first vs last quarter of the session — compression-agnostic."""
    if len(readings) < 6:
        return "stable"
    ordered = sorted(readings, key=lambda r: r["ts"])
    n = len(ordered)
    quarter = max(1, n // 4)
    earlier = [r["value"] for r in ordered[:quarter]]
    recent  = [r["value"] for r in ordered[3 * quarter:]]
    if not recent or not earlier:
        return "stable"
    mean_r = sum(recent)  / len(recent)
    mean_e = sum(earlier) / len(earlier)
    if mean_e == 0:
        return "stable"
    pct = (mean_r - mean_e) / mean_e * 100
    if pct > 3:
        return f"rising (+{abs(pct):.0f}%)"
    if pct < -3:
        return f"falling (-{abs(pct):.0f}%)"
    return "stable"


def _last_value(readings: list[dict]) -> float | None:
    return readings[-1]["value"] if readings else None


async def build_alert_context(
    alert:        dict[str, Any],
    baseline:     dict[str, Any] | None = None,
    session_alerts: list[dict] | None   = None,
) -> dict[str, Any]:
    """Build context dict for per-alert LLM explanation (Mode A)."""
    device_id  = alert.get("device_id", "")
    persona_id = alert.get("persona_id", "")
    label      = alert.get("patient_label", alert.get("patient_id", "Patient"))

    history = await _fetch_history(device_id)
    hr_hist   = history.get("heart_rate", [])
    spo2_hist = history.get("spo2", [])
    hrv_hist  = history.get("heart_rate_variability", [])

    bl = baseline or {}
    personalised = bool(bl)

    conditions_met = alert.get("conditions_met", [])
    shap_lines = [c for c in conditions_met if "%" in c and "baseline" not in c.lower()[:5]]

    recent_alerts = []
    if session_alerts:
        for a in session_alerts[-3:]:
            ts = a.get("timestamp", 0)
            age_s = time.time() - ts
            recent_alerts.append({
                "time_ago":    _age_str(age_s),
                "description": a.get("rule_name", a.get("rule_id", "")),
                "severity":    a.get("severity", "warning"),
            })

    return {
        "patient": {
            "label":                    label,
            "persona_id":               persona_id,
            "description":              _get_persona_description(persona_id),
        },
        "baseline": {
            "hr_normal":     f"{bl.get('hr_mean', '—'):.0f} ± {bl.get('hr_std', '—'):.1f} bpm" if bl.get("hr_mean") else "population average",
            "spo2_normal":   f"{bl.get('spo2_mean', '—'):.1f} ± {bl.get('spo2_std', '—'):.1f}%" if bl.get("spo2_mean") else "population average",
            "hrv_normal":    f"{bl.get('hrv_mean', '—'):.0f} ± {bl.get('hrv_std', '—'):.1f} ms" if bl.get("hrv_mean") else "population average",
            "personalised":  personalised,
        },
        "current_vitals": {
            "heart_rate": {"value": _last_value(hr_hist),   "unit": "bpm", "trend": _trend(hr_hist)},
            "spo2":       {"value": _last_value(spo2_hist), "unit": "%",   "trend": _trend(spo2_hist)},
            "hrv":        {"value": _last_value(hrv_hist),  "unit": "ms",  "trend": _trend(hrv_hist)},
        },
        "alert": {
            "rule_id":            alert.get("rule_id", ""),
            "description":        alert.get("rule_name", alert.get("description", "")),
            "severity":           alert.get("severity", "warning"),
            "risk_score":         alert.get("risk_score"),
            "shap_contributions": shap_lines,
        },
        "recent_session_alerts": recent_alerts,
    }


async def build_summary_context(
    patient_id:   str,
    device_id:    str,
    persona_id:   str,
    label:        str,
    compression:  float,
    elapsed_min:  float,
    baseline:     dict[str, Any] | None = None,
    session_alerts: list[dict] | None   = None,
) -> dict[str, Any]:
    """Build context dict for on-demand patient briefing (Mode B)."""
    # Query InfluxDB for the full monitoring session, not just the last 5 minutes.
    elapsed_real_min = elapsed_min / max(compression, 1.0)
    history = await _fetch_history(device_id, elapsed_real_min)
    hr_hist   = history.get("heart_rate", [])
    spo2_hist = history.get("spo2", [])
    hrv_hist  = history.get("heart_rate_variability", [])

    bl = baseline or {}
    personalised = bool(bl)

    # Include all session alerts so the LLM understands the full alert pattern,
    # not just the 3 most recent.
    all_alerts = []
    if session_alerts:
        for a in sorted(session_alerts, key=lambda x: x.get("timestamp") or 0):
            ts = a.get("timestamp") or 0
            all_alerts.append({
                "time_ago":    _age_str(time.time() - ts),
                "rule":        a.get("rule_id", ""),
                "description": a.get("rule_name", ""),
                "severity":    a.get("severity", "warning"),
                "source":      a.get("source", ""),
            })

    return {
        "patient": {
            "label":                      label,
            "persona_id":                 persona_id,
            "description":                _get_persona_description(persona_id),
            "monitoring_session_minutes": round(elapsed_min, 1),
        },
        "personal_baseline": {
            "hr_normal":    f"{bl.get('hr_mean', '—'):.0f} ± {bl.get('hr_std', '—'):.1f} bpm" if bl.get("hr_mean") else "population average",
            "spo2_normal":  f"{bl.get('spo2_mean', '—'):.1f} ± {bl.get('spo2_std', '—'):.1f}%" if bl.get("spo2_mean") else "population average",
            "hrv_normal":   f"{bl.get('hrv_mean', '—'):.0f} ± {bl.get('hrv_std', '—'):.1f} ms" if bl.get("hrv_mean") else "population average",
            "personalised": personalised,
        },
        "current_vitals": {
            "heart_rate": {"value": _last_value(hr_hist),   "unit": "bpm", "trend": _trend(hr_hist)},
            "spo2":       {"value": _last_value(spo2_hist), "unit": "%",   "trend": _trend(spo2_hist)},
            "hrv":        {"value": _last_value(hrv_hist),  "unit": "ms",  "trend": _trend(hrv_hist)},
        },
        "alert_history": {
            "total_alerts_fired": len(all_alerts),
            "alerts": all_alerts,
        },
        "compression": compression,
    }


def _age_str(seconds: float) -> str:
    if seconds < 120:
        return f"{int(seconds)} sec"
    return f"{int(seconds / 60)} min"
