"""
rule_engine/edge/engine.py
---------------------------
Edge rule engine — direct sensor evaluation, no MQTT round-trip.

Mirrors the computation model of an on-device rule engine (e.g. ESP32 firmware):
  - Receives SensorReadings synchronously on every simulator tick.
  - Evaluates threshold rules immediately with two noise-rejection mechanisms:
      1. consecutive_readings  — last N readings must all breach.
      2. min_observation_seconds — condition must be continuously true for N real-seconds.
  - After a rule fires, publishes the alert to MQTT (alerts/edge/{persona_id}/{rule_id})
    so remote monitoring systems receive it — same as a wearable transmitting over BLE/LTE.
  - Also keeps a local deque of recent alerts for demo_api direct access (dev/offline mode).

Called by: simulator.core.scenario_engine.ScenarioEngine._tick_sensor()
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from rule_engine.edge.config import EdgeConfig, EdgeRule
from rule_engine.shared.logger import get_logger
from simulator.sensors.base import SensorReading

logger = get_logger(__name__)

_OPERATORS = {
    ">":  lambda v, t: v > t,
    "<":  lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
}


@dataclass
class _RuleState:
    """Per (rule_id, device_id) evaluation state."""
    # Sliding window of recent breach booleans for consecutive_readings check
    breach_window: deque = field(default_factory=lambda: deque(maxlen=1))
    # Wall-clock time when the current continuous breach started (None = not breaching)
    breach_start: float | None = None
    # Wall-clock time the rule last fired (for cooldown)
    last_fired: float = 0.0


class EdgeEngine:
    """
    Threshold rule evaluator that reads sensor values directly.

    One instance is shared across all sensor ticks for a single patient engine.
    Multiple patient engines each own their own EdgeEngine instance.
    """

    def __init__(self, config: EdgeConfig, mqtt_config: dict[str, Any]) -> None:
        self._config      = config
        self._mqtt_config = mqtt_config
        self._publisher   = None   # set via set_mqtt_publisher()

        # (rule_id, device_id) → _RuleState
        self._states: dict[tuple[str, str], _RuleState] = {}

        # Initialise per-rule breach windows to the correct maxlen
        self._rule_map: dict[str, EdgeRule] = {r.id: r for r in config.rules}

        # Bounded alert history for demo_api direct read
        self._recent_alerts: deque[dict[str, Any]] = deque(maxlen=200)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_mqtt_publisher(self, publisher: Any) -> None:
        """Provide the shared MQTTPublisher so alerts can be transmitted."""
        self._publisher = publisher

    def process_reading(
        self,
        reading: SensorReading,
        persona_id: str,
        poc_type: str,
        device_id: str,
    ) -> None:
        """Evaluate all rules against one sensor reading. Synchronous — no I/O."""
        now = time.monotonic()
        wall = time.time()

        for rule in self._config.rules:
            if rule.sensor != reading.sensor_name:
                continue

            op = _OPERATORS.get(rule.operator)
            if op is None:
                continue

            key   = (rule.id, device_id)
            state = self._get_state(key, rule)

            breaching = op(reading.value, rule.threshold)

            # --- update breach window (consecutive_readings check) ---
            state.breach_window.append(breaching)

            if not breaching:
                # Condition cleared — reset the observation clock
                state.breach_start = None
                continue

            # --- start / maintain observation clock ---
            if state.breach_start is None:
                state.breach_start = now

            # --- noise rejection gate 1: consecutive readings ---
            if len(state.breach_window) < rule.consecutive_readings:
                continue
            if not all(state.breach_window):
                continue

            # --- noise rejection gate 2: min observation window ---
            elapsed = now - state.breach_start
            if elapsed < rule.min_observation_seconds:
                continue

            # --- cooldown gate ---
            if (now - state.last_fired) < rule.cooldown_seconds:
                continue

            # --- fire ---
            state.last_fired  = now
            state.breach_start = None   # reset so re-breach needs a fresh window
            state.breach_window.clear()

            alert = self._build_alert(
                rule=         rule,
                persona_id=   persona_id,
                poc_type=     poc_type,
                device_id=    device_id,
                sensor_value= reading.value,
                elapsed_secs= elapsed,
                wall=         wall,
            )
            self._recent_alerts.appendleft(alert)
            self._publish(alert, rule)

            logger.info(
                "Edge alert fired",
                extra={
                    "event":      "edge_alert",
                    "rule_id":    rule.id,
                    "persona_id": persona_id,
                    "sensor":     rule.sensor,
                    "value":      reading.value,
                    "threshold":  rule.threshold,
                    "severity":   rule.severity,
                },
            )

    def get_recent_alerts(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return the most recent alerts — direct memory read, no MQTT needed."""
        return list(self._recent_alerts)[:limit]

    def reset(self) -> None:
        """Clear all evaluation state when a scenario is reloaded."""
        self._states.clear()
        self._recent_alerts.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, key: tuple[str, str], rule: EdgeRule) -> _RuleState:
        if key not in self._states:
            state = _RuleState(
                breach_window=deque(maxlen=rule.consecutive_readings),
            )
            self._states[key] = state
        return self._states[key]

    def _build_alert(
        self,
        *,
        rule: EdgeRule,
        persona_id: str,
        poc_type: str,
        device_id: str,
        sensor_value: float,
        elapsed_secs: float,
        wall: float,
    ) -> dict[str, Any]:
        return {
            "timestamp_utc":  datetime.fromtimestamp(wall, tz=timezone.utc).isoformat(),
            "rule_id":        rule.id,
            "description":    rule.description,
            "severity":       rule.severity,
            "source":         "edge",
            "persona_id":     persona_id,
            "poc_type":       poc_type,
            "device_id":      device_id,
            "sensor_name":    rule.sensor,
            "sensor_value":   round(sensor_value, 3),
            "threshold":      rule.threshold,
            "risk_score":     0.0,
            "risk_level":     "none",
            "conditions_met": [
                f"{rule.sensor} {rule.operator} {rule.threshold} "
                f"for {int(elapsed_secs)}s"
            ],
        }

    def _publish(self, alert: dict[str, Any], rule: EdgeRule) -> None:
        """Publish alert to MQTT so remote monitoring receives it."""
        if self._publisher is None:
            return
        topic   = f"{self._config.alert_topic_prefix}/{alert['persona_id']}/{rule.id}"
        payload = json.dumps(alert)
        try:
            self._publisher.publish_raw(topic, payload, qos=1)
        except Exception as exc:
            logger.error(
                "Edge alert publish failed",
                extra={"event": "edge_publish_error", "rule_id": rule.id, "error": str(exc)},
            )
