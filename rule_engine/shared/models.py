"""
rule_engine/shared/models.py
-----------------------------
Lightweight dataclasses used by both edge and cloud engines.

No imports from the simulator package — this module must remain standalone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleReading:
    """A single sensor reading forwarded to the rule engine."""
    sensor_name:   str
    value:         float
    unit:          str
    persona_id:    str
    poc_type:      str
    device_id:     str
    phase:         str
    condition:     str      # "normal" | "warning" | "critical"
    compression:   float = 1.0
    timestamp:     float = field(default_factory=time.time)
    patient_label: str   = ""
    quality:       str   = "good"    # "good" | "uncertain" | "bad"
    fault_active:  bool  = False

    @classmethod
    def from_mqtt_payload(cls, payload: dict[str, Any]) -> "RuleReading":
        """Build from a parsed MQTT JSON payload dict."""
        return cls(
            sensor_name=   payload.get("sensor_name", ""),
            value=         float(payload.get("value", 0.0)),
            unit=          payload.get("unit", ""),
            persona_id=    payload.get("persona_id", ""),
            poc_type=      payload.get("poc_type", ""),
            device_id=     payload.get("device_id", ""),
            phase=         payload.get("phase", ""),
            condition=     payload.get("condition", "normal"),
            compression=   float(payload.get("compression", 1.0)),
            patient_label= payload.get("patient_label", ""),
            quality=       payload.get("quality", "good"),
            fault_active=  bool(payload.get("fault_active", False)),
        )


@dataclass
class RuleAlert:
    """Alert emitted by an engine when a rule fires."""
    rule_id:          str
    description:      str
    severity:         str     # "info" | "warning" | "critical" | "emergency"
    source:           str     # "edge" | "cloud"
    persona_id:       str
    device_id:        str
    sensor_name:      str
    sensor_value:     float
    threshold:        float
    timestamp:        float = field(default_factory=time.time)
    risk_score:       float = 0.0
    risk_level:       str   = "none"
    conditions_met:   list[str] = field(default_factory=list)
    patient_label:    str   = ""

    def to_mqtt_payload(self) -> dict[str, Any]:
        from datetime import datetime, timezone
        return {
            "timestamp_utc":  datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "rule_id":        self.rule_id,
            "description":    self.description,
            "severity":       self.severity,
            "source":         self.source,
            "persona_id":     self.persona_id,
            "device_id":      self.device_id,
            "sensor_name":    self.sensor_name,
            "sensor_value":   round(self.sensor_value, 3),
            "threshold":      self.threshold,
            "risk_score":     round(self.risk_score, 1),
            "risk_level":     self.risk_level,
            "conditions_met": self.conditions_met,
            "patient_label":  self.patient_label or self.device_id,
        }
